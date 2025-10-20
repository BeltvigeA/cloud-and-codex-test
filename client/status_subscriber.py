"""Background subscriber for streaming status updates from Bambu printers."""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from .bambuPrinter import extractStateText, looksLikeAmsFilamentConflict, safeDisconnectPrinter


try:  # pragma: no cover - dependency handled dynamically in tests
    _bambuModule = importlib.import_module("bambulabs_api")
    _printerClass = getattr(_bambuModule, "Printer", None)
except ImportError:  # pragma: no cover - surfaced via callbacks at runtime
    _bambuModule = None
    _printerClass = None


@dataclass(frozen=True)
class PrinterCredentials:
    """Normalized credentials for connecting to a printer."""

    ipAddress: str
    serialNumber: str
    accessCode: str
    nickname: Optional[str] = None


class BambuStatusSubscriber:
    """Manage live status subscriptions across multiple Bambu printers."""

    def __init__(
        self,
        onUpdate: Callable[[Dict[str, Any], Dict[str, Any]], None],
        onError: Callable[[str, Dict[str, Any]], None],
        *,
        logger: Optional[logging.Logger] = None,
        pollInterval: float = 1.0,
        heartbeatInterval: float = 5.0,
        reconnectDelay: float = 3.0,
    ) -> None:
        self.onUpdate = onUpdate
        self.onError = onError
        self.log = logger or logging.getLogger(__name__)
        self.pollInterval = max(0.5, float(pollInterval))
        self.heartbeatInterval = max(1.0, float(heartbeatInterval))
        self.reconnectDelay = max(1.0, float(reconnectDelay))
        self._threads: Dict[str, threading.Thread] = {}
        self._stops: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def startAll(self, printers: Iterable[Dict[str, Any]]) -> None:
        """Start worker threads for each printer configuration."""

        for printerConfig in printers:
            self.startPrinter(printerConfig)

    def start_all(self, printers: Iterable[Dict[str, Any]]) -> None:
        """Snake_case alias maintained for compatibility with legacy callers."""

        self.startAll(printers)

    def startPrinter(self, printerConfig: Dict[str, Any]) -> None:
        serial = str(printerConfig.get("serialNumber") or "").strip()
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        accessCode = str(printerConfig.get("accessCode") or "").strip()
        if not serial or not ipAddress or not accessCode:
            sanitizedMessage = "Missing printer credentials (ip/serial/access)"
            self.onError(sanitizedMessage, dict(printerConfig))
            return

        with self._lock:
            if serial in self._stops:
                return

            stopEvent = threading.Event()
            workerThread = threading.Thread(
                target=self._worker,
                args=(dict(printerConfig), stopEvent),
                name=f"BambuStatusSubscriber-{serial}",
                daemon=True,
            )
            self._stops[serial] = stopEvent
            self._threads[serial] = workerThread
            workerThread.start()

    def start_printer(self, printerConfig: Dict[str, Any]) -> None:
        self.startPrinter(printerConfig)

    def stopPrinter(self, serialNumber: str) -> None:
        serial = str(serialNumber or "").strip()
        if not serial:
            return
        with self._lock:
            stopEvent = self._stops.pop(serial, None)
            workerThread = self._threads.pop(serial, None)
        if stopEvent:
            stopEvent.set()
        if workerThread and workerThread.is_alive():
            workerThread.join(timeout=self.heartbeatInterval)

    def stop_printer(self, serialNumber: str) -> None:
        self.stopPrinter(serialNumber)

    def stopAll(self) -> None:
        with self._lock:
            stopEvents = list(self._stops.values())
            workerThreads = list(self._threads.values())
            self._stops.clear()
            self._threads.clear()

        for event in stopEvents:
            event.set()
        for workerThread in workerThreads:
            if workerThread.is_alive():
                workerThread.join(timeout=self.heartbeatInterval)

    def stop_all(self) -> None:
        self.stopAll()

    def _worker(self, printerConfig: Dict[str, Any], stopEvent: threading.Event) -> None:
        serial = str(printerConfig.get("serialNumber") or "").strip()
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        accessCode = str(printerConfig.get("accessCode") or "").strip()
        nickname = printerConfig.get("nickname")

        if _printerClass is None:
            self.onError("bambulabs_api.Printer is unavailable", dict(printerConfig))
            return

        lastSnapshot: Optional[Dict[str, Any]] = None
        lastEmit = 0.0

        while not stopEvent.is_set():
            printerInstance = None
            try:
                printerInstance = _printerClass(ipAddress, accessCode, serial)
                self._connectPrinter(printerInstance)

                while not stopEvent.is_set():
                    statusPayload = self._collectSnapshot(printerInstance, printerConfig)
                    statusPayload["printerSerial"] = serial
                    statusPayload["printerIp"] = ipAddress
                    statusPayload["nickname"] = nickname
                    statusPayload["status"] = statusPayload.get("status") or "update"

                    emitNow = False
                    if lastSnapshot is None:
                        emitNow = True
                    elif self._statusChanged(lastSnapshot, statusPayload):
                        emitNow = True
                    elif time.monotonic() - lastEmit >= self.heartbeatInterval:
                        emitNow = True

                    if emitNow:
                        lastSnapshot = dict(statusPayload)
                        lastEmit = time.monotonic()
                        try:
                            self.onUpdate(dict(statusPayload), dict(printerConfig))
                        except Exception:  # pragma: no cover - consumer responsibility
                            self.log.exception("Status update callback failed")

                    if stopEvent.wait(self.pollInterval):
                        break

            except Exception as error:  # noqa: BLE001 - ensure resiliency in background threads
                if stopEvent.is_set():
                    break
                sanitizedMessage = self._sanitizeErrorMessage(str(error), accessCode)
                self.onError(sanitizedMessage, dict(printerConfig))
                stopEvent.wait(self.reconnectDelay)
            finally:
                if printerInstance is not None:
                    safeDisconnectPrinter(printerInstance)

    def _connectPrinter(self, printer: Any) -> None:
        connectMethod = getattr(printer, "mqtt_start", None) or getattr(printer, "connect", None)
        if callable(connectMethod):
            try:
                connectMethod()
            except Exception as error:  # pragma: no cover - surface via callbacks
                raise RuntimeError(f"Unable to connect printer: {error}") from error

    def _collectSnapshot(self, printer: Any, printerConfig: Dict[str, Any]) -> Dict[str, Any]:
        statePayload: Any = None
        percentagePayload: Any = None
        gcodePayload: Any = None

        try:
            statePayload = printer.get_state()
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            self.log.debug("get_state failed", exc_info=error)

        try:
            percentagePayload = printer.get_percentage()
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            self.log.debug("get_percentage failed", exc_info=error)

        gcodeGetter = getattr(printer, "get_gcode_state", None)
        if callable(gcodeGetter):
            try:
                gcodePayload = gcodeGetter()
            except Exception as error:  # pragma: no cover - depends on SDK behaviour
                self.log.debug("get_gcode_state failed", exc_info=error)

        snapshot = self._normalizeSnapshot(statePayload, percentagePayload, gcodePayload, printerConfig)
        return snapshot

    def _normalizeSnapshot(
        self,
        statePayload: Any,
        percentagePayload: Any,
        gcodePayload: Any,
        printerConfig: Dict[str, Any],
    ) -> Dict[str, Any]:
        sources = [payload for payload in (statePayload, percentagePayload, gcodePayload) if payload is not None]

        gcodeState = self._coerceString(gcodePayload)
        if not gcodeState:
            gcodeStateCandidate = self._findValue(sources, {"gcode_state", "gcodeState", "subtask_name"})
            gcodeState = self._coerceString(gcodeStateCandidate)

        progressCandidate = self._findValue(
            sources,
            {"mc_percent", "progress", "percentage", "progressPercent"},
        )
        if progressCandidate is None:
            progressCandidate = percentagePayload
        progressPercent = self._coerceFloat(progressCandidate)

        remainingCandidate = self._findValue(
            sources,
            {"mc_remaining_time", "remaining_time", "remainingTimeSeconds"},
        )
        remainingTimeSeconds = self._coerceInt(remainingCandidate)

        nozzleCandidate = self._findValue(sources, {"nozzle_temper", "nozzleTemp", "nozzle_temperature"})
        nozzleTemp = self._coerceFloat(nozzleCandidate)

        bedCandidate = self._findValue(sources, {"bed_temper", "bedTemp", "bed_temperature"})
        bedTemp = self._coerceFloat(bedCandidate)

        stateText = extractStateText(statePayload) or gcodeState or ""
        hmsCode = self._extractHmsCode(sources)
        errorMessage = self._extractErrorMessage(sources)
        if not hmsCode and looksLikeAmsFilamentConflict(statePayload):
            hmsCode = "HMS_07FF-2000-0002-0004"
            if not errorMessage:
                errorMessage = "Possible AMS filament conflict"

        normalized: Dict[str, Any] = {
            "status": "update",
            "state": stateText,
            "gcodeState": gcodeState,
            "progressPercent": progressPercent,
            "nozzleTemp": nozzleTemp,
            "bedTemp": bedTemp,
            "remainingTimeSeconds": remainingTimeSeconds,
            "hmsCode": hmsCode,
            "errorMessage": errorMessage,
        }

        return normalized

    def _extractHmsCode(self, sources: List[Any]) -> Optional[str]:
        candidate = self._findValue(sources, {"hms", "hms_code", "error_code", "print_error_code"})
        textCandidate = self._coerceString(candidate)
        if textCandidate and textCandidate.upper().startswith("HMS_"):
            return textCandidate

        combinedText = self._stringifyFragments(sources)
        if combinedText:
            for token in combinedText.replace("\n", " ").split():
                if token.upper().startswith("HMS_"):
                    return token.strip(".,;:()[]{}")
        return None

    def _extractErrorMessage(self, sources: List[Any]) -> Optional[str]:
        candidate = self._findValue(
            sources,
            {"error_message", "err_msg", "error", "message", "tips", "desc", "description"},
        )
        textCandidate = self._coerceString(candidate)
        if textCandidate:
            return textCandidate

        combinedText = self._stringifyFragments(sources)
        if combinedText:
            lowered = combinedText.lower()
            if any(marker in lowered for marker in ("error", "warning", "filament", "conflict")):
                return combinedText.strip()
        return None

    def _statusChanged(self, previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
        trackedKeys = (
            "state",
            "gcodeState",
            "progressPercent",
            "nozzleTemp",
            "bedTemp",
            "remainingTimeSeconds",
            "hmsCode",
            "errorMessage",
        )
        for key in trackedKeys:
            if self._valuesDiffer(previous.get(key), current.get(key)):
                return True
        return False

    def _valuesDiffer(self, first: Any, second: Any) -> bool:
        if first is None and second is None:
            return False
        if isinstance(first, (int, float)) and isinstance(second, (int, float)):
            return abs(float(first) - float(second)) > 0.05
        return first != second

    def _findValue(self, sources: Iterable[Any], keyNames: Set[str]) -> Any:
        normalizedTargets = {self._normalizeKey(name) for name in keyNames}
        sentinel = object()

        def search(value: Any) -> Any:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalizedKey = self._normalizeKey(key)
                    if normalizedKey in normalizedTargets:
                        return nested
                    result = search(nested)
                    if result is not sentinel:
                        return result
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    result = search(item)
                    if result is not sentinel:
                        return result
            return sentinel

        for source in sources:
            result = search(source)
            if result is not sentinel:
                return result
        return None

    def _normalizeKey(self, key: Any) -> str:
        return str(key).strip().replace("-", "_").replace(" ", "_").lower()

    def _coerceFloat(self, value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            candidate = value.strip().replace("°c", "").replace("°", "")
            if candidate:
                try:
                    return float(candidate)
                except ValueError:
                    return None
        return None

    def _coerceInt(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.isdigit():
                try:
                    return int(candidate)
                except ValueError:
                    return None
        return None

    def _coerceString(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return str(value) if value else None

    def _stringifyFragments(self, sources: Iterable[Any]) -> str:
        fragments: List[str] = []
        for source in sources:
            fragments.append(self._stringifyFragment(source))
        joined = " ".join(fragment for fragment in fragments if fragment)
        return joined.strip()

    def _stringifyFragment(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            return " ".join(self._stringifyFragment(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return " ".join(self._stringifyFragment(item) for item in value)
        return str(value)

    def _sanitizeErrorMessage(self, message: str, accessCode: str) -> str:
        if accessCode and accessCode in message:
            return message.replace(accessCode, "***")
        return message
