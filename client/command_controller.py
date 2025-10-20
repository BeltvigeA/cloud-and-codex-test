from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:  # pragma: no cover - optional dependency resolved at runtime
    import bambulabs_api as bambuApi
except Exception:  # pragma: no cover - surfaced through logs and callbacks
    bambuApi = None

from .base44_client import BASE44_FUNCTIONS_BASE

log = logging.getLogger(__name__)

CONTROL_FUNCTION_URL = f"{BASE44_FUNCTIONS_BASE}/control"
ACK_FUNCTION_URL = f"{BASE44_FUNCTIONS_BASE}/ackPrinterCommand"

CONTROL_POLL_SECONDS = float(os.getenv("CONTROL_POLL_SEC", "3"))
CONNECT_TIMEOUT_SECONDS = 10.0

CACHE_DIRECTORY = Path(os.path.expanduser("~/.printmaster"))
CACHE_FILE_PATH = CACHE_DIRECTORY / "command-cache.json"

_cacheData: Optional[Dict[str, Any]] = None
_cacheLock = threading.Lock()


def _buildHeaders() -> Dict[str, str]:
    apiKey = os.getenv("BASE44_API_KEY", "").strip()
    if not apiKey:
        raise RuntimeError("BASE44_API_KEY is missing")
    return {"Content-Type": "application/json", "X-API-Key": apiKey}


