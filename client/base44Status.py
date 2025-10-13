"""Background reporter that sends printer snapshots to Base44."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from typing import Any, Callable, Iterable

import requests

LOG = logging.getLogger(__name__)

BASE44_STATUS_URL = (
    "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions/updatePrinterStatus"
)


def _buildHeaders(apiKey: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
    }


def _isoUtcNow() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _coerceInt(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return int(float(stripped))
    except (TypeError, ValueError):
        return 0
    return 0


class Base44StatusReporter:
    """Periodically pushes printer snapshots to Base44 while running."""

    def __init__(self, getPrintersSnapshotCallable: Callable[[], Iterable[dict[str, Any]]], intervalSec: int = 5) -> None:
        self._getPrintersSnapshotCallable = getPrintersSnapshotCallable
        self._intervalSec = max(1, int(intervalSec))
        self._thread: threading.Thread | None = None
        self._stopEvent = threading.Event()
        self._recipientId = ""
        self._apiKey = ""

    def start(self, recipientId: str, apiKey: str) -> None:
        self._recipientId = recipientId.strip()
        self._apiKey = apiKey.strip()
        self._stopEvent.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._runLoop, name="base44-status", daemon=True)
        self._thread.start()
        LOG.info("Base44StatusReporter started (recipientId=%s, every=%ss)", self._recipientId, self._intervalSec)

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        LOG.info("Base44StatusReporter stopped")

    def _runLoop(self) -> None:
        while not self._stopEvent.is_set():
            try:
                if not self._recipientId or not self._apiKey:
                    LOG.debug("Skipping Base44 post: missing recipient or API key")
                else:
                    snapshots = list(self._getPrintersSnapshotCallable() or [])
                    for snapshot in snapshots:
                        payload = {
                            "recipientId": self._recipientId,
                            "printerIpAddress": snapshot.get("ip"),
                            "serialNumber": snapshot.get("serial"),
                            "status": snapshot.get("status") or "offline",
                            "online": bool(snapshot.get("online")),
                            "jobProgress": _coerceInt(snapshot.get("progress")),
                            "currentJobId": snapshot.get("currentJobId"),
                            "bedTemp": snapshot.get("bed"),
                            "nozzleTemp": snapshot.get("nozzle"),
                            "fanSpeed": snapshot.get("fan"),
                            "printSpeed": snapshot.get("speed"),
                            "filamentUsed": snapshot.get("filamentUsed"),
                            "timeRemaining": _coerceInt(snapshot.get("timeRemaining")),
                            "errorMessage": snapshot.get("error"),
                            "lastUpdateTimestamp": _isoUtcNow(),
                            "firmwareVersion": snapshot.get("firmware"),
                        }

                        LOG.info(
                            "[POST] %s recipientId=%s ip=%s status=%s bed=%s nozzle=%s",
                            BASE44_STATUS_URL,
                            payload["recipientId"],
                            payload["printerIpAddress"],
                            payload["status"],
                            payload["bedTemp"],
                            payload["nozzleTemp"],
                        )

                        response = requests.post(
                            BASE44_STATUS_URL,
                            headers=_buildHeaders(self._apiKey),
                            data=json.dumps(payload),
                            timeout=10,
                        )

                        LOG.info("[POST][resp] code=%s body=%s", response.status_code, response.text[:500])
            except Exception as error:  # noqa: BLE001
                LOG.exception("Status push failed: %s", error)

            self._stopEvent.wait(self._intervalSec)


__all__ = ["Base44StatusReporter", "BASE44_STATUS_URL"]
