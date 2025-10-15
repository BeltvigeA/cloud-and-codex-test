"""Background poller for Firestore printer commands."""

from __future__ import annotations

import threading
import time
from typing import Optional

from .commands import listPendingCommands
from .logbus import log


class CommandPoller:
    """Periodically fetch pending commands for the active recipient."""

    def __init__(self, intervalSec: float = 5.0) -> None:
        self._intervalSeconds = max(1.0, float(intervalSec))
        self._recipientId: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stopEvent = threading.Event()

    def start(self, recipientId: str) -> None:
        self._recipientId = (recipientId or "").strip()
        if self._thread and self._thread.is_alive():
            log("INFO", "control", "poller_already_running", recipientId=self._recipientId)
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._run, name="command-poller", daemon=True)
        self._thread.start()
        log(
            "INFO",
            "control",
            "poller_started",
            recipientId=self._recipientId,
            intervalSec=self._intervalSeconds,
        )

    def stop(self) -> None:
        self._stopEvent.set()
        threadHandle = self._thread
        if threadHandle and threadHandle.is_alive():
            threadHandle.join(timeout=1.0)
        self._thread = None
        log("INFO", "control", "poller_stopped")

    def setRecipientId(self, recipientId: str) -> None:
        self._recipientId = (recipientId or "").strip()
        log("INFO", "control", "poller_recipient_updated", recipientId=self._recipientId)

    def _run(self) -> None:
        while not self._stopEvent.is_set():
            currentRecipientId = self._recipientId
            if currentRecipientId:
                try:
                    listPendingCommands(currentRecipientId)
                except Exception as error:  # noqa: BLE001 - logging error but continue loop
                    log("ERROR", "control", "poll_exception", error=str(error))
            sleepSlices = int(self._intervalSeconds * 10)
            for _ in range(max(1, sleepSlices)):
                if self._stopEvent.is_set():
                    break
                time.sleep(0.1)
