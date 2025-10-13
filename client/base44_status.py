# -*- coding: utf-8 -*-
"""Status reporting utilities for posting printer telemetry to Base44."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import requests

STATUS_UPDATES_URL = (
    "https://print-flow-pro-eb683cc6.base44.app/"
    "api/apps/68b61486e7c52405eb683cc6/functions/updatePrinterStatus"
)

logger = logging.getLogger(__name__)


def getDefaultPrinterApiToken() -> str:
    """Return the printer API token from the environment if available."""

    return os.getenv("PRINTER_API_TOKEN", "").strip()


class Base44StatusReporter:
    """Background worker that periodically posts printer status updates to Base44."""

    def __init__(
        self,
        *,
        getRecipientId: Callable[[], Optional[str]],
        getApiKey: Callable[[], Optional[str]],
        listConnectedPrinters: Callable[[], List[Any]],
        buildSnapshot: Callable[[Any], Dict[str, Any]],
        intervalSeconds: float = 5.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._getRecipientId = getRecipientId
        self._getApiKey = getApiKey
        self._listConnectedPrinters = listConnectedPrinters
        self._buildSnapshot = buildSnapshot
        self._intervalSeconds = max(1.0, float(intervalSeconds))
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._httpSession = session or requests.Session()

    def start(self) -> None:
        """Start the reporting thread if it is not already running."""

        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._runLoop, name="Base44StatusReporter", daemon=True)
        self._thread.start()
        logger.info("Base44StatusReporter started")

    def stop(self) -> None:
        """Stop the reporting thread and wait briefly for it to exit."""

        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        logger.info("Base44StatusReporter stopped")

    def _runLoop(self) -> None:
        backoffSeconds = 1.0
        while not self._stopEvent.is_set():
            try:
                recipientId = (self._getRecipientId() or "").strip()
                apiKey = (self._getApiKey() or "").strip()
                if not recipientId:
                    logger.debug("Status not sent: missing recipientId")
                elif not apiKey:
                    logger.debug("Status not sent: missing API key")
                else:
                    printers = []
                    try:
                        printers = list(self._listConnectedPrinters() or [])
                    except Exception as error:  # noqa: BLE001
                        logger.exception("Unable to list printers for Base44 status reporting: %s", error)
                    if printers:
                        for printer in printers:
                            if self._stopEvent.is_set():
                                break
                            try:
                                snapshot = self._buildSnapshot(printer) or {}
                            except Exception as error:  # noqa: BLE001
                                logger.exception("Failed to build snapshot for Base44 status: %s", error)
                                continue
                            if not snapshot:
                                continue
                            payload = self._buildPayload(recipientId, snapshot)
                            try:
                                self._postPayload(payload, apiKey)
                            except Exception as error:  # noqa: BLE001
                                logger.exception("Failed to post Base44 status update: %s", error)
                    else:
                        logger.debug("Status not sent: no connected printers")
                backoffSeconds = 1.0
            except Exception as error:  # noqa: BLE001
                logger.exception("Unexpected error in Base44 status loop: %s", error)
                backoffSeconds = min(backoffSeconds * 2.0, 30.0)
            finally:
                sleepSeconds = backoffSeconds if backoffSeconds > 1.0 else self._intervalSeconds
                self._sleepWithStopCheck(sleepSeconds)

    def _sleepWithStopCheck(self, sleepSeconds: float) -> None:
        endTime = time.monotonic() + max(0.2, sleepSeconds)
        while time.monotonic() < endTime:
            if self._stopEvent.is_set():
                return
            time.sleep(0.2)

    def _buildPayload(self, recipientId: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        statusValue = self._normalizeStatus(str(snapshot.get("status") or ""))
        payload: Dict[str, Any] = {
            "recipientId": recipientId,
            "printerIpAddress": snapshot.get("ip"),
            "serialNumber": snapshot.get("serial"),
            "printerId": snapshot.get("base44PrinterId"),
            "status": statusValue,
            "online": bool(snapshot.get("online", False)),
            "jobProgress": self._coerceInt(snapshot.get("progress"), defaultValue=0),
            "currentJobId": snapshot.get("currentJobId"),
            "bedTemp": snapshot.get("bedTemp"),
            "nozzleTemp": snapshot.get("nozzleTemp"),
            "fanSpeed": snapshot.get("fanSpeed"),
            "printSpeed": snapshot.get("printSpeed"),
            "filamentUsed": snapshot.get("filamentUsed"),
            "timeRemaining": self._coerceInt(snapshot.get("timeRemainingSec"), defaultValue=0),
            "errorMessage": snapshot.get("error"),
            "lastUpdateTimestamp": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "firmwareVersion": snapshot.get("firmware"),
        }
        return payload

    def _postPayload(self, payload: Dict[str, Any], apiKey: str) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": apiKey,
        }
        logger.info(
            "[POST] %s recipientId=%s ip=%s status=%s bed=%s nozzle=%s",
            STATUS_UPDATES_URL,
            payload.get("recipientId"),
            payload.get("printerIpAddress"),
            payload.get("status"),
            payload.get("bedTemp"),
            payload.get("nozzleTemp"),
        )
        response = self._httpSession.post(
            STATUS_UPDATES_URL,
            headers=headers,
            data=json.dumps(payload),
            timeout=10,
        )
        bodyPreview = (response.text or "")[:2000]
        logger.info("[POST][resp] code=%s body=%s", response.status_code, bodyPreview)
        response.raise_for_status()

    def _normalizeStatus(self, rawStatus: str) -> str:
        normalized = rawStatus.strip().lower()
        if not normalized:
            return "offline"
        mapping = {
            "ready": "idle",
            "idle": "idle",
            "online": "idle",
            "standby": "idle",
            "printing": "printing",
            "running": "printing",
            "busy": "printing",
            "paused": "paused",
            "pausing": "paused",
            "resuming": "printing",
            "error": "error",
            "alarm": "error",
            "fault": "error",
            "offline": "offline",
            "disconnected": "offline",
            "unknown": "offline",
        }
        return mapping.get(normalized, "offline")

    def _coerceInt(self, value: Any, *, defaultValue: int = 0) -> int:
        if isinstance(value, bool):
            return defaultValue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                try:
                    return int(float(stripped))
                except ValueError:
                    return defaultValue
        return defaultValue


__all__ = ["Base44StatusReporter", "getDefaultPrinterApiToken", "STATUS_UPDATES_URL"]
