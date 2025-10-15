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


class LogBusHandler(logging.Handler):
    """Bridge standard logging records into the structured log bus."""

    def __init__(self) -> None:
        super().__init__()
        self.setLevel(logging.NOTSET)
        self._exceptionFormatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(__name__):
            return

        try:
            category = self._resolveCategory(record)
            eventName = self._resolveEventName(record)
            message = record.getMessage()
            context = self._buildContext(record)
            BUS.emit(record.levelname, category, eventName, message, **context)
        except Exception:  # noqa: BLE001 - logging handlers must not raise
            self.handleError(record)

    def _resolveCategory(self, record: logging.LogRecord) -> str:
        parts = [part for part in record.name.split('.') if part]
        for part in reversed(parts):
            lowered = part.lower()
            if lowered in {'commands', 'controls'}:
                return 'control'
            if lowered == 'base44status':
                return 'status-base44'
            if lowered in {'bambuclient', 'bambuprinter', 'status'}:
                return 'status-printer'
        return 'listener'

    def _resolveEventName(self, record: logging.LogRecord) -> str:
        if record.funcName:
            return record.funcName
        if record.name:
            return record.name.split('.')[-1]
        return 'log'

    def _buildContext(self, record: logging.LogRecord) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            'logger': record.name,
            'module': record.module,
            'line': record.lineno,
        }
        if record.threadName:
            context['thread'] = record.threadName
        if record.processName:
            context['process'] = record.processName
        if record.exc_info:
            context['exception'] = self._exceptionFormatter.formatException(record.exc_info)
        if record.stack_info:
            context['stack'] = record.stack_info
        for key in ('extra', 'data'):
            if hasattr(record, key):
                value = getattr(record, key)
                if isinstance(value, dict):
                    for extraKey, extraValue in value.items():
                        context.setdefault(extraKey, extraValue)
        return context


def installLogBusHandler() -> None:
    rootLogger = logging.getLogger()
    for handler in rootLogger.handlers:
        if isinstance(handler, LogBusHandler):
            return
    rootLogger.addHandler(LogBusHandler())


__all__ = ['BUS', 'LogBus', 'LogEvent', 'LogBusHandler', 'installLogBusHandler', 'log']
