from __future__ import annotations

import base64
import io
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import requests

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import bambuPrinter
from .bambuPrinter import BambuPrintOptions, sendBambuPrintJob, bambulabsApi
from . import bambuPrinter
# TODO: Bed reference capture import fjernet - må implementeres på nytt
# from .autoprint.bedref_capture import run_bed_reference_capture
from .autoprint.brake_flow import BrakeFlow, BrakeFlowContext, buildBrakeFlowErrorPayload
from .autoprint.plate_reference import (
    captureReferenceSequence, captureZAxisReferenceSequence
)
from .base44_client import (
    acknowledgeCommand,
    listPendingCommandsForRecipient,
    postCommandResult,
    postReportError,
    postReportPrinterImage,
)
from .client import buildBaseUrl, defaultBaseUrl, getPrinterControlEndpointUrl

log = logging.getLogger(__name__)

bambuApi = bambulabsApi


def _resolveControlPollSeconds() -> float:
    try:
        value = float(os.getenv("CONTROL_POLL_SEC", "15"))
    except ValueError:
        value = 15.0
    return max(3.0, value)


CONTROL_POLL_SECONDS = _resolveControlPollSeconds()
CONNECT_TIMEOUT_SECONDS = 30.0

CACHE_DIRECTORY = Path(os.path.expanduser("~/.printmaster"))
CACHE_FILE_PATH = CACHE_DIRECTORY / "command-cache.json"

_cacheData: Optional[Dict[str, Any]] = None
_cacheLock = threading.Lock()


cameraDebugEnabled = (
    str(os.getenv("PRINTMASTER_CAMERA_DEBUG", ""))
    .strip()
    .lower()
    not in ("", "0", "false", "off")
)


def _resolveCameraSnapshotIntervalSeconds() -> float:
    try:
        value = float(os.getenv("CAMERA_SNAPSHOT_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(1.0, value)


cameraSnapshotIntervalSeconds = _resolveCameraSnapshotIntervalSeconds()


def captureCameraSnapshot(printer: Any, serial: str) -> Path:
    cameraBaseDirectory = Path.home() / ".printmaster" / "camera"
    currentTimestamp = datetime.now(timezone.utc)
    dateDirectory = cameraBaseDirectory / currentTimestamp.strftime("%Y-%m-%d")
    serialDirectory = dateDirectory / serial
    timeString = currentTimestamp.strftime("%H-%M-%S-%fZ")
    filePath = serialDirectory / f"{serial}-{timeString}.jpg"
    serialDirectory.mkdir(parents=True, exist_ok=True)

    startTime = time.perf_counter()
    log.info("[camera] starting capture for %s → %s", serial, filePath)

    mqttStarter = getattr(printer, "mqtt_start", None)
    if callable(mqttStarter):
        try:
            mqttStarter()
            if cameraDebugEnabled:
                log.info(
                    "[camera] mqtt_start() ok in %.3fs", time.perf_counter() - startTime
                )
        except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
            log.warning(
                "[camera] mqtt_start() failed: %s", error, exc_info=cameraDebugEnabled
            )

    connectMethod = getattr(printer, "connect", None)
    if callable(connectMethod):
        try:
            connectMethod()
            if cameraDebugEnabled:
                log.info(
                    "[camera] connect() ok at %.3fs", time.perf_counter() - startTime
                )
        except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
            log.warning(
                "[camera] connect() failed: %s", error, exc_info=cameraDebugEnabled
            )

    getStateMethod = getattr(printer, "get_state", None)
    if callable(getStateMethod):
        readinessDeadline = time.monotonic() + 8.0
        lastReadinessError: Optional[Exception] = None
        while time.monotonic() < readinessDeadline:
            try:
                getStateMethod()
                if cameraDebugEnabled:
                    log.info("[camera] get_state() ok; readiness reached")
                break
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                lastReadinessError = error
                time.sleep(0.25)
        else:
            log.warning(
                "[camera] readiness not confirmed before deadline: %s",
                lastReadinessError,
            )

    startedHereFlag = False
    cameraAliveMethod = getattr(printer, "camera_client_alive", None)
    cameraStartMethod = getattr(printer, "camera_start", None)
    cameraStopMethod = getattr(printer, "camera_stop", None) or getattr(
        printer, "camera_off", None
    )
    cameraRetryCount = 3
    retryDelaySeconds = 0.75
    cameraReadinessDeadline = time.monotonic() + 6.0
    cameraIsAlive = False
    if callable(cameraAliveMethod):
        try:
            cameraIsAlive = bool(cameraAliveMethod())
            if cameraDebugEnabled:
                log.info("[camera] camera_client_alive(): %s", cameraIsAlive)
        except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
            cameraIsAlive = False
            if cameraDebugEnabled:
                log.info("[camera] camera_client_alive() raised: %s", error)
    if not cameraIsAlive and callable(cameraStartMethod):
        try:
            cameraStartMethod()
            startedHereFlag = True
            if cameraDebugEnabled:
                log.info("[camera] camera_start() called")
        except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
            log.warning(
                "[camera] camera_start() failed: %s",
                error,
                exc_info=cameraDebugEnabled,
            )
    if callable(cameraAliveMethod):
        while time.monotonic() < cameraReadinessDeadline:
            try:
                if cameraAliveMethod():
                    cameraIsAlive = True
                    if cameraDebugEnabled:
                        log.info("[camera] camera_client_alive() confirmed ready")
                    break
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                cameraIsAlive = False
                if cameraDebugEnabled:
                    log.info("[camera] camera_client_alive() retry failed: %s", error)
            time.sleep(0.25)
        else:
            if cameraDebugEnabled:
                log.info("[camera] camera readiness deadline reached without confirmation")

    errorMessages: List[str] = []

    def saveBytes(buffer: bytes) -> None:
        with open(filePath, "wb") as handle:
            handle.write(buffer)
        if cameraDebugEnabled:
            log.info("[camera] saved %d bytes to %s", len(buffer), filePath)

    cameraImageMethod = getattr(printer, "get_camera_image", None)
    if callable(cameraImageMethod):
        lastImageError: Optional[Exception] = None
        for attemptIndex in range(cameraRetryCount):
            try:
                methodStart = time.perf_counter()
                pillowImage = cameraImageMethod()
                byteStream = io.BytesIO()
                pillowImage.save(byteStream, format="JPEG")
                saveBytes(byteStream.getvalue())
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_image() ok in %.3fs",
                        time.perf_counter() - methodStart,
                    )
                if startedHereFlag and callable(cameraStopMethod):
                    try:
                        cameraStopMethod()
                    except Exception:  # pragma: no cover - diagnostic logging only
                        log.debug(
                            "[camera] camera_stop/off failed (ignored)",
                            exc_info=cameraDebugEnabled,
                        )
                return filePath
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                lastImageError = error
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_image() attempt %d failed: %s",
                        attemptIndex + 1,
                        error,
                        exc_info=True,
                    )
                if attemptIndex + 1 < cameraRetryCount:
                    time.sleep(retryDelaySeconds)
        if lastImageError is not None:
            errorMessages.append(f"get_camera_image: {lastImageError}")

    cameraFrameMethod = getattr(printer, "get_camera_frame", None)
    if callable(cameraFrameMethod):
        lastFrameError: Optional[Exception] = None
        for attemptIndex in range(cameraRetryCount):
            try:
                methodStart = time.perf_counter()
                frameData = cameraFrameMethod()
                if isinstance(frameData, str):
                    rawBytes = base64.b64decode(frameData, validate=False)
                    saveBytes(rawBytes)
                else:
                    raise RuntimeError(
                        f"unexpected type from get_camera_frame: {type(frameData)}"
                    )
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_frame() ok in %.3fs",
                        time.perf_counter() - methodStart,
                    )
                if startedHereFlag and callable(cameraStopMethod):
                    try:
                        cameraStopMethod()
                    except Exception:  # pragma: no cover - diagnostic logging only
                        log.debug(
                            "[camera] camera_stop/off failed (ignored)",
                            exc_info=cameraDebugEnabled,
                        )
                return filePath
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                lastFrameError = error
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_frame() attempt %d failed: %s",
                        attemptIndex + 1,
                        error,
                        exc_info=True,
                    )
                if attemptIndex + 1 < cameraRetryCount:
                    time.sleep(retryDelaySeconds)
        if lastFrameError is not None:
            errorMessages.append(f"get_camera_frame: {lastFrameError}")

    cameraSnapshotMethod = getattr(printer, "get_camera_snapshot", None)
    if callable(cameraSnapshotMethod):
        lastSnapshotError: Optional[Exception] = None
        for attemptIndex in range(cameraRetryCount):
            try:
                methodStart = time.perf_counter()
                snapshotData = cameraSnapshotMethod()
                if isinstance(snapshotData, (bytes, bytearray)):
                    saveBytes(bytes(snapshotData))
                elif isinstance(snapshotData, str):
                    saveBytes(base64.b64decode(snapshotData, validate=False))
                else:
                    raise RuntimeError(
                        f"unexpected type from get_camera_snapshot: {type(snapshotData)}"
                    )
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_snapshot() ok in %.3fs",
                        time.perf_counter() - methodStart,
                    )
                if startedHereFlag and callable(cameraStopMethod):
                    try:
                        cameraStopMethod()
                    except Exception:  # pragma: no cover - diagnostic logging only
                        log.debug(
                            "[camera] camera_stop/off failed (ignored)",
                            exc_info=cameraDebugEnabled,
                        )
                return filePath
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                lastSnapshotError = error
                if cameraDebugEnabled:
                    log.info(
                        "[camera] get_camera_snapshot() attempt %d failed: %s",
                        attemptIndex + 1,
                        error,
                        exc_info=True,
                    )
                if attemptIndex + 1 < cameraRetryCount:
                    time.sleep(retryDelaySeconds)
        if lastSnapshotError is not None:
            errorMessages.append(f"get_camera_snapshot: {lastSnapshotError}")

    if startedHereFlag and callable(cameraStopMethod):
        try:
            cameraStopMethod()
        except Exception:  # pragma: no cover - diagnostic logging only
            log.debug(
                "[camera] camera_stop/off failed (ignored)",
                exc_info=cameraDebugEnabled,
            )

    attemptedMessage = " | ".join(errorMessages or ["no camera method available"])
    raise RuntimeError(f"Camera capture failed – tried: {attemptedMessage}")


