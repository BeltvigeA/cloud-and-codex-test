"""Background reporter that sends printer snapshots to Base44."""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from typing import Any, Callable, Iterable, Optional

import requests

LOG = logging.getLogger(__name__)

BASE44_FUNCTION_BASE = (
    "https://print-flow-pro-eb683cc6.base44.app/api/apps/68b61486e7c52405eb683cc6/functions"
)

STATUS_UPDATES_URL = f"{BASE44_FUNCTION_BASE}/updatePrinterStatus"

BASE44_STATUS_URL = STATUS_UPDATES_URL

HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": (os.getenv("PRINTER_API_TOKEN") or "").strip(),
}

LIST_PENDING_FILES_URL = f"{BASE44_FUNCTION_BASE}/listPendingFiles"
DEFAULT_TIMEOUT_SECONDS = 10


def loadApiKey() -> str:
    """Resolve the Base44 API key from the current environment."""

    return (os.getenv("PRINTER_API_TOKEN") or "").strip()


def listPendingFiles(
    recipientId: str,
    *,
    apiKey: Optional[str] = None,
    timeoutSeconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Retrieve pending print jobs for the provided recipient in a resilient way."""

    normalizedRecipient = (recipientId or "").strip()
    if not normalizedRecipient:
        LOG.error("[pending] recipientId mangler – returnerer tom liste")
        return []

    resolvedApiKey = (apiKey or "").strip() or loadApiKey()
    headers = {"Content-Type": "application/json"}
    if resolvedApiKey:
        headers["X-API-Key"] = resolvedApiKey

    body = {"recipientId": normalizedRecipient}

    try:
        response = requests.post(
            LIST_PENDING_FILES_URL,
            headers=headers,
            json=body,
            timeout=max(1.0, float(timeoutSeconds)),
        )
    except Exception as error:  # noqa: BLE001 - ensure the caller never crashes
        LOG.error("[pending] Feil ved henting: %s", error)
        return []

    contentType = response.headers.get("content-type", "")
    textBody = response.text or ""

    trimmedBody = textBody.strip()
    responseCount = "empty"
    payload: list[dict[str, Any]] = []

    if response.status_code == 204 or not trimmedBody:
        LOG.info("[pending] url=%s recipientId=%s code=%s json=empty", LIST_PENDING_FILES_URL, normalizedRecipient, response.status_code)
        return []

    if "application/json" not in contentType.lower():
        LOG.error(
            "[pending] Ikke-JSON svar (%s) for recipient %s: %s",
            response.status_code,
            normalizedRecipient,
            textBody[:200],
        )
        return []

    try:
        parsed = response.json()
    except Exception as error:  # noqa: BLE001 - treat malformed JSON gracefully
        LOG.error("[pending] Feil ved parsing av JSON for %s: %s", normalizedRecipient, error)
        return []

    if isinstance(parsed, list):
        payload = [item for item in parsed if isinstance(item, dict)]
        responseCount = f"{len(payload)} items"
    else:
        LOG.error("[pending] Uventet JSON-format: %s", parsed)
        return []

    LOG.info(
        "[pending] url=%s recipientId=%s code=%s json=%s",
        LIST_PENDING_FILES_URL,
        normalizedRecipient,
        response.status_code,
        responseCount,
    )
    return payload


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


def _sanitizeStatus(snapshot: dict[str, Any]) -> tuple[str, bool]:
    onlineFlag = bool(snapshot.get("online"))
    statusValue = str(snapshot.get("status") or "").strip()
    if not statusValue:
        statusValue = "idle" if onlineFlag else "offline"
    return statusValue, onlineFlag


def _resolveHeaders(apiKeyOverride: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (apiKeyOverride or "").strip() or HEADERS.get("X-API-Key", "")
    if not token:
        token = (os.getenv("PRINTER_API_TOKEN") or "").strip()
    if token:
        headers["X-API-Key"] = token
    return headers


def _isZeroLike(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


class Base44StatusReporter:
    """Periodically pushes printer snapshots to Base44 while running."""

    def __init__(self, getPrintersSnapshotCallable: Callable[[], Iterable[dict[str, Any]]], intervalSec: int = 5) -> None:
        self._getPrintersSnapshotCallable = getPrintersSnapshotCallable
        self._intervalSec = max(1, int(intervalSec))
        self._thread: threading.Thread | None = None
        self._stopEvent = threading.Event()
        self._recipientId = ""
        self._apiKeyOverride = ""
        self._lastSnapshotByPrinter: dict[tuple[str, str], dict[str, Any]] = {}
        self._mqttOfflineSince: dict[tuple[str, str], float] = {}

    def start(self, recipientId: str, apiKey: str | None = None) -> None:
        self._recipientId = recipientId.strip()
        self._apiKeyOverride = (apiKey or "").strip()
        self._stopEvent.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._runLoop, name="base44-status", daemon=True)
        self._thread.start()
        LOG.info(
            "Base44StatusReporter started (recipientId=%s, every=%ss)",
            self._recipientId,
            self._intervalSec,
        )

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        LOG.info("Base44StatusReporter stopped")

    def _buildPayload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        statusValue, onlineFlag = _sanitizeStatus(snapshot)
        serialKey = str(snapshot.get("serial") or "").strip().lower()
        ipKey = str(snapshot.get("ip") or "").strip()
        key = (serialKey, ipKey)

        previous = self._lastSnapshotByPrinter.get(key, {})
        lastTemps = {
            "bedTemp": previous.get("bedTemp"),
            "nozzleTemp": previous.get("nozzleTemp"),
            "timestamp": previous.get("timestamp", 0.0),
        }

        currentBed = snapshot.get("bed")
        currentNozzle = snapshot.get("nozzle")

        if onlineFlag and statusValue.lower() not in {"offline", "unknown"}:
            if _isZeroLike(currentBed) and lastTemps["bedTemp"] not in (None, ""):
                currentBed = lastTemps["bedTemp"]
            if _isZeroLike(currentNozzle) and lastTemps["nozzleTemp"] not in (None, ""):
                currentNozzle = lastTemps["nozzleTemp"]

        progressValue = _coerceInt(snapshot.get("progress"))

        mqttReady = snapshot.get("mqttReady")
        if mqttReady is False:
            offlineSince = self._mqttOfflineSince.get(key)
            if offlineSince is None:
                self._mqttOfflineSince[key] = time.time()
            elif time.time() - offlineSince > 30:
                onlineFlag = False
        else:
            self._mqttOfflineSince.pop(key, None)

        payload = {
            "recipientId": self._recipientId,
            "printerIpAddress": snapshot.get("ip"),
            "serialNumber": snapshot.get("serial"),
            "status": statusValue,
            "online": onlineFlag,
            "jobProgress": progressValue,
            "currentJobId": snapshot.get("currentJobId"),
            "bedTemp": currentBed,
            "nozzleTemp": currentNozzle,
            "fanSpeed": snapshot.get("fan"),
            "printSpeed": snapshot.get("speed"),
            "filamentUsed": snapshot.get("filamentUsed"),
            "timeRemaining": _coerceInt(snapshot.get("timeRemaining")),
            "errorMessage": snapshot.get("error"),
            "lastUpdateTimestamp": _isoUtcNow(),
            "firmwareVersion": snapshot.get("firmware"),
        }

        self._lastSnapshotByPrinter[key] = {
            "bedTemp": payload.get("bedTemp"),
            "nozzleTemp": payload.get("nozzleTemp"),
            "timestamp": time.time(),
        }

        return payload

    def _runLoop(self) -> None:
        while not self._stopEvent.is_set():
            try:
                if not self._recipientId:
                    LOG.debug("Skipping Base44 post: missing recipient")
                else:
                    snapshots = list(self._getPrintersSnapshotCallable() or [])
                    for snapshot in snapshots:
                        payload = self._buildPayload(snapshot)

                        LOG.info(
                            "[POST] %s recipientId=%s ip=%s status=%s bed=%s nozzle=%s",
                            STATUS_UPDATES_URL,
                            payload["recipientId"],
                            payload["printerIpAddress"],
                            payload["status"],
                            payload["bedTemp"],
                            payload["nozzleTemp"],
                        )

                        response = requests.post(
                            STATUS_UPDATES_URL,
                            headers=_resolveHeaders(self._apiKeyOverride),
                            json=payload,
                            timeout=10,
                        )

                        LOG.info("[POST][resp] code=%s body=%s", response.status_code, response.text[:500])
            except Exception as error:  # noqa: BLE001
                LOG.exception("Status push failed: %s", error)

            self._stopEvent.wait(self._intervalSec)


__all__ = [
    "BASE44_FUNCTION_BASE",
    "STATUS_UPDATES_URL",
    "BASE44_STATUS_URL",
    "LIST_PENDING_FILES_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "HEADERS",
    "Base44StatusReporter",
    "loadApiKey",
    "listPendingFiles",
]
