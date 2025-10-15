
"""Background poller that fetches pending control commands from Firestore and logs them.

It uses client.commands.listPendingCommands which already performs structured logging
to the 'control' category (poll_start/poll_ok/poll_failed/incoming/...).
This poller ensures those logs show up in the GUI even before any printer is connected.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from .commands import listPendingCommands
from .logbus import log


class CommandPoller:
    def __init__(self, intervalSec: float = 5.0) -> None:
        self._intervalSec = max(1.0, float(intervalSec))
        self._recipientId: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stopEvent = threading.Event()

    def start(self, recipientId: str) -> None:
        self._recipientId = (recipientId or "").strip()
        self._stopEvent.clear()
        if self._thread and self._thread.is_alive():
            # Already running; just update recipient
            return
        self._thread = threading.Thread(target=self._run, name="command-poller", daemon=True)
        self._thread.start()
        log("INFO", "control", "poller_started", recipientId=self._recipientId, intervalSec=self._intervalSec)

    def stop(self) -> None:
        self._stopEvent.set()
        log("INFO", "control", "poller_stopping")
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        log("INFO", "control", "poller_stopped")

    def setRecipientId(self, recipientId: str) -> None:
        self._recipientId = (recipientId or "").strip()
        log("INFO", "control", "poller_recipient_updated", recipientId=self._recipientId)

    # --- internals ---
    def _run(self) -> None:
        while not self._stopEvent.is_set():
            rid = self._recipientId
            if rid:
                try:
                    # The function itself logs poll_start/poll_ok/poll_failed and incoming items
                    listPendingCommands(rid)
                except Exception as e:  # noqa: BLE001
                    log("ERROR", "control", "poll_exception", error=str(e))
            # Sleep in short steps so stop() is responsive
            for _ in range(int(self._intervalSec * 10)):
                if self._stopEvent.is_set():
                    break
                time.sleep(0.1)
