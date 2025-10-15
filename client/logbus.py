"""Structured logging helpers shared across client modules."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

_LOG = logging.getLogger(__name__)


@dataclass
class LogEvent:
    ts: float
    level: str
    category: str
    event: str
    message: str
    ctx: Dict[str, Any]


class LogBus:
    """In-memory, console, and file-backed log dispatcher."""

    def __init__(self, maxMemory: int = 5000, folder: str | None = None) -> None:
        self._buffer: List[LogEvent] = []
        self._lock = threading.Lock()
        self._maxMemory = max(1, int(maxMemory))
        self._folder = folder or os.path.expanduser('~/.printmaster/logs')
        os.makedirs(self._folder, exist_ok=True)

    def emit(self, level: str, category: str, event: str, message: str = '', **context: Any) -> None:
        normalizedLevel = level.upper()
        eventRecord = LogEvent(time.time(), normalizedLevel, category, event, message, dict(context))
        consoleMessage = f"[{category}] {event}: {message} | {context}"
        logMethod = getattr(_LOG, normalizedLevel.lower(), _LOG.info)
        logMethod(consoleMessage)

        with self._lock:
            self._buffer.append(eventRecord)
            if len(self._buffer) > self._maxMemory:
                self._buffer = self._buffer[-self._maxMemory :]

        logPath = os.path.join(self._folder, time.strftime('%Y-%m-%d') + '.jsonl')
        try:
            with open(logPath, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(asdict(eventRecord), ensure_ascii=False) + '\n')
        except OSError:
            _LOG.debug('Failed to write structured log to %s', logPath, exc_info=True)

    def snapshot(self) -> List[LogEvent]:
        with self._lock:
            return list(self._buffer)


BUS = LogBus()


def log(level: str, category: str, event: str, message: str = '', **context: Any) -> None:
    BUS.emit(level, category, event, message, **context)


__all__ = ['BUS', 'LogBus', 'LogEvent', 'log']
