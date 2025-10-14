"""Background reporter that sends printer snapshots to Base44."""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from typing import Any, Callable, Iterable

from .base44 import callFunction, getDefaultApiKey, getStatusFunctionName
from .commands import completeCommand, listPendingCommands
from .pending import requestPendingPollTrigger
from .health import HealthGate, HealthState
from .reachability import tcpCheck
from .bambuClient import BambuLanClient
from .controls import executeCommand

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
        self._pingIntervalSeconds = max(0.5, float(os.getenv("PING_INTERVAL_SECONDS", "5")))
        self._failsToOffline = max(1, int(os.getenv("PING_FAILS_TO_OFFLINE", "3")))
        self._oksToOnline = max(1, int(os.getenv("PING_OKS_TO_ONLINE", "1")))
        self._statusIntervalSeconds = max(1.0, float(os.getenv("STATUS_INTERVAL_SECONDS", "10")))
        self._healthByPrinter: dict[tuple[str, str], HealthGate] = {}
        self._lastPingTimestampByPrinter: dict[tuple[str, str], float] = {}
        self._lastStatusTimestampByPrinter: dict[tuple[str, str], float] = {}
        self._lanClients: dict[tuple[str, str], BambuLanClient] = {}

    def start(self, recipientId: str, apiKey: str | None = None) -> None:
        self._recipientId = recipientId.strip()
        self._apiKeyOverride = (apiKey or "").strip()
        self._stopEvent.clear()
        if self._isRunning:
            return
        self._statusFunctionName = getStatusFunctionName()
        self._nextCommandPollTimestamp = 0.0
        self._commandBackoffSeconds = float(self._commandPollIntervalSec)
        self._healthByPrinter.clear()
        self._lastPingTimestampByPrinter.clear()
        self._lastStatusTimestampByPrinter.clear()
        self._resetLanClients()
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
        self._resetLanClients()
        LOG.info("Base44StatusReporter stopped")

    def _resetLanClients(self) -> None:
        for client in list(self._lanClients.values()):
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                LOG.debug("Failed to disconnect LAN client", exc_info=True)
        self._lanClients.clear()

    def _buildPayload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self._recipientId:
            return {}

        key = self._resolvePrinterKey(snapshot)
        effectiveSnapshot = self._applyHealthToSnapshot(key, snapshot)

        statusValue, onlineFlag = _sanitizeStatus(effectiveSnapshot)
        mqttReady = bool(effectiveSnapshot.get("mqttReady"))
        if not mqttReady:
            onlineFlag = False
        serialKey = str(effectiveSnapshot.get("serial") or "").strip().lower()
        ipKey = str(effectiveSnapshot.get("ip") or "").strip()
        keyTuple = (serialKey, ipKey)

        previous = self._lastSnapshotByPrinter.get(keyTuple, {})
        lastTemps = {
            "bedTemp": previous.get("bedTemp"),
            "nozzleTemp": previous.get("nozzleTemp"),
            "timestamp": previous.get("timestamp", 0.0),
        }

        currentBed = effectiveSnapshot.get("bed")
        currentNozzle = effectiveSnapshot.get("nozzle")

        mqttReady = effectiveSnapshot.get("mqttReady")
        if mqttReady is False:
            offlineSince = self._mqttOfflineSince.setdefault(keyTuple, time.time())
            if time.time() - offlineSince > 30:
                onlineFlag = False
        else:
            self._mqttOfflineSince.pop(keyTuple, None)

        progressValue = _coerceInt(effectiveSnapshot.get("progress"))
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
            "printerIpAddress": effectiveSnapshot.get("ip"),
            "serialNumber": effectiveSnapshot.get("serial"),
            "status": statusValue,
            "online": onlineFlag,
            "jobProgress": progressValue,
            "currentJobId": effectiveSnapshot.get("currentJobId"),
            "bedTemp": currentBed,
            "nozzleTemp": currentNozzle,
            "fanSpeed": effectiveSnapshot.get("fan"),
            "printSpeed": effectiveSnapshot.get("speed"),
            "filamentUsed": effectiveSnapshot.get("filamentUsed"),
            "timeRemaining": _coerceInt(effectiveSnapshot.get("timeRemaining")),
            "errorMessage": effectiveSnapshot.get("error"),
            "lastUpdateTimestamp": _isoUtcNow(),
            "firmwareVersion": effectiveSnapshot.get("firmware"),
            "mqttReady": mqttReady,
        }

        self._lastSnapshotByPrinter[keyTuple] = {
            "bedTemp": payload.get("bedTemp"),
            "nozzleTemp": payload.get("nozzleTemp"),
            "timestamp": time.time(),
        }

        return payload

    def _runLoop(self) -> None:
        self._isRunning = True
        try:
            while not self._stopEvent.is_set():
                snapshots: list[dict[str, Any]] = []
                if not self._recipientId:
                    LOG.debug("Skipping Base44 post: missing recipient")
                else:
                    snapshots = list(self._safeCollectSnapshots())
                    now = time.time()
                    for snapshot in snapshots:
                        key = self._resolvePrinterKey(snapshot)
                        if key is None:
                            continue
                        ipAddress = self._resolvePrinterIp(snapshot)
                        gate = self._getHealthGate(key)
                        stateAfter, stateChanged = self._ensureHealthState(key, gate, ipAddress, now)

                        if stateChanged and stateAfter.hasState:
                            changeSnapshot = self._buildStateChangeSnapshot(snapshot, ipAddress, stateAfter)
                            payload = self._buildPayload(changeSnapshot)
                            self._postPayload(payload, key)
                            self._lastStatusTimestampByPrinter[key] = now

                        if stateAfter.hasState and stateAfter.isOnline:
                            lastStatusAt = self._lastStatusTimestampByPrinter.get(key, 0.0)
                            if stateChanged or (now - lastStatusAt) >= self._statusIntervalSeconds:
                                telemetrySnapshot = dict(snapshot)
                                telemetrySnapshot["online"] = True
                                payload = self._buildPayload(telemetrySnapshot)
                                self._postPayload(payload, key)
                                self._lastStatusTimestampByPrinter[key] = now

                try:
                    self._pollPendingCommands(snapshots)
                except Exception as error:  # noqa: BLE001
                    LOG.exception("Status push failed: %s", error)

                self._stopEvent.wait(self._intervalSec)
        finally:
            self._isRunning = False


    def _resolvePrinterKey(self, snapshot: dict[str, Any]) -> tuple[str, str] | None:
        serialKey = str(snapshot.get("serial") or snapshot.get("serialNumber") or "").strip().lower()
        ipKey = str(
            snapshot.get("printerIpAddress")
            or snapshot.get("ip")
            or snapshot.get("ipAddress")
            or ""
        ).strip()
        if not serialKey and not ipKey:
            return None
        return (serialKey, ipKey)

    def _resolvePrinterIp(self, snapshot: dict[str, Any]) -> str:
        return str(
            snapshot.get("printerIpAddress")
            or snapshot.get("ip")
            or snapshot.get("ipAddress")
            or ""
        ).strip()

    def _getHealthGate(self, key: tuple[str, str]) -> HealthGate:
        gate = self._healthByPrinter.get(key)
        if gate is None:
            gate = HealthGate(self._failsToOffline, self._oksToOnline)
            self._healthByPrinter[key] = gate
        return gate

    @staticmethod
    def _stateChanged(before: HealthState, after: HealthState) -> bool:
        return (before.hasState != after.hasState) or (before.isOnline != after.isOnline)

    def _ensureHealthState(
        self,
        key: tuple[str, str],
        gate: HealthGate,
        ipAddress: str,
        currentTime: float,
    ) -> tuple[HealthState, bool]:
        previousState = gate.state
        lastPing = self._lastPingTimestampByPrinter.get(key, 0.0)
        shouldProbe = not previousState.hasState or (currentTime - lastPing) >= self._pingIntervalSeconds
        resultState = previousState

        if shouldProbe:
            reachable = False
            if ipAddress:
                try:
                    reachable = tcpCheck(ipAddress)
                except Exception:  # noqa: BLE001 - treat as unreachable
                    reachable = False
            resultState = gate.observe(reachable)
            self._lastPingTimestampByPrinter[key] = currentTime

        return resultState, self._stateChanged(previousState, resultState)

    def _buildStateChangeSnapshot(
        self,
        snapshot: dict[str, Any],
        ipAddress: str,
        state: HealthState,
    ) -> dict[str, Any]:
        resolved = dict(snapshot)
        mqttReady = bool(resolved.get("mqttReady"))
        resolved["mqttReady"] = mqttReady
        resolved["online"] = state.isOnline and mqttReady
        if ipAddress:
            resolved.setdefault("printerIpAddress", ipAddress)
            resolved.setdefault("ip", ipAddress)
        if resolved["online"]:
            resolved.setdefault("status", resolved.get("status") or "Online")
        else:
            resolved["status"] = resolved.get("status") or "Offline"
            resolved["bedTemp"] = None
            resolved["nozzleTemp"] = None
            resolved["bed"] = None
            resolved["nozzle"] = None
            resolved["progress"] = 0
            resolved["timeRemaining"] = None
            resolved["remainingTimeSeconds"] = None
        return resolved

    def _applyHealthToSnapshot(
        self,
        key: tuple[str, str] | None,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        if key is None:
            return snapshot
        gate = self._healthByPrinter.get(key)
        if gate is None:
            return snapshot
        resolved = dict(snapshot)
        mqttReady = bool(snapshot.get("mqttReady"))
        resolved["mqttReady"] = mqttReady
        if not gate.state.hasState or not mqttReady:
            resolved["online"] = False
            resolved["status"] = resolved.get("status") or "Offline"
            resolved["bedTemp"] = None
            resolved["nozzleTemp"] = None
            resolved["bed"] = None
            resolved["nozzle"] = None
            resolved["progress"] = 0
            resolved["timeRemaining"] = None
            resolved["remainingTimeSeconds"] = None
            return resolved
        resolved["online"] = gate.state.isOnline and mqttReady
        if not resolved["online"]:
            resolved["status"] = resolved.get("status") or "Offline"
            resolved["bedTemp"] = None
            resolved["nozzleTemp"] = None
            resolved["bed"] = None
            resolved["nozzle"] = None
            resolved["progress"] = 0
            resolved["timeRemaining"] = None
            resolved["remainingTimeSeconds"] = None
        return resolved

    def _postPayload(self, payload: dict[str, Any], key: tuple[str, str]) -> None:
        if not payload:
            return

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

        wasOnline = self._lastOnlineStateByPrinter.get(key, False)
        nowOnline = bool(payload.get("online"))
        self._lastOnlineStateByPrinter[key] = nowOnline
        if wasOnline and not nowOnline:
            requestPendingPollTrigger()


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

        if commandType == "poke":
            self._handlePokeCommand(command, snapshots)
            return

        self._handleControlCommand(command, commandType, snapshots)


    def _handlePokeCommand(self, command: dict[str, Any], snapshots: list[dict[str, Any]]) -> None:
        commandId = str(command.get("commandId") or "").strip()
        if not commandId:
            LOG.warning("[commands] Hopper over kommando uten ID: %r", command)
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

        key = self._resolvePrinterKey(snapshot)
        if key is not None:
            gate = self._getHealthGate(key)
            self._ensureHealthState(key, gate, printerIp or self._resolvePrinterIp(snapshot), time.time())

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


    def _handleControlCommand(
        self,
        command: dict[str, Any],
        commandType: str,
        snapshots: list[dict[str, Any]],
    ) -> None:
        commandId = str(command.get("commandId") or "").strip()
        printerIp = str(command.get("printerIpAddress") or "").strip()
        if not printerIp:
            if not completeCommand(commandId, False, recipientId=self._recipientId, error="Printeradresse mangler"):
                LOG.error("[commands] Klarte ikke markere %s uten ip", commandId)
            return

        snapshot = self._findOrRefreshSnapshot(printerIp, snapshots)
        if snapshot is None:
            if not completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error=f"Printer {printerIp} ikke funnet",
            ):
                LOG.error("[commands] Klarte ikke markere %s som manglende", commandId)
            return

        credentials = self._resolvePrinterCredentials(snapshot, command, printerIp)
        if credentials is None:
            if not completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error="Mangler tilgangskode",
            ):
                LOG.error("[commands] Klarte ikke markere %s uten tilgangskode", commandId)
            return

        key = self._resolvePrinterKey(snapshot) or (
            credentials["serial"].strip().lower(),
            credentials["ip"],
        )
        gate = self._getHealthGate(key)
        self._ensureHealthState(key, gate, printerIp, time.time())

        try:
            client = self._getLanClient(key, credentials)
            executeCommand(client, command)
        except NotImplementedError as error:
            LOG.error("[commands] Kommando ikke støttet: %s", error)
            completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error=str(error),
            )
            return
        except Exception as error:
            LOG.exception("[commands] Kontrollkommando feilet for %s", printerIp)
            completeCommand(
                commandId,
                False,
                recipientId=self._recipientId,
                error=str(error),
            )
            return

        if not completeCommand(commandId, True, recipientId=self._recipientId):
            LOG.error("[commands] Klarte ikke bekrefte %s", commandId)
            return

        LOG.info("[commands] Kommando %s ferdig for %s", commandType, printerIp)


    def _findOrRefreshSnapshot(
        self,
        printerIp: str,
        snapshots: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        snapshot = self._findSnapshotForIp(printerIp, snapshots)
        if snapshot is not None:
            return snapshot
        freshSnapshots = self._safeCollectSnapshots()
        snapshot = self._findSnapshotForIp(printerIp, freshSnapshots)
        if snapshot is not None:
            snapshots[:] = freshSnapshots
        return snapshot


    def _resolvePrinterCredentials(
        self,
        snapshot: dict[str, Any],
        command: dict[str, Any],
        printerIp: str,
    ) -> dict[str, str] | None:
        metadata = command.get("metadata") or {}

        def _extract(source: dict[str, Any], keys: Iterable[str]) -> str:
            for key in keys:
                if key in source:
                    value = source.get(key)
                    if value is not None:
                        text = str(value).strip()
                        if text:
                            return text
            return ""

        serialValue = _extract(snapshot, ("serial", "serialNumber", "printerSerial"))
        if not serialValue:
            serialValue = _extract(metadata, ("serial", "serialNumber", "printerSerial"))

        accessCodeValue = _extract(
            snapshot,
            ("accessCode", "access_code", "lanAccessCode", "lan_access_code"),
        )
        if not accessCodeValue:
            accessCodeValue = _extract(
                metadata,
                ("accessCode", "access_code", "lanAccessCode", "lan_access_code"),
            )

        if not accessCodeValue:
            return None

        return {
            "ip": printerIp,
            "serial": serialValue or "",
            "accessCode": accessCodeValue,
        }


    def _getLanClient(
        self,
        key: tuple[str, str],
        credentials: dict[str, str],
    ) -> BambuLanClient:
        normalizedKey = (key[0].strip().lower(), credentials["ip"])
        client = self._lanClients.get(normalizedKey)
        if client is not None:
            if client.ipAddress != credentials["ip"] or client.accessCode != credentials["accessCode"]:
                try:
                    client.disconnect()
                except Exception:
                    LOG.debug("Klarte ikke å koble fra gammel klient", exc_info=True)
                client = None
        if client is None:
            client = BambuLanClient(
                credentials["ip"],
                credentials["accessCode"],
                credentials.get("serial") or None,
            )
            self._lanClients[normalizedKey] = client
        return client


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
