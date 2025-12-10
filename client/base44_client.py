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

# Printer backend URL for image uploads (configurable)
PRINTER_BACKEND_BASE = os.getenv("PRINTER_BACKEND_BASE_URL", "https://printer-backend-934564650450.europe-west1.run.app")

# Base44 functions base - now configurable via environment variable (legacy)
BASE44_FUNCTIONS_BASE = _resolveFunctionsBaseUrl()

# PrintPro3D backend API endpoints
REPORT_ERROR_URL = f"{PRINTPRO3D_BASE}/api/printer-events/error"
REPORT_IMAGE_URL = f"{PRINTER_BACKEND_BASE}/api/printer-images/upload"

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
    """Build headers for PrintPro3D backend API (formerly Base44 functions)"""
    apiKey = _resolveApiKey("PRINTER_BACKEND_API_KEY", "BASE44_FUNCTIONS_API_KEY", "BASE44_API_KEY")
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

    Endpoint: POST /api/printer-status/update

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
    statusUrl = f"{PRINTPRO3D_BASE}/api/printer-status/update"

    # Bruk control headers (ikke functions headers)
    headers = _buildControlHeaders()

    try:
        # Use longer timeout if camera image is included (large payload)
        has_camera_image = 'cameraImage' in preparedPayload
        timeout = 30 if has_camera_image else 10

        if has_camera_image:
            log.debug(f"Sending status update with camera image to {statusUrl}")
        else:
            log.debug(f"Sending status update to {statusUrl}")

        response = requests.post(
            statusUrl,
            json=preparedPayload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        printer_serial = preparedPayload.get("printerSerial", recipientId)

        result = response.json() if response.content else {}

        # Log camera image upload success
        if has_camera_image and result:
            if result.get('imageUploaded'):
                log.info(f"✅ Camera image uploaded successfully for printer {printer_serial}")
                image_url = result.get('imageUrl', '')
                if image_url:
                    log.info(f"   🔗 Image URL: {image_url[:80]}...")
            else:
                log.warning(f"⚠️  Camera image not uploaded (backend did not confirm)")

        log.info(f"Status update successful for printer {printer_serial} (recipient: {recipientId})")
        return result
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
    """
    POST camera snapshot to printer backend for permanent storage.

    Saves images to Google Cloud Storage via the printer backend API.
    The old Base44 endpoint only stored images temporarily.

    Expected payload format:
    {
        "recipientId": "RID123",  # Optional - will be auto-filled if missing
        "printerSerial": "01P00A381200434",
        "printerIpAddress": "192.168.1.100",  # Optional
        "imageType": "webcam",  # Optional - default is "webcam"
        "imageData": "data:image/jpeg;base64,/9j/4AAQ..."  # Base64 data URI
    }

    Converts to multipart/form-data upload for backend API.
    """
    import base64
    from io import BytesIO

    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}

    # Extract and decode image data
    imageDataUri = preparedPayload.get("imageData", "")
    if not imageDataUri:
        log.warning("postReportPrinterImage: missing imageData")
        return {}

    # Strip data URI prefix if present (e.g., "data:image/jpeg;base64,...")
    if imageDataUri.startswith("data:"):
        imageDataUri = imageDataUri.split(",", 1)[1] if "," in imageDataUri else imageDataUri

    try:
        # Decode base64 image data
        imageBytes = base64.b64decode(imageDataUri)
    except Exception as e:
        log.error(f"Failed to decode image data: {e}")
        return {}

    # Prepare multipart form data
    files = {"image": ("snapshot.jpg", BytesIO(imageBytes), "image/jpeg")}
    data = {
        "recipientId": preparedPayload.get("recipientId"),
        "printerSerial": preparedPayload.get("printerSerial", ""),
        "imageType": preparedPayload.get("imageType", "webcam")
    }

    # Include printerIpAddress if provided
    if "printerIpAddress" in preparedPayload and preparedPayload["printerIpAddress"]:
        data["printerIpAddress"] = preparedPayload["printerIpAddress"]

    # Build headers (without Content-Type - requests will set it for multipart)
    apiKey = _resolveApiKey("PRINTER_BACKEND_API_KEY", "BASE44_FUNCTIONS_API_KEY", "BASE44_API_KEY")
    headers = {"X-API-Key": apiKey}

    try:
        response = requests.post(
            REPORT_IMAGE_URL,
            files=files,
            data=data,
            headers=headers,
            timeout=30,  # Longer timeout for image upload
        )
        response.raise_for_status()
        printer_serial = data.get("printerSerial", "unknown")
        log.info(f"Image uploaded successfully for printer {printer_serial}")
        return response.json() if response.content else {}
    except requests.RequestException as error:
        printer_serial = data.get("printerSerial", "unknown")
        log.error(f"Failed to upload image for printer {printer_serial}: {error}")
        return {}


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

