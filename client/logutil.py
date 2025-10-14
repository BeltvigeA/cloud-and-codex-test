"""Logging helpers for rate-limiting repeated error messages."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict

_LOG = logging.getLogger(__name__)
_LAST_EVENT_TIMES: Dict[str, float] = {}
_LOCK = threading.Lock()


def rateLimitError(key: str, message: str, minSeconds: float = 5.0) -> None:
    """Log an error message at most once per *minSeconds* for the given key."""

    now = time.time()
    with _LOCK:
        lastTime = _LAST_EVENT_TIMES.get(key, 0.0)
        if now - lastTime < max(0.1, float(minSeconds)):
            return
        _LAST_EVENT_TIMES[key] = now
    _LOG.error(message)


# snake_case compatibility alias
rate_limit_error = rateLimitError
