from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict

import requests

BASE44_FUNCTIONS_BASE = "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
UPDATE_STATUS_URL = f"{BASE44_FUNCTIONS_BASE}/updatePrinterStatus"
REPORT_ERROR_URL = f"{BASE44_FUNCTIONS_BASE}/reportPrinterError"


def _buildHeaders() -> Dict[str, str]:
    apiKey = os.getenv("BASE44_API_KEY", "").strip()
    if not apiKey:
        raise RuntimeError("BASE44_API_KEY is missing")
    return {"Content-Type": "application/json", "X-API-Key": apiKey}


def _isoNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def postUpdateStatus(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to updatePrinterStatus. payload MUST match the required schema."""

    preparedPayload = dict(payload)
    preparedPayload.setdefault("lastUpdateTimestamp", _isoNow())
    response = requests.post(UPDATE_STATUS_URL, json=preparedPayload, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    return response.json() if response.content else {}


def postReportError(payload: Dict[str, object]) -> Dict[str, object]:
    """POST to reportPrinterError. payload MUST match the required schema."""

    response = requests.post(REPORT_ERROR_URL, json=payload, headers=_buildHeaders(), timeout=10)
    response.raise_for_status()
    return response.json() if response.content else {}
