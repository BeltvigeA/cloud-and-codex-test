"""Shared helpers for interacting with Base44 function endpoints."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import requests

LOG = logging.getLogger(__name__)

_DEFAULT_BASE_URL = (
    "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
)
_DEFAULT_PENDING_FUNCTION = "listRecipientFiles"
_DEFAULT_STATUS_FUNCTION = "updatePrinterStatus"
_DEFAULT_TIMEOUT_SECONDS = 10.0


def getBaseUrl() -> str:
    """Resolve the Base44 function base URL from the environment."""

    baseUrl = os.getenv("BASE44_BASE", "") or _DEFAULT_BASE_URL
    return baseUrl.rstrip("/")


def getDefaultApiKey() -> str:
    """Resolve the API key from known environment variables."""

    return (os.getenv("BASE44_API_KEY") or os.getenv("PRINTER_API_TOKEN") or "").strip()


def getStatusFunctionName() -> str:
    """Return the configured status function name."""

    return os.getenv("BASE44_STATUS_FN", _DEFAULT_STATUS_FUNCTION)


def getPendingFunctionName() -> str:
    """Return the configured pending-files function name."""

    return os.getenv("BASE44_PENDING_FN", _DEFAULT_PENDING_FUNCTION)


def callFunction(
    functionName: str,
    payload: Dict[str, Any],
    *,
    apiKey: Optional[str] = None,
    timeoutSeconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Optional[Any]:
    """Call a Base44 function and return the parsed response when possible."""

    baseUrl = getBaseUrl()
    if not baseUrl:
        LOG.error("[base44] Function base URL is not configured.")
        return None

    url = f"{baseUrl}/{functionName}".rstrip("/")
    headers = {"Content-Type": "application/json"}
    resolvedApiKey = (apiKey or "").strip() or getDefaultApiKey()
    if resolvedApiKey:
        headers["X-API-Key"] = resolvedApiKey

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=max(1.0, float(timeoutSeconds)),
        )
    except Exception as error:  # noqa: BLE001 - propagate failure via logging only
        LOG.error("[base44] %s: exception %s", functionName, error)
        return None

    contentType = (response.headers.get("content-type") or "").lower()
    textBody = response.text or ""

    if response.status_code == 204 or not textBody.strip():
        LOG.info("[base44] %s: %s (empty)", functionName, response.status_code)
        return None

    if response.status_code >= 400:
        LOG.error(
            "[base44] %s: HTTP %s %s",
            functionName,
            response.status_code,
            textBody[:200],
        )
        return None

    if "application/json" not in contentType:
        LOG.error(
            "[base44] %s: non-JSON %s: %s",
            functionName,
            response.status_code,
            textBody[:200],
        )
        return None

    try:
        parsed = response.json()
    except json.JSONDecodeError as error:
        LOG.error("[base44] %s: invalid JSON (%s)", functionName, error)
        return None

    if isinstance(parsed, dict) and "error_type" in parsed:
        LOG.error("[base44] %s: remote error %s", functionName, parsed)
        return None

    return parsed


# Compatibility aliases for snake_case references
call_function = callFunction
get_base_url = getBaseUrl
get_default_api_key = getDefaultApiKey
get_status_function_name = getStatusFunctionName
get_pending_function_name = getPendingFunctionName
