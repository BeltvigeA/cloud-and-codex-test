"""Pending job utilities for interacting with the Cloud Run backend."""

from __future__ import annotations

import logging
import threading
from importlib import import_module
from typing import Any, Dict, List, Optional

import requests

LOG = logging.getLogger(__name__)

_PENDING_TRIGGER = threading.Event()


def requestPendingPollTrigger() -> None:
    """Signal that an immediate pending poll should run."""

    _PENDING_TRIGGER.set()


def consumePendingPollTrigger() -> bool:
    """Return True if an immediate pending poll has been requested."""

    if _PENDING_TRIGGER.is_set():
        _PENDING_TRIGGER.clear()
        return True
    return False


def listPending(
    recipientId: str,
    *,
    baseUrl: str,
    apiKey: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve pending files for the specified recipient from Cloud Run."""

    normalizedRecipient = (recipientId or "").strip()
    if not normalizedRecipient:
        LOG.error("[pending] recipientId is required")
        return []

    try:
        clientModule = import_module("client.client")
        pendingUrl = clientModule.buildPendingUrl(baseUrl, normalizedRecipient)
    except Exception as error:  # pragma: no cover - unexpected import failure
        LOG.error("[pending] Unable to resolve pending URL: %s", error)
        return []

    headers = {"Accept": "application/json"}
    if apiKey:
        headers["X-API-Key"] = apiKey

    try:
        response = requests.get(pendingUrl, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        LOG.error("[pending] Failed to fetch pending jobs from %s: %s", pendingUrl, error)
        return []

    try:
        responseData = response.json()
    except ValueError as error:
        LOG.error("[pending] Invalid JSON payload from %s: %s", pendingUrl, error)
        return []

    jobs: List[Dict[str, Any]] = []
    if isinstance(responseData, dict):
        payloadItems = responseData.get("pendingFiles")
        if isinstance(payloadItems, list):
            jobs = [item for item in payloadItems if isinstance(item, dict)]
    elif isinstance(responseData, list):
        jobs = [item for item in responseData if isinstance(item, dict)]

    LOG.info(
        "[pending] url=%s recipientId=%s count=%s",
        pendingUrl,
        normalizedRecipient,
        len(jobs),
    )
    return jobs


# snake_case compatibility exports
request_pending_poll_trigger = requestPendingPollTrigger
consume_pending_poll_trigger = consumePendingPollTrigger
list_pending = listPending
