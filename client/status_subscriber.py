"""Background subscriber for streaming status updates from Bambu printers."""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import requests

from .bambuPrinter import extractStateText, looksLikeAmsFilamentConflict, safeDisconnectPrinter
from .base44_client import postReportError, postUpdateStatus

# Reduce noise from third-party SDK logger
logging.getLogger("bambulabs_api").setLevel(logging.WARNING)


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
        self.statusDebugEnabled = (
            str(os.getenv("PRINTMASTER_STATUS_DEBUG", "")).strip().lower()
            not in ("", "0", "false", "off")
        )
        self.pollInterval = max(0.5, float(pollInterval))
        self.heartbeatInterval = max(1.0, float(heartbeatInterval))
        self.reconnectDelay = max(1.0, float(reconnectDelay))
        self._threads: Dict[str, threading.Thread] = {}
        self._stops: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.errorCountBySerial: Dict[str, int] = {}
        self.errorCountLock = threading.Lock()
        self.logEvery = 50
        self.defaultRecipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()

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

        self.defaultRecipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()

        if not self._pingHost(ipAddress, 1000):
            if self._shouldLogConnectionFailure(serial):
                sanitizedMessage = f"Printer unreachable at {ipAddress}"
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
        self._resetConnectionFailures(serial)

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
                self._resetConnectionFailures(serial)
                printerMetadata = self._fetchPrinterMetadata(printerInstance)
                lastBase44Comparable: Optional[Dict[str, Any]] = None
                lastBase44Emit = 0.0
                lastErrorComparable: Optional[Dict[str, Any]] = None
                lastErrorEmit = 0.0

                while not stopEvent.is_set():
                    resolvedApiKey = self._resolveBase44ApiKey(printerConfig)
                    if resolvedApiKey:
                        self._ensureEnvironmentValue("BASE44_FUNCTIONS_API_KEY", resolvedApiKey)
                        self._ensureEnvironmentValue("BASE44_API_KEY", resolvedApiKey)

                    statusPayload = self._collectSnapshot(printerInstance, printerConfig, printerMetadata)
                    statusPayload["printerSerial"] = serial
                    statusPayload["printerIp"] = ipAddress
                    statusPayload["nickname"] = nickname
                    statusPayload["status"] = statusPayload.get("status") or "update"

                    base44Package = self._buildBase44Payloads(statusPayload, printerConfig, resolvedApiKey)
                    if base44Package is not None:
                        (
                            updatePayload,
                            updateComparable,
                            errorPayload,
                            errorComparable,
                        ) = base44Package

                        if updatePayload and updateComparable is not None:
                            shouldSendUpdate = False
                            if self._payloadsDiffer(lastBase44Comparable, updateComparable):
                                shouldSendUpdate = True
                            elif time.monotonic() - lastBase44Emit >= self.heartbeatInterval:
                                shouldSendUpdate = True

                            if shouldSendUpdate:
                                try:
                                    postUpdateStatus(updatePayload)
                                except Exception as error:
                                    self._logBase44Failure("update", error)
                                else:
                                    lastBase44Comparable = dict(updateComparable)
                                    lastBase44Emit = time.monotonic()

                        if errorPayload and errorComparable is not None:
                            shouldSendError = False
                            if self._payloadsDiffer(lastErrorComparable, errorComparable):
                                shouldSendError = True
                            elif time.monotonic() - lastErrorEmit >= self.heartbeatInterval:
                                shouldSendError = True

                            if shouldSendError:
                                try:
                                    postReportError(errorPayload)
                                except Exception as error:
                                    self._logBase44Failure("error", error)
                                else:
                                    lastErrorComparable = dict(errorComparable)
                                    lastErrorEmit = time.monotonic()

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
                if self._shouldLogConnectionFailure(serial):
                    self.onError(sanitizedMessage, dict(printerConfig))
                stopEvent.wait(self.reconnectDelay * 2)
            finally:
                if printerInstance is not None:
                    safeDisconnectPrinter(printerInstance)

    def _shouldLogConnectionFailure(self, serial: str) -> bool:
        key = serial or "unknown"
        with self.errorCountLock:
            failureCount = self.errorCountBySerial.get(key, 0) + 1
            self.errorCountBySerial[key] = failureCount
        return failureCount == 1 or failureCount % self.logEvery == 0

    def _resetConnectionFailures(self, serial: str) -> None:
        key = serial or "unknown"
        with self.errorCountLock:
            if key in self.errorCountBySerial:
                self.errorCountBySerial.pop(key, None)

    def _pingHost(self, ipAddress: str, timeoutMillis: int) -> bool:
        if not ipAddress:
            return False
        pingExecutable = shutil.which("ping")
        if not pingExecutable:
            return True
        systemName = platform.system().lower()
        timeoutSeconds = max(1, int(max(timeoutMillis, 100) / 1000))
        if "windows" in systemName:
            command = [
                pingExecutable,
                "-n",
                "1",
                "-w",
                str(max(timeoutMillis, 100)),
                ipAddress,
            ]
        else:
            command = [
                pingExecutable,
                "-c",
                "1",
                "-W",
                str(timeoutSeconds),
                ipAddress,
            ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return True

    def _connectPrinter(self, printer: Any) -> None:
        mqttStart = getattr(printer, "mqtt_start", None)
        if callable(mqttStart):
            try:
                startTime = time.perf_counter()
                mqttStart()
                self.log.info(
                    "[status] mqtt_start() ok in %.3fs",
                    time.perf_counter() - startTime,
                )
            except Exception as error:  # pragma: no cover - surface via callbacks
                raise RuntimeError(f"Unable to start printer MQTT: {error}") from error

        connectMethod = getattr(printer, "connect", None)
        if callable(connectMethod):
            try:
                connectStartTime = time.perf_counter()
                connectMethod()
                self.log.info(
                    "[status] connect() ok in %.3fs",
                    time.perf_counter() - connectStartTime,
                )
            except Exception as error:  # pragma: no cover - surface via callbacks
                raise RuntimeError(f"Unable to connect printer: {error}") from error

        try:
            from . import bambuPrinter as _bp

            wait = getattr(_bp, "_waitForMqttReady", None)
            if callable(wait):
                readinessStartTime = time.perf_counter()
                wait(printer, timeout=15.0)
                self.log.info(
                    "[status] readiness ok in %.3fs",
                    time.perf_counter() - readinessStartTime,
                )
        except Exception:  # pragma: no cover - readiness wait is best effort
            pass

    def _collectSnapshot(
        self,
        printer: Any,
        printerConfig: Dict[str, Any],
        printerMetadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        statePayload: Any = None
        percentagePayload: Any = None
        gcodePayload: Any = None

        try:
            statePayload = printer.get_state()
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            self.log.debug("get_state failed", exc_info=error)

        if self.statusDebugEnabled and isinstance(statePayload, dict):
            self.log.info("[status] keys(state)=%s", sorted(list(statePayload.keys()))[:40])

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

        snapshot = self._normalizeSnapshot(
            statePayload,
            percentagePayload,
            gcodePayload,
            printerConfig,
            printerMetadata,
        )
        return snapshot

    def _normalizeSnapshot(
        self,
        statePayload: Any,
        percentagePayload: Any,
        gcodePayload: Any,
        printerConfig: Dict[str, Any],
        printerMetadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sources = [
            payload
            for payload in (statePayload, percentagePayload, gcodePayload, printerMetadata)
            if payload is not None
        ]

        gcodeState = self._coerceString(gcodePayload)
        if not gcodeState:
            gcodeStateCandidate = self._findValue(sources, {"gcode_state", "gcodeState", "subtask_name"})
            gcodeState = self._coerceString(gcodeStateCandidate)

        progressCandidate = self._findValue(
            sources,
            {
                "mc_percent",
                "progress",
                "percentage",
                "progressPercent",
                "last_print_percentage",
                "print_percent",
                "percent",
            },
        )
        if progressCandidate is None:
            progressCandidate = percentagePayload
        progressPercent = self._coerceFloat(progressCandidate)

        remainingCandidate = self._findValue(
            sources,
            {"mc_remaining_time", "remaining_time", "remainingTimeSeconds"},
        )
        remainingTimeSeconds = self._coerceInt(remainingCandidate)

        nozzleCandidate = self._findValue(
            sources,
            {
                "nozzle_temper",
                "nozzle_temp",
                "nozzleTemp",
                "nozzle_temperature",
                "nozzle_current_temper",
                "nozzle_target_temper",
                "nozzle",
            },
        )
        nozzleTemp = self._coerceFloat(nozzleCandidate)

        bedCandidate = self._findValue(
            sources,
            {
                "bed_temper",
                "bed_temp",
                "bedTemp",
                "bed_temperature",
                "bed_current_temper",
                "bed_target_temper",
                "bed",
            },
        )
        bedTemp = self._coerceFloat(bedCandidate)

        fanCandidate = self._findValue(
            sources,
            {
                "fan_speed",
                "fanSpeed",
                "cooling_fan_speed",
                "chamber_fan_speed",
                "fan_gear",
                "fan",
            },
        )
        fanSpeedPercent = self._normalizePercentage(fanCandidate)

        printSpeedCandidate = self._findValue(
            sources,
            {"print_speed", "printSpeed", "speed", "speed_level", "speed_multiplier"},
        )
        printSpeed = self._coerceFloat(printSpeedCandidate)

        filamentCandidate = self._findValue(
            sources,
            {
                "filament_used",
                "filamentUsed",
                "filament_consumed",
                "filament_length",
                "filament_weight",
            },
        )
        filamentUsed = self._coerceFloat(filamentCandidate)

        jobCandidate = self._findValue(
            sources,
            {"job_id", "task_id", "current_job_id", "print_id", "jobId"},
        )
        currentJobId = self._coerceString(jobCandidate)

        if self.statusDebugEnabled:
            self.log.info(
                "[status] parsed progress=%s remaining=%s nozzle=%s bed=%s gcode=%s",
                progressPercent,
                remainingTimeSeconds,
                nozzleTemp,
                bedTemp,
                gcodeState,
            )

        firmwareVersion = self._extractFirmwareVersion(sources)

        stateText = extractStateText(statePayload) or gcodeState or ""
        hmsCode = self._extractHmsCode(sources)
        errorMessage = self._extractErrorMessage(sources)
        hasAmsConflict = False
        if not hmsCode and looksLikeAmsFilamentConflict(statePayload):
            hmsCode = "HMS_07FF-2000-0002-0004"
            hasAmsConflict = True
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
            "fanSpeedPercent": fanSpeedPercent,
            "printSpeed": printSpeed,
            "filamentUsed": filamentUsed,
            "currentJobId": currentJobId,
            "firmwareVersion": firmwareVersion,
            "hmsCode": hmsCode,
            "errorMessage": errorMessage,
            "hasAmsConflict": hasAmsConflict,
            "rawStatePayload": statePayload,
            "rawPercentagePayload": percentagePayload,
            "rawGcodePayload": gcodePayload,
            "printerMetadata": printerMetadata,
        }

        return normalized

    def _normalizePercentage(self, value: Any) -> Optional[float]:
        numeric = self._coerceFloat(value)
        if numeric is None:
            return None
        if numeric < 0:
            return 0.0
        if numeric <= 1.0:
            numeric *= 100.0
        elif 1.0 < numeric <= 255.0 and numeric > 100.0:
            numeric = (numeric / 255.0) * 100.0
        return max(0.0, min(numeric, 100.0))

    def _fetchPrinterMetadata(self, printer: Any) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        for methodName in ("get_info", "get_printer_info", "printer_info"):
            infoGetter = getattr(printer, methodName, None)
            if callable(infoGetter):
                try:
                    infoPayload = infoGetter()
                    if infoPayload:
                        metadata.setdefault("info", infoPayload)
                        break
                except Exception as error:  # pragma: no cover - depends on SDK behaviour
                    self.log.debug("%s failed", methodName, exc_info=error)

        for methodName in ("get_version", "get_firmware_version"):
            versionGetter = getattr(printer, methodName, None)
            if callable(versionGetter):
                try:
                    firmwarePayload = versionGetter()
                    if firmwarePayload:
                        metadata.setdefault("firmware", firmwarePayload)
                        break
                except Exception as error:  # pragma: no cover - depends on SDK behaviour
                    self.log.debug("%s failed", methodName, exc_info=error)

        firmwareAttribute = getattr(printer, "firmware_version", None)
        if firmwareAttribute:
            metadata.setdefault("firmware", firmwareAttribute)

        return metadata

    def _extractFirmwareVersion(self, sources: Iterable[Any]) -> Optional[str]:
        firmwareCandidate = self._findValue(
            sources,
            {
                "firmware_version",
                "firmwareVersion",
                "firmware",
                "fw_ver",
                "fwVersion",
                "software_version",
            },
        )
        textCandidate = self._coerceString(firmwareCandidate)
        if textCandidate:
            return textCandidate

        for source in sources:
            if isinstance(source, dict):
                for key, value in source.items():
                    normalizedKey = self._normalizeKey(key)
                    if "firmware" in normalizedKey:
                        textValue = self._coerceString(value)
                        if textValue:
                            return textValue
        return None

    def _buildBase44Payloads(
        self,
        snapshot: Dict[str, Any],
        printerConfig: Dict[str, Any],
        apiKey: Optional[str],
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]:
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        recipientId = self._resolveRecipientId(printerConfig)
        resolvedApiKey = apiKey or self._resolveBase44ApiKey(printerConfig)
        if not ipAddress or not recipientId or not resolvedApiKey:
            return None

        status, isErrorState, combinedErrorMessage = self._deriveStatusAttributes(snapshot)

        optionalFields: Dict[str, Any] = {}
        progressValue = self._coerceFloat(snapshot.get("progressPercent"))
        if progressValue is not None:
            optionalFields["jobProgress"] = max(0, min(100, int(round(progressValue))))

        jobId = self._coerceString(snapshot.get("currentJobId"))
        if jobId:
            optionalFields["currentJobId"] = jobId

        bedTemp = self._coerceFloat(snapshot.get("bedTemp"))
        if bedTemp is not None:
            optionalFields["bedTemp"] = bedTemp

        nozzleTemp = self._coerceFloat(snapshot.get("nozzleTemp"))
        if nozzleTemp is not None:
            optionalFields["nozzleTemp"] = nozzleTemp

        fanSpeed = self._coerceFloat(snapshot.get("fanSpeedPercent"))
        if fanSpeed is not None:
            optionalFields["fanSpeed"] = max(0, min(100, int(round(fanSpeed))))

        printSpeed = self._coerceFloat(snapshot.get("printSpeed"))
        if printSpeed is not None:
            optionalFields["printSpeed"] = max(0, int(round(printSpeed)))

        filamentUsed = self._coerceFloat(snapshot.get("filamentUsed"))
        if filamentUsed is not None:
            optionalFields["filamentUsed"] = filamentUsed

        remainingSeconds = self._coerceInt(snapshot.get("remainingTimeSeconds"))
        if remainingSeconds is not None:
            optionalFields["timeRemaining"] = max(0, remainingSeconds)

        firmwareVersion = self._coerceString(snapshot.get("firmwareVersion"))
        if firmwareVersion:
            optionalFields["firmwareVersion"] = firmwareVersion

        updatePayload: Dict[str, Any] = {
            "recipientId": recipientId,
            "printerIpAddress": ipAddress,
            "status": status,
        }
        if combinedErrorMessage:
            updatePayload["errorMessage"] = combinedErrorMessage
        updatePayload.update(optionalFields)
        updateComparable = {key: value for key, value in updatePayload.items() if key != "lastUpdateTimestamp"}

        errorPayload: Optional[Dict[str, Any]] = None
        errorComparable: Optional[Dict[str, Any]] = None
        if isErrorState:
            errorPayload = {
                "recipientId": recipientId,
                "printerIpAddress": ipAddress,
                "errorMessage": combinedErrorMessage or "Unknown error",
            }
            errorPayload.update(optionalFields)
            errorComparable = dict(errorPayload)

        return updatePayload, updateComparable, errorPayload, errorComparable

    def _resolveBase44ApiKey(self, printerConfig: Dict[str, Any]) -> str:
        for envKey in ("BASE44_FUNCTIONS_API_KEY", "BASE44_API_KEY"):
            envCandidate = os.getenv(envKey, "").strip()
            if envCandidate:
                return envCandidate
        return ""

    def _ensureEnvironmentValue(self, key: str, value: str) -> None:
        if not value:
            return
        if os.getenv(key) == value:
            return
        os.environ[key] = value

    def _resolveRecipientId(self, printerConfig: Dict[str, Any]) -> Optional[str]:
        envCandidate = os.getenv("BASE44_RECIPIENT_ID", "").strip()
        if envCandidate:
            return envCandidate
        return self.defaultRecipientId or None

    def _deriveStatusAttributes(self, snapshot: Dict[str, Any]) -> Tuple[str, bool, Optional[str]]:
        stateText = self._coerceString(snapshot.get("state"))
        gcodeState = self._coerceString(snapshot.get("gcodeState"))
        progressPercent = self._coerceFloat(snapshot.get("progressPercent"))
        hmsCode = self._coerceString(snapshot.get("hmsCode"))
        errorMessage = self._coerceString(snapshot.get("errorMessage"))
        hasAmsConflict = bool(snapshot.get("hasAmsConflict"))

        offline = self._isOfflineSnapshot(snapshot, stateText, gcodeState)
        paused = self._looksPaused(stateText, gcodeState)
        printing = self._looksPrinting(stateText, gcodeState, progressPercent)

        errorIndicators = False
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("error", "fault", "jam", "alarm")):
                errorIndicators = True
                break
        if hmsCode:
            errorIndicators = True
        if errorMessage:
            errorIndicators = True
        if hasAmsConflict:
            errorIndicators = True

        status = "idle"
        if offline:
            status = "offline"
        elif errorIndicators:
            status = "error"
        elif paused:
            status = "paused"
        elif printing:
            status = "printing"

        combinedErrorMessage = self._composeErrorMessage(errorMessage, hmsCode, hasAmsConflict)
        isErrorState = status == "error" or hasAmsConflict
        return status, isErrorState, combinedErrorMessage

    def _composeErrorMessage(
        self,
        errorMessage: Optional[str],
        hmsCode: Optional[str],
        hasAmsConflict: bool,
    ) -> Optional[str]:
        text = self._coerceString(errorMessage)
        code = self._coerceString(hmsCode)
        if code:
            if text:
                if code not in text:
                    text = f"{text} ({code})"
            else:
                text = code
        if hasAmsConflict and not text:
            text = "Possible AMS filament conflict"
        return text

    def _isOfflineSnapshot(
        self,
        snapshot: Dict[str, Any],
        stateText: Optional[str],
        gcodeState: Optional[str],
    ) -> bool:
        rawState = snapshot.get("rawStatePayload")
        rawGcode = snapshot.get("rawGcodePayload")
        rawPercentage = snapshot.get("rawPercentagePayload")
        if rawState is None and rawGcode is None and rawPercentage is None:
            return True
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("offline", "disconnected", "unreachable")):
                return True
        return False

    def _looksPaused(self, stateText: Optional[str], gcodeState: Optional[str]) -> bool:
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("pause", "paused", "pausing")):
                return True
        return False

    def _looksPrinting(
        self,
        stateText: Optional[str],
        gcodeState: Optional[str],
        progressPercent: Optional[float],
    ) -> bool:
        if progressPercent is not None and progressPercent > 0.1:
            return True
        for text in (stateText, gcodeState):
            if not text:
                continue
            lowered = text.lower()
            if any(
                keyword in lowered
                for keyword in (
                    "print",
                    "warm",
                    "heat",
                    "prepare",
                    "start",
                    "running",
                    "busy",
                    "working",
                )
            ):
                if any(stop in lowered for stop in ("finish", "completed", "complete", "idle", "standby")):
                    continue
                return True
        return False

    def _payloadsDiffer(
        self,
        previous: Optional[Dict[str, Any]],
        current: Dict[str, Any],
    ) -> bool:
        if previous is None:
            return True
        keys = set(previous.keys()) | set(current.keys())
        for key in keys:
            if self._valuesDiffer(previous.get(key), current.get(key)):
                return True
        return False

    def _logBase44Failure(self, operation: str, error: Exception) -> None:
        if isinstance(error, requests.HTTPError):
            response = error.response
            statusCode = getattr(response, "status_code", "unknown")
            bodyText = None
            if response is not None:
                try:
                    bodyText = response.text
                except Exception:  # pragma: no cover - defensive logging
                    bodyText = None
            if bodyText:
                self.log.warning("Base44 %s request failed (%s): %s", operation, statusCode, bodyText)
            else:
                self.log.warning("Base44 %s request failed (%s)", operation, statusCode)
        elif isinstance(error, requests.RequestException):
            self.log.warning("Base44 %s request failed: %s", operation, error)
        else:
            self.log.warning("Base44 %s request failed: %s", operation, error)

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
            candidate = candidate.replace("%", "").replace("rpm", "")
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
