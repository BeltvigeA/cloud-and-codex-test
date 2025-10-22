from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Hardkodet functions-base: ikke per-printer
BASE44_FUNCTIONS_BASE = "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
DEFAULT_CONTROL_BASE_URL = "https://printer-backend-934564650450.europe-west1.run.app"
UPDATE_STATUS_URL = f"{BASE44_FUNCTIONS_BASE}/updatePrinterStatus"
REPORT_ERROR_URL = f"{BASE44_FUNCTIONS_BASE}/reportPrinterError"


def _resolveApiKey(*envKeys: str) -> str:
    for envKey in envKeys:
        apiKeyCandidate = os.getenv(envKey, "").strip()
        if apiKeyCandidate:
            return apiKeyCandidate
    raise RuntimeError("API key is missing")


def _buildFunctionsHeaders() -> Dict[str, str]:
    apiKey = _resolveApiKey("BASE44_FUNCTIONS_API_KEY", "BASE44_API_KEY")
    return {"Content-Type": "application/json", "X-API-Key": apiKey}


def _buildControlHeaders() -> Dict[str, str]:
    apiKey = _resolveApiKey("PRINTER_BACKEND_API_KEY", "BASE44_API_KEY")
    return {"Content-Type": "application/json", "X-API-Key": apiKey}


def _ensureRecipient(payload: Dict[str, object]) -> bool:
    recipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()
    if not recipientId:
        log.warning("Base44: missing BASE44_RECIPIENT_ID; skipping post.")
        return False
    payload["recipientId"] = recipientId
    return True


def _isoNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolveControlBaseUrl() -> str:
    baseCandidate = (
        os.getenv("BASE44_API_BASE")
        or os.getenv("PRINTER_BACKEND_BASE_URL")
        or DEFAULT_CONTROL_BASE_URL
    )
    sanitized = baseCandidate.strip()
    if not sanitized:
        sanitized = DEFAULT_CONTROL_BASE_URL
    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        sanitized = f"https://{sanitized}"
    return sanitized.rstrip("/")


def postUpdateStatus(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to updatePrinterStatus. payload MUST match the required schema."""

    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}
    preparedPayload.setdefault("lastUpdateTimestamp", _isoNow())
    response = requests.post(
        UPDATE_STATUS_URL,
        json=preparedPayload,
        headers=_buildFunctionsHeaders(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def postReportError(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to reportPrinterError. payload MUST match the required schema."""

    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}
    response = requests.post(
        REPORT_ERROR_URL,
        json=preparedPayload,
        headers=_buildFunctionsHeaders(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def listPendingCommandsForRecipient(recipientId: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"recipientId": recipientId}
    if limit is not None:
        params["limit"] = int(limit)
    baseUrl = _resolveControlBaseUrl()
    from .client import getPrinterControlEndpointUrl

    controlEndpointUrl = getPrinterControlEndpointUrl(baseUrl)
    response = requests.get(
        controlEndpointUrl,
        headers=_buildControlHeaders(),
        params=params or None,
        timeout=10,
    )
    response.raise_for_status()
    if not response.content:
        return []
    payload = response.json()
    commandsPayload: Optional[List[Any]] = None
    if isinstance(payload, dict):
        commandsCandidate = payload.get("commands")
        if isinstance(commandsCandidate, list):
            commandsPayload = commandsCandidate
    elif isinstance(payload, list):
        commandsPayload = payload
    commandCount: Optional[int] = None
    if commandsPayload is not None:
        commandCount = len(commandsPayload)
    if commandCount is not None and _shouldLogPendingCount(recipientId):
        log.info("Pending commands fetched for %s: %d", recipientId, commandCount)
    if not commandsPayload:
        return []
    return [entry for entry in commandsPayload if isinstance(entry, dict)]


def acknowledgeCommand(commandId: str) -> None:
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/control/ack"
    payload = {"commandId": commandId}
    response = requests.post(
        url,
        json=payload,
        headers=_buildControlHeaders(),
        timeout=10,
    )
    response.raise_for_status()
    log.debug("ACK sent for %s", commandId)


def postCommandResult(
    commandId: str,
    status: str,
    message: Optional[str] = None,
    errorMessage: Optional[str] = None,
) -> None:
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/control/result"
    body: Dict[str, Any] = {"commandId": commandId, "status": str(status or "").strip() or "completed"}
    if message is not None:
        messageValue = str(message).strip()
        if messageValue:
            body["message"] = messageValue
    if errorMessage is not None:
        errorValue = str(errorMessage).strip()
        if errorValue:
            body["errorMessage"] = errorValue
    response = requests.post(
        url,
        json=body,
        headers=_buildControlHeaders(),
        timeout=10,
    )
    response.raise_for_status()
    log.debug("RESULT sent for %s (status=%s)", commandId, body["status"])
_pendingCommandLogLock = threading.Lock()
_pendingCommandLogCounters: Dict[str, int] = {}


def _shouldLogPendingCount(recipientId: str) -> bool:
    key = recipientId or "unknown"
    with _pendingCommandLogLock:
        currentCount = _pendingCommandLogCounters.get(key, 0) + 1
        _pendingCommandLogCounters[key] = currentCount
    return currentCount == 1 or currentCount % 50 == 0