# ---------- Bed reference helpers ----------
def _ensurePrinterConnected(
    printer: Any,
    *,
    serial: str = "",
    timeout: float = 15.0,
) -> None:
    """
    Best-effort: start MQTT, connect(), and wait until camera/mqtt is usable.
    Safe to call multiple times.
    """
    printer_id = serial or "unknown printer"

    connectMethod = getattr(printer, "connect", None)
    if callable(connectMethod):
        try:
            connectMethod()
        except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
            raise RuntimeError(f"Unable to connect to printer: {error}") from error

    mqttStarter = getattr(printer, "mqtt_start", None)
    mqttReadyMethod = getattr(printer, "mqtt_client_ready", None)
    if callable(mqttStarter):
        readinessDeadline = time.monotonic() + min(timeout, 10.0)
        lastError: Optional[Exception] = None
        readyConfirmed = False

        while True:
            try:
                mqttStarter()
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                raise RuntimeError(f"Unable to start printer MQTT: {error}") from error

            if not callable(mqttReadyMethod):
                readyConfirmed = True
                break

            try:
                if mqttReadyMethod():
                    readyConfirmed = True
                    break
            except Exception as error:  # noqa: BLE001 - third-party SDK raises generic Exception
                lastError = error

            if time.monotonic() >= readinessDeadline:
                break

            time.sleep(0.25)

        if callable(mqttReadyMethod) and not readyConfirmed:
            log.error("[motion] printer %s MQTT not ready after 10 seconds", printer_id)
            if lastError is not None:
                raise RuntimeError(
                    f"Printer MQTT connection not ready: {lastError}"
                ) from lastError
            raise RuntimeError("Printer MQTT connection not ready")

    try:
        from . import bambuPrinter as _bambuPrinter

        waitMethod = getattr(_bambuPrinter, "_waitForMqttReady", None)
        if callable(waitMethod):
            waitMethod(printer, timeout=timeout)
    except Exception:
        pass





# TODO: captureBedReference modul-funksjon må implementeres på nytt
#
# Forventet funksjonalitet:
# - Capture bed reference via Bambu-API bevegelse
# - Home printer alltid
# - Z-akse bevegelse i absolutte steg
# - Ta bilde ved hvert steg
#
# Parametere som må støttes:
# - printer: Any (printer instans fra bambulabs_api)
# - serialNumber: str (printer serial number)
# - frames: int = 30 (antall rammer/bilder)
# - zStepMm: float = 2.0 (Z-akse steg i millimeter)
# - feedrate: int = 1200 (bevegelseshastighet)
# - homeXy: bool = True (om XY skal homes)
# - use_job_fallback: bool = False (legacy parameter)
#
# Må implementere:
# 1. Sørg for at printer er tilkoblet (_ensurePrinterConnected)
# 2. Kalkuler total_mm basert på frames og zStepMm
# 3. Kjør Z-akse sekvensfangst via captureZAxisReferenceSequence
# 4. Logg resultat
# 5. Returner liste med Path-objekter til lagrede bilder
#
# Eksempel implementasjon (må kodes):
# def captureBedReference(
#     printer: Any,
#     serialNumber: str,
#     *,
#     frames: int = 30,
#     zStepMm: float = 2.0,
#     feedrate: int = 1200,
#     homeXy: bool = True,
#     use_job_fallback: bool = False,
# ) -> List[Path]:
#     from .plate_reference import captureZAxisReferenceSequence
#
#     _ensurePrinterConnected(printer)
#
#     total_mm = 200.0  # eller beregn basert på frames
#     log.info("[bedref] z-axis capture start for %s (step=%.1f, total=%.1f)",
#              serialNumber, float(zStepMm), total_mm)
#
#     # TODO: Implementer capture-logikk her
#     paths = []  # Plasseholder
#
#     log.info("[bedref] saved %d reference frame(s) for %s", len(paths), serialNumber)
#     return paths


def _determinePollMode() -> str:
    candidate = (os.getenv("CONTROL_POLL_MODE", "recipient") or "recipient").strip().lower()
    if candidate == "printer":
        global _printerModeWarningEmitted
        if not _printerModeWarningEmitted:
            log.warning(
                "CONTROL_POLL_MODE=printer is deprecated; falling back to legacy per-printer polling."
            )
            _printerModeWarningEmitted = True
        return "printer"
    return "recipient"


_recipientRouters: Dict[str, "RecipientCommandRouter"] = {}
_recipientRoutersLock = threading.Lock()
_printerModeWarningEmitted = False


def _registerRecipientRouter(recipientId: str, pollInterval: float) -> "RecipientCommandRouter":
    with _recipientRoutersLock:
        existing = _recipientRouters.get(recipientId)
        if existing is not None and existing.isActive:
            existing.updatePollInterval(pollInterval)
            return existing
        router = RecipientCommandRouter(recipientId, pollInterval)
        _recipientRouters[recipientId] = router
        return router


def _unregisterRecipientRouter(recipientId: str, router: "RecipientCommandRouter") -> None:
    with _recipientRoutersLock:
        if _recipientRouters.get(recipientId) is router:
            _recipientRouters.pop(recipientId, None)


def _normalizeCommandMetadata(command: Dict[str, Any]) -> Dict[str, Any]:
    metadata = command.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            if isinstance(parsed, dict):
                command["metadata"] = parsed
                return parsed
        except Exception:
            log.debug("Unable to parse command metadata for %s", command.get("commandId"), exc_info=True)
    command["metadata"] = {}
    return {}


def _formatMetadataForLog(metadata: Dict[str, Any]) -> str:
    if not metadata:
        return "{}"
    try:
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    except TypeError:
        sanitizedMetadata: Dict[str, Any] = {}
        for key, value in metadata.items():
            try:
                json.dumps(value, ensure_ascii=False)
                sanitizedMetadata[key] = value
            except TypeError:
                sanitizedMetadata[key] = str(value)
        try:
            return json.dumps(sanitizedMetadata, ensure_ascii=False, sort_keys=True)
        except Exception:  # noqa: BLE001 - best effort logging fallback
            return str(sanitizedMetadata)