def _resolveRecipientId() -> str:
    recipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()
    if not recipientId:
        raise RuntimeError("BASE44_RECIPIENT_ID is missing")
    return recipientId


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
    ) -> None:
        self.serial = serial
        self.ipAddress = ipAddress
        self.accessCode = accessCode
        self.nickname = nickname or serial
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._printerInstance: Optional[Any] = None
        self._printerLock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._run, name=f"CommandWorker-{self.serial}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._disconnectPrinter()

    def _run(self) -> None:
        log.info("CommandWorker started for %s (%s)", self.nickname, self.serial)
        try:
            while not self._stopEvent.is_set():
                try:
                    commands = self._pollCommands()
                except Exception as error:
                    log.debug("Control poll failed for %s: %s", self.serial, error)
                    commands = []
                for command in commands:
                    commandId = str(command.get("commandId") or "").strip()
                    if not commandId:
                        continue
                    if not _reserveCommand(commandId):
                        continue
                    try:
                        printer = self._connectPrinter()
                        status, message = self._executeCommand(printer, command)
                        self._acknowledgeCommand(commandId, status, message=message)
                        _finalizeCommand(commandId, status)
                        log.info("Command %s on %s: %s", commandId, self.serial, status)
                    except Exception as error:
                        errorMessage = f"{type(error).__name__}: {error}"
                        log.warning("Command %s failed on %s: %s", commandId, self.serial, errorMessage)
                        try:
                            self._acknowledgeCommand(commandId, "failed", error=errorMessage)
                        except Exception:
                            log.debug("Unable to acknowledge failure for %s", commandId, exc_info=True)
                        _finalizeCommand(commandId, "failed")
                self._stopEvent.wait(CONTROL_POLL_SECONDS)
        finally:
            log.info("CommandWorker stopped for %s", self.serial)

    def _pollCommands(self) -> List[Dict[str, Any]]:
        payload = {
            "recipientId": _resolveRecipientId(),
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
        }
        response = requests.post(
            CONTROL_FUNCTION_URL,
            json=payload,
            headers=_buildHeaders(),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        if not response.content:
            return []
        data = response.json()
        commands = data.get("commands") if isinstance(data, dict) else []
        if not isinstance(commands, list):
            return []
        return [command for command in commands if isinstance(command, dict)]

    def _acknowledgeCommand(
        self,
        commandId: str,
        status: str,
        *,
        message: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "recipientId": _resolveRecipientId(),
            "printerSerial": self.serial,
            "commandId": commandId,
            "status": status,
        }
        if message:
            payload["message"] = message
        if error:
            payload["errorMessage"] = error
        response = requests.post(
            ACK_FUNCTION_URL,
            json=payload,
            headers=_buildHeaders(),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

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

        def sendGcode(gcode: str) -> None:
            sendMethod = getattr(printer, "send_gcode", None)
            if sendMethod is None:
                raise RuntimeError("send_gcode is unavailable in bambulabs_api")
            sendMethod(gcode)

        def sendControlPayload(payload: Dict[str, Any]) -> None:
            if hasattr(printer, "publish"):
                printer.publish(payload)
                return
            if hasattr(printer, "send_request"):
                printer.send_request(payload)
                return
            mqttClient = getattr(printer, "_mqtt_client", None)
            if mqttClient is not None:
                topic = f"device/{self.serial}/request"
                body = json.dumps(payload).encode("utf-8")
                mqttClient.publish(topic, body, qos=1)
                return
            raise RuntimeError("No available transport to publish control payload")

        if commandType == "heat":
            nozzleTemp = metadata.get("nozzleTemp")
            bedTemp = metadata.get("bedTemp")
            if nozzleTemp is None and bedTemp is None:
                raise ValueError("heat requires nozzleTemp and/or bedTemp")
            if nozzleTemp is not None:
                sendGcode(f"M104 S{float(nozzleTemp):.0f}")
            if bedTemp is not None:
                sendGcode(f"M140 S{float(bedTemp):.0f}")
            message = f"Heating nozzle={nozzleTemp} bed={bedTemp}"
        elif commandType == "cooldown":
            sendGcode("M104 S0")
            sendGcode("M140 S0")
            message = "Cooldown initiated"
        elif commandType == "pause":
            if hasattr(printer, "pause_print"):
                printer.pause_print()
            else:
                sendControlPayload({"print": {"command": "pause"}})
            message = "Print paused"
        elif commandType == "resume":
            if hasattr(printer, "resume_print"):
                printer.resume_print()
            else:
                sendControlPayload({"print": {"command": "resume"}})
            message = "Print resumed"
        elif commandType == "stop":
            if hasattr(printer, "stop_print"):
                printer.stop_print()
            else:
                sendControlPayload({"print": {"command": "stop"}})
            message = "Print stopped"
        elif commandType == "setfan":
            percentValue = float(metadata.get("percent", 0))
            pwmValue = max(0, min(255, int(round(percentValue * 255.0 / 100.0))))
            sendGcode(f"M106 S{pwmValue}")
            message = f"Fan set to {percentValue}%"
        elif commandType == "setspeed":
            percentValue = float(metadata.get("percent", 100))
            clamped = max(10, min(300, int(round(percentValue))))
            sendGcode(f"M220 S{clamped}")
            message = f"Speed override set to {clamped}%"
        elif commandType == "setflow":
            percentValue = float(metadata.get("percent", 100))
            clamped = max(50, min(200, int(round(percentValue))))
            sendGcode(f"M221 S{clamped}")
            message = f"Flow override set to {clamped}%"
        elif commandType == "home":
            sendGcode("G28")
            message = "Homing"
        elif commandType == "jog":
            axis = str(metadata.get("axis") or "X").upper()
            distance = float(metadata.get("distance", 0))
            feedrate = float(metadata.get("feedrate", 1200))
            if axis not in {"X", "Y", "Z", "E"}:
                raise ValueError("jog axis must be X, Y, Z, or E")
            sendGcode("G91")
            sendGcode(f"G0 {axis}{distance} F{int(feedrate)}")
            sendGcode("G90")
            message = f"Jogged {axis}{distance} at F{int(feedrate)}"
        elif commandType == "sendgcode":
            gcodeValue = metadata.get("gcode")
            if not gcodeValue:
                raise ValueError("sendGcode requires metadata.gcode")
            sendGcode(str(gcodeValue))
            message = "G-code sent"
        else:
            raise NotImplementedError(f"Unsupported commandType: {commandType}")

        return "completed", message

