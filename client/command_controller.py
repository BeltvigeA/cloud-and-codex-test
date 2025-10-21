from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:  # pragma: no cover - optional dependency resolved at runtime
    import bambulabs_api as bambuApi
except Exception:  # pragma: no cover - surfaced through logs and callbacks
    bambuApi = None

from . import bambuPrinter
from .base44_client import (
    acknowledgeCommand,
    listPendingCommandsForRecipient,
    postCommandResult,
)
from .client import buildBaseUrl, defaultBaseUrl, getPrinterControlEndpointUrl

log = logging.getLogger(__name__)

CONTROL_POLL_SECONDS = float(os.getenv("CONTROL_POLL_SEC", "3"))
CONNECT_TIMEOUT_SECONDS = 10.0

CACHE_DIRECTORY = Path(os.path.expanduser("~/.printmaster"))
CACHE_FILE_PATH = CACHE_DIRECTORY / "command-cache.json"

_cacheData: Optional[Dict[str, Any]] = None
_cacheLock = threading.Lock()


def _determinePollMode() -> str:
    candidate = (os.getenv("CONTROL_POLL_MODE", "recipient") or "recipient").strip().lower()
    return candidate if candidate in {"printer", "recipient"} else "recipient"


_recipientRouters: Dict[str, "RecipientCommandRouter"] = {}
_recipientRoutersLock = threading.Lock()


def _registerRecipientRouter(recipientId: str, pollInterval: float) -> "RecipientCommandRouter":
    with _recipientRoutersLock:
        existing = _recipientRouters.get(recipientId)
        if existing is not None and existing.isActive:
            existing.updatePollInterval(pollInterval)
            return existing
        router = RecipientCommandRouter(recipientId, pollInterval)
        _recipientRouters[recipientId] = router
        return router


def _unregisterRecipientRouter(recipientId: str, router: "RecipientCommandRouter") -> None:
    with _recipientRoutersLock:
        if _recipientRouters.get(recipientId) is router:
            _recipientRouters.pop(recipientId, None)


def _normalizeCommandMetadata(command: Dict[str, Any]) -> Dict[str, Any]:
    metadata = command.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            if isinstance(parsed, dict):
                command["metadata"] = parsed
                return parsed
        except Exception:
            log.debug("Unable to parse command metadata for %s", command.get("commandId"), exc_info=True)
    command["metadata"] = {}
    return {}