def _collectSerialCandidates(command: Dict[str, Any], metadata: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []
    for container in (metadata, command):
        if not isinstance(container, dict):
            continue
        for key in ("printerSerial", "serial", "printerId"):
            value = container.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
    return candidates


class RecipientCommandRouter:
    def __init__(self, recipientId: str, pollInterval: float) -> None:
        self.recipientId = recipientId
        self.pollIntervalSeconds = max(3.0, float(pollInterval))
        self._lock = threading.Lock()
        self._workers: Dict[str, "CommandWorker"] = {}
        self._backlog: List[Dict[str, Any]] = []
        self._stopEvent = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"RecipientCommandRouter-{recipientId}",
            daemon=True,
        )
        self._pollErrorCount = 0
        self._thread.start()

    @property
    def isActive(self) -> bool:
        return self._thread.is_alive() and not self._stopEvent.is_set()

    def updatePollInterval(self, pollInterval: float) -> None:
        try:
            value = float(pollInterval)
        except (TypeError, ValueError):
            value = self.pollIntervalSeconds
        self.pollIntervalSeconds = max(3.0, value)

    def registerWorker(self, worker: "CommandWorker") -> None:
        with self._lock:
            self._workers[worker.serial] = worker
            if not self._backlog:
                return
            pendingCommands = list(self._backlog)
            self._backlog = []
            workersSnapshot = dict(self._workers)
        if pendingCommands:
            self._routeCommands(pendingCommands, workersSnapshot, queued=True)

    def unregisterWorker(self, serial: str) -> None:
        shouldStop = False
        with self._lock:
            self._workers.pop(serial, None)
            shouldStop = not self._workers
        if shouldStop:
            self._stopEvent.set()

    def _snapshotWorkers(self) -> Dict[str, "CommandWorker"]:
        with self._lock:
            return dict(self._workers)

    def pollOnce(
        self,
        workers: Optional[Dict[str, "CommandWorker"]] = None,
        *,
        suppressCheckLog: bool = False,
    ) -> None:
        log.info("Checking for pending commands for recipient %s.", self.recipientId)
        try:
            commands = listPendingCommandsForRecipient(self.recipientId)
            self._pollErrorCount = 0
        except Exception as error:  # noqa: BLE001 - log and continue
            self._pollErrorCount += 1
            if self._pollErrorCount == 1 or self._pollErrorCount % 50 == 0:
                log.warning("Control poll failed for recipient %s: %s", self.recipientId, error)
            return
        if not commands:
            log.info("No pending commands for recipient %s.", self.recipientId)
        else:
            log.info(
                "Fetched %d pending commands for recipient %s.",
                len(commands),
                self.recipientId,
            )
        with self._lock:
            workersSnapshot = dict(workers) if workers is not None else dict(self._workers)
            backlogCommands = list(self._backlog)
            if not commands and not backlogCommands:
                return
            if not workersSnapshot:
                if commands:
                    self._backlog.extend(commands)
                    log.info(
                        "Queued %d pending commands for recipient %s (no active printers yet).",
                        len(commands),
                        self.recipientId,
                    )
                return
            self._backlog = []
        if backlogCommands:
            self._routeCommands(backlogCommands, workersSnapshot, queued=True)
        if commands:
            self._routeCommands(commands, workersSnapshot, queued=False)

    poll_once = pollOnce

    def _run(self) -> None:
        log.info("Recipient command poller started for %s", self.recipientId)
        try:
            while not self._stopEvent.is_set():
                workers = self._snapshotWorkers()
                self.pollOnce(workers, suppressCheckLog=False)
                if self._stopEvent.wait(self.pollIntervalSeconds):
                    break
        finally:
            log.info("Recipient command poller stopped for %s", self.recipientId)
            _unregisterRecipientRouter(self.recipientId, self)

    def _selectWorker(
        self,
        workers: Dict[str, "CommandWorker"],
        command: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Optional["CommandWorker"]:
        for serial in _collectSerialCandidates(command, metadata):
            worker = workers.get(serial)
            if worker is not None:
                return worker
        return None

    def _routeCommands(
        self,
        commands: List[Dict[str, Any]],
        workers: Dict[str, "CommandWorker"],
        *,
        queued: bool,
    ) -> None:
        unrouted: List[Dict[str, Any]] = []
        for command in commands:
            metadata = _normalizeCommandMetadata(command)
            worker = self._selectWorker(workers, command, metadata)
            commandIdValue = str(command.get("commandId") or "")
            if worker is not None:
                metadataSummary = _formatMetadataForLog(metadata)
                if commandIdValue:
                    messagePrefix = "Routing queued command" if queued else "Routing command"
                    log.info(
                        "%s %s → %s metadata=%s",
                        messagePrefix,
                        commandIdValue,
                        worker.serial,
                        metadataSummary,
                    )
                else:
                    log.info(
                        "Routing command to %s metadata=%s",
                        worker.serial,
                        metadataSummary,
                    )
                worker.enqueueCommand(command)
            else:
                log.info(
                    "No local target for command %s yet (kept in queue) meta=%s",
                    command.get("commandId"),
                    metadata,
                )
                unrouted.append(command)
        if unrouted:
            with self._lock:
                self._backlog.extend(unrouted)


def _ensureCacheLoaded() -> Dict[str, Any]:
    global _cacheData
    with _cacheLock:
        if _cacheData is None:
            try:
                CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
                if CACHE_FILE_PATH.exists():
                    loaded = json.loads(CACHE_FILE_PATH.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        _cacheData = loaded
                    else:
                        _cacheData = {"commands": {}}
                else:
                    _cacheData = {"commands": {}}
            except Exception:
                log.debug("Unable to load command cache", exc_info=True)
                _cacheData = {"commands": {}}
        return _cacheData


def _writeCache() -> None:
    with _cacheLock:
        if _cacheData is None:
            return
        try:
            CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)
            CACHE_FILE_PATH.write_text(json.dumps(_cacheData, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            log.debug("Unable to persist command cache", exc_info=True)


def _reserveCommand(commandId: str) -> bool:
    cache = _ensureCacheLoaded()
    commands = cache.setdefault("commands", {})
    if commandId in commands:
        return False
    commands[commandId] = {"status": "reserved", "timestamp": time.time()}
    _writeCache()
    return True


def _finalizeCommand(commandId: str, status: str) -> None:
    cache = _ensureCacheLoaded()
    commands = cache.setdefault("commands", {})
    entry = commands.get(commandId, {})
    entry.update({"status": status, "timestamp": time.time()})
    commands[commandId] = entry
    _writeCache()


class CommandWorker:
    """Poll Base44 for printer control commands and execute them on a specific printer."""

    def __init__(
        self,
        *,
        serial: str,
        ipAddress: str,
        accessCode: str,
        nickname: Optional[str] = None,
        apiKey: Optional[str] = None,
        recipientId: Optional[str] = None,
        baseUrl: Optional[str] = None,
        pollInterval: Optional[float] = None,
    ) -> None:
        self.serial = serial
        self.ipAddress = ipAddress
        self.accessCode = accessCode
        self.nickname = nickname or serial
        self._stopEvent = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._printerInstance: Optional[Any] = None
        self._printerLock = threading.Lock()
        self.apiKeyValue = (
            apiKey
            or os.getenv("PRINTER_BACKEND_API_KEY", "")
            or os.getenv("BASE44_API_KEY", "")
        ).strip()
        self.recipientIdValue = (recipientId or os.getenv("BASE44_RECIPIENT_ID", "")).strip()
        self.controlBaseUrl = (baseUrl or os.getenv("PRINTER_BACKEND_BASE_URL", "")).strip()
        baseCandidate = self.controlBaseUrl or defaultBaseUrl
        try:
            self.controlBaseUrl = buildBaseUrl(baseCandidate)
        except Exception:
            log.debug("Invalid control base URL %s – falling back to default", baseCandidate, exc_info=True)
            self.controlBaseUrl = buildBaseUrl(defaultBaseUrl)
        self.controlEndpointUrl = getPrinterControlEndpointUrl(self.controlBaseUrl)
        self.controlAckUrl = f"{self.controlBaseUrl}/api/control/ack"  # No longer used by new API
        self.controlResultUrl = f"{self.controlBaseUrl}/api/control"  # Base URL for result endpoint
        self.pollErrorCount = 0
        self.pollLogEvery = 50
        self.pollIntervalSeconds = max(
            3.0,
            float(pollInterval) if pollInterval is not None else CONTROL_POLL_SECONDS,
        )
        self.pollMode = _determinePollMode()
        self._commandQueue: Optional[queue.Queue] = None
        self._router: Optional[RecipientCommandRouter] = None
        self._statusThread: Optional[threading.Thread] = None
        self._statusStopEvent = threading.Event()
        self._statusLock = threading.Lock()
        self._lastStatus: Dict[str, Any] = {}
        self._lastRawStatus: Optional[Dict[str, Any]] = None
        self._lastRemoteFile: Optional[str] = None
        self._lastProgressBucket: Optional[int] = None
        self._lastStatusTimestamp: float = 0.0
        self._statusWarningLogged = False
        self._jobActive = False
        self._sawActivityDuringJob = False
        self._activeJobMetadata: Dict[str, Any] = {}
        self._activeJobKey: str = "job"
        self._checkpointSnapshots: Dict[int, Path] = {}
        self._capturedCheckpoints: set[int] = set()
        self._brakeFlowThread: Optional[threading.Thread] = None
        self._brakeFlowLock = threading.Lock()
        self._brakeFlowBlocked = False
        self._lastErrorCodeReported: Optional[str] = None
        self._printErrorCodeUnsupportedLogged = False
        self.cameraSnapshotIntervalSeconds = cameraSnapshotIntervalSeconds
        self._cameraLoopThread: Optional[threading.Thread] = None
        self._cameraLoopStopEvent = threading.Event()
        self._idleConnectErrorCount = 0
        self._sequence_id: int = 0
        log.debug(
            "CommandWorker configured for %s using control endpoint %s", self.serial, self.controlEndpointUrl
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopEvent.clear()
        if self.pollMode == "recipient":
            if not self.recipientIdValue:
                raise RuntimeError("Missing recipientId for recipient polling mode")
            if self._commandQueue is None:
                self._commandQueue = queue.Queue()
            self._router = _registerRecipientRouter(self.recipientIdValue, self.pollIntervalSeconds)
            self._router.registerWorker(self)
        self._thread = threading.Thread(target=self._run, name=f"CommandWorker-{self.serial}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        if self.pollMode == "recipient" and self._router is not None:
            self._router.unregisterWorker(self.serial)
            self._router = None
        self._drainQueue()
        self._stopStatusMonitor()
        self._stopCameraSnapshotLoop()
        if self._brakeFlowThread and self._brakeFlowThread.is_alive():
            self._brakeFlowThread.join(timeout=2.0)
        self._brakeFlowThread = None
        self._resetBrakeJobState()
        self._disconnectPrinter()

    def _getNextSequenceId(self) -> str:
        """Generer neste sequence_id for MQTT kommandoer"""
        seq_id = str(self._sequence_id)
        self._sequence_id += 1
        if self._sequence_id > 9999:  # Reset ved høye tall
            self._sequence_id = 0
        return seq_id

    def _addSequenceIdToPayload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Legg til sequence_id i payload hvis den mangler"""
        if "sequence_id" not in payload:
            payload["sequence_id"] = self._getNextSequenceId()
        return payload

    def _run(self) -> None:
        if self.pollMode == "recipient":
            self._runRecipientMode()
        else:
            self._runPrinterMode()

    def _runPrinterMode(self) -> None:
        log.info("CommandWorker started for %s (%s) [printer-mode]", self.nickname, self.serial)
        try:
            while not self._stopEvent.is_set():
                self._attemptIdlePrinterConnection()
                try:
                    commands = self._pollCommands()
                    self.pollErrorCount = 0
                except Exception as error:
                    self.pollErrorCount += 1
                    if self.pollErrorCount == 1 or self.pollErrorCount % self.pollLogEvery == 0:
                        log.warning("Control poll failed for %s: %s", self.serial, error)
                    commands = []
                for command in commands:
                    self._processCommand(command)
                self._stopEvent.wait(self.pollIntervalSeconds)
        finally:
            log.info("CommandWorker stopped for %s [printer-mode]", self.serial)

    def _runRecipientMode(self) -> None:
        log.info("CommandWorker started for %s (%s) [recipient-mode]", self.nickname, self.serial)
        queueRef = self._commandQueue
        try:
            if queueRef is None:
                return
            while not self._stopEvent.is_set():
                self._attemptIdlePrinterConnection()
                try:
                    command = queueRef.get(timeout=self.pollIntervalSeconds)
                except queue.Empty:
                    continue
                commandIdValue = str(command.get("commandId") or "")
                if commandIdValue:
                    log.debug("Dequeued command %s → %s", commandIdValue, self.serial)
                self._processCommand(command)
        finally:
            log.info("CommandWorker stopped for %s [recipient-mode]", self.serial)
            if self._router is not None:
                self._router.unregisterWorker(self.serial)
                self._router = None
            self._drainQueue()

    def _processCommand(self, command: Dict[str, Any]) -> None:
        commandId = str(command.get("commandId") or "").strip()
        if not commandId:
            return
        if not _reserveCommand(commandId):
            return

        metadata = _normalizeCommandMetadata(command)
        metadataSummary = _formatMetadataForLog(metadata)
        log.info("Processing command %s for %s metadata=%s", commandId, self.serial, metadataSummary)

        try:
            self._sendCommandAck(commandId, "processing")
        except UnsupportedControlEndpointError as error:
            log.warning("ACK endpoint unavailable for %s: %s", commandId, error)
        except Exception as error:  # noqa: BLE001 - log but continue executing command
            log.warning("Failed to acknowledge command %s: %s", commandId, error)

        try:
            printer = self._connectPrinter()
            status, message = self._executeCommand(printer, command)
        except Exception as error:
            errorMessage = f"{type(error).__name__}: {error}"
            log.warning("Command %s failed on %s: %s", commandId, self.serial, errorMessage)
            printerRef = self._printerInstance
            if printerRef is not None:
                self._collectAndReportBambuError(
                    printerRef,
                    {
                        "event": {
                            "commandId": commandId,
                            "error": errorMessage,
                            "commandType": command.get("commandType"),
                        }
                    },
                )
            try:
                self._sendCommandResult(commandId, "failed", errorMessage=errorMessage)
            except UnsupportedControlEndpointError as resultError:
                log.warning("RESULT endpoint unavailable for %s: %s", commandId, resultError)
            except Exception:
                log.debug("Unable to submit failed result for %s", commandId, exc_info=True)
            _finalizeCommand(commandId, "failed")
            return

        try:
            self._sendCommandResult(commandId, status, message=message)
        except UnsupportedControlEndpointError as resultError:
            log.warning("RESULT endpoint unavailable for %s: %s", commandId, resultError)
        except Exception:
            log.debug("Unable to submit result for %s", commandId, exc_info=True)
        _finalizeCommand(commandId, status)
        statusValue = str(status)
        messageValue = str(message).strip() if message is not None else ""
        statusLine = f"{statusValue} – {messageValue}" if messageValue else statusValue
        progressSuffix = self._buildProgressSuffix()
        if progressSuffix:
            log.info(
                "Command %s on %s: %s (%s)",
                commandId,
                self.serial,
                statusLine,
                progressSuffix,
            )
        else:
            log.info("Command %s on %s: %s", commandId, self.serial, statusLine)

    def enqueueCommand(self, command: Dict[str, Any]) -> None:
        if self.pollMode != "recipient":
            return
        if self._commandQueue is None:
            self._commandQueue = queue.Queue()
        try:
            self._commandQueue.put_nowait(command)
            commandIdValue = str(command.get("commandId") or "")
            if commandIdValue:
                log.debug("Enqueued command %s → %s", commandIdValue, self.serial)
        except Exception:
            log.debug("Unable to enqueue command %s", command.get("commandId"), exc_info=True)

    def _drainQueue(self) -> None:
        if self._commandQueue is None:
            return
        try:
            while True:
                self._commandQueue.get_nowait()
        except queue.Empty:
            pass
        self._commandQueue = None

    def _pollCommands(self) -> List[Dict[str, Any]]:
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        params = {
            "recipientId": self.recipientIdValue,
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
        }
        headers = {"Content-Type": "application/json", "X-API-Key": self.apiKeyValue}
        response = requests.get(
            self.controlEndpointUrl,
            params=params,
            headers=headers,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        if not response.content:
            return []
        data = response.json()
        if isinstance(data, list):
            commands = data
        elif isinstance(data, dict):
            commands = data.get("commands", [])
        else:
            commands = []
        if not isinstance(commands, list):
            return []
        normalized = [command for command in commands if isinstance(command, dict)]
        if normalized:
            commandIds = [str(entry.get("commandId")) for entry in normalized]
            log.info(
                "Fetched %d command(s) for %s: %s",
                len(normalized),
                self.serial,
                ", ".join(commandIds),
            )
        return normalized

    def _sendCommandAck(self, commandId: str, status: str) -> bool:
        if self.pollMode == "recipient":
            acknowledgeCommand(commandId)
            return True
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        payload = {
            "recipientId": self.recipientIdValue,
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
            "commandId": commandId,
            "status": status,
            "startedAt": _isoTimestamp(),
        }
        log.debug("Sending ACK for %s via %s", commandId, self.controlAckUrl)
        return self._postControlPayload(self.controlAckUrl, payload, "ack")

    def _sendCommandResult(
        self,
        commandId: str,
        status: str,
        *,
        message: Optional[str] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        if self.pollMode == "recipient":
            normalizedStatus = str(status or "").strip()
            normalizedLower = normalizedStatus.lower()
            successStatusSet = {"completed", "success", "ok", "done"}
            failureStatusSet = {"failed", "error", "errored", "ko"}
            if not normalizedLower:
                normalizedLower = "completed" if not errorMessage else "failed"
            elif normalizedLower in successStatusSet:
                normalizedLower = "completed"
            elif normalizedLower in failureStatusSet:
                normalizedLower = "failed"
            messageValue = str(message) if message is not None else None
            errorValue = str(errorMessage) if errorMessage is not None else None
            postCommandResult(
                commandId,
                normalizedLower,
                message=messageValue,
                errorMessage=errorValue,
                recipientId=self.recipientIdValue,
            )
            return
        if not self.apiKeyValue or not self.recipientIdValue:
            raise RuntimeError("Missing API key or recipientId for CommandWorker")
        
        # New API format: POST /api/control/:commandId/result?recipientId=xxx
        # Body: { "success": bool, "result": str|null, "error": str|null }
        normalizedStatus = str(status or "").strip().lower()
        successStatusSet = {"completed", "success", "ok", "done"}
        isSuccess = normalizedStatus in successStatusSet or (
            normalizedStatus and normalizedStatus not in {"failed", "error", "errored", "ko"}
        )
        
        resultUrl = f"{self.controlResultUrl}/{commandId}/result"
        payload: Dict[str, Any] = {
            "success": isSuccess,
            "result": str(message).strip() if message else ("Command executed successfully" if isSuccess else None),
            "error": str(errorMessage).strip() if errorMessage else None,
        }
        params = {"recipientId": self.recipientIdValue}
        log.debug("Sending RESULT for %s via %s", commandId, resultUrl)
        self._postControlPayloadWithParams(resultUrl, payload, params, "result")

    def _postControlPayload(self, url: str, payload: Dict[str, Any], action: str) -> bool:
        headers = {"Content-Type": "application/json", "X-API-Key": self.apiKeyValue}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=CONNECT_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.HTTPError as error:  # pragma: no cover - HTTP path validated via tests
            statusCode = getattr(error.response, "status_code", None)
            if statusCode in {404, 405}:
                raise UnsupportedControlEndpointError(f"/{action}") from error
            raise
        return True

    def _postControlPayloadWithParams(
        self, url: str, payload: Dict[str, Any], params: Dict[str, str], action: str
    ) -> bool:
        """Post control payload with query parameters for new API format."""
        headers = {"Content-Type": "application/json", "X-API-Key": self.apiKeyValue}
        try:
            response = requests.post(
                url, json=payload, headers=headers, params=params, timeout=CONNECT_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except requests.HTTPError as error:
            statusCode = getattr(error.response, "status_code", None)
            if statusCode in {404, 405}:
                raise UnsupportedControlEndpointError(f"/{action}") from error
            raise
        return True

    def _connectPrinter(self) -> Any:
        if bambuApi is None:
            raise RuntimeError("bambulabs_api is not installed")
        with self._printerLock:
            if self._printerInstance is not None:
                return self._printerInstance
            printer = bambuApi.Printer(self.ipAddress, self.accessCode, self.serial)
            mqttStart = getattr(printer, "mqtt_start", None)
            connectMethod = getattr(printer, "connect", None)
            if not callable(mqttStart) and not callable(connectMethod):
                raise RuntimeError("bambulabs_api.Printer is missing connect/mqtt_start")
            if callable(mqttStart):
                try:
                    mqttStart()
                except Exception:
                    log.debug("Printer mqtt_start() failed", exc_info=True)
            if callable(connectMethod):
                try:
                    connectMethod()
                except Exception:
                    log.debug("Printer connect() failed", exc_info=True)
            waitForReady = getattr(bambuPrinter, "_waitForMqttReady", None)
            if callable(waitForReady):
                try:
                    waitForReady(printer, timeout=30.0)
                except Exception:
                    log.debug("Printer MQTT readiness wait failed", exc_info=True)
            self._ensureStatusMonitor(printer)
            self._printerInstance = printer
            return printer

    def _disconnectPrinter(self) -> None:
        with self._printerLock:
            if self._printerInstance is None:
                return
            self._stopStatusMonitor()
            try:
                if hasattr(self._printerInstance, "disconnect"):
                    self._printerInstance.disconnect()
            except Exception:
                log.debug("Error while disconnecting printer %s", self.serial, exc_info=True)
            finally:
                self._printerInstance = None

    def _attemptIdlePrinterConnection(self) -> None:
        if self._stopEvent.is_set():
            return
        with self._printerLock:
            printerReady = self._printerInstance is not None
        if printerReady:
            self._idleConnectErrorCount = 0
            return
        try:
            self._connectPrinter()
        except Exception as error:  # noqa: BLE001 - propagate through logging
            self._idleConnectErrorCount += 1
            if self._idleConnectErrorCount == 1 or self._idleConnectErrorCount % self.pollLogEvery == 0:
                log.warning(
                    "Idle printer connect failed for %s: %s",
                    self.serial,
                    error,
                )
        else:
            self._idleConnectErrorCount = 0

    def _ensureStatusMonitor(self, printer: Any) -> None:
        if self._statusThread and self._statusThread.is_alive():
            return
        self._statusStopEvent.clear()
        self._statusWarningLogged = False
        self._lastStatusTimestamp = 0.0
        self._statusThread = threading.Thread(
            target=self._statusMonitorLoop,
            name=f"CommandWorkerStatus-{self.serial}",
            args=(printer,),
            daemon=True,
        )
        self._statusThread.start()
        self._ensureCameraSnapshotLoop(printer)

    def _stopStatusMonitor(self) -> None:
        self._statusStopEvent.set()
        if self._statusThread and self._statusThread.is_alive():
            self._statusThread.join(timeout=2.0)
        self._statusThread = None
        self._stopCameraSnapshotLoop()

    def _ensureCameraSnapshotLoop(self, printer: Any) -> None:
        if self.cameraSnapshotIntervalSeconds <= 0:
            return
        if self._cameraLoopThread and self._cameraLoopThread.is_alive():
            return
        self._cameraLoopStopEvent.clear()
        self._cameraLoopThread = threading.Thread(
            target=self._cameraSnapshotLoop,
            name=f"CommandWorkerCamera-{self.serial}",
            args=(printer,),
            daemon=True,
        )
        self._cameraLoopThread.start()

    def _stopCameraSnapshotLoop(self) -> None:
        self._cameraLoopStopEvent.set()
        if self._cameraLoopThread and self._cameraLoopThread.is_alive():
            self._cameraLoopThread.join(timeout=2.0)
        self._cameraLoopThread = None

    def _cameraSnapshotLoop(self, printer: Any) -> None:
        intervalSeconds = max(1.0, float(self.cameraSnapshotIntervalSeconds))
        while not self._cameraLoopStopEvent.is_set() and not self._stopEvent.is_set():
            if not self._isPrinterConnected(printer):
                if self._cameraLoopStopEvent.wait(1.0):
                    break
                continue
            try:
                snapshotPath = captureCameraSnapshot(printer, self.serial)
            except Exception:  # noqa: BLE001 - propagates through logging in captureCameraSnapshot
                log.debug("[camera] periodic snapshot failed for %s", self.serial, exc_info=cameraDebugEnabled)
            else:
                self._postCameraSnapshot(snapshotPath)
            if self._cameraLoopStopEvent.wait(intervalSeconds):
                break

    def _isPrinterConnected(self, printer: Any) -> bool:
        with self._printerLock:
            return self._printerInstance is printer and self._printerInstance is not None

    def _postCameraSnapshot(self, snapshotPath: Path) -> bool:
        try:
            imageBytes = snapshotPath.read_bytes()
        except Exception as error:  # noqa: BLE001 - filesystem errors should not block command
            log.warning(
                "[camera] failed to read snapshot for upload: %s",
                error,
                exc_info=cameraDebugEnabled,
            )
            return False
        encodedImage = base64.b64encode(imageBytes).decode("ascii")
        imageDataUri = f"data:image/jpeg;base64,{encodedImage}"
        payload = {
            "printerSerial": self.serial,
            "printerIpAddress": self.ipAddress,
            "imageType": "webcam",
            "imageData": imageDataUri,
        }
        log.info(
            "[camera] posting snapshot to Base44 for %s",
            self.serial,
        )
        try:
            postReportPrinterImage(payload)
        except Exception as error:  # noqa: BLE001 - HTTP client raises generic Exception
            log.warning(
                "[camera] failed to post snapshot for %s: %s",
                self.serial,
                error,
                exc_info=cameraDebugEnabled,
            )
            return False
        log.info(
            "[camera] snapshot posted for %s",
            self.serial,
        )
        return True
    # --- Adapter for bed-reference capture ---
    # Enkel adapter slik at andre deler av klienten kan be worker ta et snapshot
    # via en instansmetode. Selve implementasjonen ligger som modul-funksjon.
    def _captureCameraSnapshot(self, printer: Any, serial: str) -> Path:
        """Returnerer sti til lagret snapshot-bilde for gitt printer/serial."""
        return captureCameraSnapshot(printer, serial)


    def _obtainPrinterInstance(self) -> Any:
        with self._printerLock:
            printer = self._printerInstance
        if printer is not None:
            return printer
        return self._connectPrinter()

    def captureReferenceSequence(self) -> List[Path]:
        printer = self._obtainPrinterInstance()
        return captureReferenceSequence(printer, self.serial, captureCameraSnapshot)
    
    def captureBedReference(
        self,
        zStepMm: float = 2.0,
        totalMm: float = 220.0,
        **kwargs,
    ) -> List[Path]:
        """
        Capture bed reference bilder ved ulike Z-høyder.

        Prosess:
        1. Koble til printer
        2. Home printer
        3. Flytt print head bakover (X=0, Y=250)
        4. Ta bilde ved Z=0
        5. Senk bed 2mm og ta bilde (gjenta til totalt 220mm)

        Args:
            zStepMm: Z-akse steg i millimeter (default 2.0)
            totalMm: Total Z-akse bevegelse i millimeter (default 220.0)

        Returns:
            Liste med Path-objekter til lagrede bilder
        """
        serial = str(getattr(self, "serial", "")).strip()
        if not serial:
            raise RuntimeError("captureBedReference krever at printer har serial number")

        log.info("[bedref] starter bed reference capture for %s (steg=%.1fmm, total=%.1fmm)",
                 serial, zStepMm, totalMm)

        # Hent printer instans
        printer = self._obtainPrinterInstance()

        # Stopp periodisk kameraloop for å unngå konflikter
        stopLoop = getattr(self, "_stopCameraSnapshotLoop", None)
        if callable(stopLoop):
            try:
                stopLoop()
                log.info("[bedref] stoppet periodisk kameraloop for %s", serial)
            except Exception as error:
                log.warning("[bedref] printer %s kunne ikke stoppe kameraloop: %s", serial, error)

        capturedPaths: List[Path] = []

        try:
            # 1. Koble til printer via connect() og MQTT
            log.info("[bedref] kobler til printer %s", serial)

            # Start MQTT først
            mqttStartMethod = getattr(printer, "mqtt_start", None)
            if callable(mqttStartMethod):
                try:
                    mqttStartMethod()
                    log.info("[bedref] MQTT startet for %s", serial)
                except Exception as error:
                    log.warning("[bedref] kunne ikke starte MQTT for %s: %s", serial, error)
            else:
                log.warning("[bedref] printer %s har ikke mqtt_start() metode", serial)

            # Deretter connect()
            connectMethod = getattr(printer, "connect", None)
            if callable(connectMethod):
                try:
                    connectMethod()
                    log.info("[bedref] connect() kalt for %s", serial)
                except Exception as error:
                    log.error("[bedref] kunne ikke koble til printer %s: %s", serial, error)
                    raise RuntimeError(f"Kunne ikke koble til printer: {error}") from error
            else:
                log.warning("[bedref] printer %s har ikke connect() metode", serial)

            # Vent på at MQTT-tilkobling etableres
            log.info("[bedref] venter 5 sekunder på at MQTT-tilkobling etableres")
            time.sleep(5.0)

            # Sjekk om MQTT er klar
            mqttReadyMethod = getattr(printer, "mqtt_client_ready", None)
            if callable(mqttReadyMethod):
                for attempt in range(10):
                    try:
                        if mqttReadyMethod():
                            log.info("[bedref] MQTT-tilkobling bekreftet klar for %s", serial)
                            break
                    except Exception:
                        pass
                    time.sleep(1.0)
                else:
                    log.warning("[bedref] printer %s kunne ikke bekrefte MQTT-tilkobling - fortsetter likevel", serial)
            else:
                log.warning("[bedref] printer %s har ikke mqtt_client_ready() - antar tilkobling er OK", serial)

            # 2. Home printer
            log.info("[bedref] starter homing for %s", serial)
            homeMethod = getattr(printer, "home_printer", None)
            if callable(homeMethod):
                try:
                    homeResult = homeMethod()
                    if homeResult:
                        log.info("[bedref] homing fullført for %s", serial)
                    else:
                        log.warning("[bedref] home_printer() returnerte False for %s", serial)
                        raise RuntimeError("Homing feilet - home_printer() returnerte False")
                except Exception as error:
                    log.error("[bedref] homing feilet for %s: %s", serial, error)
                    raise RuntimeError(f"Homing feilet: {error}") from error
            else:
                log.error("[bedref] printer %s har ikke home_printer() metode", serial)
                raise RuntimeError("Printer mangler home_printer() metode")

            # Vent på at homing fullføres - homing kan ta lang tid!
            log.info("[bedref] venter på at homing fullføres komplett")

            # Sjekk printer state for å vente til homing er ferdig
            homingTimeout = time.monotonic() + 120.0  # Maks 2 minutter
            homingDetected = False

            getStateMethod = getattr(printer, "get_state", None)
            if callable(getStateMethod):
                while time.monotonic() < homingTimeout:
                    try:
                        state = getStateMethod()
                        if state:
                            # Sjekk ulike state-felt for "homing"
                            stateStr = str(state).lower()
                            gcodeState = str(state.get("gcode_state", "")).lower() if isinstance(state, dict) else ""
                            motionState = str(state.get("motion_state", "")).lower() if isinstance(state, dict) else ""

                            # Detekter om vi holder på med homing
                            if "homing" in stateStr or "homing" in gcodeState or "homing" in motionState:
                                homingDetected = True
                                log.info("[bedref] homing pågår, venter...")
                                time.sleep(1.0)
                                continue

                            # Hvis vi har sett homing og nå er den ferdig (idle/ready)
                            if homingDetected:
                                if any(status in gcodeState or status in motionState for status in ["idle", "ready", "finish"]):
                                    log.info("[bedref] homing fullført (state bekreftet)")
                                    break

                        time.sleep(0.5)
                    except Exception as error:
                        log.warning("[bedref] kunne ikke lese state under homing: %s", error)
                        time.sleep(1.0)

                # Hvis vi aldri så homing, eller timeout, gi en advarsel men fortsett
                if not homingDetected:
                    log.warning("[bedref] printer %s kunne ikke bekrefte homing via state, venter 45 sekunder ekstra", serial)
                    time.sleep(45.0)
                else:
                    # Vent litt ekstra etter at homing er bekreftet ferdig
                    log.info("[bedref] venter 3 sekunder ekstra etter homing")
                    time.sleep(3.0)
            else:
                # Fallback hvis get_state ikke finnes
                log.warning("[bedref] printer %s get_state() ikke tilgjengelig, bruker fast ventetid på 45 sekunder", serial)
                time.sleep(45.0)

            # 3. Flytt print head bakover (midten av X, helt bak på Y)
            log.info("[bedref] flytter print head bakover (X=128, Y=250) for %s", serial)
            gcodeMethod = getattr(printer, "gcode", None)
            if callable(gcodeMethod):
                try:
                    # Sett til absolutt posisjonering
                    gcodeMethod("G90")
                    # Flytt til bak posisjon (X=128 er midten på 256mm bed)
                    gcodeMethod("G1 X128 Y250 F6000")
                    log.info("[bedref] print head flyttet til bak posisjon for %s", serial)
                except Exception as error:
                    log.warning("[bedref] kunne ikke flytte print head via gcode for %s: %s",
                               serial, error)
                    # Fortsett likevel - ikke kritisk
            else:
                log.warning("[bedref] printer %s har ikke gcode() metode - hopper over head parking", serial)

            # Vent på at bevegelse fullføres - økt til 4 sekunder
            log.info("[bedref] venter 4 sekunder på at print head bevegelse fullføres")
            time.sleep(4.0)

            # 4. Ta bilde ved Z=0
            log.info("[bedref] tar bilde 1 ved Z=0 for %s", serial)
            imagePath = self._captureBedReferenceImage(printer, serial, 0, 0.0)
            capturedPaths.append(imagePath)
            log.info("[bedref] bilde 1 lagret: %s", imagePath)

            # 5. Senk bed og ta bilder
            # VIKTIG: move_z_axis(height) flytter Z-aksen til absolutt posisjon
            # Etter homing er Z=0 på toppen. For å senke bed, må vi flytte Z OPP (positive verdier)
            currentHeight = 0.0
            imageNumber = 1

            while currentHeight < totalMm:
                # Beregn neste høyde
                currentHeight += zStepMm
                if currentHeight > totalMm:
                    currentHeight = totalMm

                imageNumber += 1

                log.info("[bedref] beveger bed ned (Z-akse opp til %.1fmm) for %s", currentHeight, serial)

                # Sjekk MQTT-tilkobling før Z-bevegelse
                mqttReadyMethod = getattr(printer, "mqtt_client_ready", None)
                if callable(mqttReadyMethod):
                    try:
                        if not mqttReadyMethod():
                            log.warning("[bedref] printer %s MQTT ikke klar, prøver å reconnect...", serial)
                            # Prøv å reconnect
                            mqttStartMethod = getattr(printer, "mqtt_start", None)
                            if callable(mqttStartMethod):
                                mqttStartMethod()
                                time.sleep(2.0)
                    except Exception as error:
                        log.warning("[bedref] kunne ikke sjekke MQTT-status: %s", error)

                # Flytt Z-aksen (absolutt posisjon)
                moveZMethod = getattr(printer, "move_z_axis", None)
                if callable(moveZMethod):
                    try:
                        # Konverter til integer for move_z_axis
                        zPosition = int(round(currentHeight))
                        log.info("[bedref] kaller move_z_axis(%d) for %s", zPosition, serial)
                        moveResult = moveZMethod(zPosition)

                        if moveResult:
                            log.info("[bedref] move_z_axis(%d) returnerte True - bevegelse OK", zPosition)
                        else:
                            log.warning("[bedref] move_z_axis(%d) returnerte False/None - sjekker feilmelding", zPosition)

                            # Hvis move_z_axis returnerer False, kan det være MQTT-problem
                            # Prøv å reconnect og prøv igjen EN gang
                            log.info("[bedref] prøver å reconnect MQTT og kjøre move_z_axis på nytt...")
                            mqttStartMethod = getattr(printer, "mqtt_start", None)
                            if callable(mqttStartMethod):
                                try:
                                    mqttStartMethod()
                                    time.sleep(3.0)
                                    # Prøv igjen
                                    moveResult = moveZMethod(zPosition)
                                    if moveResult:
                                        log.info("[bedref] move_z_axis(%d) suksess etter reconnect!", zPosition)
                                    else:
                                        log.error("[bedref] move_z_axis(%d) feilet også etter reconnect", zPosition)
                                except Exception as reconnectError:
                                    log.error("[bedref] reconnect feilet: %s", reconnectError)

                    except Exception as error:
                        log.error("[bedref] move_z_axis(%d) kastet exception: %s", zPosition, error)
                        # Ikke raise - fortsett med neste bilde
                else:
                    log.error("[bedref] printer %s har ikke move_z_axis() metode", serial)
                    raise RuntimeError("Printer mangler move_z_axis() metode")

                # Vent lengre på at bevegelse fullføres - økt til 5 sekunder
                log.info("[bedref] venter 5 sekunder på at Z-akse bevegelse fullføres")
                time.sleep(5.0)

                # Ta bilde
                log.info("[bedref] tar bilde %d ved Z=%.1fmm for %s", imageNumber, currentHeight, serial)
                imagePath = self._captureBedReferenceImage(printer, serial, imageNumber, currentHeight)
                capturedPaths.append(imagePath)
                log.info("[bedref] bilde %d lagret: %s", imageNumber, imagePath)

            log.info("[bedref] bed reference capture fullført for %s - %d bilder lagret",
                    serial, len(capturedPaths))

            return capturedPaths

        finally:
            # Start periodisk kameraloop igjen
            startLoop = getattr(self, "_startCameraSnapshotLoop", None) or getattr(self, "_ensureCameraSnapshotLoop", None)
            if callable(startLoop):
                try:
                    startLoop(printer)
                    log.info("[bedref] startet periodisk kameraloop igjen for %s", serial)
                except Exception as error:
                    log.warning("[bedref] kunne ikke starte kameraloop igjen: %s", error)

    def _captureBedReferenceImage(self, printer: Any, serial: str, imageNumber: int, zHeight: float) -> Path:
        """
        Tar et enkelt bed reference bilde og lagrer det i riktig mappe.

        Args:
            printer: Printer instans
            serial: Printer serial number
            imageNumber: Bildenummer (starter på 0)
            zHeight: Z-høyde for dette bildet

        Returns:
            Path til lagret bilde
        """
        # Opprett målmappe: .printmaster/bed-reference/<serial>/
        bedRefDirectory = Path.home() / ".printmaster" / "bed-reference" / serial
        bedRefDirectory.mkdir(parents=True, exist_ok=True)

        # Ta snapshot ved hjelp av eksisterende funksjon
        tempImagePath = captureCameraSnapshot(printer, serial)

        # Flytt til riktig plassering med riktig navn: bedRef001.jpg, bedRef002.jpg, etc.
        targetFileName = f"bedRef{imageNumber:03d}.jpg"
        targetPath = bedRefDirectory / targetFileName

        # Kopier filen
        shutil.copy2(tempImagePath, targetPath)

        log.info("[bedref] kopierte %s til %s", tempImagePath, targetPath)

        return targetPath

    def runBrakeDemo(
        self,
        context: Optional[BrakeFlowContext] = None,
        *,
        reportFailure: bool = False,
    ) -> bool:
        with self._brakeFlowLock:
            printer = self._obtainPrinterInstance()
            activeContext = context or self._buildBrakeFlowContext()
            if activeContext is None:
                log.debug("[brake] no context available for %s", self.serial)
                return True
            result = BrakeFlow.run_demo(printer, activeContext, captureCameraSnapshot)
        if result:
            if self._brakeFlowBlocked:
                log.info("[brake] clearing obstruction block for %s", self.serial)
            self._brakeFlowBlocked = False
        elif reportFailure:
            self._handleBrakeFailure(activeContext)
        return result

    def _statusMonitorLoop(self, printer: Any) -> None:
        startTime = time.monotonic()
        while not self._statusStopEvent.is_set():
            try:
                snapshot = self._collectPrinterStatusSnapshot(printer)
                if snapshot:
                    self._handlePrinterStatus(snapshot)
            except Exception:
                log.debug("Printer status collection failed for %s", self.serial, exc_info=True)
            if not self._statusWarningLogged:
                elapsed = time.monotonic() - startTime
                if elapsed >= 10.0 and self._lastStatusTimestamp <= 0:
                    log.warning(
                        "No printer status received within %.1fs after connect for %s", elapsed, self.serial
                    )
                    self._statusWarningLogged = True
            self._statusStopEvent.wait(1.0)

    def _collectPrinterStatusSnapshot(self, printer: Any) -> Dict[str, Any]:
        sources: List[Any] = []

        def collectFromTarget(target: Any, accessors: List[str]) -> None:
            for accessor in accessors:
                attribute = getattr(target, accessor, None)
                if attribute is None:
                    continue
                try:
                    value = attribute() if callable(attribute) else attribute
                except Exception:
                    continue
                if value is not None:
                    sources.append(value)
                    break

        collectFromTarget(printer, ["get_state", "get_current_state"])
        collectFromTarget(printer, ["get_percentage", "current_layer_num"])
        collectFromTarget(printer, ["get_time", "get_remaining_time"])
        collectFromTarget(printer, ["gcode", "get_gcode_state"])

        snapshot: Dict[str, Any] = {"statusSources": sources}
        for candidate in sources:
            if isinstance(candidate, dict):
                snapshot.setdefault("rawStatus", candidate)
                break
        return snapshot

    @staticmethod
    def _normalizeKeyName(name: Any) -> str:
        return "".join(character for character in str(name).lower() if character.isalnum())

    @classmethod
    def _searchStatusValue(cls, payload: Any, normalizedKeys: set[str]) -> Any:
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalizedKey = cls._normalizeKeyName(key)
                if normalizedKey in normalizedKeys:
                    return value
                nested = cls._searchStatusValue(value, normalizedKeys)
                if nested is not None:
                    return nested
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                nested = cls._searchStatusValue(item, normalizedKeys)
                if nested is not None:
                    return nested
        return None

    @classmethod
    def _extractStatusValue(cls, sources: List[Any], aliases: List[str]) -> Any:
        normalizedAliases = {cls._normalizeKeyName(alias) for alias in aliases}
        for source in sources:
            value = cls._searchStatusValue(source, normalizedAliases)
            if value is not None:
                return value
        return None

    @staticmethod
    def _coerceIntValue(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value))
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            match = re.search(r"-?\d+(?:\.\d+)?", stripped)
            if match:
                try:
                    return int(round(float(match.group(0))))
                except Exception:
                    return None
        return None

    @staticmethod
    def _coerceFloatValue(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            match = re.search(r"-?\d+(?:\.\d+)?", stripped)
            if match:
                try:
                    return float(match.group(0))
                except Exception:
                    return None
        return None

    @staticmethod
    def _coerceStringValue(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)

    def _normalizeStatusSnapshot(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sources: List[Any] = []
        if isinstance(payload, dict):
            sources.append(payload)
            statusSources = payload.get("statusSources")
            if isinstance(statusSources, list):
                sources.extend(statusSources)
            rawCandidate = payload.get("rawStatus")
            if rawCandidate is not None:
                sources.append(rawCandidate)
        elif payload is not None:
            sources.append(payload)

        if not sources:
            return {}

        percentValue = self._extractStatusValue(
            sources,
            ["mc_percent", "progress", "percentage", "progresspercent", "last_print_percentage"],
        )
        remainingValue = self._extractStatusValue(
            sources,
            ["mc_remaining_time", "remaining_time", "remainingtimeseconds", "eta", "remainingtime"],
        )
        gcodeValue = self._extractStatusValue(sources, ["gcode_state", "gcodestate", "subtask_name", "subtaskname"])
        jobStateValue = self._extractStatusValue(
            sources,
            ["job_state", "jobstate", "printer_state", "print_state", "state"],
        )
        remoteFileValue = self._extractStatusValue(
            sources,
            ["remote_file", "remotefile", "file", "filename", "sd_filename", "sdfilename"],
        )

        normalized: Dict[str, Any] = {}
        percentInt = self._coerceIntValue(percentValue)
        if percentInt is not None:
            normalized["mc_percent"] = max(0, min(100, percentInt))
        remainingInt = self._coerceIntValue(remainingValue)
        if remainingInt is not None:
            normalized["mc_remaining_time"] = max(0, remainingInt)
        gcodeText = self._coerceStringValue(gcodeValue)
        if gcodeText:
            normalized["gcode_state"] = gcodeText
        jobStateText = self._coerceStringValue(jobStateValue)
        if jobStateText:
            normalized["job_state"] = jobStateText
        remoteFileText = self._coerceStringValue(remoteFileValue)
        if remoteFileText:
            normalized["remoteFile"] = remoteFileText

        rawSource = None
        for source in sources:
            if isinstance(source, dict):
                rawSource = source
                break
        if rawSource is not None:
            normalized["rawStatus"] = rawSource
        return normalized

    def _handlePrinterStatus(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        normalized = self._normalizeStatusSnapshot(payload)
        if not normalized:
            return
        rawStatus = normalized.pop("rawStatus", None)
        with self._statusLock:
            self._lastStatus = dict(normalized)
            if isinstance(rawStatus, dict):
                self._lastRawStatus = dict(rawStatus)
            elif isinstance(payload, dict):
                self._lastRawStatus = dict(payload)
            self._lastStatusTimestamp = time.monotonic()
        remoteFile = normalized.get("remoteFile")
        if remoteFile:
            try:
                remoteFileText = str(remoteFile)
            except Exception:
                remoteFileText = remoteFile
            if remoteFileText != self._lastRemoteFile and not self._jobActive:
                self._sawActivityDuringJob = False
            self._lastRemoteFile = remoteFileText
        self._recordCheckpointIfNeeded(normalized)
        self._logProgressIfNeeded(normalized)
        self._checkForCompletion(normalized)
        self._checkForPrinterError(normalized)

    def _recordCheckpointIfNeeded(self, status: Dict[str, Any]) -> None:
        if not self._jobActive or not self._activeJobMetadata:
            return
        percentValue = status.get("mc_percent")
        try:
            percentFloat = float(percentValue)
        except (TypeError, ValueError):
            return
        percentFloat = max(0.0, min(100.0, percentFloat))
        thresholds = (0, 33, 66, 100)
        pending = [value for value in thresholds if value not in self._capturedCheckpoints]
        if not pending:
            return
        with self._printerLock:
            printer = self._printerInstance
        if printer is None:
            return
        for checkpoint in pending:
            if percentFloat + 0.5 < checkpoint:
                continue
            try:
                snapshotPath = captureCameraSnapshot(printer, self.serial)
            except Exception as error:  # noqa: BLE001
                log.warning(
                    "[checkpoint] capture failed for %s at %d%%: %s",
                    self.serial,
                    checkpoint,
                    error,
                )
                continue
            targetDirectory = Path.home() / ".printmaster" / "bed-checkpoints" / self.serial / self._activeJobKey
            try:
                targetDirectory.mkdir(parents=True, exist_ok=True)
            except Exception as error:  # noqa: BLE001 - filesystem issues should not crash
                log.warning("[checkpoint] failed to prepare directory %s: %s", targetDirectory, error)
            targetPath = targetDirectory / f"pct_{checkpoint:03d}.jpg"
            storedPath = snapshotPath
            try:
                shutil.copy2(snapshotPath, targetPath)
                storedPath = targetPath
            except Exception as error:  # noqa: BLE001
                log.debug("[checkpoint] copy failed for %s: %s", targetPath, error)
            self._checkpointSnapshots[checkpoint] = storedPath
            self._capturedCheckpoints.add(checkpoint)
            log.info(
                "[checkpoint] stored capture for %s at %d%% → %s",
                self.serial,
                checkpoint,
                storedPath,
            )

    def _logProgressIfNeeded(self, status: Dict[str, Any]) -> None:
        percent = status.get("mc_percent")
        if percent is None:
            return
        bucket = 10 if percent >= 100 else percent // 10
        if bucket == self._lastProgressBucket:
            return
        self._lastProgressBucket = bucket
        state = status.get("gcode_state") or status.get("job_state")
        if state:
            log.info("Progress %s%% on %s (%s)", percent, self.serial, state)
        else:
            log.info("Progress %s%% on %s", percent, self.serial)

    def _resetBrakeJobState(self) -> None:
        self._activeJobMetadata = {}
        self._activeJobKey = "job"
        self._checkpointSnapshots.clear()
        self._capturedCheckpoints.clear()

    def _extractJobValue(self, metadata: Dict[str, Any], aliases: List[str]) -> Any:
        normalizedAliases = {self._normalizeKeyName(alias) for alias in aliases}
        return self._searchStatusValue(metadata, normalizedAliases)

    @staticmethod
    def _interpretBoolean(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return None

    def _sanitizeJobKey(self, candidate: Any) -> str:
        text = self._coerceStringValue(candidate) or "job"
        sanitized = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in text)
        return sanitized or "job"

    def _prepareBrakeMetadata(self, metadata: Dict[str, Any], fileName: Optional[str]) -> None:
        rawMetadata = dict(metadata) if isinstance(metadata, dict) else {}
        enableRaw = self._extractJobValue(rawMetadata, ["enable_brake_plate", "enableBrakePlate"])
        platesRaw = self._extractJobValue(rawMetadata, ["plates_requested", "platesRequested"])
        heightRaw = self._extractJobValue(rawMetadata, ["print_z_height", "printZHeight"])
        jobRaw = self._extractJobValue(rawMetadata, ["job_key", "jobKey", "fileName", "filename"])
        jobKeyCandidate = jobRaw or fileName or self._lastRemoteFile
        jobKey = self._sanitizeJobKey(jobKeyCandidate)
        enableFlag = self._interpretBoolean(enableRaw)
        platesValue = self._coerceIntValue(platesRaw) or 0
        heightValue = self._coerceFloatValue(heightRaw)
        self._activeJobKey = jobKey
        self._activeJobMetadata = {
            "raw": rawMetadata,
            "enableBrakePlate": bool(enableFlag) if enableFlag is not None else False,
            "platesRequested": max(0, int(platesValue)),
            "printZHeight": heightValue,
            "fileName": fileName,
            "jobKey": jobKey,
        }
        self._checkpointSnapshots.clear()
        self._capturedCheckpoints.clear()

    def _buildBrakeFlowContext(self) -> Optional[BrakeFlowContext]:
        if not self._activeJobMetadata:
            return None
        metadata = self._activeJobMetadata
        return BrakeFlowContext(
            serial=self.serial,
            ipAddress=self.ipAddress,
            jobKey=str(metadata.get("jobKey") or self._activeJobKey),
            enableBrakePlate=bool(metadata.get("enableBrakePlate")),
            platesRequested=int(metadata.get("platesRequested") or 0),
            printZHeight=metadata.get("printZHeight"),
            checkpointPaths=dict(self._checkpointSnapshots),
            metadata=dict(metadata.get("raw") or {}),
        )

    def _handleBrakeFailure(self, context: BrakeFlowContext) -> None:
        if not self._brakeFlowBlocked:
            payload = buildBrakeFlowErrorPayload(context)
            try:
                postReportError(payload)
            except Exception as error:  # noqa: BLE001
                log.warning("[brake] failed to report obstruction for %s: %s", self.serial, error)
        self._brakeFlowBlocked = True

    def _runBrakeDemoIfNeeded(self) -> None:
        context = self._buildBrakeFlowContext()
        self._resetBrakeJobState()
        if context is None:
            return
        if not context.shouldTrigger():
            log.debug(
                "[brake] skip brake demo for %s (plates=%s enable=%s)",
                self.serial,
                context.platesRequested,
                context.enableBrakePlate,
            )
            return
        if self._brakeFlowThread and self._brakeFlowThread.is_alive():
            log.debug("[brake] demo already running for %s", self.serial)
            return

        def _runner() -> None:
            try:
                if not self.runBrakeDemo(context, reportFailure=True):
                    log.warning("[brake] obstruction confirmed for %s", self.serial)
            except Exception as error:  # noqa: BLE001
                log.error("[brake] brake demo failed for %s: %s", self.serial, error, exc_info=True)
            finally:
                self._brakeFlowThread = None

        log.info("[brake] scheduling brake demo for %s job=%s", self.serial, context.normalizedJobKey())
        thread = threading.Thread(target=_runner, name=f"BrakeFlow-{self.serial}", daemon=True)
        self._brakeFlowThread = thread
        thread.start()

    def _checkForCompletion(self, status: Dict[str, Any]) -> None:
        rawPercent = status.get("mc_percent")
        try:
            percentFloat = float(rawPercent)
        except (TypeError, ValueError):
            percentFloat = None
        stateText = status.get("gcode_state") or status.get("job_state") or ""
        stateNormalized = str(stateText).strip().lower() if stateText is not None else ""
        completedStates = {"finish", "finished", "completed", "idle", "complete"}

        inProgress = False
        if percentFloat is not None and 0.0 < percentFloat < 100.0:
            inProgress = True
        elif stateNormalized and stateNormalized not in completedStates:
            inProgress = True

        if inProgress:
            self._sawActivityDuringJob = True
            self._jobActive = True

        isCompletePercent = percentFloat is not None and percentFloat >= 100.0
        isCompleteState = bool(stateNormalized) and stateNormalized in completedStates
        if not (isCompletePercent or isCompleteState):
            return
        if not self._sawActivityDuringJob:
            return
        if self._jobActive:
            log.info("Print completed on %s — ready for next job", self.serial)
        self._jobActive = False
        self._sawActivityDuringJob = False
        self._deleteRemoteFile()
        self._runBrakeDemoIfNeeded()

    def _checkForPrinterError(self, status: Dict[str, Any]) -> None:
        printer = self._printerInstance
        if printer is None:
            return
        code = self._readPrinterErrorCode(printer)
        if code is None:
            return
        normalizedCode = str(code).strip()
        if not normalizedCode or normalizedCode in {"0", "0000"}:
            return
        if normalizedCode == self._lastErrorCodeReported:
            return
        context = {"event": status, "status": status}
        self._collectAndReportBambuError(printer, context)
        self._lastErrorCodeReported = normalizedCode

    def _deleteRemoteFile(self) -> None:
        remoteFile = self._lastRemoteFile
        if not remoteFile:
            return
        printer = self._printerInstance
        if printer is None:
            return
        try:
            deleteHelper = getattr(bambuPrinter, "deleteRemoteFile", None)
            if callable(deleteHelper):
                if deleteHelper(printer, remoteFile):
                    log.info("Deleted remote file %s on %s", remoteFile, self.serial)
                else:
                    log.info("Skip delete for remote file %s on %s", remoteFile, self.serial)
        except Exception:
            log.info("Skip delete for remote file %s on %s", remoteFile, self.serial, exc_info=True)
        finally:
            self._lastRemoteFile = None

    def _readPrinterErrorCode(self, printer: Any) -> Optional[Any]:
        if printer is None:
            return None
        accessor = getattr(printer, "print_error_code", None)
        if accessor is not None:
            try:
                return accessor() if callable(accessor) else accessor
            except Exception:
                log.debug("print_error_code accessor failed", exc_info=True)
        if not self._printErrorCodeUnsupportedLogged:
            log.info("Printer %s does not expose print_error_code", self.serial)
            self._printErrorCodeUnsupportedLogged = True
        return None

    def _copyLastStatus(self) -> Dict[str, Any]:
        with self._statusLock:
            return dict(self._lastStatus)

    def _buildProgressSuffix(self) -> Optional[str]:
        lastStatus = self._copyLastStatus()
        if not lastStatus:
            return None
        segments: List[str] = []
        percent = lastStatus.get("mc_percent")
        if percent is not None:
            segments.append(f"{percent}%")
        state = lastStatus.get("gcode_state") or lastStatus.get("job_state")
        if state:
            segments.append(str(state))
        remaining = lastStatus.get("mc_remaining_time")
        if remaining is not None:
            segments.append(f"ETA {remaining}s")
        return " | ".join(segments) if segments else None

    def _collectAndReportBambuError(self, printer: Any, context: Dict[str, Any]) -> None:
        if printer is None:
            return
        try:
            codeValue = self._readPrinterErrorCode(printer)
            if codeValue is None:
                return
            normalizedCode = str(codeValue).strip()
            if not normalizedCode or normalizedCode in {"0", "0000"}:
                return
            payload: Dict[str, Any] = {
                "printerSerial": self.serial,
                "printerIpAddress": self.ipAddress,
                "recipientId": self.recipientIdValue or None,
                "errorCode": normalizedCode,
                "timestamp": _isoTimestamp(),
            }
            lastStatus = self._copyLastStatus()
            if lastStatus:
                payload["gcodeState"] = lastStatus.get("gcode_state") or lastStatus.get("job_state")
                if lastStatus.get("mc_percent") is not None:
                    payload["progressPercent"] = lastStatus.get("mc_percent")
            rawEvent = context.get("event")
            if rawEvent is None and isinstance(self._lastRawStatus, dict):
                rawEvent = self._lastRawStatus
            if rawEvent is not None:
                payload["printerEvent"] = rawEvent
            if normalizedCode.upper().startswith("HMS_"):
                payload["hmsCode"] = normalizedCode
            postReportError(payload)
            log.error("Bambu error on %s: code=%s state=%s", self.serial, normalizedCode, payload.get("gcodeState"))
            self._lastErrorCodeReported = normalizedCode
        except Exception:
            log.debug("Error while reporting Bambu failure for %s", self.serial, exc_info=True)

    def _executeCommand(self, printer: Any, command: Dict[str, Any]) -> Tuple[str, str]:
        rawCommandType = str(command.get("commandType") or "").strip()
        metadataValue = command.get("metadata")
        metadata = metadataValue if isinstance(metadataValue, dict) else {}
        message = ""

        def callPrinterMethod(name: str, *args: Any, **kwargs: Any) -> None:
            method = getattr(printer, name, None)
            if callable(method):
                method(*args, **kwargs)
                return
            fallbackNames = {"cancel_print": ["stop_print"]}.get(name, [])
            for fallbackName in fallbackNames:
                fallbackMethod = getattr(printer, fallbackName, None)
                if callable(fallbackMethod):
                    fallbackMethod(*args, **kwargs)
                    return
            raise RuntimeError(f"Printer method {name} is unavailable")

        def sendGcode(gcode: str) -> None:
            sender = getattr(printer, "send_gcode", None)
            if callable(sender):
                sender(gcode)
                return
            raise RuntimeError("send_gcode is unavailable in bambulabs_api")

        def sendControlPayload(payload: Dict[str, Any]) -> None:
            # Legg til sequence_id i hver kontroll-payload
            payload = self._addSequenceIdToPayload(payload)
            controlMethod = getattr(printer, "send_control", None)
            if callable(controlMethod):
                controlMethod(payload)
                return
            sendRequest = getattr(printer, "send_request", None)
            if callable(sendRequest):
                sendRequest(payload)
                return
            raise RuntimeError("No API transport available for control payload (API-only policy)")

        camelCaseConverted = re.sub(r"(?<!^)(?=[A-Z])", "_", rawCommandType)
        normalizedType = camelCaseConverted.replace("-", "_").lower()

        def _read_last_state_flags() -> tuple[bool, bool, str]:
            """
            Returns: (paused, busy, state_text)
              paused: printer er i pause-aktig tilstand
              busy:   printer har/har hatt aktivitet (kjører el. ikke ferdig)
            """
            st = self._copyLastStatus()
            state_text = str(st.get("gcode_state") or st.get("job_state") or "").strip().lower()
            paused = state_text in {"pause", "paused", "pausing"}
            # Busy hvis prosent >0 og <100, eller state antyder pågående aktivitet
            pct = st.get("mc_percent")
            try:
                pctf = float(pct)
            except (TypeError, ValueError):
                pctf = None
            active_states = {
                "start",
                "starting",
                "prepare",
                "preparing",
                "heat",
                "heating",
                "print",
                "printing",
                "run",
                "running",
                "work",
                "busy",
                "homing",
            }
            busy = (pctf is not None and 0.0 < pctf < 100.0) or (state_text in active_states)
            return paused, busy, state_text

        if normalizedType in {"heat", "setheat"}:
            nozzleTemp = metadata.get("nozzleTemp")
            bedTemp = metadata.get("bedTemp")
            if nozzleTemp is None and bedTemp is None:
                raise ValueError("heat requires nozzleTemp and/or bedTemp")
            if nozzleTemp is not None:
                try:
                    callPrinterMethod("set_nozzle_temperature", float(nozzleTemp))
                except RuntimeError:
                    sendGcode(f"M104 S{int(float(nozzleTemp))}")
            if bedTemp is not None:
                try:
                    callPrinterMethod("set_bed_temperature", float(bedTemp))
                except RuntimeError:
                    sendGcode(f"M140 S{int(float(bedTemp))}")
            message = f"Heating nozzle={nozzleTemp} bed={bedTemp}"
        elif normalizedType in {"cool", "cooldown"}:
            try:
                callPrinterMethod("set_nozzle_temperature", 0)
                callPrinterMethod("set_bed_temperature", 0)
            except RuntimeError:
                sendGcode("M104 S0")
                sendGcode("M140 S0")
            message = "Cooling started"
        elif normalizedType in {"pause", "resume", "stop", "stop_print", "cancel"}:
            # aktivitet/tilstand-guard
            paused, busy, state_txt = _read_last_state_flags()
            if normalizedType == "pause" and paused:
                return "completed", "Already paused"
            if normalizedType == "resume":
                if not paused and busy:
                    return "completed", "Already running"
                if not paused and not busy:
                    return "completed", "Nothing to resume"
            if normalizedType in {"stop", "stop_print", "cancel"} and not busy and not paused:
                return "completed", "Nothing to stop"

            controlConfig = {
                "pause": (["pause_printer"], "pause", "Paused"),
                "resume": (["resume_printer"], "resume", "Resumed"),
                "stop": (["stop_printer"], "stop", "Stopped"),
                "stop_print": (["stop_printer"], "stop", "Stopped"),
                "cancel": (["stop_printer"], "stop", "Cancelled"),
            }
            methods, transportCmd, okMessage = controlConfig[normalizedType]

            used = None
            for meth in methods:
                method = getattr(printer, meth, None)
                if callable(method):
                    log.info("Control %s → printer.%s()", normalizedType, meth)
                    method()
                    used = meth
                    break
            if not used:
                # Bygg korrekt MQTT payload format
                payload = {
                    "print": {
                        "sequence_id": self._getNextSequenceId(),
                        "command": transportCmd
                    }
                }

                controlSender = getattr(printer, "send_control", None)
                if callable(controlSender):
                    log.info("Control %s → send_control(%s)", normalizedType, payload)
                    # Send kun "print" delen av payload
                    controlSender(payload["print"])
                else:
                    requestSender = getattr(printer, "send_request", None)
                    if callable(requestSender):
                        log.info("Control %s → send_request(%s)", normalizedType, payload)
                        # send_request forventer hele payload
                        requestSender(payload)
                    else:
                        raise RuntimeError("No API transport available for control payload")
            message = okMessage
        elif normalizedType in {"camera", "camera_on", "camera_off"}:
            desiredState: Optional[bool]
            if normalizedType == "camera_on":
                desiredState = True
            elif normalizedType == "camera_off":
                desiredState = False
            else:
                stateValue = metadata.get("cameraState")
                desiredState = None
                if isinstance(stateValue, bool):
                    desiredState = stateValue
                elif isinstance(stateValue, str):
                    normalizedState = stateValue.strip().lower()
                    if normalizedState == "on":
                        desiredState = True
                    elif normalizedState == "off":
                        desiredState = False
                if desiredState is None:
                    raise ValueError("camera requires metadata.cameraState to be 'on'/'off' or boolean")
            if desiredState is False:
                try:
                    callPrinterMethod("camera_off")
                except RuntimeError as error:
                    raise RuntimeError("camera_off is unavailable") from error
                message = "Camera disabled"
            else:
                snapshotPath = captureCameraSnapshot(printer, self.serial)
                message = f"Camera snapshot saved to {snapshotPath}"
                if self._postCameraSnapshot(snapshotPath):
                    message = (
                        f"Camera snapshot saved to {snapshotPath} and sent to Base44"
                    )
        elif normalizedType in {"set_speed", "speed", "setspeed"}:
            percentValue = metadata.get("percent") or metadata.get("speedPercent")
            if percentValue is None:
                raise ValueError("set_speed requires percent metadata")
            try:
                callPrinterMethod("set_print_speed_factor", float(percentValue))
            except RuntimeError:
                clamped = max(10, min(300, int(round(float(percentValue)))))
                sendGcode(f"M220 S{clamped}")
                percentValue = clamped
            message = f"Speed set to {percentValue}%"
        elif normalizedType in {"set_fan", "fan", "setfan"}:
            percentValue = metadata.get("percent") or metadata.get("fanPercent")
            if percentValue is None:
                raise ValueError("set_fan requires percent metadata")
            try:
                callPrinterMethod("set_fan_speed", float(percentValue))
            except RuntimeError:
                pwmValue = max(0, min(255, int(round(float(percentValue) * 255.0 / 100.0))))
                sendGcode(f"M106 S{pwmValue}")
            message = f"Fan set to {percentValue}%"
        elif normalizedType == "start_print":
            fileName = metadata.get("fileName")
            if not fileName:
                raise ValueError("start_print requires metadata.fileName")
            if self._brakeFlowBlocked:
                message = "Brake flow blocked: obstruction pending manual clear"
                log.warning("[brake] blocking start_print on %s: %s", self.serial, message)
                return "failed", message
            self._prepareBrakeMetadata(metadata, fileName)
            plateIndex = metadata.get("plateIndex")
            paramPath = metadata.get("paramPath")
            useAms = metadata.get("useAms")
            # --- NEW: alltid bruk API-stien (sikrer timelapse-aktivering) ---
            def _norm_key(s: str) -> str:
                # Normaliserer nøkler: fjerner alt utenom bokstaver og tall
                # "enable_timelapse" -> "enabletimelapse"
                # "enableTimeLapse" -> "enabletimelapse"
                return "".join(ch for ch in str(s).lower() if ch.isalnum())

            def _search_bool(obj) -> bool:
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        nk = _norm_key(k)
                        if nk in ("enabletimelapse", "timelapseenabled"):
                            return bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("1","true","yes","on")
                        if _search_bool(v):
                            return True
                elif isinstance(obj, list):
                    return any(_search_bool(x) for x in obj)
                return False

            def _search_str(obj, keys=("timelapsedirectory","timelapsepath")):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        nk = _norm_key(k)
                        if nk in keys:
                            try:
                                return str(v)
                            except Exception:
                                return None
                        sv = _search_str(v, keys)
                        if sv:
                            return sv
                elif isinstance(obj, list):
                    for x in obj:
                        sv = _search_str(x, keys)
                        if sv:
                            return sv
                return None

            # DEBUG: Logg RAW metadata FØRST
            log.info("[timelapse] ===== START TIMELAPSE DEBUG =====")
            log.info("[timelapse] RAW metadata mottatt (type=%s): %s", type(metadata), metadata)

            # Søk etter timelapse i metadata
            enable_timelapse = _search_bool(metadata or {})
            tl_dir_raw = _search_str(metadata or {})
            tl_dir = Path(tl_dir_raw).expanduser() if tl_dir_raw else None

            # DEBUG: Logg resultat av søk
            log.info("[timelapse] enable_timelapse funnet: %s", enable_timelapse)
            log.info("[timelapse] tl_dir_raw funnet: %s", tl_dir_raw)
            log.info("[timelapse] tl_dir etter parsing: %s", tl_dir)

            # DEBUG: Sjekk om unencryptedData finnes
            if isinstance(metadata, dict):
                if "unencryptedData" in metadata:
                    log.info("[timelapse] metadata inneholder 'unencryptedData': %s", metadata["unencryptedData"])
                    udata = metadata.get("unencryptedData")
                    if isinstance(udata, dict) and "enable_timelapse" in udata:
                        log.info("[timelapse] FUNNET enable_timelapse i unencryptedData: %s", udata.get("enable_timelapse"))
                else:
                    log.warning("[timelapse] printer %s metadata inneholder IKKE 'unencryptedData'", self.serial)
            log.info("[timelapse] ===== SLUTT TIMELAPSE DEBUG =====")

            options = bambuPrinter.BambuPrintOptions(
                ipAddress=self.ipAddress,
                serialNumber=self.serial,
                accessCode=self.accessCode,
                useAms=useAms,
                plateIndex=plateIndex,
                enableTimeLapse=enable_timelapse,
                timeLapseDirectory=tl_dir,
            )

            log.info("[timelapse] BambuPrintOptions opprettet med enableTimeLapse=%s, timeLapseDirectory=%s",
                    enable_timelapse, tl_dir)
            result = bambuPrinter.startPrintViaApi(
                ip=self.ipAddress,
                serial=self.serial,
                accessCode=self.accessCode,
                uploaded_name=str(fileName),
                plate_index=plateIndex,
                param_path=paramPath,
                options=options,
                job_metadata=metadata if isinstance(metadata, dict) else None,
            )
            acknowledged = result.get("acknowledged")
            message = (
                f"Print started (acknowledged={acknowledged})"
                if acknowledged is not None
                else "Print started"
            )
            self._jobActive = True
            try:
                self._lastRemoteFile = str(fileName)
            except Exception:
                self._lastRemoteFile = None
            self._sawActivityDuringJob = False
        elif normalizedType in {"home", "light_on", "light_off", "lightoff", "lighton", "move", "jog", "load_filament", "unload_filament", "sendgcode"}:
            if normalizedType == "home":
                sendGcode("G28")
                message = "Homing"
            elif normalizedType in {"move", "jog"}:
                axisParts: List[str] = []
                for axisKey in ("x", "y", "z", "e"):
                    if axisKey in metadata and metadata[axisKey] is not None:
                        axisParts.append(f"{axisKey.upper()}{float(metadata[axisKey])}")
                feedrate = metadata.get("feedrate")
                if feedrate is not None:
                    axisParts.append(f"F{int(float(feedrate))}")
                if not axisParts:
                    raise ValueError("move requires at least one axis or feedrate")
                sendGcode("G1 " + " ".join(axisParts))
                message = "Moved"
            elif normalizedType == "sendgcode":
                gcodeValue = metadata.get("gcode")
                if not gcodeValue:
                    raise ValueError("sendGcode requires metadata.gcode")
                sendGcode(str(gcodeValue))
                message = "G-code sent"
            elif normalizedType in {"light_on", "lightoff", "light_off", "lighton"}:
                isOn = normalizedType in {"light_on", "lighton"}
                sendControlPayload({"command": "light", "param": {"on": isOn}})
                message = "Light on" if isOn else "Light off"
            elif normalizedType in {"load_filament", "unload_filament"}:
                slotValue = int(metadata.get("slot", 1))
                commandName = "load_filament" if normalizedType == "load_filament" else "unload_filament"
                sendControlPayload({"command": commandName, "param": {"slot": slotValue}})
                message = ("Load" if commandName == "load_filament" else "Unload") + f" filament slot {slotValue}"
        else:
            raise ValueError(f"Unsupported commandType: {rawCommandType}")

        return "completed", message


def forceRecipientPoll(recipientId: str) -> None:
    normalized = str(recipientId or "").strip()
    if not normalized:
        return
    log.info("Triggering immediate command poll for recipient %s.", normalized)
    with _recipientRoutersLock:
        router = _recipientRouters.get(normalized)
    if router is None or not router.isActive:
        return
    router.pollOnce(suppressCheckLog=False)


force_recipient_poll = forceRecipientPoll


def _isoTimestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class UnsupportedControlEndpointError(RuntimeError):
    """Raised when the backend does not expose the expected control endpoint."""


