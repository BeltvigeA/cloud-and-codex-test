"""Background reporter that sends printer snapshots to Base44."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Callable, Iterable

from .base44 import callFunction, getDefaultApiKey, getStatusFunctionName
from .commands import completeCommand, listPendingCommands
from .pending import requestPendingPollTrigger

LOG = logging.getLogger(__name__)

def loadApiKey() -> str:
    """Resolve the Base44 API key from the current environment."""

    return getDefaultApiKey()


def _isoUtcNow() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _coerceInt(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return int(float(stripped))
    except (TypeError, ValueError):
        return 0
    return 0


def _sanitizeStatus(snapshot: dict[str, Any]) -> tuple[str, bool]:
    onlineFlag = bool(snapshot.get("online"))
    statusValue = str(snapshot.get("status") or "").strip()
    if not statusValue:
        statusValue = "idle" if onlineFlag else "offline"
    return statusValue, onlineFlag


def _isZeroLike(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


class Base44StatusReporter:
    """Periodically pushes printer snapshots to Base44 while running."""

    def __init__(
        self,
        getPrintersSnapshotCallable: Callable[[], Iterable[dict[str, Any]]],
        intervalSec: int = 5,
        commandPollIntervalSec: int = 5,
    ) -> None:
        self._getPrintersSnapshotCallable = getPrintersSnapshotCallable
        self._intervalSec = max(1, int(intervalSec))
        self._thread: threading.Thread | None = None
        self._stopEvent = threading.Event()
        self._recipientId = ""
        self._apiKeyOverride = ""
        self._lastSnapshotByPrinter: dict[tuple[str, str], dict[str, Any]] = {}
        self._mqttOfflineSince: dict[tuple[str, str], float] = {}
        self._lastOnlineStateByPrinter: dict[tuple[str, str], bool] = {}
        self._isRunning = False
        self._statusFunctionName = getStatusFunctionName()
        self._commandPollIntervalSec = max(1, int(commandPollIntervalSec))
        self._nextCommandPollTimestamp = 0.0
        self._commandBackoffSeconds = float(self._commandPollIntervalSec)

    def start(self, recipientId: str, apiKey: str | None = None) -> None:
        self._recipientId = recipientId.strip()
        self._apiKeyOverride = (apiKey or "").strip()
        self._stopEvent.clear()
        if self._isRunning:
            return
        self._statusFunctionName = getStatusFunctionName()
        self._nextCommandPollTimestamp = 0.0
        self._commandBackoffSeconds = float(self._commandPollIntervalSec)
        self._thread = threading.Thread(target=self._runLoop, name="base44-status", daemon=True)
        self._thread.start()
        LOG.info(
            "Base44StatusReporter started (recipientId=%s, every=%ss)",
            self._recipientId,
            self._intervalSec,
        )

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self._isRunning = False
        LOG.info("Base44StatusReporter stopped")

    def _buildPayload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self._recipientId:
            return {}

        statusValue, onlineFlag = _sanitizeStatus(snapshot)
        serialKey = str(snapshot.get("serial") or "").strip().lower()
        ipKey = str(snapshot.get("ip") or "").strip()
        key = (serialKey, ipKey)

        previous = self._lastSnapshotByPrinter.get(key, {})
        lastTemps = {
            "bedTemp": previous.get("bedTemp"),
            "nozzleTemp": previous.get("nozzleTemp"),
            "timestamp": previous.get("timestamp", 0.0),
        }

        currentBed = snapshot.get("bed")
        currentNozzle = snapshot.get("nozzle")

        mqttReady = snapshot.get("mqttReady")
        if mqttReady is False:
            offlineSince = self._mqttOfflineSince.setdefault(key, time.time())
            if time.time() - offlineSince > 30:
                onlineFlag = False
        else:
            self._mqttOfflineSince.pop(key, None)

        progressValue = _coerceInt(snapshot.get("progress"))
        if not onlineFlag:
            progressValue = 0

        shouldPreserveTemps = (
            mqttReady is not False
            and onlineFlag
            and statusValue.lower() not in {"offline", "unknown"}
        )

        if shouldPreserveTemps:
            if _isZeroLike(currentBed) and lastTemps["bedTemp"] not in (None, ""):
                currentBed = lastTemps["bedTemp"]
            if _isZeroLike(currentNozzle) and lastTemps["nozzleTemp"] not in (None, ""):
                currentNozzle = lastTemps["nozzleTemp"]
        else:
            currentBed = None
            currentNozzle = None
            onlineFlag = False if mqttReady is False else onlineFlag
            if statusValue.lower() not in {"offline", "unknown"} and not onlineFlag:
                statusValue = "offline"

        payload = {
            "recipientId": self._recipientId,
            "printerIpAddress": snapshot.get("ip"),
            "serialNumber": snapshot.get("serial"),
            "status": statusValue,
            "online": onlineFlag,
            "jobProgress": progressValue,
            "currentJobId": snapshot.get("currentJobId"),
            "bedTemp": currentBed,
            "nozzleTemp": currentNozzle,
            "fanSpeed": snapshot.get("fan"),
            "printSpeed": snapshot.get("speed"),
            "filamentUsed": snapshot.get("filamentUsed"),
            "timeRemaining": _coerceInt(snapshot.get("timeRemaining")),
            "errorMessage": snapshot.get("error"),
            "lastUpdateTimestamp": _isoUtcNow(),
            "firmwareVersion": snapshot.get("firmware"),
        }

        self._lastSnapshotByPrinter[key] = {
            "bedTemp": payload.get("bedTemp"),
            "nozzleTemp": payload.get("nozzleTemp"),
            "timestamp": time.time(),
        }

        return payload

    def _runLoop(self) -> None:
        self._isRunning = True
        try:
            while not self._stopEvent.is_set():
                try:
                    snapshots: list[dict[str, Any]] = []
                    if not self._recipientId:
                        LOG.debug("Skipping Base44 post: missing recipient")
                    else:
                        snapshots = list(self._getPrintersSnapshotCallable() or [])
                        for snapshot in snapshots:
                            payload = self._buildPayload(snapshot)
                            if not payload:
                                continue

                            LOG.info(
                                "[POST] %s recipientId=%s ip=%s status=%s bed=%s nozzle=%s",
                                self._statusFunctionName,
                                payload.get("recipientId"),
                                payload.get("printerIpAddress"),
                                payload.get("status"),
                                payload.get("bedTemp"),
                                payload.get("nozzleTemp"),
                            )

                            callFunction(
                                self._statusFunctionName,
                                payload,
                                apiKey=self._apiKeyOverride,
                            )

                            serialKey = str(payload.get("serialNumber") or "").strip().lower()
                            ipKey = str(payload.get("printerIpAddress") or "").strip()
                            key = (serialKey, ipKey)
                            wasOnline = self._lastOnlineStateByPrinter.get(key, False)
                            nowOnline = bool(payload.get("online"))
                            self._lastOnlineStateByPrinter[key] = nowOnline
                            if nowOnline and not wasOnline:
                                requestPendingPollTrigger()

                    self._pollPendingCommands(snapshots)
                except Exception as error:  # noqa: BLE001
                    LOG.exception("Status push failed: %s", error)

                self._stopEvent.wait(self._intervalSec)
        finally:
            self._isRunning = False


    def _pollPendingCommands(self, snapshots: list[dict[str, Any]]) -> None:
        if self._commandPollIntervalSec <= 0 or not self._recipientId:
            return

        currentTime = time.time()
        if currentTime < self._nextCommandPollTimestamp:
            return

        commands = listPendingCommands(self._recipientId)
        if commands is None:
            self._commandBackoffSeconds = min(
                30.0,
                max(self._commandBackoffSeconds * 2, float(self._commandPollIntervalSec)),
            )
            self._nextCommandPollTimestamp = currentTime + self._commandBackoffSeconds
            LOG.warning(
                "[commands] Kommando-poll feilet for %s. Nytt forsøk om %.0fs",
                self._recipientId,
                self._commandBackoffSeconds,
            )
            return

        self._commandBackoffSeconds = float(self._commandPollIntervalSec)
        self._nextCommandPollTimestamp = currentTime + self._commandPollIntervalSec

        if not commands:
            LOG.debug("[commands] Ingen kommandoer for %s", self._recipientId)
            return

        for command in commands:
            self._handleCommand(command, snapshots)


    def _handleCommand(self, command: dict[str, Any], snapshots: list[dict[str, Any]]) -> None:
        commandId = str(command.get("commandId") or "").strip()
        commandType = str(command.get("commandType") or "").strip().lower()
        if not commandId:
            LOG.warning("[commands] Hopper over kommando uten ID: %r", command)
            return

        if commandType != "poke":
            errorMessage = f"Ukjent type {command.get('commandType')}"
            if not completeCommand(commandId, False, recipientId=self._recipientId, error=errorMessage):
                LOG.error("[commands] Klarte ikke markere %s som ukjent", commandId)
            return

        printerIp = str(command.get("printerIpAddress") or "").strip()
        if not printerIp:
            if not completeCommand(commandId, False, recipientId=self._recipientId, error="Printeradresse mangler"):
                LOG.error("[commands] Klarte ikke markere %s uten ip", commandId)
            return

        snapshot = self._findSnapshotForIp(printerIp, snapshots)
        if snapshot is None:
            freshSnapshots = self._safeCollectSnapshots()
            snapshot = self._findSnapshotForIp(printerIp, freshSnapshots)
            if snapshot is None:
                if not completeCommand(
                    commandId,
                    False,
                    recipientId=self._recipientId,
                    error=f"Printer {printerIp} ikke funnet",
                ):
                    LOG.error("[commands] Klarte ikke markere %s som manglende", commandId)
                return
            snapshots[:] = freshSnapshots

        payload = self._buildPayload(snapshot)
        if not payload:
            if not completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error="Statusdata utilgjengelig",
            ):
                LOG.error("[commands] Klarte ikke markere %s uten payload", commandId)
            return

        payload.setdefault("printerIpAddress", printerIp)

        response = callFunction(
            self._statusFunctionName,
            payload,
            apiKey=self._apiKeyOverride,
        )

        if isinstance(response, dict) and response.get("ok") is False:
            if not completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error="Statusoppdatering avvist",
            ):
                LOG.error("[commands] Klarte ikke markere %s som avvist", commandId)
            return

        if not completeCommand(commandId, True, recipientId=self._recipientId):
            LOG.error("[commands] Klarte ikke bekrefte %s", commandId)
            return

        LOG.info("[commands] Poke ferdig for %s", printerIp)


    def _findSnapshotForIp(self, printerIp: str, snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalizedIp = printerIp.strip()
        if not normalizedIp:
            return None
        for snapshot in snapshots:
            candidateIp = str(
                snapshot.get("printerIpAddress")
                or snapshot.get("ip")
                or snapshot.get("ipAddress")
                or ""
            ).strip()
            if candidateIp == normalizedIp:
                return snapshot
        return None


    def _safeCollectSnapshots(self) -> list[dict[str, Any]]:
        try:
            return list(self._getPrintersSnapshotCallable() or [])
        except Exception as error:  # noqa: BLE001
            LOG.exception("[commands] Klarte ikke hente printerstatus: %s", error)
            return []


__all__ = [
    "Base44StatusReporter",
    "loadApiKey",
]
