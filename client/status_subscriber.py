"""Background subscriber for streaming status updates from Bambu printers."""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import requests

from .bambuPrinter import extractStateText, looksLikeAmsFilamentConflict, safeDisconnectPrinter
from .base44_client import postReportError, postUpdateStatus

# Import event reporting modules
try:
    from .event_reporter import EventReporter
    from .hms_handler import parse_hms_error, capture_camera_frame_from_printer
    _event_reporting_available = True
except ImportError:
    _event_reporting_available = False
    EventReporter = None

# Import config manager
try:
    from .config_manager import get_config_manager
    _config_manager_available = True
except ImportError:
    _config_manager_available = False

# Reduce noise from third-party SDK logger
logging.getLogger("bambulabs_api").setLevel(logging.WARNING)


try:  # pragma: no cover - dependency handled dynamically in tests
    _bambuModule = importlib.import_module("bambulabs_api")
    _printerClass = getattr(_bambuModule, "Printer", None)
except ImportError:  # pragma: no cover - surfaced via callbacks at runtime
    _bambuModule = None
    _printerClass = None


@dataclass(frozen=True)
class PrinterCredentials:
    """Normalized credentials for connecting to a printer."""

    ipAddress: str
    serialNumber: str
    accessCode: str
    nickname: Optional[str] = None


