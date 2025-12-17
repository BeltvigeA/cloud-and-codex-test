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
REPORT_IMAGE_URL = f"{PRINTPRO3D_BASE}/api/printer-images/upload"

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
        "printerIpAddress": "192.168.1.100",  # Optional - one of serial/IP required
        "imageType": "webcam",  # Optional - default is "webcam"
        "imageData": "data:image/jpeg;base64,/9j/4AAQ...",  # Base64 data URI
        "timestamp": "2025-12-11T12:00:00.000Z"  # Optional - defaults to now
    }

    Sends as JSON to the backend API (not multipart/form-data).
    """
    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}

    # Validate imageData is present
    imageDataUri = preparedPayload.get("imageData", "")
    if not imageDataUri:
        log.warning("postReportPrinterImage: missing imageData")
        return {}

    # Ensure imageData has data URI prefix
    if not imageDataUri.startswith("data:"):
        # If raw base64, add prefix (assume JPEG)
        imageDataUri = f"data:image/jpeg;base64,{imageDataUri}"
        preparedPayload["imageData"] = imageDataUri

    # Add timestamp if not provided
    if "timestamp" not in preparedPayload:
        preparedPayload["timestamp"] = _isoNow()

    # Ensure either printerSerial or printerIpAddress is present
    if not preparedPayload.get("printerSerial") and not preparedPayload.get("printerIpAddress"):
        log.warning("postReportPrinterImage: missing both printerSerial and printerIpAddress")
        return {}

    # Set default imageType
    preparedPayload.setdefault("imageType", "webcam")

    # Build headers with Content-Type for JSON
    headers = _buildFunctionsHeaders()

    printer_id = preparedPayload.get("printerSerial") or preparedPayload.get("printerIpAddress") or "unknown"

    try:
        log.info(f"Posting printer image to {REPORT_IMAGE_URL} for printer {printer_id}")
        response = requests.post(
            REPORT_IMAGE_URL,
            json=preparedPayload,
            headers=headers,
            timeout=30,  # Longer timeout for image upload
        )
        response.raise_for_status()
        log.info(f"Image uploaded successfully for printer {printer_id}")
        return response.json() if response.content else {}
    except requests.RequestException as error:
        log.error(f"Failed to upload image for printer {printer_id}: {error}")
        return {}


def listPendingCommandsForRecipient(recipientId: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    # Resolve API key for query parameter
    apiKey = _resolveApiKey("PRINTER_BACKEND_API_KEY", "BASE44_API_KEY")
    
    params: Dict[str, Any] = {
        "recipientId": recipientId,
        "apiKey": apiKey,
    }
    if limit is not None:
        params["limit"] = int(limit)
    baseUrl = _resolveControlBaseUrl()
    from .client import getPrinterControlEndpointUrl

    controlEndpointUrl = getPrinterControlEndpointUrl(baseUrl)
    response = requests.get(
        controlEndpointUrl,
        headers={"Content-Type": "application/json"},  # No X-API-Key, using query param instead
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
    # Normalize 'id' to 'commandId' for API compatibility
    normalized: List[Dict[str, Any]] = []
    for entry in commandsPayload:
        if not isinstance(entry, dict):
            continue
        # New API returns 'id', normalize to 'commandId' for internal use
        if "id" in entry and "commandId" not in entry:
            entry["commandId"] = entry["id"]
        normalized.append(entry)
    return normalized


def acknowledgeCommand(commandId: str) -> None:
    """
    Acknowledge receipt of a command.
    
    NOTE: The new API automatically transitions command status from 'pending' 
    to 'sent' when polled, so explicit ACK is no longer required.
    This function is kept for backward compatibility but is now a no-op.
    """
    log.debug("ACK for %s (no-op, handled automatically by API)", commandId)


def postCommandResult(
    commandId: str,
    status: str,
    message: Optional[str] = None,
    errorMessage: Optional[str] = None,
    recipientId: Optional[str] = None,
) -> None:
    """
    Report command result to the backend.
    
    API format: POST /control/:commandId/result?recipientId=xxx&apiKey=xxx
    Body: { "success": bool, "result": str|null, "error": str|null }
    """
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/control/{commandId}/result"
    
    # Resolve API key for query parameter
    apiKey = _resolveApiKey("PRINTER_BACKEND_API_KEY", "BASE44_API_KEY")
    
    # Resolve recipientId from config or environment if not provided
    resolvedRecipientId = recipientId
    if not resolvedRecipientId:
        if _config_manager_available:
            try:
                config = get_config_manager()
                resolvedRecipientId = config.get_recipient_id()
            except Exception:
                pass
        if not resolvedRecipientId:
            resolvedRecipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()
    
    # Determine success based on status
    normalizedStatus = str(status or "").strip().lower()
    successStatusSet = {"completed", "success", "ok", "done"}
    isSuccess = normalizedStatus in successStatusSet or (normalizedStatus and normalizedStatus not in {"failed", "error", "errored", "ko"})
    
    # Build body with new format
    body: Dict[str, Any] = {
        "success": isSuccess,
        "result": str(message).strip() if message else ("Command executed successfully" if isSuccess else None),
        "error": str(errorMessage).strip() if errorMessage else None,
    }
    
    # Build query params with apiKey
    params: Dict[str, str] = {"apiKey": apiKey}
    if resolvedRecipientId:
        params["recipientId"] = resolvedRecipientId
    
    response = requests.post(
        url,
        json=body,
        headers={"Content-Type": "application/json"},  # No X-API-Key, using query param
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    log.debug("RESULT sent for %s (success=%s)", commandId, body["success"])

_pendingCommandLogLock = threading.Lock()
_pendingCommandLogCounters: Dict[str, int] = {}


def _shouldLogPendingCount(recipientId: str) -> bool:
    key = recipientId or "unknown"
    with _pendingCommandLogLock:
        currentCount = _pendingCommandLogCounters.get(key, 0) + 1
        _pendingCommandLogCounters[key] = currentCount
    return currentCount == 1 or currentCount % 50 == 0


def postReportCompletedJob(
    *,
    product_name: str,
    printer_serial: str,
    printer_ip: str,
    job_id: Optional[str] = None,
    success: bool = True,
    print_time_seconds: Optional[int] = None,
    completed_at: Optional[str] = None,
    image_base64: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Report completed print job to backend.
    
    Endpoint: POST /api/print-jobs/completed?recipientId=xxx
    
    This sends completed job data to the Print Analysis system where it
    will be queued for review with status 'AwaitingReview'.
    
    Args:
        product_name: File name (used for product matching in backend)
        printer_serial: Printer serial number
        printer_ip: Printer IP address
        job_id: Existing job UUID (optional - new one created if not provided)
        success: True if print was successful, False if failed
        print_time_seconds: Print duration in seconds
        completed_at: ISO timestamp for completion (defaults to now)
        image_base64: Base64-encoded image of finished print
    
    Returns:
        Backend response with jobId and status
    """
    # Resolve recipientId
    recipientId = None
    if _config_manager_available:
        try:
            config = get_config_manager()
            recipientId = config.get_recipient_id()
        except Exception:
            pass
    if not recipientId:
        recipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()
    
    if not recipientId:
        log.warning("postReportCompletedJob: missing recipientId; skipping.")
        return {}
    
    url = f"{PRINTPRO3D_BASE}/api/print-jobs/completed"
    params = {"recipientId": recipientId}
    
    # Build payload according to API spec
    payload: Dict[str, Any] = {
        "productName": product_name,
        "printerSerial": printer_serial,
        "printerIpAddress": printer_ip,
        "success": success,
    }
    
    if job_id:
        payload["jobId"] = job_id
    if print_time_seconds is not None:
        payload["printTimeSeconds"] = print_time_seconds
    if completed_at:
        payload["completedAt"] = completed_at
    else:
        payload["completedAt"] = _isoNow()
    if image_base64:
        payload["image"] = image_base64
    
    headers = _buildControlHeaders()
    
    try:
        log.info(f"📤 Reporting completed job to {url}")
        log.info(f"   Product: {product_name}")
        log.info(f"   Printer: {printer_serial}")
        log.info(f"   Job ID: {job_id or 'new'}")
        log.info(f"   Success: {success}")
        if image_base64:
            log.info(f"   Image: {len(image_base64)} chars (base64)")
        
        # Use longer timeout if image is included
        timeout = 30 if image_base64 else 15
        
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json() if response.content else {}
        
        job_id_result = result.get("jobId") or result.get("job_id")
        log.info(f"✅ Completed job reported successfully: jobId={job_id_result}")
        
        return result
    except requests.RequestException as error:
        log.error(f"❌ Failed to report completed job for {product_name}: {error}")
        return {}


