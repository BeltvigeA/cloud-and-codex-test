from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

from . import commands as commandModule


class BambuLanClient:
    def __init__(self, ipAddress: str, accessCode: str, serialNumber: Optional[str]) -> None:
        self.ipAddress = ipAddress
        self.accessCode = accessCode
        self.serialNumber = serialNumber

    def disconnect(self) -> None:  # pragma: no cover - basic stub
        return None


def listPendingCommands(recipientId: Optional[str] = None) -> List[Dict[str, Any]]:
    return commandModule.listPendingCommands(recipientId)


def completeCommand(
    commandId: str,
    success: bool,
    *,
    recipientId: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    return commandModule.completeCommand(
        commandId,
        success,
        recipientId=recipientId,
        error=error,
    )


def callFunction(
    functionName: str,
    payload: Dict[str, Any],
    *,
    apiKey: Optional[str] = None,
    timeoutSeconds: float = 30,
) -> Dict[str, Any]:
    baseUrl = os.getenv("BASE44_BASE")
    if not baseUrl:
        raise RuntimeError("BASE44_BASE is not configured")
    url = f"{baseUrl.rstrip('/')}/functions/{functionName}"
    headers = {"Accept": "application/json"}
    if apiKey:
        headers["X-API-Key"] = apiKey
    response = requests.post(
        url,
        data=json.dumps(payload),
        headers=headers,
        timeout=timeoutSeconds,
    )
    response.raise_for_status()
    return response.json()


def tcpCheck(ipAddress: str, port: int = 8899, timeoutSeconds: float = 1.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeoutSeconds)
    try:
        sock.connect((ipAddress, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def executeCommand(client: Any, command: Dict[str, Any]) -> None:  # pragma: no cover - monkeypatched in tests
    raise NotImplementedError("executeCommand must be monkeypatched for testing")


class Base44StatusReporter:
    def __init__(
        self,
        snapshotProvider: Callable[[], Iterable[Dict[str, Any]]],
        *,
        intervalSec: float = 60.0,
        commandPollIntervalSec: float = 60.0,
    ) -> None:
        self._snapshotProvider = snapshotProvider
        self._intervalSec = intervalSec
        self._commandPollIntervalSec = commandPollIntervalSec
        self._recipientId: Optional[str] = os.getenv("BASE44_RECIPIENT_ID")
        self._statusFunctionName: str = os.getenv("BASE44_STATUS_FUNCTION", "updatePrinterStatus")
        self._statusFunctionApiKey: Optional[str] = os.getenv("BASE44_FUNCTION_API_KEY")
        self._commandBackoffSeconds: float = commandPollIntervalSec
        self._nextCommandPollTimestamp: float = 0.0

    def _pollPendingCommands(self, snapshots: List[Dict[str, Any]]) -> None:
        if not self._recipientId:
            return
        now = time.time()
        if now < self._nextCommandPollTimestamp:
            return
        self._nextCommandPollTimestamp = now + self._commandBackoffSeconds

        try:
            pendingCommands = listPendingCommands(self._recipientId)
        except Exception:
            return

        for command in pendingCommands:
            commandType = command.get("commandType")
            commandId = command.get("commandId")
            printerIp = command.get("printerIpAddress")
            metadata = command.get("metadata", {}) or {}

            if commandType == "poke":
                if not printerIp or not tcpCheck(printerIp):
                    completeCommand(commandId, False, recipientId=self._recipientId, error="offline")
                    continue

                payload = {
                    "printerIpAddress": printerIp,
                    "commandId": commandId,
                    "snapshots": snapshots,
                }
                callFunction(
                    self._statusFunctionName,
                    payload,
                    apiKey=self._statusFunctionApiKey,
                    timeoutSeconds=self._intervalSec,
                )
                completeCommand(commandId, True, recipientId=self._recipientId)
                continue

            if not printerIp:
                completeCommand(commandId, False, recipientId=self._recipientId, error="missing_ip")
                continue

            if not tcpCheck(printerIp):
                completeCommand(commandId, False, recipientId=self._recipientId, error="offline")
                continue

            accessCode = metadata.get("accessCode", "")
            serialNumber = metadata.get("serialNumber")
            client = BambuLanClient(printerIp, accessCode, serialNumber)
            try:
                executeCommand(client, command)
            except Exception as exc:  # pragma: no cover - surfaced via tests
                completeCommand(
                    commandId,
                    False,
                    recipientId=self._recipientId,
                    error=str(exc),
                )
            else:
                completeCommand(commandId, True, recipientId=self._recipientId)
            finally:
                try:
                    client.disconnect()
                except Exception:  # pragma: no cover - defensive
                    pass

    def _buildPayload(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "printerIpAddress": snapshot.get("ip"),
            "serial": snapshot.get("serial"),
            "status": snapshot.get("status"),
            "online": bool(snapshot.get("online")),
            "mqttReady": bool(snapshot.get("mqttReady")),
            "progress": snapshot.get("progress"),
            "bed": snapshot.get("bed"),
            "nozzle": snapshot.get("nozzle"),
            "timeRemaining": snapshot.get("timeRemaining"),
        }
        return payload