class BambuStatusSubscriber:
    """Manage live status subscriptions across multiple Bambu printers."""

    def __init__(
        self,
        onUpdate: Callable[[Dict[str, Any], Dict[str, Any]], None],
        onError: Callable[[str, Dict[str, Any]], None],
        *,
        logger: Optional[logging.Logger] = None,
        pollInterval: float = 1.0,
        heartbeatInterval: float = 5.0,
        reconnectDelay: float = 3.0,
        baseUrl: Optional[str] = None,
        apiKey: Optional[str] = None,
    ) -> None:
        self.onUpdate = onUpdate
        self.onError = onError
        self.log = logger or logging.getLogger(__name__)
        self.statusDebugEnabled = (
            str(os.getenv("PRINTMASTER_STATUS_DEBUG", "")).strip().lower()
            not in ("", "0", "false", "off")
        )
        self.pollInterval = max(0.5, float(pollInterval))
        self.heartbeatInterval = max(1.0, float(heartbeatInterval))
        self.reconnectDelay = max(1.0, float(reconnectDelay))
        self._threads: Dict[str, threading.Thread] = {}
        self._stops: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.errorCountBySerial: Dict[str, int] = {}
        self.errorCountLock = threading.Lock()
        self.logEvery = 50

        # Get configuration from config manager (prefer) or environment variables (fallback)
        config_base_url = None
        config_api_key = None
        config_recipient_id = None

        if _config_manager_available:
            try:
                config = get_config_manager()
                config_base_url = config.get_backend_url()
                config_api_key = config.get_api_key()
                config_recipient_id = config.get_recipient_id()
            except Exception as e:
                self.log.debug(f"Could not load config from config manager: {e}")

        # Use config values (preferred) or parameters (fallback) or env (last resort)
        self.base_url = config_base_url or baseUrl or os.getenv("BASE44_API_URL", "").strip()
        self.api_key = config_api_key or apiKey or os.getenv("BASE44_API_KEY", "").strip() or os.getenv("BASE44_FUNCTIONS_API_KEY", "").strip()
        self.defaultRecipientId = config_recipient_id or os.getenv("BASE44_RECIPIENT_ID", "").strip()

        # Initialize event reporter if credentials available
        self.event_reporter: Optional[EventReporter] = None

        if _event_reporting_available and self.base_url and self.api_key and self.defaultRecipientId:
            try:
                self.log.info("🔧 Initializing EventReporter in status_subscriber...")
                self.event_reporter = EventReporter(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    recipient_id=self.defaultRecipientId
                )
                self.log.info("✅ EventReporter initialized successfully")
            except Exception as e:
                self.log.warning(f"Failed to initialize event reporter: {e}")
        elif not _event_reporting_available:
            self.log.debug("Event reporting modules not available")
        elif not _config_manager_available:
            self.log.debug("Event reporting not configured (config manager not available)")
        else:
            self.log.warning("⚠️  EventReporter NOT initialized (missing credentials)")
            self.log.warning(f"   base_url: {'✅' if self.base_url else '❌'}")
            self.log.warning(f"   api_key: {'✅' if self.api_key else '❌'}")
            self.log.warning(f"   recipient_id: {'✅' if self.defaultRecipientId else '❌'}")

        # Track reported HMS errors to avoid duplicates
        self.reported_hms_errors: Dict[str, Set[str]] = {}
        self.hms_errors_lock = threading.Lock()

        # Track last status update time per printer for periodic reporting
        self.last_status_report_time: Dict[str, float] = {}
        self.status_report_lock = threading.Lock()
        self.status_report_interval = 300.0  # 5 minutes

        # Track previous printer states for pause detection
        self.previous_states: Dict[str, Optional[str]] = {}
        self.previous_states_lock = threading.Lock()

        # Camera capture tracking
        self.last_camera_capture: Dict[str, float] = {}  # {printer_serial: timestamp}
        self.camera_capture_lock = threading.Lock()
        self.camera_capture_interval_active = 30.0   # 30 seconds during print
        self.camera_capture_interval_idle = 300.0    # 5 minutes when idle

    def startAll(self, printers: Iterable[Dict[str, Any]]) -> None:
        """Start worker threads for each printer configuration."""

        for printerConfig in printers:
            self.startPrinter(printerConfig)

    def start_all(self, printers: Iterable[Dict[str, Any]]) -> None:
        """Snake_case alias maintained for compatibility with legacy callers."""

        self.startAll(printers)

    def startPrinter(self, printerConfig: Dict[str, Any]) -> None:
        serial = str(printerConfig.get("serialNumber") or "").strip()
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        accessCode = str(printerConfig.get("accessCode") or "").strip()
        if not serial or not ipAddress or not accessCode:
            sanitizedMessage = "Missing printer credentials (ip/serial/access)"
            self.onError(sanitizedMessage, dict(printerConfig))
            return

        self.defaultRecipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip()

        if not self._pingHost(ipAddress, 1000):
            if self._shouldLogConnectionFailure(serial):
                sanitizedMessage = f"Printer unreachable at {ipAddress}"
                self.onError(sanitizedMessage, dict(printerConfig))
            return

        with self._lock:
            if serial in self._stops:
                return

            stopEvent = threading.Event()
            workerThread = threading.Thread(
                target=self._worker,
                args=(dict(printerConfig), stopEvent),
                name=f"BambuStatusSubscriber-{serial}",
                daemon=True,
            )
            self._stops[serial] = stopEvent
            self._threads[serial] = workerThread
            workerThread.start()

    def start_printer(self, printerConfig: Dict[str, Any]) -> None:
        self.startPrinter(printerConfig)

    def stopPrinter(self, serialNumber: str) -> None:
        serial = str(serialNumber or "").strip()
        if not serial:
            return
        with self._lock:
            stopEvent = self._stops.pop(serial, None)
            workerThread = self._threads.pop(serial, None)
        if stopEvent:
            stopEvent.set()
        if workerThread and workerThread.is_alive():
            workerThread.join(timeout=self.heartbeatInterval)
        self._resetConnectionFailures(serial)

    def stop_printer(self, serialNumber: str) -> None:
        self.stopPrinter(serialNumber)

    def stopAll(self) -> None:
        with self._lock:
            stopEvents = list(self._stops.values())
            workerThreads = list(self._threads.values())
            self._stops.clear()
            self._threads.clear()

        for event in stopEvents:
            event.set()
        for workerThread in workerThreads:
            if workerThread.is_alive():
                workerThread.join(timeout=self.heartbeatInterval)

    def stop_all(self) -> None:
        self.stopAll()

    def _worker(self, printerConfig: Dict[str, Any], stopEvent: threading.Event) -> None:
        serial = str(printerConfig.get("serialNumber") or "").strip()
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        accessCode = str(printerConfig.get("accessCode") or "").strip()
        nickname = printerConfig.get("nickname")

        if _printerClass is None:
            self.onError("bambulabs_api.Printer is unavailable", dict(printerConfig))
            return

        lastSnapshot: Optional[Dict[str, Any]] = None
        lastEmit = 0.0

        while not stopEvent.is_set():
            printerInstance = None
            try:
                printerInstance = _printerClass(ipAddress, accessCode, serial)
                self._connectPrinter(printerInstance)
                self._resetConnectionFailures(serial)
                printerMetadata = self._fetchPrinterMetadata(printerInstance)
                lastBase44Comparable: Optional[Dict[str, Any]] = None
                lastBase44Emit = 0.0
                lastErrorComparable: Optional[Dict[str, Any]] = None
                lastErrorEmit = 0.0

                while not stopEvent.is_set():
                    resolvedApiKey = self._resolveBase44ApiKey(printerConfig)
                    if resolvedApiKey:
                        self._ensureEnvironmentValue("BASE44_FUNCTIONS_API_KEY", resolvedApiKey)
                        self._ensureEnvironmentValue("BASE44_API_KEY", resolvedApiKey)

                    statusPayload = self._collectSnapshot(printerInstance, printerConfig, printerMetadata)
                    statusPayload["printerSerial"] = serial
                    statusPayload["printerIp"] = ipAddress
                    statusPayload["nickname"] = nickname
                    statusPayload["status"] = statusPayload.get("status") or "update"

                    # ============================================
                    # ENHANCED HMS ERROR DETECTION AND REPORTING
                    # ============================================
                    if self.event_reporter:
                        # Get raw state payload for deep HMS inspection
                        rawState = statusPayload.get("rawStatePayload")

                        # Collect all HMS errors from different possible locations
                        hms_errors = []

                        # 1. Check normalized hmsCode (existing method)
                        normalizedHmsCode = statusPayload.get("hmsCode")
                        if normalizedHmsCode:
                            hms_errors.append(normalizedHmsCode)
                            self.log.info(f"🔍 Found HMS in normalized field: {normalizedHmsCode}")

                        # 2. Check raw state payload for HMS in various field names
                        if isinstance(rawState, dict):
                            possible_hms_fields = [
                                'hms',           # Standard field
                                'hms_list',      # Alternative
                                'hmsErrors',     # CamelCase variant
                                'errors',        # Generic
                                'alarm',         # Older firmware
                                'mc_hms',        # Machine HMS
                                'print_error',   # Print error
                                'hms_code',      # Direct code field
                            ]

                            for field in possible_hms_fields:
                                if field in rawState:
                                    value = rawState.get(field)
                                    self.log.info(f"🔍 Found HMS field '{field}': {value}")

                                    # Handle HMS as list
                                    if isinstance(value, list) and len(value) > 0:
                                        for item in value:
                                            # Item can be string or dict
                                            if isinstance(item, str) and item:
                                                hms_errors.append(item)
                                            elif isinstance(item, dict):
                                                # Try to extract code from dict
                                                code = (
                                                    item.get('code') or
                                                    item.get('hms_code') or
                                                    item.get('error_code') or
                                                    item.get('id') or
                                                    str(item)
                                                )
                                                if code:
                                                    hms_errors.append(str(code))

                                    # Handle HMS as string
                                    elif isinstance(value, str) and value:
                                        hms_errors.append(value)

                                    # Handle HMS as dict
                                    elif isinstance(value, dict):
                                        # Try nested errors
                                        if 'errors' in value:
                                            nested = value['errors']
                                            if isinstance(nested, list):
                                                hms_errors.extend([str(e) for e in nested if e])
                                        elif 'list' in value:
                                            nested = value['list']
                                            if isinstance(nested, list):
                                                hms_errors.extend([str(e) for e in nested if e])

                        # ============================================
                        # LOG HMS ERROR DETECTION RESULTS
                        # ============================================
                        if hms_errors:
                            self.log.warning("⚠️  ⚠️  ⚠️  HMS ERRORS DETECTED! ⚠️  ⚠️  ⚠️")
                            self.log.warning(f"   Printer: {serial}")
                            self.log.warning(f"   Error Count: {len(hms_errors)}")
                            self.log.warning(f"   HMS Codes: {hms_errors}")
                            self.log.warning("=" * 80)

                            # Process each unique HMS error
                            unique_errors = list(set(hms_errors))  # Remove duplicates
                            for hms_code in unique_errors:
                                try:
                                    self.log.info(f"🚨 Processing HMS error: {hms_code}")
                                    self._handle_hms_error(hms_code, serial, ipAddress, printerInstance)
                                except Exception as e:
                                    self.log.error(f"❌ HMS error handling failed for {hms_code}: {e}")
                        else:
                            self.log.debug(f"✅ No HMS errors detected for {serial}")

                        # Periodic status updates (every 5 minutes by default)
                        current_time = time.monotonic()
                        with self.status_report_lock:
                            last_report_time = self.last_status_report_time.get(serial, 0)
                            if current_time - last_report_time >= self.status_report_interval:
                                try:
                                    self._report_status_update(statusPayload, serial, ipAddress)
                                    self.last_status_report_time[serial] = current_time
                                except Exception as e:
                                    self.log.debug(f"Status update reporting failed: {e}")

                        # ============================================
                        # UNEXPECTED PAUSE DETECTION FOR HMS ERRORS
                        # ============================================
                        if self.event_reporter:
                            try:
                                gcode_state = statusPayload.get('gcodeState')

                                # Get previous state (thread-safe)
                                with self.previous_states_lock:
                                    previous_state = self.previous_states.get(serial)

                                # Detect unexpected pause (state transition to PAUSE)
                                if gcode_state == 'PAUSE':
                                    self.log.info(f"🔍 Printer {serial} is PAUSED")

                                    # Check if this is a NEW pause (state changed from non-paused to paused)
                                    if previous_state and previous_state != 'PAUSE':
                                        self.log.warning("⚠️  UNEXPECTED PAUSE DETECTED!")
                                        self.log.warning(f"   Printer: {serial}")
                                        self.log.warning(f"   Previous state: {previous_state}")
                                        self.log.warning(f"   Current state: PAUSE")

                                        # Check if pause was user-initiated or error-caused
                                        self._check_pause_reason(serial, ipAddress, printerInstance, statusPayload)

                                # Update previous state (thread-safe)
                                with self.previous_states_lock:
                                    self.previous_states[serial] = gcode_state

                            except Exception as e:
                                self.log.debug(f"Pause detection failed: {e}")

                    # ============================================
                    # CAMERA IMAGE CAPTURE AND UPLOAD
                    # ============================================
                    # Check if we should capture camera image now
                    should_capture = self._should_capture_camera_image(serial, statusPayload)

                    if should_capture and _event_reporting_available:
                        try:
                            # Capture image to file with extensive logging
                            # Pass access code directly from worker context (more efficient than config lookup)
                            image_file_path = self._capture_camera_image_to_file(serial, ipAddress, accessCode)

                            if image_file_path:
                                # Read the saved image file
                                with open(image_file_path, 'rb') as f:
                                    image_data = f.read()

                                self.log.info(f"   ✅ Image captured: {len(image_data)} bytes")

                                # Encode to base64
                                import base64
                                from datetime import datetime
                                camera_image_base64 = base64.b64encode(image_data).decode('utf-8')
                                camera_timestamp = datetime.utcnow().isoformat() + 'Z'

                                self.log.info(f"   📦 Image encoded: {len(camera_image_base64)} chars")

                                # Add camera data to status payload
                                statusPayload['cameraImage'] = camera_image_base64
                                statusPayload['cameraImageTimestamp'] = camera_timestamp
                                statusPayload['cameraImageSize'] = len(image_data)

                                self.log.info(f"   📷 Camera image ready for upload")
                            else:
                                self.log.debug(f"⚠️  No camera image captured (see detailed logs above)")

                        except Exception as e:
                            self.log.warning(f"⚠️  Camera capture failed: {e}")
                            import traceback
                            self.log.warning(f"   Traceback:\n{traceback.format_exc()}")
                            # Don't fail status update if camera fails

                    base44Package = self._buildBase44Payloads(statusPayload, printerConfig, resolvedApiKey)
                    if base44Package is not None:
                        (
                            updatePayload,
                            updateComparable,
                            errorPayload,
                            errorComparable,
                        ) = base44Package

                        if updatePayload and updateComparable is not None:
                            shouldSendUpdate = False
                            if self._payloadsDiffer(lastBase44Comparable, updateComparable):
                                shouldSendUpdate = True
                            elif time.monotonic() - lastBase44Emit >= self.heartbeatInterval:
                                shouldSendUpdate = True

                            if shouldSendUpdate:
                                try:
                                    postUpdateStatus(updatePayload)
                                except Exception as error:
                                    self._logBase44Failure("update", error)
                                else:
                                    lastBase44Comparable = dict(updateComparable)
                                    lastBase44Emit = time.monotonic()

                        if errorPayload and errorComparable is not None:
                            shouldSendError = False
                            if self._payloadsDiffer(lastErrorComparable, errorComparable):
                                shouldSendError = True
                            elif time.monotonic() - lastErrorEmit >= self.heartbeatInterval:
                                shouldSendError = True

                            if shouldSendError:
                                try:
                                    postReportError(errorPayload)
                                except Exception as error:
                                    self._logBase44Failure("error", error)
                                else:
                                    lastErrorComparable = dict(errorComparable)
                                    lastErrorEmit = time.monotonic()

                    emitNow = False
                    if lastSnapshot is None:
                        emitNow = True
                    elif self._statusChanged(lastSnapshot, statusPayload):
                        emitNow = True
                    elif time.monotonic() - lastEmit >= self.heartbeatInterval:
                        emitNow = True

                    if emitNow:
                        lastSnapshot = dict(statusPayload)
                        lastEmit = time.monotonic()
                        try:
                            self.onUpdate(dict(statusPayload), dict(printerConfig))
                        except Exception:  # pragma: no cover - consumer responsibility
                            self.log.exception("Status update callback failed")

                    if stopEvent.wait(self.pollInterval):
                        break

            except Exception as error:  # noqa: BLE001 - ensure resiliency in background threads
                if stopEvent.is_set():
                    break
                sanitizedMessage = self._sanitizeErrorMessage(str(error), accessCode)
                if self._shouldLogConnectionFailure(serial):
                    self.onError(sanitizedMessage, dict(printerConfig))
                stopEvent.wait(self.reconnectDelay * 2)
            finally:
                if printerInstance is not None:
                    safeDisconnectPrinter(printerInstance)

    def _shouldLogConnectionFailure(self, serial: str) -> bool:
        key = serial or "unknown"
        with self.errorCountLock:
            failureCount = self.errorCountBySerial.get(key, 0) + 1
            self.errorCountBySerial[key] = failureCount
        return failureCount == 1 or failureCount % self.logEvery == 0

    def _resetConnectionFailures(self, serial: str) -> None:
        key = serial or "unknown"
        with self.errorCountLock:
            if key in self.errorCountBySerial:
                self.errorCountBySerial.pop(key, None)

    def _pingHost(self, ipAddress: str, timeoutMillis: int) -> bool:
        if not ipAddress:
            return False
        pingExecutable = shutil.which("ping")
        if not pingExecutable:
            return True
        systemName = platform.system().lower()
        timeoutSeconds = max(1, int(max(timeoutMillis, 100) / 1000))
        if "windows" in systemName:
            command = [
                pingExecutable,
                "-n",
                "1",
                "-w",
                str(max(timeoutMillis, 100)),
                ipAddress,
            ]
        else:
            command = [
                pingExecutable,
                "-c",
                "1",
                "-W",
                str(timeoutSeconds),
                ipAddress,
            ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return True

    def _connectPrinter(self, printer: Any) -> None:
        # Extract serial for logging if available
        serialForLogging = getattr(printer, "serial", None) or getattr(printer, "serial_number", None) or "N/A"

        mqttStart = getattr(printer, "mqtt_start", None)
        if callable(mqttStart):
            try:
                self.log.info(
                    "[PRINTER_COMM] MQTT Status Subscription Started",
                    extra={
                        "method": "MQTT",
                        "protocol": "MQTT_TLS",
                        "port": 8883,
                        "serial": serialForLogging,
                        "topic": f"device/{serialForLogging}/report",
                        "action": "mqtt_subscribe_status",
                        "comm_direction": "printer_to_client"
                    }
                )
                startTime = time.perf_counter()
                mqttStart()
                self.log.info(
                    "[status] mqtt_start() ok in %.3fs",
                    time.perf_counter() - startTime,
                )
            except Exception as error:  # pragma: no cover - surface via callbacks
                raise RuntimeError(f"Unable to start printer MQTT: {error}") from error

        connectMethod = getattr(printer, "connect", None)
        if callable(connectMethod):
            try:
                connectStartTime = time.perf_counter()
                connectMethod()
                self.log.info(
                    "[status] connect() ok in %.3fs",
                    time.perf_counter() - connectStartTime,
                )
            except Exception as error:  # pragma: no cover - surface via callbacks
                raise RuntimeError(f"Unable to connect printer: {error}") from error

        try:
            from . import bambuPrinter as _bp

            wait = getattr(_bp, "_waitForMqttReady", None)
            if callable(wait):
                readinessStartTime = time.perf_counter()
                wait(printer, timeout=15.0)
                self.log.info(
                    "[status] readiness ok in %.3fs",
                    time.perf_counter() - readinessStartTime,
                )
        except Exception:  # pragma: no cover - readiness wait is best effort
            pass

    def _collectSnapshot(
        self,
        printer: Any,
        printerConfig: Dict[str, Any],
        printerMetadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        statePayload: Any = None
        percentagePayload: Any = None
        gcodePayload: Any = None

        try:
            statePayload = printer.get_state()
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            self.log.debug("get_state failed", exc_info=error)

        # ============================================
        # RAW STATUS DATA LOGGING FOR HMS DEBUG
        # ============================================
        if isinstance(statePayload, dict):
            serial = printerConfig.get("serialNumber", "unknown")
            ip = printerConfig.get("ipAddress", "unknown")

            self.log.info("=" * 80)
            self.log.info("📦 RAW STATUS DATA RECEIVED")
            self.log.info(f"   Printer: {serial}")
            self.log.info(f"   IP: {ip}")
            self.log.info("   Full payload:")

            # Log entire status payload structure
            import json
            try:
                self.log.info(json.dumps(statePayload, indent=2, default=str))
            except Exception:
                self.log.info(str(statePayload))

            self.log.info("=" * 80)

        if self.statusDebugEnabled and isinstance(statePayload, dict):
            self.log.info("[status] keys(state)=%s", sorted(list(statePayload.keys()))[:40])

        try:
            percentagePayload = printer.get_percentage()
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            self.log.debug("get_percentage failed", exc_info=error)

        gcodeGetter = getattr(printer, "get_gcode_state", None)
        if callable(gcodeGetter):
            try:
                gcodePayload = gcodeGetter()
            except Exception as error:  # pragma: no cover - depends on SDK behaviour
                self.log.debug("get_gcode_state failed", exc_info=error)

        snapshot = self._normalizeSnapshot(
            statePayload,
            percentagePayload,
            gcodePayload,
            printerConfig,
            printerMetadata,
        )
        return snapshot

    def _normalizeSnapshot(
        self,
        statePayload: Any,
        percentagePayload: Any,
        gcodePayload: Any,
        printerConfig: Dict[str, Any],
        printerMetadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sources = [
            payload
            for payload in (statePayload, percentagePayload, gcodePayload, printerMetadata)
            if payload is not None
        ]

        gcodeState = self._coerceString(gcodePayload)
        if not gcodeState:
            gcodeStateCandidate = self._findValue(sources, {"gcode_state", "gcodeState", "subtask_name"})
            gcodeState = self._coerceString(gcodeStateCandidate)

        progressCandidate = self._findValue(
            sources,
            {
                "mc_percent",
                "progress",
                "percentage",
                "progressPercent",
                "last_print_percentage",
                "print_percent",
                "print_percentage",
                "percent",
            },
        )
        if progressCandidate is None:
            progressCandidate = percentagePayload
        progressPercent = self._coerceFloat(progressCandidate)

        remainingCandidate = self._findValue(
            sources,
            {"mc_remaining_time", "remaining_time", "remainingTimeSeconds"},
        )
        remainingTimeSeconds = self._coerceInt(remainingCandidate)

        nozzleCandidate = self._findValue(
            sources,
            {
                "nozzle_temper",
                "nozzle_temp",
                "nozzleTemp",
                "nozzle_temperature",
                "nozzle_current_temper",
                "nozzle_target_temper",
                "nozzle",
                "nozzle_temp*",
            },
        )
        nozzleTemp = self._coerceFloat(nozzleCandidate)

        bedCandidate = self._findValue(
            sources,
            {
                "bed_temper",
                "bed_temp",
                "bedTemp",
                "bed_temperature",
                "bed_current_temper",
                "bed_target_temper",
                "bed",
                "bed_temp*",
            },
        )
        bedTemp = self._coerceFloat(bedCandidate)

        fanCandidate = self._findValue(
            sources,
            {
                "fan_speed",
                "fanSpeed",
                "cooling_fan_speed",
                "chamber_fan_speed",
                "fan_gear",
                "fan",
            },
        )
        fanSpeedPercent = self._normalizePercentage(fanCandidate)

        printSpeedCandidate = self._findValue(
            sources,
            {"print_speed", "printSpeed", "speed", "speed_level", "speed_multiplier"},
        )
        printSpeed = self._coerceFloat(printSpeedCandidate)

        filamentCandidate = self._findValue(
            sources,
            {
                "filament_used",
                "filamentUsed",
                "filament_consumed",
                "filament_length",
                "filament_weight",
            },
        )
        filamentUsed = self._coerceFloat(filamentCandidate)

        jobCandidate = self._findValue(
            sources,
            {"job_id", "task_id", "current_job_id", "print_id", "jobId"},
        )
        currentJobId = self._coerceString(jobCandidate)

        if self.statusDebugEnabled:
            self.log.info(
                "[status] parsed progress=%s remaining=%s nozzle=%s bed=%s gcode=%s",
                progressPercent,
                remainingTimeSeconds,
                nozzleTemp,
                bedTemp,
                gcodeState,
            )

        firmwareVersion = self._extractFirmwareVersion(sources)

        stateText = extractStateText(statePayload) or gcodeState or ""
        hmsCode = self._extractHmsCode(sources)
        errorMessage = self._extractErrorMessage(sources)
        hasAmsConflict = False
        if not hmsCode and looksLikeAmsFilamentConflict(statePayload):
            hmsCode = "HMS_07FF-2000-0002-0004"
            hasAmsConflict = True
            if not errorMessage:
                errorMessage = "Possible AMS filament conflict"

        normalized: Dict[str, Any] = {
            "status": "update",
            "state": stateText,
            "gcodeState": gcodeState,
            "progressPercent": progressPercent,
            "nozzleTemp": nozzleTemp,
            "bedTemp": bedTemp,
            "remainingTimeSeconds": remainingTimeSeconds,
            "fanSpeedPercent": fanSpeedPercent,
            "printSpeed": printSpeed,
            "filamentUsed": filamentUsed,
            "currentJobId": currentJobId,
            "firmwareVersion": firmwareVersion,
            "hmsCode": hmsCode,
            "errorMessage": errorMessage,
            "hasAmsConflict": hasAmsConflict,
            "rawStatePayload": statePayload,
            "rawPercentagePayload": percentagePayload,
            "rawGcodePayload": gcodePayload,
            "printerMetadata": printerMetadata,
        }

        return normalized

    def _normalizePercentage(self, value: Any) -> Optional[float]:
        numeric = self._coerceFloat(value)
        if numeric is None:
            return None
        if numeric < 0:
            return 0.0
        if numeric <= 1.0:
            numeric *= 100.0
        elif 1.0 < numeric <= 255.0 and numeric > 100.0:
            numeric = (numeric / 255.0) * 100.0
        return max(0.0, min(numeric, 100.0))

    def _fetchPrinterMetadata(self, printer: Any) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        for methodName in ("get_info", "get_printer_info", "printer_info"):
            infoGetter = getattr(printer, methodName, None)
            if callable(infoGetter):
                try:
                    infoPayload = infoGetter()
                    if infoPayload:
                        metadata.setdefault("info", infoPayload)
                        break
                except Exception as error:  # pragma: no cover - depends on SDK behaviour
                    self.log.debug("%s failed", methodName, exc_info=error)

        for methodName in ("get_version", "get_firmware_version"):
            versionGetter = getattr(printer, methodName, None)
            if callable(versionGetter):
                try:
                    firmwarePayload = versionGetter()
                    if firmwarePayload:
                        metadata.setdefault("firmware", firmwarePayload)
                        break
                except Exception as error:  # pragma: no cover - depends on SDK behaviour
                    self.log.debug("%s failed", methodName, exc_info=error)

        firmwareAttribute = getattr(printer, "firmware_version", None)
        if firmwareAttribute:
            metadata.setdefault("firmware", firmwareAttribute)

        return metadata

    def _extractFirmwareVersion(self, sources: Iterable[Any]) -> Optional[str]:
        firmwareCandidate = self._findValue(
            sources,
            {
                "firmware_version",
                "firmwareVersion",
                "firmware",
                "fw_ver",
                "fwVersion",
                "software_version",
            },
        )
        textCandidate = self._coerceString(firmwareCandidate)
        if textCandidate:
            return textCandidate

        for source in sources:
            if isinstance(source, dict):
                for key, value in source.items():
                    normalizedKey = self._normalizeKey(key)
                    if "firmware" in normalizedKey:
                        textValue = self._coerceString(value)
                        if textValue:
                            return textValue
        return None

    def _buildBase44Payloads(
        self,
        snapshot: Dict[str, Any],
        printerConfig: Dict[str, Any],
        apiKey: Optional[str],
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]]:
        ipAddress = str(printerConfig.get("ipAddress") or "").strip()
        serialNumber = str(printerConfig.get("serialNumber") or snapshot.get("printerSerial") or "").strip()
        recipientId = self._resolveRecipientId(printerConfig)
        resolvedApiKey = apiKey or self._resolveBase44ApiKey(printerConfig)
        if not ipAddress or not recipientId or not resolvedApiKey:
            return None

        status, isErrorState, combinedErrorMessage = self._deriveStatusAttributes(snapshot)

        optionalFields: Dict[str, Any] = {}
        progressValue = self._coerceFloat(snapshot.get("progressPercent"))
        if progressValue is not None:
            optionalFields["jobProgress"] = max(0, min(100, int(round(progressValue))))

        jobId = self._coerceString(snapshot.get("currentJobId"))
        if jobId:
            optionalFields["currentJobId"] = jobId

        bedTemp = self._coerceFloat(snapshot.get("bedTemp"))
        if bedTemp is not None:
            optionalFields["bedTemp"] = bedTemp

        nozzleTemp = self._coerceFloat(snapshot.get("nozzleTemp"))
        if nozzleTemp is not None:
            optionalFields["nozzleTemp"] = nozzleTemp

        fanSpeed = self._coerceFloat(snapshot.get("fanSpeedPercent"))
        if fanSpeed is not None:
            optionalFields["fanSpeed"] = max(0, min(100, int(round(fanSpeed))))

        printSpeed = self._coerceFloat(snapshot.get("printSpeed"))
        if printSpeed is not None:
            optionalFields["printSpeed"] = max(0, int(round(printSpeed)))

        filamentUsed = self._coerceFloat(snapshot.get("filamentUsed"))
        if filamentUsed is not None:
            optionalFields["filamentUsed"] = filamentUsed

        remainingSeconds = self._coerceInt(snapshot.get("remainingTimeSeconds"))
        if remainingSeconds is not None:
            optionalFields["timeRemaining"] = max(0, remainingSeconds)

        firmwareVersion = self._coerceString(snapshot.get("firmwareVersion"))
        if firmwareVersion:
            optionalFields["firmwareVersion"] = firmwareVersion

        # Add camera image data if available
        cameraImage = snapshot.get("cameraImage")
        if cameraImage:
            optionalFields["cameraImage"] = cameraImage
            self.log.debug("   📷 Including camera image in status update payload")

        cameraImageTimestamp = snapshot.get("cameraImageTimestamp")
        if cameraImageTimestamp:
            optionalFields["cameraImageTimestamp"] = cameraImageTimestamp

        cameraImageSize = snapshot.get("cameraImageSize")
        if cameraImageSize:
            optionalFields["cameraImageSize"] = cameraImageSize

        updatePayload: Dict[str, Any] = {
            "recipientId": recipientId,
            "printerSerial": serialNumber,
            "printerIpAddress": ipAddress,
            "status": status,
        }
        if combinedErrorMessage:
            updatePayload["errorMessage"] = combinedErrorMessage
        updatePayload.update(optionalFields)
        # Exclude camera image and timestamp from comparison (don't trigger updates just because image changed)
        updateComparable = {
            key: value
            for key, value in updatePayload.items()
            if key not in ("lastUpdateTimestamp", "cameraImage", "cameraImageTimestamp", "cameraImageSize")
        }

        errorPayload: Optional[Dict[str, Any]] = None
        errorComparable: Optional[Dict[str, Any]] = None
        if isErrorState:
            errorPayload = {
                "recipientId": recipientId,
                "printerIpAddress": ipAddress,
                "errorMessage": combinedErrorMessage or "Unknown error",
            }
            errorPayload.update(optionalFields)
            errorComparable = dict(errorPayload)

        return updatePayload, updateComparable, errorPayload, errorComparable

    def _resolveBase44ApiKey(self, printerConfig: Dict[str, Any]) -> str:
        for envKey in ("BASE44_FUNCTIONS_API_KEY", "BASE44_API_KEY"):
            envCandidate = os.getenv(envKey, "").strip()
            if envCandidate:
                return envCandidate
        return ""

    def _ensureEnvironmentValue(self, key: str, value: str) -> None:
        if not value:
            return
        if os.getenv(key) == value:
            return
        os.environ[key] = value

    def _resolveRecipientId(self, printerConfig: Dict[str, Any]) -> Optional[str]:
        envCandidate = os.getenv("BASE44_RECIPIENT_ID", "").strip()
        if envCandidate:
            return envCandidate
        return self.defaultRecipientId or None

    def _deriveStatusAttributes(self, snapshot: Dict[str, Any]) -> Tuple[str, bool, Optional[str]]:
        stateText = self._coerceString(snapshot.get("state"))
        gcodeState = self._coerceString(snapshot.get("gcodeState"))
        progressPercent = self._coerceFloat(snapshot.get("progressPercent"))
        hmsCode = self._coerceString(snapshot.get("hmsCode"))
        errorMessage = self._coerceString(snapshot.get("errorMessage"))
        hasAmsConflict = bool(snapshot.get("hasAmsConflict"))

        offline = self._isOfflineSnapshot(snapshot, stateText, gcodeState)
        paused = self._looksPaused(stateText, gcodeState)
        printing = self._looksPrinting(stateText, gcodeState, progressPercent)

        errorIndicators = False
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("error", "fault", "jam", "alarm")):
                errorIndicators = True
                break
        if hmsCode:
            errorIndicators = True
        if errorMessage:
            errorIndicators = True
        if hasAmsConflict:
            errorIndicators = True

        status = "idle"
        if offline:
            status = "offline"
        elif errorIndicators:
            status = "error"
        elif paused:
            status = "paused"
        elif printing:
            status = "printing"

        combinedErrorMessage = self._composeErrorMessage(errorMessage, hmsCode, hasAmsConflict)
        isErrorState = status == "error" or hasAmsConflict
        return status, isErrorState, combinedErrorMessage

    def _composeErrorMessage(
        self,
        errorMessage: Optional[str],
        hmsCode: Optional[str],
        hasAmsConflict: bool,
    ) -> Optional[str]:
        text = self._coerceString(errorMessage)
        code = self._coerceString(hmsCode)
        if code:
            if text:
                if code not in text:
                    text = f"{text} ({code})"
            else:
                text = code
        if hasAmsConflict and not text:
            text = "Possible AMS filament conflict"
        return text

    def _isOfflineSnapshot(
        self,
        snapshot: Dict[str, Any],
        stateText: Optional[str],
        gcodeState: Optional[str],
    ) -> bool:
        rawState = snapshot.get("rawStatePayload")
        rawGcode = snapshot.get("rawGcodePayload")
        rawPercentage = snapshot.get("rawPercentagePayload")
        if rawState is None and rawGcode is None and rawPercentage is None:
            return True
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("offline", "disconnected", "unreachable")):
                return True
        return False

    def _looksPaused(self, stateText: Optional[str], gcodeState: Optional[str]) -> bool:
        for text in (stateText, gcodeState):
            if text and any(keyword in text.lower() for keyword in ("pause", "paused", "pausing")):
                return True
        return False

    def _looksPrinting(
        self,
        stateText: Optional[str],
        gcodeState: Optional[str],
        progressPercent: Optional[float],
    ) -> bool:
        if progressPercent is not None and progressPercent > 0.1:
            return True
        for text in (stateText, gcodeState):
            if not text:
                continue
            lowered = text.lower()
            if any(
                keyword in lowered
                for keyword in (
                    "print",
                    "warm",
                    "heat",
                    "prepare",
                    "start",
                    "running",
                    "busy",
                    "working",
                )
            ):
                if any(stop in lowered for stop in ("finish", "completed", "complete", "idle", "standby")):
                    continue
                return True
        return False

    def _payloadsDiffer(
        self,
        previous: Optional[Dict[str, Any]],
        current: Dict[str, Any],
    ) -> bool:
        if previous is None:
            return True
        keys = set(previous.keys()) | set(current.keys())
        for key in keys:
            if self._valuesDiffer(previous.get(key), current.get(key)):
                return True
        return False

    def _logBase44Failure(self, operation: str, error: Exception) -> None:
        if isinstance(error, requests.HTTPError):
            response = error.response
            statusCode = getattr(response, "status_code", "unknown")
            bodyText = None
            if response is not None:
                try:
                    bodyText = response.text
                except Exception:  # pragma: no cover - defensive logging
                    bodyText = None
            if bodyText:
                self.log.warning("Base44 %s request failed (%s): %s", operation, statusCode, bodyText)
            else:
                self.log.warning("Base44 %s request failed (%s)", operation, statusCode)
        elif isinstance(error, requests.RequestException):
            self.log.warning("Base44 %s request failed: %s", operation, error)
        else:
            self.log.warning("Base44 %s request failed: %s", operation, error)

    def _extractHmsCode(self, sources: List[Any]) -> Optional[str]:
        candidate = self._findValue(sources, {"hms", "hms_code", "error_code", "print_error_code"})
        textCandidate = self._coerceString(candidate)
        if textCandidate and textCandidate.upper().startswith("HMS_"):
            return textCandidate

        combinedText = self._stringifyFragments(sources)
        if combinedText:
            for token in combinedText.replace("\n", " ").split():
                if token.upper().startswith("HMS_"):
                    return token.strip(".,;:()[]{}")
        return None

    def _extractErrorMessage(self, sources: List[Any]) -> Optional[str]:
        candidate = self._findValue(
            sources,
            {"error_message", "err_msg", "error", "message", "tips", "desc", "description"},
        )
        textCandidate = self._coerceString(candidate)
        if textCandidate:
            return textCandidate

        combinedText = self._stringifyFragments(sources)
        if combinedText:
            lowered = combinedText.lower()
            if any(marker in lowered for marker in ("error", "warning", "filament", "conflict")):
                return combinedText.strip()
        return None

    def _statusChanged(self, previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
        trackedKeys = (
            "state",
            "gcodeState",
            "progressPercent",
            "nozzleTemp",
            "bedTemp",
            "remainingTimeSeconds",
            "hmsCode",
            "errorMessage",
        )
        for key in trackedKeys:
            if self._valuesDiffer(previous.get(key), current.get(key)):
                return True
        return False

    def _valuesDiffer(self, first: Any, second: Any) -> bool:
        if first is None and second is None:
            return False
        if isinstance(first, (int, float)) and isinstance(second, (int, float)):
            return abs(float(first) - float(second)) > 0.05
        return first != second

    def _findValue(self, sources: Iterable[Any], keyNames: Set[str]) -> Any:
        normalizedTargets = {self._normalizeKey(name) for name in keyNames}
        prefixTargets = {target[:-1] for target in normalizedTargets if target.endswith("*")}
        exactTargets = {target for target in normalizedTargets if not target.endswith("*")}
        sentinel = object()

        def search(value: Any) -> Any:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalizedKey = self._normalizeKey(key)
                    if normalizedKey in exactTargets or any(
                        normalizedKey.startswith(prefix) for prefix in prefixTargets
                    ):
                        return nested
                    result = search(nested)
                    if result is not sentinel:
                        return result
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    result = search(item)
                    if result is not sentinel:
                        return result
            return sentinel

        for source in sources:
            result = search(source)
            if result is not sentinel:
                return result
        return None

    def _normalizeKey(self, key: Any) -> str:
        return str(key).strip().replace("-", "_").replace(" ", "_").lower()

    def _unwrapNumericValue(self, value: Any) -> Any:
        if isinstance(value, dict):
            preferredKeys = ("current", "value", "actual", "temperature", "temper", "target")
            for key in preferredKeys:
                if key in value:
                    nested = self._unwrapNumericValue(value.get(key))
                    if nested is not None:
                        return nested
            for nestedValue in value.values():
                nested = self._unwrapNumericValue(nestedValue)
                if nested is not None:
                    return nested
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                nested = self._unwrapNumericValue(item)
                if nested is not None:
                    return nested
            return None
        return value

    def _coerceFloat(self, value: Any) -> Optional[float]:
        candidateValue = self._unwrapNumericValue(value)
        # Check for bool BEFORE checking for int/float since bool is subclass of int
        # But we need to explicitly check type, not isinstance, to handle 0 correctly
        if type(candidateValue) is bool:
            return None
        if isinstance(candidateValue, (int, float)):
            return float(candidateValue)
        if isinstance(candidateValue, str):
            candidate = candidateValue.strip().replace("°c", "").replace("°", "")
            candidate = candidate.replace("%", "").replace("rpm", "")
            if candidate:
                try:
                    return float(candidate)
                except ValueError:
                    return None
        return None

    def _coerceInt(self, value: Any) -> Optional[int]:
        candidateValue = self._unwrapNumericValue(value)
        # Same fix as _coerceFloat - use type() instead of isinstance()
        if type(candidateValue) is bool:
            return None
        if isinstance(candidateValue, int):
            return candidateValue
        if isinstance(candidateValue, float):
            return int(candidateValue)
        if isinstance(candidateValue, str):
            candidate = candidateValue.strip()
            if candidate.isdigit():
                try:
                    return int(candidate)
                except ValueError:
                    return None
        return None

    def _coerceString(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return str(value) if value else None

    def _stringifyFragments(self, sources: Iterable[Any]) -> str:
        fragments: List[str] = []
        for source in sources:
            fragments.append(self._stringifyFragment(source))
        joined = " ".join(fragment for fragment in fragments if fragment)
        return joined.strip()

    def _stringifyFragment(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            return " ".join(self._stringifyFragment(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return " ".join(self._stringifyFragment(item) for item in value)
        return str(value)

    def _handle_hms_error(
        self,
        hms_code: str,
        printer_serial: str,
        printer_ip: str,
        printer_instance: Optional[Any] = None
    ) -> None:
        """
        Handle HMS error detection and reporting

        Args:
            hms_code: HMS error code
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            printer_instance: Optional connected printer instance for camera capture
        """
        if not self.event_reporter:
            self.log.error("❌ EventReporter NOT INITIALIZED!")
            self.log.error("   HMS error will NOT be reported to backend")
            self.log.error("   Check that base_url, api_key, recipient_id are set")
            return

        # Check if already reported
        with self.hms_errors_lock:
            if printer_serial not in self.reported_hms_errors:
                self.reported_hms_errors[printer_serial] = set()
            if hms_code in self.reported_hms_errors[printer_serial]:
                self.log.debug(f"ℹ️  HMS error {hms_code} already reported (skipping)")
                return
            self.reported_hms_errors[printer_serial].add(hms_code)

        self.log.info("=" * 80)
        self.log.info("🚨 HANDLING HMS ERROR")
        self.log.info(f"   HMS Code: {hms_code}")
        self.log.info(f"   Printer: {printer_serial}")
        self.log.info(f"   IP: {printer_ip}")

        # Parse error
        error_data = parse_hms_error(hms_code)

        self.log.info(f"   Module: {error_data.get('module', 'unknown')}")
        self.log.info(f"   Severity: {error_data.get('severity', 'unknown')}")
        description = error_data.get('description', 'N/A')
        self.log.info(f"   Description: {description[:100]}...")

        # Capture camera snapshot (non-blocking, best effort)
        image_data = None
        if printer_instance:
            try:
                self.log.info("📸 Attempting to capture error snapshot...")
                image_data = capture_camera_frame_from_printer(printer_instance)
                if image_data:
                    self.log.info(f"✅ Captured error snapshot ({len(image_data)} bytes)")
                else:
                    self.log.warning("⚠️  No image captured")
            except Exception as e:
                self.log.warning(f"⚠️  Could not capture error snapshot: {e}")

        # Report event to backend
        try:
            self.log.info("📤 Reporting HMS error to backend...")
            event_id = self.event_reporter.report_hms_error(
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                hms_code=hms_code,
                error_data=error_data,
                image_data=image_data
            )

            if event_id:
                self.log.info(f"✅ HMS error reported successfully: event_id={event_id}")
            else:
                self.log.error("❌ Failed to report HMS error (no event_id returned)")

        except Exception as e:
            self.log.error(f"❌ ERROR reporting HMS error to backend: {e}")
            import traceback
            self.log.error(traceback.format_exc())

        self.log.info("=" * 80)

    def _report_status_update(
        self,
        status_data: Dict[str, Any],
        printer_serial: str,
        printer_ip: str
    ) -> None:
        """
        Report periodic status update to backend

        Args:
            status_data: Raw status data from printer
            printer_serial: Printer serial number
            printer_ip: Printer IP address
        """
        if not self.event_reporter:
            return

        try:
            # Extract relevant status fields from normalized snapshot
            extracted_status = {
                "progress": status_data.get("progressPercent"),
                "bedTemp": status_data.get("bedTemp"),
                "nozzleTemp": status_data.get("nozzleTemp"),
                "remainingTimeSeconds": status_data.get("remainingTimeSeconds"),
                "gcodeState": status_data.get("gcodeState"),
                "currentLayer": status_data.get("currentLayer"),
                "totalLayers": status_data.get("totalLayers"),
                "state": status_data.get("state"),
                "fanSpeedPercent": status_data.get("fanSpeedPercent"),
                "printSpeed": status_data.get("printSpeed"),
                "filamentUsed": status_data.get("filamentUsed"),
                "currentJobId": status_data.get("currentJobId"),
            }

            # Remove None values
            extracted_status = {k: v for k, v in extracted_status.items() if v is not None}

            # Only report if we have meaningful status data
            if extracted_status:
                self.event_reporter.report_event(
                    event_type="status_update",
                    printer_serial=printer_serial,
                    printer_ip=printer_ip,
                    event_status="info",
                    status_data=extracted_status
                )

        except Exception as e:
            self.log.debug(f"Error reporting status update: {e}")

    def _check_pause_reason(
        self,
        printer_serial: str,
        printer_ip: str,
        printer_instance: Optional[Any],
        status_data: Dict[str, Any]
    ) -> None:
        """
        Check if pause was caused by HMS error or user action

        Uses Bambu API print_error_code() to detect errors

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            printer_instance: Connected printer instance
            status_data: Current status payload
        """
        self.log.info("=" * 80)
        self.log.info("🔍 CHECKING PAUSE REASON")
        self.log.info(f"   Printer: {printer_serial}")
        self.log.info(f"   IP: {printer_ip}")

        try:
            # Check if we have a printer instance
            if not printer_instance:
                self.log.warning("⚠️  No printer instance available, cannot check error code")
                return

            # Check error code via API
            self.log.info("🔍 Calling printer.print_error_code()...")
            error_code_method = getattr(printer_instance, "print_error_code", None)

            if not callable(error_code_method):
                self.log.warning("⚠️  print_error_code() method not available on printer instance")
                return

            error_code = error_code_method()
            self.log.info(f"📊 Error code: {error_code}")

            if error_code and error_code > 0:
                # Error detected!
                self.log.error("❌ HMS ERROR DETECTED via error code!")
                self.log.error(f"   Printer: {printer_serial}")
                self.log.error(f"   Error code: {error_code}")

                # Try to get HMS error codes from status data
                hms_errors = self._extract_hms_from_status(status_data)

                if hms_errors:
                    self.log.error(f"   HMS errors found: {hms_errors}")

                    # Report each HMS error
                    for hms_code in hms_errors:
                        self._handle_hms_error(hms_code, printer_serial, printer_ip, printer_instance)
                else:
                    # Error code present but no specific HMS codes
                    # Report generic error
                    self.log.warning("⚠️  Error code present but no specific HMS codes found")
                    self._report_generic_pause_error(printer_serial, printer_ip, error_code)
            else:
                # No error - this was a user-initiated pause
                self.log.info("✅ Pause was USER-INITIATED (error_code = 0)")
                self.log.info("   No HMS error to report")

        except Exception as e:
            self.log.error(f"❌ Failed to check pause reason: {e}")
            import traceback
            self.log.error(traceback.format_exc())

        self.log.info("=" * 80)

    def _extract_hms_from_status(self, status_data: Dict[str, Any]) -> List[str]:
        """
        Extract HMS error codes from status data

        Args:
            status_data: Current status payload

        Returns:
            List of HMS error codes
        """
        hms_errors = []

        try:
            # Check normalized hmsCode field
            hms_code = status_data.get("hmsCode")
            if hms_code:
                hms_errors.append(hms_code)

            # Check raw state payload for HMS in various field names
            raw_state = status_data.get("rawStatePayload")
            if isinstance(raw_state, dict):
                possible_hms_fields = [
                    'hms',
                    'hms_list',
                    'hmsErrors',
                    'errors',
                    'alarm',
                    'mc_hms',
                    'print_error',
                    'hms_code',
                ]

                for field in possible_hms_fields:
                    if field in raw_state:
                        value = raw_state.get(field)

                        # Handle HMS as list
                        if isinstance(value, list) and len(value) > 0:
                            for item in value:
                                if isinstance(item, str) and item:
                                    hms_errors.append(item)
                                elif isinstance(item, dict):
                                    code = (
                                        item.get('code') or
                                        item.get('hms_code') or
                                        item.get('error_code') or
                                        str(item)
                                    )
                                    if code:
                                        hms_errors.append(str(code))

                        # Handle HMS as string
                        elif isinstance(value, str) and value:
                            hms_errors.append(value)

        except Exception as e:
            self.log.warning(f"⚠️  Could not extract HMS errors from status: {e}")

        # Remove duplicates
        return list(set(hms_errors))

    def _get_printer_access_code(self, printer_serial: str) -> Optional[str]:
        """
        Get access code for printer from config

        Args:
            printer_serial: Printer serial number

        Returns:
            Access code string or None
        """

        try:
            if not _config_manager_available:
                self.log.warning("⚠️  Config manager not available")
                return None

            config = get_config_manager()

            # ConfigManager stores printers in a different structure
            # Try multiple possible locations:

            # Method 1: Check if printers are stored directly
            if hasattr(config, 'printers') and isinstance(config.printers, list):
                for printer in config.printers:
                    if printer.get('serialNumber') == printer_serial:
                        access_code = printer.get('accessCode')
                        if access_code:
                            self.log.debug(f"✅ Found access code for {printer_serial} (Method 1)")
                            return access_code

            # Method 2: Check config data structure
            if hasattr(config, 'data') and isinstance(config.data, dict):
                printers = config.data.get('printers', [])
                for printer in printers:
                    if printer.get('serialNumber') == printer_serial:
                        access_code = printer.get('accessCode')
                        if access_code:
                            self.log.debug(f"✅ Found access code for {printer_serial} (Method 2)")
                            return access_code

            # Method 3: Try to get from config file directly
            config_data = config.get_all()
            if isinstance(config_data, dict):
                printers = config_data.get('printers', [])
                for printer in printers:
                    if printer.get('serialNumber') == printer_serial:
                        access_code = printer.get('accessCode')
                        if access_code:
                            self.log.debug(f"✅ Found access code for {printer_serial} (Method 3)")
                            return access_code

            # Method 4: Use config.get() API
            try:
                printers = config.get('printers', [])
                if isinstance(printers, list):
                    for printer in printers:
                        if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                            access_code = printer.get('accessCode')
                            if access_code:
                                self.log.debug(f"✅ Found access code for {printer_serial} (Method 4)")
                                return access_code
            except Exception as e:
                self.log.debug(f"config.get() failed: {e}")

            # Method 5: Try to_dict() as fallback
            try:
                config_data = config.to_dict()
                if isinstance(config_data, dict):
                    printers = config_data.get('printers', [])
                    if isinstance(printers, list):
                        for printer in printers:
                            if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                                access_code = printer.get('accessCode')
                                if access_code:
                                    self.log.debug(f"✅ Found access code for {printer_serial} (Method 5)")
                                    return access_code
            except Exception as e:
                self.log.debug(f"to_dict() fallback failed: {e}")

            # Method 6: Try direct _config access as last resort
            try:
                if hasattr(config, '_config') and isinstance(config._config, dict):
                    printers = config._config.get('printers', [])
                    if isinstance(printers, list):
                        for printer in printers:
                            if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                                access_code = printer.get('accessCode')
                                if access_code:
                                    self.log.debug(f"✅ Found access code for {printer_serial} (Method 6)")
                                    return access_code
            except Exception as e:
                self.log.debug(f"_config fallback failed: {e}")

            self.log.warning(f"⚠️  No access code found for printer {printer_serial} in config")
            self.log.debug(f"   Config structure: {type(config)}")
            self.log.debug(f"   Available attributes: {dir(config)}")

            return None

        except Exception as e:
            self.log.error(f"❌ Failed to get printer access code: {e}")
            import traceback
            self.log.debug(traceback.format_exc())
            return None

    def _should_capture_camera_image(self, printer_serial: str, status_data: Dict[str, Any]) -> bool:
        """
        Determine if we should capture camera image now

        Capture strategy:
        - Every 30 seconds during active print
        - Every 5 minutes when idle
        - Always on first status update for new printer

        Args:
            printer_serial: Printer serial number
            status_data: Current status data from printer (normalized snapshot)

        Returns:
            True if should capture, False otherwise
        """
        current_time = time.monotonic()

        with self.camera_capture_lock:
            last_capture = self.last_camera_capture.get(printer_serial, 0)

            # Get printer state
            gcode_state = status_data.get('gcodeState', 'IDLE') or 'IDLE'
            gcode_state_upper = str(gcode_state).upper()

            # Determine capture interval based on state
            if gcode_state_upper in ['RUNNING', 'PRINTING', 'PREPARE', 'PREHEATING']:
                capture_interval = self.camera_capture_interval_active  # 30 seconds
            else:
                capture_interval = self.camera_capture_interval_idle  # 5 minutes

            # Check if enough time has passed
            time_since_last = current_time - last_capture

            if time_since_last >= capture_interval:
                self.log.debug(
                    f"📸 Should capture camera (state={gcode_state}, "
                    f"interval={capture_interval:.0f}s, last={time_since_last:.0f}s ago)"
                )
                self.last_camera_capture[printer_serial] = current_time
                return True

        return False

    def _report_generic_pause_error(
        self,
        printer_serial: str,
        printer_ip: str,
        error_code: int
    ) -> None:
        """
        Report a generic pause error when error_code > 0 but no specific HMS codes

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            error_code: Error code from printer
        """
        self.log.warning("📤 Reporting generic pause error to backend...")

        if not self.event_reporter:
            self.log.error("❌ EventReporter not initialized, cannot report error")
            return

        try:
            event_id = self.event_reporter.report_event(
                event_type="printer_error",
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                event_status="warning",
                error_data={
                    "errorCode": error_code,
                    "description": f"Printer paused unexpectedly with error code {error_code}"
                },
                message=f"Unexpected pause detected (error code: {error_code})"
            )

            if event_id:
                self.log.info(f"✅ Generic pause error reported: {event_id}")
            else:
                self.log.warning("⚠️  Failed to report generic pause error")

        except Exception as e:
            self.log.error(f"❌ Failed to report generic pause error: {e}")
            import traceback
            self.log.error(traceback.format_exc())

    def _capture_camera_image_to_file(
        self,
        printer_serial: str,
        printer_ip: str,
        access_code: Optional[str] = None
    ) -> Optional[str]:
        """Capture camera image from printer and save to local file

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            access_code: Optional printer access code (if not provided, will look up from config)

        Returns:
            Path to saved image file, or None if capture failed
        """

        import os
        from datetime import datetime

        try:
            self.log.info("=" * 80)
            self.log.info("📸 CAMERA CAPTURE STARTED")
            self.log.info(f"   Printer Serial: {printer_serial}")
            self.log.info(f"   Printer IP: {printer_ip}")

            # Generate output path
            timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')
            date_str = datetime.utcnow().strftime('%Y-%m-%d')

            output_dir = os.path.join(
                os.path.expanduser('~'),
                '.printmaster',
                'camera',
                date_str,
                printer_serial
            )

            self.log.info(f"   Output dir: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            self.log.info(f"   ✅ Output directory created/verified")

            output_path = os.path.join(
                output_dir,
                f"{printer_serial}-{timestamp}.jpg"
            )

            self.log.info(f"   Target file: {output_path}")

            # Get printer access code
            self.log.info(f"   🔑 Getting access code for {printer_serial}...")

            if access_code:
                self.log.info(f"   ✅ Access code provided directly ({len(access_code)} chars)")
            else:
                self.log.info(f"   📋 Looking up access code from config...")
                access_code = self._get_printer_access_code(printer_serial)

            if not access_code:
                self.log.error(f"   ❌ No access code found for {printer_serial}")
                self.log.error(f"   Cannot capture camera image without access code")
                self.log.info("=" * 80)
                return None

            self.log.info(f"   ✅ Access code found ({len(access_code)} chars)")

            # ============================================
            # TRY HTTP CAMERA SNAPSHOT (FASTER AND MORE RELIABLE)
            # ============================================
            self.log.info(f"   📷 Requesting camera snapshot via HTTP...")

            image_data = None
            camera_url = f"http://{printer_ip}/camera/snapshot"

            self.log.info(f"   🌐 GET {camera_url}")

            try:
                response = requests.get(
                    camera_url,
                    auth=('bblp', access_code),  # Basic auth with access code
                    timeout=5
                )

                if response.status_code == 200:
                    image_data = response.content
                    self.log.info(f"   ✅ Camera snapshot received via HTTP ({len(image_data)} bytes)")
                else:
                    self.log.warning(f"   ⚠️  HTTP request failed: {response.status_code}")
                    image_data = None

            except requests.exceptions.Timeout:
                self.log.warning(f"   ⚠️  HTTP request timed out")
                image_data = None
            except requests.exceptions.ConnectionError:
                self.log.warning(f"   ⚠️  Connection error - printer may be offline")
                image_data = None
            except Exception as e:
                self.log.warning(f"   ⚠️  HTTP request failed: {e}")
                image_data = None

            # ============================================
            # FALLBACK TO MJPEG STREAM IF HTTP FAILED
            # ============================================
            if not image_data:
                self.log.info(f"   📹 Falling back to MJPEG stream...")

                # Import Bambu API
                self.log.info(f"   📦 Importing bambulabs_api...")
                try:
                    from bambulabs_api import Printer
                    self.log.info(f"   ✅ bambulabs_api imported successfully")
                except ImportError as e:
                    self.log.error(f"   ❌ Failed to import bambulabs_api: {e}")
                    self.log.info("=" * 80)
                    return None

                # Initialize printer connection
                self.log.info(f"   🔌 Connecting to printer at {printer_ip}...")
                try:
                    printer = Printer(
                        ip_address=printer_ip,
                        access_code=access_code,
                        serial=printer_serial
                    )
                    self.log.info(f"   ✅ Printer connection initialized")
                except Exception as e:
                    self.log.error(f"   ❌ Failed to initialize printer connection: {e}")
                    self.log.info("=" * 80)
                    return None

                # Start camera stream
                self.log.info(f"   📹 Starting camera stream...")
                try:
                    if hasattr(printer, 'camera_start'):
                        printer.camera_start()
                        self.log.info(f"   ✅ Camera stream started")

                        # Wait longer for stream to initialize
                        import time
                        time.sleep(3)  # Increased from 1s to 3s
                        self.log.info(f"   ⏱️  Waited 3s for camera initialization")
                except Exception as e:
                    self.log.warning(f"   ⚠️  Failed to start camera stream: {e}")

                # Try to get camera frame
                self.log.info(f"   📷 Requesting camera frame from MJPEG stream...")
                methods_tried = []

                # Method 1: get_camera_frame (recommended for active streams)
                if hasattr(printer, 'get_camera_frame'):
                    methods_tried.append('get_camera_frame()')
                    try:
                        self.log.info(f"   Trying: printer.get_camera_frame()...")
                        image_data = printer.get_camera_frame()
                        if image_data:
                            self.log.info(f"   ✅ Success with get_camera_frame()")
                    except Exception as e:
                        self.log.warning(f"   ⚠️  get_camera_frame() failed: {e}")

                # Method 2: get_camera_image()
                if not image_data and hasattr(printer, 'get_camera_image'):
                    methods_tried.append('get_camera_image()')
                    try:
                        self.log.info(f"   Trying: printer.get_camera_image()...")
                        image_data = printer.get_camera_image()
                        if image_data:
                            self.log.info(f"   ✅ Success with get_camera_image()")
                    except Exception as e:
                        self.log.warning(f"   ⚠️  get_camera_image() failed: {e}")

                # Method 3: get_latest_jpg()
                if not image_data and hasattr(printer, 'get_latest_jpg'):
                    methods_tried.append('get_latest_jpg()')
                    try:
                        self.log.info(f"   Trying: printer.get_latest_jpg()...")
                        image_data = printer.get_latest_jpg()
                        if image_data:
                            self.log.info(f"   ✅ Success with get_latest_jpg()")
                    except Exception as e:
                        self.log.warning(f"   ⚠️  get_latest_jpg() failed: {e}")

                # Stop camera stream to save bandwidth
                if hasattr(printer, 'camera_stop'):
                    try:
                        printer.camera_stop()
                        self.log.info(f"   🛑 Camera stream stopped")
                    except Exception as e:
                        self.log.debug(f"   Note: camera_stop failed: {e}")

            # ============================================
            # CHECK IF WE GOT IMAGE DATA
            # ============================================
            if not image_data:
                self.log.error(f"   ❌ No image data received from printer")
                self.log.error(f"   Tried: HTTP snapshot, MJPEG stream")
                self.log.info("=" * 80)
                return None

            # Save to file
            self.log.info(f"   💾 Saving image to file...")
            with open(output_path, 'wb') as f:
                f.write(image_data)

            file_size = len(image_data)
            file_size_kb = file_size / 1024

            self.log.info(f"   ✅ Camera image saved successfully!")
            self.log.info(f"   File: {output_path}")
            self.log.info(f"   Size: {file_size_kb:.2f} KB ({file_size} bytes)")
            self.log.info("=" * 80)

            return output_path

        except Exception as e:
            self.log.error(f"❌ CAMERA CAPTURE FAILED")
            self.log.error(f"   Error: {e}")
            import traceback
            self.log.error(f"   Traceback:\n{traceback.format_exc()}")
            self.log.info("=" * 80)
            return None

    def _sanitizeErrorMessage(self, message: str, accessCode: str) -> str:
        if accessCode and accessCode in message:
            return message.replace(accessCode, "***")
        return message
