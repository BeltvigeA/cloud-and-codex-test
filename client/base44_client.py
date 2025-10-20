from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict

import requests

log = logging.getLogger(__name__)

# Hardkodet functions-base: ikke per-printer
BASE44_FUNCTIONS_BASE = "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
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
