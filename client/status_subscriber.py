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

# Import status reporter
try:
    from .status_reporter import StatusReporter
    _status_reporter_available = True
except ImportError:
    _status_reporter_available = False
    StatusReporter = None

# Import config manager
try:
    from .config_manager import get_config_manager
    _config_manager_available = True
except ImportError:
    _config_manager_available = False

# Reduce noise from third-party SDK logger - set to CRITICAL to suppress
# "Printer Values Not Available Yet" ERROR messages that spam during initialization
logging.getLogger("bambulabs_api").setLevel(logging.CRITICAL)


class _PrinterValuesNotAvailableFilter(logging.Filter):
    """Filter to suppress noisy 'Printer Values Not Available Yet' messages."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        # Return False to suppress the message, True to allow it
        message = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
        if "Printer Values Not Available Yet" in message:
            return False
        return True


# Apply filter to root logger to catch messages from any source
logging.getLogger().addFilter(_PrinterValuesNotAvailableFilter())


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
        enableStatusReporting: bool = True,
        statusReportInterval: int = 60,
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

        # Initialize status reporter if enabled and credentials available
        self.status_reporter: Optional[StatusReporter] = None

        # Hardcoded base URL for status reporting
        status_reporter_base_url = "https://printpro3d-api-931368217793.europe-west1.run.app"

        # Prefer config values, fall back to parameters, fall back to env
        final_api_key = config_api_key or apiKey or self.api_key
        final_recipient_id = config_recipient_id or self.defaultRecipientId

        if enableStatusReporting and _status_reporter_available and final_api_key and final_recipient_id:
            try:
                self.log.info("🔧 Initializing StatusReporter in status_subscriber...")
                self.status_reporter = StatusReporter(
                    base_url=status_reporter_base_url,
                    api_key=final_api_key,
                    recipient_id=final_recipient_id,
                    report_interval=statusReportInterval,
                    logger=self.log,
                )
                self.log.info("✅ StatusReporter initialized successfully")
                self.log.info(f"   Recipient ID: {final_recipient_id[:8]}...")
                self.log.info(f"   Report interval: {statusReportInterval}s")
            except Exception as e:
                self.log.warning(f"Failed to initialize status reporter: {e}")
        elif not enableStatusReporting:
            self.log.debug("Status reporting disabled by configuration")
        elif not _status_reporter_available:
            self.log.debug("Status reporter module not available")
        else:
            self.log.warning("⚠️  StatusReporter NOT initialized (missing credentials)")
            self.log.warning(f"   api_key: {'✅' if final_api_key else '❌'}")
            self.log.warning(f"   recipient_id: {'✅' if final_recipient_id else '❌'}")

        # Track reported HMS errors to avoid duplicates
        self.reported_hms_errors: Dict[str, Set[str]] = {}
        self.hms_errors_lock = threading.Lock()

        # Track completed job ids to avoid duplicate completion reports
        self.completedJobIds: Dict[str, Set[str]] = {}
        self.completedJobsLock = threading.Lock()

        # Camera capture tracking
        self.last_camera_capture: Dict[str, float] = {}  # {printer_serial: timestamp}
        self.camera_capture_lock = threading.Lock()
        self.camera_capture_interval_active = 30.0   # 30 seconds during print
        self.camera_capture_interval_idle = 300.0    # 5 minutes when idle

        # MQTT dump cache for full printer data (refreshed every 60 seconds)
        self.mqttDumpCache: Dict[str, Dict[str, Any]] = {}  # {serial: {timestamp, data}}
        self.mqttDumpCacheLock = threading.Lock()
        self.mqttDumpInterval = 60.0  # 60 seconds between full mqtt_dump calls
        self.mqttDumpRetryInterval = 5.0  # 5 seconds retry on failure
        self.lastMqttDumpTime: Dict[str, float] = {}  # {serial: timestamp}

        # Stale connection detection
        self.connectionStaleTimeout = 60.0  # Force reconnect if no data for 60s


        # Optional callback for mqtt_dump updates
        self.onMqttDump: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None

        # Live status cache (updated every poll cycle)
        self.liveStatusCache: Dict[str, Dict[str, Any]] = {}  # {serial: status_data}
        self.liveStatusCacheLock = threading.Lock()

        # MQTT reconnect tracking - for retry logic when ping succeeds but MQTT fails
        self.mqttReconnectAttempts: Dict[str, int] = {}  # {serial: attempt_count}
        self.mqttReconnectLock = threading.Lock()
        self.mqttReconnectAttempts: Dict[str, int] = {}  # {serial: attempt_count}
        self.mqttReconnectLock = threading.Lock()
        self.mqttMaxReconnectAttempts = 0  # 0 = infinite retries

        # Active printer instances for reuse
        self.active_printers: Dict[str, Any] = {}
        self.active_printers_lock = threading.Lock()


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

    def get_active_printer(self, serial: str) -> Optional[Any]:
        """Get the active printer instance for a given serial, if connected."""
        with self.active_printers_lock:
            return self.active_printers.get(serial)


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
                # ============================================
                # PING CHECK BEFORE MQTT CONNECTION
                # ============================================
                # Verify printer is reachable before attempting MQTT connection
                # This prevents spam of "Printer Values Not Available Yet" errors
                if not self._pingHost(ipAddress, 1000):
                    self.log.debug(f"⏳ Printer {serial} at {ipAddress} is not reachable, skipping MQTT connection")
                    # Report offline status if StatusReporter is available
                    if self.status_reporter:
                        try:
                            self.status_reporter.report_offline(serial, ipAddress)
                        except Exception as offline_err:
                            self.log.debug(f"Failed to report offline status: {offline_err}")
                    # Wait before next ping attempt
                    stopEvent.wait(10.0)
                    continue

                printerInstance = _printerClass(ipAddress, accessCode, serial)
                with self.active_printers_lock:
                    self.active_printers[serial] = printerInstance

                self._connectPrinter(printerInstance)
                self._resetConnectionFailures(serial)
                printerMetadata = self._fetchPrinterMetadata(printerInstance)
                lastBase44Comparable: Optional[Dict[str, Any]] = None
                lastBase44Emit = 0.0
                lastSuccessfulStateTime = time.monotonic()

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

                    # Check for valid data to reset stale timer
                    current_monotonic = time.monotonic()
                    # We consider it valid if we have non-empty gcode state or temperatures
                    if statusPayload.get("gcodeState") or statusPayload.get("nozzleTemp"):
                        lastSuccessfulStateTime = current_monotonic
                    
                    # STALE CONNECTION CHECK
                    if current_monotonic - lastSuccessfulStateTime > self.connectionStaleTimeout:
                        self.log.warning(f"⚠️  No valid data from {serial} for {self.connectionStaleTimeout}s - forcing reconnect")
                        # Break inner loop to trigger reconnection in outer loop
                        break

                    # ============================================
                    # UPDATE LIVE STATUS CACHE
                    # ============================================
                    with self.liveStatusCacheLock:
                        self.liveStatusCache[serial] = {
                            "timestamp": time.time(),
                            "data": dict(statusPayload),
                        }

                    # ============================================
                    # PERIODIC MQTT DUMP (Smart Retry: 60s normal, 5s retry)
                    # ============================================
                    lastDumpTime = self.lastMqttDumpTime.get(serial, 0.0)
                    
                    # Determine interval based on whether last attempt was successful (we can infer this if needed, 
                    # but simpler is to check if we have cached data recently)
                    # For now, we utilize the loop variable logic:
                    # If we failed last time, we want to retry sooner.
                    
                    # We use a localized variable for next check interval if not tracked in class
                    # But better: Check if cache has recent data
                    
                    currentInterval = self.mqttDumpInterval
                    
                    # logic: If time since last dump > interval, try again.
                    # But if we failed, we want 'lastDumpTime' to be set so that next check is in 5 seconds.
                    
                    if current_monotonic - lastDumpTime >= self.mqttDumpInterval:
                        try:
                            mqttDumpGetter = getattr(printerInstance, "mqtt_dump", None)
                            if callable(mqttDumpGetter):
                                self.log.info(f"📦 Fetching mqtt_dump for {serial}...")
                                mqttDumpData = mqttDumpGetter()
                                
                                if mqttDumpData:
                                    from datetime import datetime
                                    dumpTimestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    
                                    with self.mqttDumpCacheLock:
                                        self.mqttDumpCache[serial] = {
                                            "timestamp": dumpTimestamp,
                                            "data": mqttDumpData,
                                        }
                                    
                                    statusPayload["mqttDump"] = mqttDumpData
                                    statusPayload["mqttDumpTimestamp"] = dumpTimestamp
                                    
                                    self.log.info(f"✅ mqtt_dump cached for {serial}")
                                    
                                    if self.onMqttDump:
                                        try:
                                            self.onMqttDump(
                                                {"serial": serial, "timestamp": dumpTimestamp, "data": mqttDumpData},
                                                printerConfig,
                                            )
                                        except Exception as calbackErr:
                                            self.log.debug(f"mqtt_dump callback failed: {calbackErr}")
                                    
                                    # Success: Wait full interval
                                    self.lastMqttDumpTime[serial] = current_monotonic
                                else:
                                    self.log.debug(f"mqtt_dump returned empty for {serial}")
                                    # Empty result: Retry sooner
                                    # Set last time so that (now - last) approx equals (interval - retry_interval)
                                    # resulting in waiting 'retry_interval' seconds from now
                                    self.lastMqttDumpTime[serial] = current_monotonic - (self.mqttDumpInterval - self.mqttDumpRetryInterval)
                                    self.log.info(f"   Empty dump. Retrying in {self.mqttDumpRetryInterval}s...")
                            else:
                                self.lastMqttDumpTime[serial] = current_monotonic
                                
                        except Exception as dumpErr:
                            self.log.warning(f"mqtt_dump failed for {serial}: {dumpErr}")
                            # Failure: Retry sooner
                            self.lastMqttDumpTime[serial] = current_monotonic - (self.mqttDumpInterval - self.mqttDumpRetryInterval)
                            self.log.info(f"   Dump failed. Retrying in {self.mqttDumpRetryInterval}s...")

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

                        # ============================================
                        # JOB COMPLETION DETECTION
                        # ============================================
                        self._maybeReportJobCompletion(statusPayload, serial, ipAddress)

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

                    # ============================================
                    # STATUS REPORTER - SEND STATUS TO BACKEND
                    # ============================================
                    # DEBUG: Check if we reach this point
                    self.log.debug(f"🔍 STATUS REPORTER CHECK: serial={serial}, ipAddress={ipAddress}")
                    self.log.debug(f"   statusPayload exists: {statusPayload is not None}")
                    if statusPayload:
                        self.log.debug(f"   statusPayload has data: {len(statusPayload)} keys")
                    else:
                        self.log.warning(f"⚠️  statusPayload is None/False - skipping status report for {serial}")

                    # Report status to backend API if StatusReporter is initialized
                    if statusPayload:
                        self._reportPrinterStatus(serial, ipAddress, statusPayload)

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
                
                # Track reconnect attempts
                with self.mqttReconnectLock:
                    attemptCount = self.mqttReconnectAttempts.get(serial, 0) + 1
                    self.mqttReconnectAttempts[serial] = attemptCount
                
                # Check if printer is still pingable (network is up)
                printerStillReachable = self._pingHost(ipAddress, 1000)
                
                if printerStillReachable:
                    # Printer is reachable but MQTT failed - send reconnecting status to GUI
                    self._sendReconnectingStatus(
                        serial=serial,
                        ipAddress=ipAddress,
                        nickname=nickname,
                        printerConfig=printerConfig,
                        attemptCount=attemptCount,
                        errorMessage=sanitizedMessage,
                    )
                    self.log.info(
                        f"🔄 MQTT reconnect attempt {attemptCount} for {serial} - "
                        f"printer pingable, retrying in {self.reconnectDelay}s..."
                    )
                    # Wait before retry (shorter delay since we know printer is reachable)
                    stopEvent.wait(self.reconnectDelay)
                else:
                    # Printer not reachable - normal error handling
                    if self._shouldLogConnectionFailure(serial):
                        self.onError(sanitizedMessage, dict(printerConfig))
                    stopEvent.wait(self.reconnectDelay * 2)
            finally:
                if printerInstance is not None:
                    with self.active_printers_lock:
                        if serial in self.active_printers and self.active_printers[serial] is printerInstance:
                            del self.active_printers[serial]
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
        # Also reset MQTT reconnect attempts counter
        with self.mqttReconnectLock:
            if key in self.mqttReconnectAttempts:
                self.mqttReconnectAttempts.pop(key, None)

    def _sendReconnectingStatus(
        self,
        serial: str,
        ipAddress: str,
        nickname: Optional[str],
        printerConfig: Dict[str, Any],
        attemptCount: int,
        errorMessage: str,
    ) -> None:
        """Send a status update showing that MQTT reconnection is in progress.
        
        This ensures the GUI shows accurate status when ping succeeds but MQTT fails,
        rather than showing stale/outdated information.
        """
        from datetime import datetime
        
        statusPayload = {
            "printerSerial": serial,
            "printerIp": ipAddress,
            "nickname": nickname,
            "status": "reconnecting",
            "mqttConnected": False,
            "mqttReconnecting": True,
            "mqttReconnectAttempt": attemptCount,
            "gcodeState": f"Reconnecting (attempt {attemptCount})...",
            "lastError": errorMessage,
            "lastUpdate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "percentage": None,
            "nozzleTemp": None,
            "bedTemp": None,
        }
        
        # Update live status cache with reconnecting state
        with self.liveStatusCacheLock:
            self.liveStatusCache[serial] = {
                "timestamp": time.time(),
                "data": dict(statusPayload),
            }
        
        # Send to GUI via the onUpdate callback
        try:
            self.onUpdate(statusPayload, dict(printerConfig))
            self.log.debug(f"Sent reconnecting status for {serial} (attempt {attemptCount})")
        except Exception as e:
            self.log.debug(f"Failed to send reconnecting status: {e}")

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

        # CRITICAL: Fetch temperatures directly from printer methods
        # These are NOT included in get_state() for Bambu Lab printers!
        directTemperatures: Dict[str, Any] = {}
        
        # Get nozzle temperature
        nozzleTempGetter = getattr(printer, "get_nozzle_temperature", None)
        if callable(nozzleTempGetter):
            try:
                nozzleTemp = nozzleTempGetter()
                if nozzleTemp is not None:
                    directTemperatures["nozzleTemp"] = float(nozzleTemp)
            except Exception:
                pass
        
        # Get bed temperature
        bedTempGetter = getattr(printer, "get_bed_temperature", None)
        if callable(bedTempGetter):
            try:
                bedTemp = bedTempGetter()
                if bedTemp is not None:
                    directTemperatures["bedTemp"] = float(bedTemp)
            except Exception:
                pass
        
        # Get chamber temperature
        chamberTempGetter = getattr(printer, "get_chamber_temperature", None)
        if callable(chamberTempGetter):
            try:
                chamberTemp = chamberTempGetter()
                if chamberTemp is not None:
                    directTemperatures["chamberTemp"] = float(chamberTemp)
            except Exception:
                pass
        
        # Merge direct temperatures into printerMetadata for _normalizeSnapshot
        if printerMetadata is None:
            printerMetadata = {}
        printerMetadata.update(directTemperatures)
        
        # CRITICAL: Get print job fields from mqtt_dump cache
        # mqtt_dump contains the raw 'print' object with all the detailed print job info
        # This is the same approach the GUI uses to display correct data
        serial = printerConfig.get("serialNumber", "")
        if serial:
            with self.mqttDumpCacheLock:
                cached_dump = self.mqttDumpCache.get(serial, {})
            dump_data = cached_dump.get("data", {}) if isinstance(cached_dump, dict) else {}
            if isinstance(dump_data, dict):
                # Get the nested 'print' object - this is where Bambu stores all the good stuff
                mqtt_print = dump_data.get("print", {}) if isinstance(dump_data.get("print"), dict) else {}
                if mqtt_print:
                    # Add these directly to printerMetadata so they get picked up by _normalizeSnapshot
                    if mqtt_print.get("print_type"):
                        printerMetadata["printType"] = mqtt_print.get("print_type")
                    if mqtt_print.get("subtask_name"):
                        printerMetadata["fileName"] = mqtt_print.get("subtask_name")
                    if mqtt_print.get("gcode_file"):
                        printerMetadata["gcodeFile"] = mqtt_print.get("gcode_file")
                    if mqtt_print.get("layer_num") is not None:
                        printerMetadata["currentLayer"] = mqtt_print.get("layer_num")
                    if mqtt_print.get("total_layer_num") is not None:
                        printerMetadata["totalLayers"] = mqtt_print.get("total_layer_num")
                    if mqtt_print.get("print_error") is not None:
                        printerMetadata["printErrorCode"] = mqtt_print.get("print_error")
                    # Light state from lights_report array
                    lights_report = mqtt_print.get("lights_report")
                    if lights_report and isinstance(lights_report, list) and len(lights_report) > 0:
                        printerMetadata["lightState"] = lights_report[0].get("mode")
                    if mqtt_print.get("skipped_objects") is not None:
                        printerMetadata["skippedObjects"] = mqtt_print.get("skipped_objects")

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
        # Build sources list including nested 'print' object from statePayload
        # Bambu MQTT data has most fields nested under the 'print' key
        sources = [
            payload
            for payload in (statePayload, percentagePayload, gcodePayload, printerMetadata)
            if payload is not None
        ]
        
        # CRITICAL: Add the nested 'print' object as a source
        # This is where Bambu printers store most of the status data
        if isinstance(statePayload, dict):
            print_data = statePayload.get("print", {})
            if isinstance(print_data, dict) and print_data:
                sources.insert(0, print_data)  # Insert at beginning for priority

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

        # Chamber temperature
        chamberCandidate = self._findValue(
            sources,
            {
                "chamber_temper",
                "chamber_temp",
                "chamberTemp",
                "chamber_temperature",
                "chamber",
            },
        )
        chamberTemp = self._coerceFloat(chamberCandidate)

        # Layer information
        currentLayerCandidate = self._findValue(
            sources,
            {"layer_num", "current_layer", "currentLayer", "layer", "current_layer_num"},
        )
        currentLayer = self._coerceInt(currentLayerCandidate)

        totalLayersCandidate = self._findValue(
            sources,
            {"total_layer_num", "total_layers", "totalLayers", "layer_count"},
        )
        totalLayers = self._coerceInt(totalLayersCandidate)

        # File name
        fileNameCandidate = self._findValue(
            sources,
            {"subtask_name", "gcode_file", "file_name", "fileName", "print_file", "task_name"},
        )
        fileName = self._coerceString(fileNameCandidate)

        # Light state
        lightStateCandidate = self._findValue(
            sources,
            {"lights_report", "light_state", "lightState", "led_state", "chamber_light"},
        )
        lightState = self._coerceString(lightStateCandidate)

        # ========== NEW PRINT JOB FIELDS ==========
        
        # Print type (e.g., "local", "cloud", "sd_card")
        printTypeCandidate = self._findValue(
            sources,
            {"print_type", "printType", "task_type", "job_type"},
        )
        printType = self._coerceString(printTypeCandidate)

        # Gcode file (separate from file name - full path)
        gcodeFileCandidate = self._findValue(
            sources,
            {"gcode_file", "gcodeFile", "file_path", "gcode_path"},
        )
        gcodeFile = self._coerceString(gcodeFileCandidate)

        # Print error code
        printErrorCodeCandidate = self._findValue(
            sources,
            {"print_error", "print_error_code", "printErrorCode", "error_code", "mc_print_error_code"},
        )
        printErrorCode = self._coerceInt(printErrorCodeCandidate)

        # Skipped objects
        skippedObjectsCandidate = self._findValue(
            sources,
            {"skipped_objects", "skippedObjects", "skip_objects"},
        )
        skippedObjects = None
        if skippedObjectsCandidate is not None:
            if isinstance(skippedObjectsCandidate, list):
                skippedObjects = skippedObjectsCandidate
            else:
                skippedObjects = self._coerceString(skippedObjectsCandidate)

        # ========== END NEW FIELDS ==========

        jobCandidate = self._findValue(
            sources,
            {"job_id", "task_id", "current_job_id", "print_id", "jobId"},
        )
        currentJobId = self._coerceString(jobCandidate)

        if self.statusDebugEnabled:
            self.log.info(
                "[status] parsed progress=%s remaining=%s nozzle=%s bed=%s chamber=%s gcode=%s layer=%s/%s",
                progressPercent,
                remainingTimeSeconds,
                nozzleTemp,
                bedTemp,
                chamberTemp,
                gcodeState,
                currentLayer,
                totalLayers,
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

        # Handle "Cannot send print job" error (0x05004004 / 83902468)
        # This occurs when trying to upload/print while printer is busy
        if printErrorCode == 83902468:
            busyMessage = "Printer Busy (Job in progress)"
            if not errorMessage:
                errorMessage = busyMessage
            elif busyMessage not in errorMessage:
                errorMessage = f"{errorMessage} - {busyMessage}"
            
            # Use a more descriptive state than "FAILED"
            if gcodeState == "FAILED":
                gcodeState = "BUSY"


        # CRITICAL FIX: Always use 0.0 for temperatures instead of None
        # This ensures the entire pipeline (GUI, JSON storage, backend API) gets consistent data
        # Frontend/GUI checks "is not None" before updating - None values cause fields to be skipped
        normalized: Dict[str, Any] = {
            "status": "update",
            "state": stateText,
            "gcodeState": gcodeState,
            "progressPercent": progressPercent,
            "nozzleTemp": nozzleTemp if nozzleTemp is not None else 0.0,
            "bedTemp": bedTemp if bedTemp is not None else 0.0,
            "chamberTemp": chamberTemp if chamberTemp is not None else 0.0,
            "remainingTimeSeconds": remainingTimeSeconds if remainingTimeSeconds is not None else 0,
            "fanSpeedPercent": fanSpeedPercent if fanSpeedPercent is not None else 0.0,
            "printSpeed": printSpeed,
            "filamentUsed": filamentUsed,
            "currentLayer": currentLayer,
            "totalLayers": totalLayers,
            "fileName": fileName,
            "lightState": lightState,
            "currentJobId": currentJobId,
            "firmwareVersion": firmwareVersion,
            "hmsCode": hmsCode,
            "errorMessage": errorMessage,
            "hasAmsConflict": hasAmsConflict,
            # NEW PRINT JOB FIELDS
            "printType": printType,
            "gcodeFile": gcodeFile,
            "printErrorCode": printErrorCode,
            "skippedObjects": skippedObjects,
            # END NEW FIELDS
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

        # NEW PRINT JOB FIELDS - Add all fields shown in Print Job tab
        fileName = self._coerceString(snapshot.get("fileName"))
        if fileName:
            optionalFields["fileName"] = fileName

        currentLayer = self._coerceInt(snapshot.get("currentLayer"))
        if currentLayer is not None:
            optionalFields["currentLayer"] = currentLayer

        totalLayers = self._coerceInt(snapshot.get("totalLayers"))
        if totalLayers is not None:
            optionalFields["totalLayers"] = totalLayers

        lightState = self._coerceString(snapshot.get("lightState"))
        if lightState:
            optionalFields["lightState"] = lightState

        printType = self._coerceString(snapshot.get("printType"))
        if printType:
            optionalFields["printType"] = printType

        gcodeFile = self._coerceString(snapshot.get("gcodeFile"))
        if gcodeFile:
            optionalFields["gcodeFile"] = gcodeFile

        printErrorCode = self._coerceInt(snapshot.get("printErrorCode"))
        if printErrorCode is not None and printErrorCode != 0:
            optionalFields["printErrorCode"] = printErrorCode

        skippedObjects = snapshot.get("skippedObjects")
        if skippedObjects:
            optionalFields["skippedObjects"] = skippedObjects

        gcodeState = self._coerceString(snapshot.get("gcodeState"))
        if gcodeState:
            optionalFields["gcodeState"] = gcodeState

        chamberTemp = self._coerceFloat(snapshot.get("chamberTemp"))
        if chamberTemp is not None:
            optionalFields["chamberTemp"] = chamberTemp

        # END NEW PRINT JOB FIELDS

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

    def _reportPrinterStatus(
        self,
        printer_serial: str,
        printer_ip: str,
        status_data: Dict[str, Any],
    ) -> None:
        """
        Report printer status to backend API using StatusReporter.

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            status_data: Raw status data from MQTT
        """
        self.log.debug(f"🔍 _reportPrinterStatus called for {printer_serial}")

        if not self.status_reporter:
            self.log.warning(f"⚠️  StatusReporter is None - cannot report status for {printer_serial}")
            return

        self.log.debug(f"✅ StatusReporter exists, calling report_status()...")

        try:
            result = self.status_reporter.report_status(
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                status_data=status_data,
            )

            if result:
                self.log.debug(
                    f"Status reported for {printer_serial}: {result.get('ok', False)}"
                )

        except Exception as e:
            self.log.error(f"❌ Failed to report status for {printer_serial}: {e}")
            import traceback
            self.log.error(f"   Traceback:\n{traceback.format_exc()}")

    def _completedStateDetected(self, status_data: Dict[str, Any]) -> bool:
        completionStates = {"finish", "finished", "completed", "complete"}

        for key in ("gcodeState", "state"):
            stateValue = self._coerceString(status_data.get(key))
            if stateValue:
                normalizedState = stateValue.strip().lower()
                if normalizedState in completionStates:
                    return True

        progressValue = status_data.get("progressPercent")
        progressPercent = self._coerceFloat(progressValue)
        if progressPercent is not None and progressPercent >= 100.0:
            return True

        return False

    def _extractCompletedFileName(self, status_data: Dict[str, Any]) -> str:
        raw_state = status_data.get("rawStatePayload") or {}
        candidates = [
            status_data.get("fileName"),
            status_data.get("projectName"),
            self._findValue([raw_state], {"jobName", "taskName", "subtaskName", "file", "fileName"}),
        ]

        for candidate in candidates:
            nameValue = self._coerceString(candidate)
            if nameValue:
                return nameValue

        return "unknown"

    def _maybeReportJobCompletion(
        self,
        status_data: Dict[str, Any],
        printer_serial: str,
        printer_ip: str
    ) -> None:
        if not self.event_reporter:
            return

        jobId = self._coerceString(status_data.get("currentJobId"))

        if not self._completedStateDetected(status_data):
            return

        fileName = self._extractCompletedFileName(status_data)

        with self.completedJobsLock:
            reportedJobs = self.completedJobIds.setdefault(printer_serial, set())
            # Use job ID if available, otherwise use file name as fallback key
            reportKey = jobId if jobId else fileName
            if reportKey in reportedJobs:
                return
            reportedJobs.add(reportKey)

        self.log.info("═" * 80)
        self.log.info("🎉 PRINT JOB COMPLETION DETECTED")
        self.log.info(f"   Printer: {printer_serial}")
        self.log.info(f"   File: {fileName}")
        self.log.info(f"   Job ID: {jobId or 'N/A'}")
        self.log.info(f"   State: {status_data.get('gcodeState')}")
        self.log.info(f"   Progress: {status_data.get('progressPercent')}%")

        try:
            eventId = self.event_reporter.report_job_completed(
                printer_serial=printer_serial,
                printer_ip=printer_ip,
                print_job_id=jobId or "unknown",
                file_name=fileName,
            )

            if eventId:
                self.log.info(f"✅ Job completion reported: {eventId}")
            else:
                self.log.warning("⚠️  Job completion report returned no event_id")

        except Exception as error:
            self.log.error(f"❌ Failed to report job completion: {error}")

        self.log.info("═" * 80)

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

            # Method 1: Use config.get() - the correct API
            printers = config.get('printers', [])
            if isinstance(printers, list):
                for printer in printers:
                    if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                        access_code = printer.get('accessCode')
                        if access_code:
                            self.log.debug(f"✅ Found access code for {printer_serial} (config.get)")
                            return access_code

            # Method 2: Try to_dict() as fallback
            try:
                config_data = config.to_dict()
                if isinstance(config_data, dict):
                    printers = config_data.get('printers', [])
                    if isinstance(printers, list):
                        for printer in printers:
                            if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                                access_code = printer.get('accessCode')
                                if access_code:
                                    self.log.debug(f"✅ Found access code for {printer_serial} (to_dict)")
                                    return access_code
            except Exception as e:
                self.log.debug(f"to_dict() fallback failed: {e}")

            # Method 3: Try direct _config access as last resort
            try:
                if hasattr(config, '_config') and isinstance(config._config, dict):
                    printers = config._config.get('printers', [])
                    if isinstance(printers, list):
                        for printer in printers:
                            if isinstance(printer, dict) and printer.get('serialNumber') == printer_serial:
                                access_code = printer.get('accessCode')
                                if access_code:
                                    self.log.debug(f"✅ Found access code for {printer_serial} (_config)")
                                    return access_code
            except Exception as e:
                self.log.debug(f"_config fallback failed: {e}")

            self.log.warning(f"⚠️  No access code found for printer {printer_serial}")
            self.log.debug(f"   Config type: {type(config)}")
            self.log.debug(f"   Available methods: {[m for m in dir(config) if not m.startswith('_')]}")

            return None

        except Exception as e:
            self.log.error(f"❌ Failed to get access code: {e}")
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
            # If this is the first time we're checking this printer, 
            # capture immediately (image is already saved to disk, just need to upload)
            if printer_serial not in self.last_camera_capture:
                self.log.info(f"📷 First camera check for {printer_serial} - uploading immediately")
                self.last_camera_capture[printer_serial] = current_time
                return True  # Upload immediately on first check
            
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

    def _capture_camera_image_to_file(
        self,
        printer_serial: str,
        printer_ip: str,
        access_code: Optional[str] = None
    ) -> Optional[str]:
        """Find the most recent camera image from disk for this printer.

        Images are already captured by captureCameraSnapshot in command_controller.py
        and saved to ~/.printmaster/camera/<date>/<serial>/

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address (unused, kept for API compatibility)
            access_code: Unused, kept for API compatibility

        Returns:
            Path to the most recent image file, or None if no image found
        """

        import os
        import glob
        from datetime import datetime

        try:
            self.log.info("=" * 80)
            self.log.info("📸 FINDING LATEST CAMERA IMAGE")
            self.log.info(f"   Printer Serial: {printer_serial}")

            # Look for existing images in today's directory
            date_str = datetime.utcnow().strftime('%Y-%m-%d')
            camera_dir = os.path.join(
                os.path.expanduser('~'),
                '.printmaster',
                'camera',
                date_str,
                printer_serial
            )

            self.log.info(f"   Looking in: {camera_dir}")

            if not os.path.isdir(camera_dir):
                self.log.warning(f"   ⚠️  Camera directory does not exist: {camera_dir}")
                self.log.info("=" * 80)
                return None

            # Find all jpg files for this printer
            pattern = os.path.join(camera_dir, f"{printer_serial}-*.jpg")
            image_files = glob.glob(pattern)

            if not image_files:
                self.log.warning(f"   ⚠️  No images found matching: {pattern}")
                self.log.info("=" * 80)
                return None

            # Get the most recent file (by modification time)
            latest_image = max(image_files, key=os.path.getmtime)
            file_size = os.path.getsize(latest_image)
            file_size_kb = file_size / 1024

            self.log.info(f"   ✅ Found {len(image_files)} image(s)")
            self.log.info(f"   📸 Using latest: {os.path.basename(latest_image)}")
            self.log.info(f"   Size: {file_size_kb:.2f} KB ({file_size} bytes)")
            self.log.info("=" * 80)

            return latest_image

        except Exception as e:
            self.log.error(f"❌ FINDING CAMERA IMAGE FAILED")
            self.log.error(f"   Error: {e}")
            import traceback
            self.log.error(f"   Traceback:\n{traceback.format_exc()}")
            self.log.info("=" * 80)
            return None

    def _sanitizeErrorMessage(self, message: str, accessCode: str) -> str:
        if accessCode and accessCode in message:
            return message.replace(accessCode, "***")
        return message
