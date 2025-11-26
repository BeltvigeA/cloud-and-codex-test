from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Import config manager
try:
    from .config_manager import get_config_manager
    _config_manager_available = True
except ImportError:
    _config_manager_available = False

def _resolveFunctionsBaseUrl() -> str:
    """Resolve Base44 functions base URL from environment or use default."""
    baseCandidate = os.getenv("BASE44_FUNCTIONS_BASE", "").strip()
    if not baseCandidate:
        baseCandidate = "https://printpro3d-api-931368217793.europe-west1.run.app/api/apps/68b61486e7c52405eb683cc6/functions"
    if not baseCandidate.startswith("http://") and not baseCandidate.startswith("https://"):
        baseCandidate = f"https://{baseCandidate}"
    return baseCandidate.rstrip("/")


# Hardkodet PrintPro3D backend URL for status updates
PRINTPRO3D_BASE = "https://printpro3d-api-931368217793.europe-west1.run.app"

# Base44 functions base - now configurable via environment variable
BASE44_FUNCTIONS_BASE = _resolveFunctionsBaseUrl()

# Legacy endpoints (kun for error og image reporting)
REPORT_ERROR_URL = f"{BASE44_FUNCTIONS_BASE}/reportPrinterError"
REPORT_IMAGE_URL = f"{BASE44_FUNCTIONS_BASE}/reportPrinterImage"

# Note: UPDATE_STATUS_URL er nå dynamisk og bygges i postUpdateStatus()


def _resolveApiKey(*envKeys: str) -> str:
    # Try config manager first if available
    if _config_manager_available:
        try:
            config = get_config_manager()
            api_key = config.get_api_key()
            if api_key:
                return api_key
        except Exception:
            pass  # Fall back to environment variables

    # Fall back to environment variables
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
    recipientId = None

    # Try config manager first if available
    if _config_manager_available:
        try:
            config = get_config_manager()
            recipientId = config.get_recipient_id()
        except Exception:
            pass  # Fall back to environment variable

    # Fall back to environment variable
    if not recipientId:
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
        or PRINTPRO3D_BASE
    )
    sanitized = baseCandidate.strip()
    if not sanitized:
        sanitized = PRINTPRO3D_BASE
    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        sanitized = f"https://{sanitized}"
    return sanitized.rstrip("/")


def postUpdateStatus(payload: Dict[str, object]) -> Dict[str, object]:
    """
    POST to printer status update endpoint.

    Endpoint: POST /printer-status

    Payload format:
    {
        "recipientId": "RID123",
        "printerSerial": "01P00A381200434",
        "printerIpAddress": "192.168.1.100",
        "status": {
            "status": "Idle",
            "online": true,
            "mqttReady": false,
            "bedTemp": 25.5,
            "nozzleTemp": 28.0,
            "fanSpeed": 0,
            "progress": 0,
            "timeRemaining": 0,
            "gcodeState": "idle",
            "currentJobId": null,
            "errorMessage": null
        }
    }
    """
    preparedPayload = dict(payload)

    # Hent recipientId
    recipientId = payload.get("recipientId")
    if not recipientId:
        # Try config manager first if available
        if _config_manager_available:
            try:
                config = get_config_manager()
                recipientId = config.get_recipient_id()
            except Exception:
                pass  # Fall back to environment variable

        # Fall back to environment variable
        if not recipientId:
            recipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()

    if not recipientId:
        printer_serial = payload.get("printerSerial", "unknown")
        log.warning("postUpdateStatus: missing recipientId for printer %s; skipping.", printer_serial)
        return {}

    # Sett recipientId i payload
    preparedPayload["recipientId"] = recipientId
    preparedPayload.setdefault("lastUpdateTimestamp", _isoNow())

    # Bygg dynamisk URL (bruker hardkodet backend URL)
    statusUrl = f"{PRINTPRO3D_BASE}/printer-status"

    # Bruk control headers (ikke functions headers)
    headers = _buildControlHeaders()

    try:
        log.debug(f"Sending status update to {statusUrl}")
        response = requests.post(
            statusUrl,
            json=preparedPayload,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        printer_serial = preparedPayload.get("printerSerial", recipientId)
        log.info(f"Status update successful for printer {printer_serial} (recipient: {recipientId})")
        return response.json() if response.content else {}
    except requests.RequestException as error:
        printer_serial = preparedPayload.get("printerSerial", recipientId)
        log.error(f"Failed to update status for printer {printer_serial} (recipient: {recipientId}): {error}")
        return {}


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


def postReportPrinterImage(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to reportPrinterImage. payload MUST match the required schema."""

    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}
    response = requests.post(
        REPORT_IMAGE_URL,
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