def _collectSerialCandidates(command: Dict[str, Any], metadata: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for container in (metadata, command):
        if not isinstance(container, dict):
            continue
        for key in ("printerSerial", "serial", "printerId"):
            value = container.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
    return candidates


def _collectIpCandidates(command: Dict[str, Any], metadata: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for container in (metadata, command):
        if not isinstance(container, dict):
            continue
        for key in ("printerIpAddress", "ip", "ipAddress"):
            value = container.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
    return candidates


class RecipientCommandRouter:
    def __init__(self, recipientId: str, pollInterval: float) -> None:
        self.recipientId = recipientId
        self.pollIntervalSeconds = max(2.0, float(pollInterval))
        self._lock = threading.Lock()
        self._workers: Dict[str, "CommandWorker"] = {}
        self._stopEvent = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"RecipientCommandRouter-{recipientId}",
            daemon=True,
        )
        self._pollErrorCount = 0
        self._thread.start()

    @property
    def isActive(self) -> bool:
        return self._thread.is_alive() and not self._stopEvent.is_set()

    def updatePollInterval(self, pollInterval: float) -> None:
        try:
            value = float(pollInterval)
        except (TypeError, ValueError):
            value = self.pollIntervalSeconds
        self.pollIntervalSeconds = max(2.0, value)

    def registerWorker(self, worker: "CommandWorker") -> None:
        with self._lock:
            self._workers[worker.serial] = worker

    def unregisterWorker(self, serial: str) -> None:
        shouldStop = False
        with self._lock:
            self._workers.pop(serial, None)
            shouldStop = not self._workers
        if shouldStop:
            self._stopEvent.set()

    def _snapshotWorkers(self) -> Dict[str, "CommandWorker"]:
        with self._lock:
            return dict(self._workers)

    def _run(self) -> None:
        log.info("Recipient command poller started for %s", self.recipientId)
        try:
            while not self._stopEvent.is_set():
                workers = self._snapshotWorkers()
                if not workers:
                    if self._stopEvent.wait(1.0):
                        break
                    continue
                try:
                    commands = listPendingCommandsForRecipient(self.recipientId)
                    self._pollErrorCount = 0
                except Exception as error:  # noqa: BLE001 - log and continue
                    self._pollErrorCount += 1
                    if self._pollErrorCount == 1 or self._pollErrorCount % 50 == 0:
                        log.warning("Recipient poll failed for %s: %s", self.recipientId, error)
                    commands = []
                for command in commands:
                    metadata = _normalizeCommandMetadata(command)
                    worker = self._selectWorker(workers, command, metadata)
                    if worker is not None:
                        commandIdValue = str(command.get("commandId") or "")
                        if commandIdValue:
                            log.debug("Routing command %s → %s", commandIdValue, worker.serial)
                        worker.enqueueCommand(command)
                    else:
                        log.info(
                            "No command worker found for command %s (meta=%s)",
                            command.get("commandId"),
                            metadata,
                        )
                self._stopEvent.wait(self.pollIntervalSeconds)
        finally:
            log.info("Recipient command poller stopped for %s", self.recipientId)
            _unregisterRecipientRouter(self.recipientId, self)

    def _selectWorker(
        self,
        workers: Dict[str, "CommandWorker"],
        command: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Optional["CommandWorker"]:
        for serial in _collectSerialCandidates(command, metadata):
            worker = workers.get(serial)
            if worker is not None:
                return worker
        ipCandidates = _collectIpCandidates(command, metadata)
        if ipCandidates:
            for worker in workers.values():
                if any(worker.matchesIp(ip) for ip in ipCandidates):
                    return worker
        return None


def _ensureCacheLoaded() -> Dict[str, Any]:
    global _cacheData
    with _cacheLock:
        if _cacheData is None:
            try:
                CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
                if CACHE_FILE_PATH.exists():
                    loaded = json.loads(CACHE_FILE_PATH.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        _cacheData = loaded
                    else:
                        _cacheData = {"commands": {}}
                else:
                    _cacheData = {"commands": {}}
            except Exception:
                log.debug("Unable to load command cache", exc_info=True)
                _cacheData = {"commands": {}}
        return _cacheData


def _writeCache() -> None:
    with _cacheLock:
        if _cacheData is None:
            return
        try:
            CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
            CACHE_FILE_PATH.write_text(json.dumps(_cacheData, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            log.debug("Unable to persist command cache", exc_info=True)


def _reserveCommand(commandId: str) -> bool:
    cache = _ensureCacheLoaded()
    commands = cache.setdefault("commands", {})
    if commandId in commands:
        return False
    commands[commandId] = {"status": "reserved", "timestamp": time.time()}
    _writeCache()
    return True


def _finalizeCommand(commandId: str, status: str) -> None:
    cache = _ensureCacheLoaded()
    commands = cache.setdefault("commands", {})
    entry = commands.get(commandId, {})
    entry.update({"status": status, "timestamp": time.time()})
    commands[commandId] = entry
    _writeCache()


class CommandWorker:
    """Poll Base44 for printer control commands and execute them on a specific printer."""

    def __init__(
        self,
        *,
        serial: str,
        ipAddress: str,
        accessCode: str,
        nickname: Optional[str] = None,
        apiKey: Optional[str] = None,
        recipientId: Optional[str] = None,
        baseUrl: Optional[str] = None,
        pollInterval: Optional[float] = None,
    ) -> None:
        self.serial = serial
        self.ipAddress = ipAddress
        self.accessCode = accessCode
        self.nickname = nickname or serial
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._printerInstance: Optional[Any] = None
        self._printerLock = threading.Lock()
        self.apiKeyValue = (apiKey or os.getenv("BASE44_API_KEY", "")).strip()
        self.recipientIdValue = (recipientId or os.getenv("BASE44_RECIPIENT_ID", "")).strip()
        self.controlBaseUrl = (baseUrl or os.getenv("PRINTER_BACKEND_BASE_URL", "")).strip()
        baseCandidate = self.controlBaseUrl or defaultBaseUrl
        try:
            self.controlBaseUrl = buildBaseUrl(baseCandidate)
        except Exception:
            log.debug("Invalid control base URL %s – falling back to default", baseCandidate, exc_info=True)
            self.controlBaseUrl = buildBaseUrl(defaultBaseUrl)
        self.controlEndpointUrl = getPrinterControlEndpointUrl(self.controlBaseUrl)
        self.controlAckUrl = f"{self.controlBaseUrl}/control/ack"
        self.controlResultUrl = f"{self.controlBaseUrl}/control/result"
        self.pollErrorCount = 0
        self.pollLogEvery = 50
        self.pollIntervalSeconds = max(
            2.0,
            float(pollInterval) if pollInterval is not None else CONTROL_POLL_SECONDS,
        )
        self.pollMode = _determinePollMode()
        self._commandQueue: Optional[queue.Queue] = None
        self._router: Optional[RecipientCommandRouter] = None
        log.debug(
            "CommandWorker configured for %s using control endpoint %s", self.serial, self.controlEndpointUrl
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        if self.pollMode == "recipient":
            if not self.recipientIdValue:
                raise RuntimeError("Missing recipientId for recipient polling mode")
            if self._commandQueue is None:
                self._commandQueue = queue.Queue()
            self._router = _registerRecipientRouter(self.recipientIdValue, self.pollIntervalSeconds)
            self._router.registerWorker(self)
        self._thread = threading.Thread(target=self._run, name=f"CommandWorker-{self.serial}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        if self.pollMode == "recipient" and self._router is not None:
            self._router.unregisterWorker(self.serial)
            self._router = None
        self._drainQueue()
        self._disconnectPrinter()

    def _run(self) -> None:
        if self.pollMode == "recipient":
            self._runRecipientMode()
        else:
            self._runPrinterMode()

    def _runPrinterMode(self) -> None:
        log.info("CommandWorker started for %s (%s) [printer-mode]", self.nickname, self.serial)
        try:
            while not self._stopEvent.is_set():
                try:
                    commands = self._pollCommands()
                    self.pollErrorCount = 0
                except Exception as error:
                    self.pollErrorCount += 1
                    if self.pollErrorCount == 1 or self.pollErrorCount % self.pollLogEvery == 0:
                        log.warning("Control poll failed for %s: %s", self.serial, error)
                    commands = []
                for command in commands:
                    self._processCommand(command)
                self._stopEvent.wait(self.pollIntervalSeconds)
        finally:
            log.info("CommandWorker stopped for %s [printer-mode]", self.serial)

    def _runRecipientMode(self) -> None:
        log.info("CommandWorker started for %s (%s) [recipient-mode]", self.nickname, self.serial)
        queueRef = self._commandQueue
        try:
            if queueRef is None:
                return
            while not self._stopEvent.is_set():
                try:
                    command = queueRef.get(timeout=self.pollIntervalSeconds)
                except queue.Empty:
                    continue
                commandIdValue = str(command.get("commandId") or "")
                if commandIdValue:
                    log.debug("Dequeued command %s → %s", commandIdValue, self.serial)
                self._processCommand(command)
        finally:
            log.info("CommandWorker stopped for %s [recipient-mode]", self.serial)
            if self._router is not None:
                self._router.unregisterWorker(self.serial)
                self._router = None
            self._drainQueue()

    def _processCommand(self, command: Dict[str, Any]) -> None:
        commandId = str(command.get("commandId") or "").strip()
        if not commandId:
            return
        if not _reserveCommand(commandId):
            return

        metadata = _normalizeCommandMetadata(command)
        log.debug("Processing command %s (metadata=%s)", commandId, metadata)

        try:
            self._sendCommandAck(commandId, "processing")
        except UnsupportedControlEndpointError as error:
            log.warning("ACK endpoint unavailable for %s: %s", commandId, error)
        except Exception as error:  # noqa: BLE001 - log but continue executing command
            log.warning("Failed to acknowledge command %s: %s", commandId, error)

        try:
            printer = self._connectPrinter()
            status, message = self._executeCommand(printer, command)
        except Exception as error:
            errorMessage = f"{type(error).__name__}: {error}"
            log.warning("Command %s failed on %s: %s", commandId, self.serial, errorMessage)
            try:
                self._sendCommandResult(commandId, "failed", errorMessage=errorMessage)
            except UnsupportedControlEndpointError as resultError:
                log.warning("RESULT endpoint unavailable for %s: %s", commandId, resultError)
            except Exception:
                log.debug("Unable to submit failed result for %s", commandId, exc_info=True)
            _finalizeCommand(commandId, "failed")
            return

        try:
            self._sendCommandResult(commandId, status, message=message)
        except UnsupportedControlEndpointError as resultError:
            log.warning("RESULT endpoint unavailable for %s: %s", commandId, resultError)
        except Exception:
            log.debug("Unable to submit result for %s", commandId, exc_info=True)
        _finalizeCommand(commandId, status)
        log.info("Command %s on %s: %s", commandId, self.serial, status)

    def enqueueCommand(self, command: Dict[str, Any]) -> None:
        if self.pollMode != "recipient":
            return
        if self._commandQueue is None:
            self._commandQueue = queue.Queue()
        try:
            self._commandQueue.put_nowait(command)
            commandIdValue = str(command.get("commandId") or "")
            if commandIdValue:
                log.debug("Enqueued command %s → %s", commandIdValue, self.serial)
        except Exception:
            log.debug("Unable to enqueue command %s", command.get("commandId"), exc_info=True)

    def matchesIp(self, candidateIp: str) -> bool:
        normalized = candidateIp.strip()
        return bool(normalized) and normalized == (self.ipAddress or "").strip()

    def _drainQueue(self) -> None:
        if self._commandQueue is None:
            return
        try:
            while True:
                self._commandQueue.get_nowait()
        except queue.Empty:
            pass
        self._commandQueue = None

    def _pollCommands(self) -> List[Dict[str, Any]]:
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        params = {
            "recipientId": self.recipientIdValue,
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
        }
        headers = {"Content-Type": "application/json", "X-API-Key": self.apiKeyValue}
        response = requests.get(
            self.controlEndpointUrl,
            params=params,
            headers=headers,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        if not response.content:
            return []
        data = response.json()
        if isinstance(data, list):
            commands = data
        elif isinstance(data, dict):
            commands = data.get("commands", [])
        else:
            commands = []
        if not isinstance(commands, list):
            return []
        normalized = [command for command in commands if isinstance(command, dict)]
        if normalized:
            commandIds = [str(entry.get("commandId")) for entry in normalized]
            log.info(
                "Fetched %d command(s) for %s: %s",
                len(normalized),
                self.serial,
                ", ".join(commandIds),
            )
        return normalized

    def _sendCommandAck(self, commandId: str, status: str) -> bool:
        if self.pollMode == "recipient":
            acknowledgeCommand(commandId)
            return True
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        payload = {
            "recipientId": self.recipientIdValue,
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
            "commandId": commandId,
            "status": status,
            "startedAt": _isoTimestamp(),
        }
        log.debug("Sending ACK for %s via %s", commandId, self.controlAckUrl)
        return self._postControlPayload(self.controlAckUrl, payload, "ack")

    def _sendCommandResult(
        self,
        commandId: str,
        status: str,
        *,
        message: Optional[str] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        if self.pollMode == "recipient":
            statusValue = str(status or "").lower()
            successStatusSet = {"completed", "success", "ok", "done"}
            success = statusValue in successStatusSet
            detail = str(message or errorMessage or "") or None
            postCommandResult(commandId, success, detail)
            return
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        payload: Dict[str, Any] = {
            "recipientId": self.recipientIdValue,
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
            "commandId": commandId,
            "status": status,
            "finishedAt": _isoTimestamp(),
        }
        if message:
            payload["message"] = str(message)
        if errorMessage:
            payload["errorMessage"] = str(errorMessage)
        log.debug("Sending RESULT for %s via %s", commandId, self.controlResultUrl)
        self._postControlPayload(self.controlResultUrl, payload, "result")

    def _postControlPayload(self, url: str, payload: Dict[str, Any], action: str) -> bool:
        headers = {"Content-Type": "application/json", "X-API-Key": self.apiKeyValue}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=CONNECT_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.HTTPError as error:  # pragma: no cover - HTTP path validated via tests
            statusCode = getattr(error.response, "status_code", None)
            if statusCode in {404, 405}:
                raise UnsupportedControlEndpointError(f"/{action}") from error
            raise
        return True

    def _connectPrinter(self) -> Any:
        if bambuApi is None:
            raise RuntimeError("bambulabs_api is not installed")
        with self._printerLock:
            if self._printerInstance is not None:
                return self._printerInstance
            printer = bambuApi.Printer(self.ipAddress, self.accessCode, self.serial)
            connectMethod = getattr(printer, "mqtt_start", None) or getattr(printer, "connect", None)
            if connectMethod is None:
                raise RuntimeError("bambulabs_api.Printer is missing connect/mqtt_start")
            connectMethod()
            waitForReady = getattr(bambuPrinter, "_waitForMqttReady", None)
            if callable(waitForReady):
                try:
                    waitForReady(printer, timeout=30.0)
                except Exception:
                    log.debug("Printer MQTT readiness wait failed", exc_info=True)
            self._printerInstance = printer
            return printer

    def _disconnectPrinter(self) -> None:
        with self._printerLock:
            if self._printerInstance is None:
                return
            try:
                if hasattr(self._printerInstance, "disconnect"):
                    self._printerInstance.disconnect()
            except Exception:
                log.debug("Error while disconnecting printer %s", self.serial, exc_info=True)
            finally:
                self._printerInstance = None

    def _executeCommand(self, printer: Any, command: Dict[str, Any]) -> Tuple[str, str]:
        commandType = str(command.get("commandType") or "").strip().lower()
        metadata = command.get("metadata") or {}
        message = ""

        def callPrinterMethod(name: str, *args: Any, **kwargs: Any) -> None:
            method = getattr(printer, name, None)
            if callable(method):
                method(*args, **kwargs)
                return
            fallbackNames = {"cancel_print": ["stop_print"]}.get(name, [])
            for fallbackName in fallbackNames:
                fallbackMethod = getattr(printer, fallbackName, None)
                if callable(fallbackMethod):
                    fallbackMethod(*args, **kwargs)
                    return
            raise RuntimeError(f"Printer method {name} is unavailable")

        def sendGcode(gcode: str) -> None:
            sender = getattr(printer, "send_gcode", None)
            if callable(sender):
                sender(gcode)
                return
            raise RuntimeError("send_gcode is unavailable in bambulabs_api")

        def sendControlPayload(payload: Dict[str, Any]) -> None:
            controlMethod = getattr(printer, "send_control", None)
            if callable(controlMethod):
                controlMethod(payload)
                return
            publishMethod = getattr(printer, "publish", None)
            if callable(publishMethod):
                publishMethod(payload)
                return
            sendRequest = getattr(printer, "send_request", None)
            if callable(sendRequest):
                sendRequest(payload)
                return
            mqttClient = getattr(printer, "_mqtt_client", None)
            if mqttClient is not None:
                topic = f"device/{self.serial}/request"
                body = json.dumps(payload).encode("utf-8")
                mqttClient.publish(topic, body, qos=1)
                return
            raise RuntimeError("No available transport to publish control payload")

        normalizedType = commandType.replace("-", "_")

        if normalizedType in {"heat", "setheat"}:
            nozzleTemp = metadata.get("nozzleTemp")
            bedTemp = metadata.get("bedTemp")
            if nozzleTemp is None and bedTemp is None:
                raise ValueError("heat requires nozzleTemp and/or bedTemp")
            if nozzleTemp is not None:
                try:
                    callPrinterMethod("set_nozzle_temperature", float(nozzleTemp))
                except RuntimeError:
                    sendGcode(f"M104 S{int(float(nozzleTemp))}")
            if bedTemp is not None:
                try:
                    callPrinterMethod("set_bed_temperature", float(bedTemp))
                except RuntimeError:
                    sendGcode(f"M140 S{int(float(bedTemp))}")
            message = f"Heating nozzle={nozzleTemp} bed={bedTemp}"
        elif normalizedType in {"cool", "cooldown"}:
            try:
                callPrinterMethod("set_nozzle_temperature", 0)
                callPrinterMethod("set_bed_temperature", 0)
            except RuntimeError:
                sendGcode("M104 S0")
                sendGcode("M140 S0")
            message = "Cooling started"
        elif normalizedType in {"pause", "resume", "stop", "stop_print", "cancel"}:
            methodMap = {
                "pause": "pause_print",
                "resume": "resume_print",
                "stop": "stop_print",
                "stop_print": "stop_print",
                "cancel": "cancel_print",
            }
            methodName = methodMap.get(normalizedType, normalizedType)
            try:
                callPrinterMethod(methodName)
            except RuntimeError:
                sendControlPayload({"command": normalizedType})
            statusMessages = {
                "pause": "Paused",
                "resume": "Resumed",
                "stop": "Stopped",
                "stop_print": "Stopped",
                "cancel": "Cancelled",
            }
            message = statusMessages.get(normalizedType, normalizedType.replace("_", " ").title())
        elif normalizedType == "camera_on":
            try:
                callPrinterMethod("camera_on")
            except RuntimeError:
                sendControlPayload({"command": "camera", "param": {"on": True}})
            message = "Camera enabled"
        elif normalizedType == "camera_off":
            try:
                callPrinterMethod("camera_off")
            except RuntimeError:
                sendControlPayload({"command": "camera", "param": {"on": False}})
            message = "Camera disabled"
        elif normalizedType in {"set_speed", "speed", "setspeed"}:
            percentValue = metadata.get("percent") or metadata.get("speedPercent")
            if percentValue is None:
                raise ValueError("set_speed requires percent metadata")
            try:
                callPrinterMethod("set_print_speed_factor", float(percentValue))
            except RuntimeError:
                clamped = max(10, min(300, int(round(float(percentValue)))))
                sendGcode(f"M220 S{clamped}")
                percentValue = clamped
            message = f"Speed set to {percentValue}%"
        elif normalizedType in {"set_fan", "fan", "setfan"}:
            percentValue = metadata.get("percent") or metadata.get("fanPercent")
            if percentValue is None:
                raise ValueError("set_fan requires percent metadata")
            try:
                callPrinterMethod("set_fan_speed", float(percentValue))
            except RuntimeError:
                pwmValue = max(0, min(255, int(round(float(percentValue) * 255.0 / 100.0))))
                sendGcode(f"M106 S{pwmValue}")
            message = f"Fan set to {percentValue}%"
        elif normalizedType == "start_print":
            fileName = metadata.get("fileName")
            if not fileName:
                raise ValueError("start_print requires metadata.fileName")
            plateIndex = metadata.get("plateIndex")
            paramPath = metadata.get("paramPath")
            useAms = metadata.get("useAms")
            try:
                callPrinterMethod("start_print", str(fileName), plateIndex or paramPath, use_ams=useAms)
                message = "Print started"
            except RuntimeError:
                options = bambuPrinter.BambuPrintOptions(
                    ipAddress=self.ipAddress,
                    serialNumber=self.serial,
                    accessCode=self.accessCode,
                    useAms=useAms,
                    plateIndex=plateIndex,
                )
                result = bambuPrinter.startPrintViaApi(
                    ip=self.ipAddress,
                    serial=self.serial,
                    accessCode=self.accessCode,
                    uploaded_name=str(fileName),
                    plate_index=plateIndex,
                    param_path=paramPath,
                    options=options,
                    job_metadata=metadata if isinstance(metadata, dict) else None,
                )
                acknowledged = result.get("acknowledged")
                message = (
                    f"Print started (acknowledged={acknowledged})"
                    if acknowledged is not None
                    else "Print started"
                )
        elif normalizedType in {"home", "light_on", "light_off", "lightoff", "lighton", "move", "jog", "load_filament", "unload_filament", "sendgcode"}:
            if normalizedType == "home":
                sendGcode("G28")
                message = "Homing"
            elif normalizedType in {"move", "jog"}:
                axisParts: List[str] = []
                for axisKey in ("x", "y", "z", "e"):
                    if axisKey in metadata and metadata[axisKey] is not None:
                        axisParts.append(f"{axisKey.upper()}{float(metadata[axisKey])}")
                feedrate = metadata.get("feedrate")
                if feedrate is not None:
                    axisParts.append(f"F{int(float(feedrate))}")
                if not axisParts:
                    raise ValueError("move requires at least one axis or feedrate")
                sendGcode("G1 " + " ".join(axisParts))
                message = "Moved"
            elif normalizedType == "sendgcode":
                gcodeValue = metadata.get("gcode")
                if not gcodeValue:
                    raise ValueError("sendGcode requires metadata.gcode")
                sendGcode(str(gcodeValue))
                message = "G-code sent"
            elif normalizedType in {"light_on", "lightoff", "light_off", "lighton"}:
                isOn = normalizedType in {"light_on", "lighton"}
                sendControlPayload({"command": "light", "param": {"on": isOn}})
                message = "Light on" if isOn else "Light off"
            elif normalizedType in {"load_filament", "unload_filament"}:
                slotValue = int(metadata.get("slot", 1))
                commandName = "load_filament" if normalizedType == "load_filament" else "unload_filament"
                sendControlPayload({"command": commandName, "param": {"slot": slotValue}})
                message = ("Load" if commandName == "load_filament" else "Unload") + f" filament slot {slotValue}"
        else:
            raise ValueError(f"Unsupported commandType: {commandType}")

        return "completed", message


def _isoTimestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnsupportedControlEndpointError(RuntimeError):
    """Raised when the backend does not expose the expected control endpoint."""


