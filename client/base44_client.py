from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

# Hardkodet functions-base: ikke per-printer
BASE44_FUNCTIONS_BASE = "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
DEFAULT_CONTROL_BASE_URL = "https://printer-backend-934564650450.europe-west1.run.app"
UPDATE_STATUS_URL = f"{BASE44_FUNCTIONS_BASE}/updatePrinterStatus"
REPORT_ERROR_URL = f"{BASE44_FUNCTIONS_BASE}/reportPrinterError"


def _buildHeaders() -> Dict[str, str]:
    apiKey = os.getenv("BASE44_API_KEY", "").strip()
    if not apiKey:
        raise RuntimeError("BASE44_API_KEY is missing")
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
    response = requests.post(UPDATE_STATUS_URL, json=preparedPayload, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    return response.json() if response.content else {}


def postReportError(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to reportPrinterError. payload MUST match the required schema."""

    preparedPayload = dict(payload)
    if not _ensureRecipient(preparedPayload):
        return {}
    response = requests.post(REPORT_ERROR_URL, json=preparedPayload, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    return response.json() if response.content else {}


def listPendingCommandsForRecipient(recipientId: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    if limit is not None:
        params["limit"] = int(limit)
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/recipients/{recipientId}/pending"
    response = requests.get(url, headers=_buildHeaders(), params=params or None, timeout=10)
    response.raise_for_status()
    if not response.content:
        return []
    payload = response.json()
    try:
        commandCount = 0
        if isinstance(payload, list):
            commandCount = len(payload)
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("commands")
            if isinstance(items, list):
                commandCount = len(items)
        if commandCount:
            log.debug("Pending commands fetched for %s: %d", recipientId, commandCount)
    except Exception:
        pass
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("commands")
        if isinstance(items, list):
            normalized: List[Dict[str, Any]] = []
            for entry in items:
                if isinstance(entry, dict):
                    normalized.append(entry)
            return normalized
        if isinstance(payload.get("metadata"), list):
            return [entry for entry in payload.get("metadata", []) if isinstance(entry, dict)]
        return []
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def acknowledgeCommand(commandId: str) -> None:
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/control/ack"
    payload = {"commandId": commandId}
    response = requests.post(url, json=payload, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    log.debug("ACK sent for %s", commandId)


def postCommandResult(commandId: str, success: bool, message: Optional[str] = None) -> None:
    baseUrl = _resolveControlBaseUrl()
    url = f"{baseUrl}/control/result"
    body: Dict[str, Any] = {"commandId": commandId, "success": bool(success)}
    if message:
        body["message"] = message
    response = requests.post(url, json=body, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    log.debug("RESULT sent for %s (success=%s)", commandId, success)
