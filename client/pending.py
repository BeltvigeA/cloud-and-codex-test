"""Pending job utilities built on top of the Base44 helpers."""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from .base44 import callFunction, getBaseUrl, getPendingFunctionName

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
    functionName: Optional[str] = None,
    apiKey: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve pending files for the specified recipient."""

    normalizedRecipient = (recipientId or "").strip()
    if not normalizedRecipient:
        LOG.error("[pending] recipientId is required")
        return []

    resolvedFunction = functionName or getPendingFunctionName()
    responseData = callFunction(
        resolvedFunction,
        {"recipientId": normalizedRecipient},
        apiKey=apiKey,
    )

    files: List[Dict[str, Any]] = []
    if isinstance(responseData, dict) and isinstance(responseData.get("files"), list):
        files = [item for item in responseData["files"] if isinstance(item, dict)]
    elif isinstance(responseData, list):
        files = [item for item in responseData if isinstance(item, dict)]
    elif responseData is None:
        files = []
    else:
        LOG.error("[pending] Unexpected payload from %s: %s", resolvedFunction, responseData)
        files = []

    LOG.info(
        "[pending] url=%s/%s recipientId=%s code=%s json=%s",
        getBaseUrl(),
        resolvedFunction,
        normalizedRecipient,
        "200" if responseData is not None else "204",
        f"{len(files)} items" if files else "empty",
    )
    return files


# snake_case compatibility exports
request_pending_poll_trigger = requestPendingPollTrigger
consume_pending_poll_trigger = consumePendingPollTrigger
list_pending = listPending
