"""Utilities for reporting printer status updates to Base44."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional

import requests

log = logging.getLogger(__name__)

BASE44_STATUS_URL = (
    "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions/updatePrinterStatus"
)

STATUS_INTERVAL_SECONDS = 7


def getDefaultPrinterApiToken() -> str:
    """Resolve the API token from the PRINTER_API_TOKEN environment variable."""

    return os.getenv("PRINTER_API_TOKEN", "").strip()


class Base44Reporter:
    """Background worker that periodically pushes printer status updates to Base44."""

    def __init__(
        self,
        *,
        getActiveRecipientId: Callable[[], str],
        listConnectedPrinters: Callable[[], Iterable[Any]],
        snapshotFunc: Callable[[Any], Dict[str, Any]],
        getApiKey: Optional[Callable[[], str]] = None,
    ) -> None:
        self._getActiveRecipientId = getActiveRecipientId
        self._listConnectedPrinters = listConnectedPrinters
        self._snapshotFunc = snapshotFunc
        self._getApiKey = getApiKey or getDefaultPrinterApiToken
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background reporting thread if it is not already running."""

        apiKey = self._resolveApiKey()
        if not apiKey:
            log.error("PRINTER_API_TOKEN is missing. Unable to report status to Base44.")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._runLoop, name="Base44StatusLoop", daemon=True)
        self._thread.start()
        log.info("Base44 status reporting started.")

    def stop(self) -> None:
        """Stop the background reporting thread."""

        thread = self._thread
        self._stopEvent.set()
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        if thread:
            log.info("Base44 status reporting stopped.")

    def _resolveApiKey(self) -> str:
        try:
            return (self._getApiKey() or "").strip()
        except Exception:  # noqa: BLE001 - guard against callable failures
            log.exception("Failed to resolve API key for Base44 reporting.")
            return ""

    def _runLoop(self) -> None:
        lastSentTimes: Dict[str, float] = {}
        session = requests.Session()

        while not self._stopEvent.is_set():
            try:
                apiKey = self._resolveApiKey()
                if not apiKey:
                    time.sleep(0.5)
                    continue

                recipientId = (self._getActiveRecipientId() or "").strip()
                if not recipientId:
                    time.sleep(0.5)
                    continue

                try:
                    printers = list(self._listConnectedPrinters())
                except Exception:  # noqa: BLE001 - defensive against provider errors
                    log.exception("Unable to list printers for Base44 reporting.")
                    printers = []

                headers = {
                    "Content-Type": "application/json",
                    "X-API-Key": apiKey,
                }

                for printer in printers:
                    if self._stopEvent.is_set():
                        break
                    snapshot = self._buildSnapshot(printer)
                    if not snapshot:
                        continue

                    serialNumber = snapshot.get("serialNumber")
                    ipAddress = snapshot.get("printerIpAddress")
                    identifier = serialNumber or ipAddress
                    if not identifier:
                        continue

                    now = time.monotonic()
                    lastSent = lastSentTimes.get(identifier, 0.0)
                    if now - lastSent < STATUS_INTERVAL_SECONDS:
                        continue

                    payload = self._preparePayload(snapshot, recipientId)
                    log.info(
                        "[POST] %s recipientId=%s ip=%s serial=%s status=%s bed=%s nozzle=%s",
                        BASE44_STATUS_URL,
                        payload.get("recipientId"),
                        payload.get("printerIpAddress"),
                        payload.get("serialNumber"),
                        payload.get("status"),
                        payload.get("bedTemp"),
                        payload.get("nozzleTemp"),
                    )

                    response = session.post(
                        BASE44_STATUS_URL,
                        headers=headers,
                        data=json.dumps(payload),
                        timeout=8,
                    )

                    bodyPreview = (response.text or "")[:400]
                    log.info("[POST][resp] code=%s body=%s", response.status_code, bodyPreview)

                    if response.status_code in {401, 403}:
                        log.error("Base44 rejected the API key (status %s).", response.status_code)
                    elif response.status_code == 404:
                        log.error("Received 404 from Base44 status endpoint. Verify the URL path.")
                    elif response.status_code == 405:
                        log.error(
                            "405 Method Not Allowed from Base44. Ensure updatePrinterStatus endpoint and POST method are used.",
                        )

                    lastSentTimes[identifier] = now

            except Exception as error:  # noqa: BLE001 - ensure loop resilience
                log.exception("Error in Base44 status reporting loop: %s", error)

            self._stopEvent.wait(0.5)

    def _buildSnapshot(self, printer: Any) -> Dict[str, Any]:
        try:
            snapshot = self._snapshotFunc(printer)
        except Exception:  # noqa: BLE001 - guard against snapshot provider errors
            log.exception("Unable to build Base44 snapshot for printer %r", printer)
            return {}
        if not isinstance(snapshot, dict):
            return {}
        serialCandidate = snapshot.get("serial") or snapshot.get("serialNumber")
        if serialCandidate:
            snapshot["serialNumber"] = str(serialCandidate).strip()
        ipCandidate = snapshot.get("ip") or snapshot.get("ipAddress") or snapshot.get("printerIpAddress")
        if ipCandidate:
            snapshot["printerIpAddress"] = str(ipCandidate).strip()
        if "status" in snapshot and isinstance(snapshot["status"], str):
            snapshot["status"] = snapshot["status"].strip()
        return snapshot

    def _preparePayload(self, snapshot: Dict[str, Any], recipientId: str) -> Dict[str, Any]:
        progressValue = snapshot.get("progress")
        if isinstance(progressValue, (int, float)):
            jobProgress = int(round(progressValue))
        else:
            jobProgress = 0

        timeRemainingValue = snapshot.get("timeRemainingSec") or snapshot.get("timeRemaining")
        if isinstance(timeRemainingValue, (int, float)):
            timeRemaining = max(0, int(round(timeRemainingValue)))
        else:
            timeRemaining = 0

        onlineValue = snapshot.get("online")
        if isinstance(onlineValue, bool):
            isOnline = onlineValue
        elif isinstance(onlineValue, str):
            isOnline = onlineValue.strip().lower() in {"true", "1", "online", "yes"}
        else:
            statusValue = str(snapshot.get("status") or "").strip().lower()
            isOnline = statusValue not in {"", "offline", "unknown"}

        payload: Dict[str, Any] = {
            "recipientId": recipientId,
            "printerIpAddress": snapshot.get("printerIpAddress"),
            "serialNumber": snapshot.get("serialNumber"),
            "printerId": snapshot.get("base44PrinterId") or snapshot.get("printerId"),
            "status": snapshot.get("status", "offline") or "offline",
            "online": isOnline,
            "jobProgress": jobProgress,
            "currentJobId": snapshot.get("currentJobId"),
            "bedTemp": snapshot.get("bedTemp"),
            "nozzleTemp": snapshot.get("nozzleTemp"),
            "fanSpeed": snapshot.get("fanSpeed"),
            "printSpeed": snapshot.get("printSpeed"),
            "filamentUsed": snapshot.get("filamentUsed"),
            "timeRemaining": timeRemaining,
            "errorMessage": snapshot.get("error") or snapshot.get("errorMessage"),
            "lastUpdateTimestamp": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "firmwareVersion": snapshot.get("firmware") or snapshot.get("firmwareVersion"),
        }

        onlineField = snapshot.get("online")
        if isinstance(onlineField, bool):
            payload["online"] = onlineField

        temperatureFields = {"bedTemp", "nozzleTemp"}
        for field in temperatureFields:
            value = payload.get(field)
            if isinstance(value, str):
                stripped = value.strip()
                try:
                    payload[field] = float(stripped)
                except ValueError:
                    payload[field] = stripped

        return payload

