from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, List, Optional

log = logging.getLogger(__name__)


def listPendingCommands(recipientId: str) -> List[Any]:
    """Fetch pending commands for the given recipient.

    The default implementation acts as a stub that returns no commands.
    Real integrations are expected to monkeypatch or override this function.
    """

    log.debug("listPendingCommands invoked for recipient %s", recipientId)
    return []


class CommandPoller:
    """Periodic poller that queries pending commands for a recipient."""

    def __init__(
        self,
        *,
        intervalSec: float = 1.0,
        sleepCallable: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._intervalSeconds = max(0.1, float(intervalSec))
        self._sleepCallable = sleepCallable or time.sleep
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._recipientId: Optional[str] = None

    def start(self, recipientId: str) -> None:
        sanitized = recipientId.strip()
        if not sanitized:
            raise ValueError("recipientId must not be empty")
        self._recipientId = sanitized
        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"CommandPoller-{sanitized}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._intervalSeconds)
        self._thread = None

    def setRecipientId(self, recipientId: str) -> None:
        self._recipientId = recipientId.strip() or None

    def _run(self) -> None:
        if not self._recipientId:
            log.debug("CommandPoller run aborted – recipientId missing")
            return
        intervalSlices = max(1, int(self._intervalSeconds * 10))
        while not self._stopEvent.is_set():
            listPendingCommands(self._recipientId)
            if self._stopEvent.is_set():
                break
            for _ in range(intervalSlices):
                if self._stopEvent.is_set():
                    break
                self._sleepCallable(0.1)
            if self._stopEvent.is_set():
                break

