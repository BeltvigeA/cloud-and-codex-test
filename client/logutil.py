"""Logging helpers for rate-limiting repeated messages."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict

from .logbus import log

_LOG = logging.getLogger(__name__)
_LAST_EVENT_TIMES: Dict[str, float] = {}
_LOCK = threading.Lock()


def rateLimit(
    key: str,
    message: str,
    *,
    level: str = "error",
    minSeconds: float = 5.0,
    category: str | None = None,
    event: str | None = None,
) -> None:
    """Log *message* with rate-limiting enforced per *key*."""

    now = time.time()
    with _LOCK:
        lastTime = _LAST_EVENT_TIMES.get(key, 0.0)
        if now - lastTime < max(0.1, float(minSeconds)):
            return
        _LAST_EVENT_TIMES[key] = now

    logMethod = getattr(logging, level, None)
    if not callable(logMethod):
        logMethod = _LOG.error
    logMethod(message)

    if category:
        try:
            resolvedEvent = event or key
            log(level.upper(), category, resolvedEvent, message)
        except Exception:  # pragma: no cover - logging must not fail
            return


def rateLimitError(
    key: str,
    message: str,
    minSeconds: float = 5.0,
    *,
    category: str | None = None,
    event: str | None = None,
) -> None:
    """Backward-compatible wrapper that logs at error level."""

    rateLimit(
        key,
        message,
        level="error",
        minSeconds=minSeconds,
        category=category,
        event=event,
    )


# snake_case compatibility aliases for legacy imports
rate_limit = rateLimit
rate_limit_error = rateLimitError
