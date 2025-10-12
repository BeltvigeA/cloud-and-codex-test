"""Utilities for dispatching print jobs to Bambu Lab printers."""

from __future__ import annotations

import base64
import importlib
import io
import json
import os
import re
import shutil
import socket
import ssl
import tempfile
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, Callable, Dict, Iterable, List, Optional, Sequence, Union

from urllib.parse import urljoin

from ftplib import FTP_TLS, error_perm

try:  # pragma: no cover - optional dependency in tests
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:  # pragma: no cover - handled gracefully by callers
    mqtt = None  # type: ignore

import requests


_bambulabsApiModule = importlib.util.find_spec("bambulabs_api")
if _bambulabsApiModule is not None:
    bambulabsApi = importlib.import_module("bambulabs_api")
else:
    bambulabsApi = None


logger = logging.getLogger(__name__)


def makeTlsContext(insecure: bool = True) -> ssl.SSLContext:
    """Create a TLS context tuned for Bambu printers."""

    context = ssl.create_default_context()
    try:  # pragma: no cover - depends on OpenSSL version
        context.options |= ssl.OP_NO_TLSv1_3
    except Exception:
        pass

    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:  # pragma: no cover - depends on OpenSSL cipher availability
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except Exception:
        pass

    return context


class ImplicitFtpTls(FTP_TLS):
    """Implicit FTPS client where TLS handshakes on connect()."""

    def __init__(self, *args, context: Optional[ssl.SSLContext] = None, **kwargs):
        tlsContext = context or makeTlsContext(insecure=True)
        super().__init__(*args, context=tlsContext, **kwargs)
        self.context = tlsContext

    def connect(
        self,
        host: str = "",
        port: int = 990,
        timeout: Optional[int] = None,
        source_address=None,
    ) -> str:
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout is not None:
            self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), self.timeout, source_address)
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


def encodeFileToBase64(filePath: Path) -> str:
    """Read the given file and return a base64 encoded string."""

    with open(filePath, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def packageGcodeToThreeMfBytes(gcodeText: str, platePath: str = "Metadata/plate_1.gcode") -> io.BytesIO:
    """Create a minimal 3MF archive containing the provided G-code text."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(platePath, gcodeText)
    buffer.seek(0)
    return buffer


def packageGcodeToThreeMf(sourcePath: Path, *, destinationPath: Optional[Path] = None) -> Path:
    """Wrap a raw G-code file in a minimal 3MF container on disk."""

    targetPath = destinationPath or sourcePath.with_suffix(".3mf")
    targetPath.parent.mkdir(parents=True, exist_ok=True)
    gcodeText = sourcePath.read_text(encoding="utf-8", errors="ignore")
    buffer = packageGcodeToThreeMfBytes(gcodeText)
    targetPath.write_bytes(buffer.getvalue())
    return targetPath


def buildCloudJobPayload(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    safeName: str,
    paramPath: Optional[str],
    plateIndex: Optional[int],
    useAms: bool,
    bedLeveling: bool,
    layerInspect: bool,
    flowCalibration: bool,
    vibrationCalibration: bool,
    secureConnection: bool,
    localPath: Path,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ip": ip,
        "serial": serial,
        "accessCode": accessCode,
        "fileName": safeName,
        "sdFileName": safeName,
        "originalFileName": localPath.name,
        "useAms": useAms,
        "bedLeveling": bedLeveling,
        "layerInspect": layerInspect,
        "flowCalibration": flowCalibration,
        "vibrationCalibration": vibrationCalibration,
        "secureConnection": secureConnection,
        "fileData": encodeFileToBase64(localPath),
    }
    if plateIndex is not None:
        payload["plateIndex"] = plateIndex
    if paramPath:
        payload["paramPath"] = paramPath
    return payload


def sendPrintJobViaCloud(baseUrl: str, jobPayload: Dict[str, Any], timeoutSeconds: int = 120) -> Dict[str, Any]:
    """Send a print job to the external cloud API and return the response."""

    normalizedBaseUrl = baseUrl.rstrip("/") + "/"
    endpoint = urljoin(normalizedBaseUrl, "print")
    response = requests.post(endpoint, json=jobPayload, timeout=timeoutSeconds)
    response.raise_for_status()
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _parseReactivateStorCommands() -> List[str]:
    envValue = os.environ.get("BAMBU_FTPS_REACTIVATE_STOR_COMMANDS")
    if not envValue:
        return ["ENABLE_STOR"]

    commands = [entry.strip() for entry in envValue.split(",")]
    return [command for command in commands if command]


def reactivateStor(ftpsClient: FTP_TLS) -> None:
    commands = _parseReactivateStorCommands()
    for command in commands:
        normalized = command.upper()
        if normalized.startswith("SITE "):
            fullCommand = command
        else:
            fullCommand = f"SITE {command}"
        try:
            ftpsClient.sendcmd(fullCommand)
        except Exception:
            continue


def uploadViaFtps(
    *,
    ip: str,
    accessCode: str,
    localPath: Path,
    remoteName: str,
    insecureTls: bool = True,
    timeout: int = 120,
    dataStream: Optional[BinaryIO] = None,
) -> str:
    """Upload a file to the printer SD card using FTPS."""

    tlsContext = makeTlsContext(insecure=insecureTls)
    ftps = ImplicitFtpTls(context=tlsContext)
    port = 990
    try:
        ftps.connect(ip, port, timeout=timeout)
    except (OSError, socket.timeout, ssl.SSLError, EOFError) as connectionError:
        errorMessage = f"Failed to connect to Bambu printer FTPS endpoint {ip}:{port}: {connectionError}"
        logger.error(errorMessage)
        raise RuntimeError(errorMessage) from connectionError
    ftps.timeout = timeout
    try:
        ftps.login("bblp", accessCode)
        try:
            ftps.sendcmd("OPTS UTF8 ON")
        except Exception:
            pass
        ftps.prot_p()
        ftps.voidcmd("TYPE I")
        ftps.set_pasv(True)
        try:
            logger.debug("FTP FEAT: %s", ftps.sendcmd("FEAT"))
        except Exception:
            pass

        usePrefix = ""
        try:
            ftps.cwd("/sdcard")
        except Exception:
            try:
                ftps.cwd("sdcard")
            except Exception:
                usePrefix = "sdcard/"

        try:
            currentDirectory = ftps.pwd()
        except Exception:
            currentDirectory = "?"
        logger.debug("FTP PWD: %s", currentDirectory)

        def buildStorageCommand(fileName: str) -> str:
            return f"STOR {usePrefix}{fileName}"

        if dataStream is not None:
            try:
                dataStream.seek(0)
            except Exception:
                pass
            uploadBytes = dataStream.read()
        else:
            uploadBytes = localPath.read_bytes()
        uploadHandle: BinaryIO = io.BytesIO(uploadBytes)

        def performUpload(command: str, fileName: str) -> str:
            uploadHandle.seek(0)
            response = ftps.storbinary(command, uploadHandle, blocksize=64 * 1024)
            logger.debug(
                "FTPS response: %r (type=%s)", response, type(response).__name__
            )
            responseText = str(response or "")
            if not responseText.startswith("226"):
                raise RuntimeError(
                    f"FTPS transfer did not complete successfully for {fileName}: {response}"
                )
            return responseText

        sanitizedFileName = sanitizeThreeMfName(remoteName)
        baseStem = Path(sanitizedFileName).stem or "upload"
        extension = ".3mf"

        def buildAlternativeName(index: int) -> str:
            candidate = f"{baseStem}_{index}{extension}"
            return sanitizeThreeMfName(candidate)

        currentFileName = sanitizedFileName
        storageCommand = buildStorageCommand(currentFileName)
        logger.debug(
            "FTPS STOR command: %s  (fileName=%s,len=%d)",
            storageCommand,
            currentFileName,
            len(currentFileName),
        )
        try:
            performUpload(storageCommand, currentFileName)
            return currentFileName
        except error_perm as initialError:
            errorText = str(initialError)
            if "550" not in errorText:
                raise

            lastError: Optional[error_perm] = initialError
            for attempt in range(1, 6):
                alternativeName = buildAlternativeName(attempt)
                logger.warning(
                    "FTPS 550 on %s, retrying with alternative name", currentFileName
                )
                reactivateStor(ftps)
                storageCommand = buildStorageCommand(alternativeName)
                logger.debug(
                    "FTPS STOR command: %s  (fileName=%s,len=%d)",
                    storageCommand,
                    alternativeName,
                    len(alternativeName),
                )
                try:
                    performUpload(storageCommand, alternativeName)
                    return alternativeName
                except error_perm as uploadError:
                    lastError = uploadError
                    currentFileName = alternativeName
                    if "550" in str(uploadError) and attempt < 5:
                        continue
                    raise

            if lastError is not None:
                raise lastError
            raise RuntimeError("FTPS upload failed without specific error")
    finally:
        try:
            ftps.quit()
        except Exception:
            try:
                ftps.close()
            except Exception:
                pass


@dataclass
class BambuApiUploadSession:
    printer: Any
    remoteName: str
    connectCamera: bool


def waitForMqttReady(printer: Any, *, timeoutSeconds: float = 10.0, pollIntervalSeconds: float = 0.3) -> None:
    """Block until the bambulabs_api printer reports ready MQTT state."""

    logger.info("Venter på at MQTT skal bli klar ...")
    stateAccessor = getattr(printer, "get_state", None)
    statusAccessor = getattr(printer, "get_status", None)
    startTime = time.monotonic()
    lastError: Optional[Exception] = None

    while time.monotonic() - startTime < timeoutSeconds:
        try:
            if stateAccessor is not None:
                stateAccessor()
            elif statusAccessor is not None:
                statusAccessor()
            logger.info("MQTT ready")
            return
        except Exception as error:  # pragma: no cover - depends on printer timing
            lastError = error
            time.sleep(pollIntervalSeconds)
            continue

        time.sleep(pollIntervalSeconds)

    errorMessage = "MQTT ble ikke klar i tide"
    logger.error(errorMessage)
    if lastError is not None:
        raise RuntimeError(errorMessage) from lastError
    raise RuntimeError(errorMessage)


def publishSpoolStart(
    *,
    printer: Any,
    ip: str,
    accessCode: str,
    serial: str,
    uploadName: str,
    paramPathOrPlate: Union[str, int, None],
) -> None:
    """Send a project_file command for external spool printing."""

    if mqtt is None:  # pragma: no cover - exercised when dependency missing
        raise RuntimeError("paho-mqtt is required for spool start publishing")

    logger.debug("Benytter printer-objekt id=%s for spool-start", id(printer))

    if isinstance(paramPathOrPlate, int):
        plateNumber = max(1, paramPathOrPlate)
        paramValue = f"Metadata/plate_{plateNumber}.gcode"
    elif paramPathOrPlate:
        paramValue = str(paramPathOrPlate)
    else:
        paramValue = "Metadata/plate_1.gcode"

    payload = {
        "print": {
            "sequence_id": str(int(time.time() * 1000) % 10_000_000),
            "command": "project_file",
            "url": f"file:///sdcard/{uploadName}",
            "param": paramValue,
            "use_ams": False,
            "bed_levelling": True,
            "flow_cali": True,
            "vibration_cali": False,
            "layer_inspect": True,
            "timelapse": True,
            "bed_type": "auto",
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": uploadName,
            "md5": "",
        }
    }

    logger.info(
        "Publishing project_file via API: url=file:///sdcard/%s param=%s use_ams=False",
        uploadName,
        paramValue,
    )

    client = mqtt.Client()
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.username_pw_set("bblp", accessCode)
    client.connect(ip, 8883, keepalive=60)
    client.loop_start()
    time.sleep(0.3)
    publishInfo = client.publish(f"device/{serial}/request", json.dumps(payload), qos=1)
    publishInfo.wait_for_publish()
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()


def waitForStartAck(printer: Any, *, timeoutSeconds: int = 60) -> bool:
    """Wait until the printer transitions into an active printing state."""

    startedSuccessfully, stateValue, progressValue, gcodeStateValue = waitForPrinterStart(
        printer, timeoutSeconds=timeoutSeconds
    )

    if startedSuccessfully:
        statusParts: List[str] = []
        if stateValue:
            statusParts.append(f"state={stateValue}")
        if gcodeStateValue:
            statusParts.append(f"gcodeState={gcodeStateValue}")
        if progressValue is not None:
            statusParts.append(f"progress={progressValue:.1f}%")
        statusSummary = " ".join(statusParts) or "bekreftet"
        logger.info("Start-ACK: %s", statusSummary)
    else:
        logger.warning(
            "Ingen start-ACK innen %ds. Vil ikke sende ny start; går over i monitor-only.",
            timeoutSeconds,
        )

    return startedSuccessfully


@contextmanager
def uploadViaBambulabsApi(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    localPath: Path,
    remoteName: str,
    connectCamera: bool = False,
) -> BambuApiUploadSession:
    """Upload a file using the official bambulabs_api client."""

    if bambulabsApi is None:
        raise RuntimeError("bambulabs_api is required for this upload strategy")

    printerClass = getattr(bambulabsApi, "Printer", None)
    if printerClass is None:
        raise RuntimeError("bambulabs_api.Printer is not available")

    printer = printerClass(ip, accessCode, serial)

    normalizedRemoteName = sanitizeThreeMfName(remoteName)
    if normalizedRemoteName != remoteName:
        logger.debug(
            "Normalized remote name for bambulabs_api upload: %s -> %s",
            remoteName,
            normalizedRemoteName,
        )

    mqttStarted = False
    cameraStarted = False

    connectMethod = getattr(printer, "connect", None)
    mqttStart = getattr(printer, "mqtt_start", None)
    if connectCamera and connectMethod:
        connectMethod()
        cameraStarted = True
    elif mqttStart:
        mqttStart()
        mqttStarted = True
    elif connectMethod:
        connectMethod()
        cameraStarted = True

    uploadMethod = getattr(printer, "upload_file", None)
    if uploadMethod is None:
        raise RuntimeError("Unable to locate upload_file on bambulabs_api.Printer")

    logger.info("Uploading via bambulabs_api as %s", normalizedRemoteName)
    with open(localPath, "rb") as fileHandle:
        response = uploadMethod(fileHandle, normalizedRemoteName)
    logger.info(
        "Upload response: %r (type=%s)", response, type(response).__name__
    )
    uploadResponseText = "" if response is None else str(response)
    uploadSuccessful = response is True or ("226" in uploadResponseText)
    if not uploadSuccessful:
        raise RuntimeError(f"Upload failed: {response}")

    try:
        yield BambuApiUploadSession(
            printer=printer, remoteName=normalizedRemoteName, connectCamera=cameraStarted
        )
    finally:
        try:
            if cameraStarted:
                disconnectMethod = getattr(printer, "disconnect", None)
                if disconnectMethod:
                    disconnectMethod()
            elif mqttStarted:
                mqttStop = getattr(printer, "mqtt_stop", None)
                if mqttStop:
                    mqttStop()
        except Exception:
            pass


def startViaBambuapiAfterUpload(
    printer: Any,
    remoteName: str,
    paramPath: Optional[str],
    plateIndex: Optional[int],
    *,
    useAms: bool,
    ip: str,
    accessCode: str,
    serial: str,
) -> bool:
    waitForMqttReady(printer)

    if useAms:
        if paramPath:
            startArgument: Any = paramPath
        else:
            startArgument = int(plateIndex or 1)
        logger.info(
            "Publishing project_file via API: url=file:///sdcard/%s param=%s use_ams=True",
            remoteName,
            startArgument,
        )
        printer.start_print(remoteName, startArgument)
        logger.info("Startkommando sendt")
    else:
        spoolParam: Union[str, int, None]
        if paramPath:
            spoolParam = paramPath
        else:
            spoolParam = f"Metadata/plate_{max(1, int(plateIndex or 1))}.gcode"
        publishSpoolStart(
            printer=printer,
            ip=ip,
            accessCode=accessCode,
            serial=serial,
            uploadName=remoteName,
            paramPathOrPlate=spoolParam,
        )

    return waitForStartAck(printer)


def waitForPrinterStart(
    printer: Any,
    *,
    timeoutSeconds: int = 60,
    pollIntervalSeconds: float = 2.0,
) -> tuple[bool, Optional[str], Optional[float], Optional[str]]:
    startTime = time.monotonic()
    stateAccessor = getattr(printer, "get_state", None)
    percentageAccessor = getattr(printer, "get_percentage", None)
    gcodeStateAccessor = getattr(printer, "get_gcode_state", None)
    statusAccessor = getattr(printer, "get_status", None)

    lastStateValue: Optional[str] = None
    lastProgressValue: Optional[float] = None
    lastGcodeStateValue: Optional[str] = None

    if not any((stateAccessor, percentageAccessor, gcodeStateAccessor, statusAccessor)):
        return False, None, None, None

    def safeInvoke(accessor: Optional[Callable[[], Any]]) -> Any:
        if accessor is None:
            return None
        try:
            return accessor()
        except Exception:
            logger.debug("Failed to query printer state", exc_info=True)
            return None

    def normalizeState(rawValue: Any) -> Optional[str]:
        if rawValue is None:
            return None
        text = str(rawValue).strip()
        return text or None

    def normalizeProgress(rawValue: Any) -> Optional[float]:
        if rawValue is None:
            return None
        try:
            return float(rawValue)
        except (TypeError, ValueError):
            return None

    while time.monotonic() - startTime < timeoutSeconds:
        stateValue = normalizeState(safeInvoke(stateAccessor))
        gcodeStateValue = normalizeState(safeInvoke(gcodeStateAccessor))
        progressValue = normalizeProgress(safeInvoke(percentageAccessor))

        statusPayload: Optional[Any] = None
        if statusAccessor is not None:
            statusPayload = safeInvoke(statusAccessor)

        if stateValue is None and isinstance(statusPayload, dict):
            stateValue = normalizeState(statusPayload.get("state")) or normalizeState(
                statusPayload.get("print")
            )

        if gcodeStateValue is None and isinstance(statusPayload, dict):
            gcodeStateValue = normalizeState(statusPayload.get("gcode_state"))
            if gcodeStateValue is None:
                printSection = statusPayload.get("print")
                if isinstance(printSection, dict):
                    gcodeStateValue = normalizeState(printSection.get("gcode_state"))

        if progressValue is None and isinstance(statusPayload, dict):
            progressValue = normalizeProgress(statusPayload.get("percentage"))
            if progressValue is None:
                printSection = statusPayload.get("print")
                if isinstance(printSection, dict):
                    progressValue = normalizeProgress(printSection.get("percentage"))

        if stateValue:
            lastStateValue = stateValue
        if progressValue is not None:
            lastProgressValue = progressValue
        if gcodeStateValue:
            lastGcodeStateValue = gcodeStateValue

        stateLower = (stateValue or "").lower()
        gcodeUpper = (gcodeStateValue or "").upper()
        hasHeatingState = any(keyword in stateLower for keyword in ("heat", "warm", "run", "print"))
        hasActiveGcode = gcodeUpper in {"HEATING", "RUNNING", "PRINTING"}
        hasProgress = (progressValue or 0.0) > 0.0

        if hasHeatingState or hasActiveGcode or hasProgress:
            return True, stateValue, progressValue, gcodeStateValue

        time.sleep(pollIntervalSeconds)

    return False, lastStateValue, lastProgressValue, lastGcodeStateValue


def pickGcodeParamFrom3mf(path: Path, plateIndex: Optional[int]) -> tuple[Optional[str], List[str]]:
    """Inspect a .3mf archive and determine the gcode metadata path."""

    if path.suffix.lower() != ".3mf" and not path.suffix.lower().endswith(".3mf"):
        return None, []

    try:
        with zipfile.ZipFile(path, "r") as archive:
            candidates = [
                name
                for name in archive.namelist()
                if name.lower().startswith("metadata/") and name.lower().endswith(".gcode")
            ]

            def plateKey(name: str) -> int:
                match = re.search(r"plate[_\-]?(\d+)\.gcode$", name, re.IGNORECASE)
                if match:
                    return int(match.group(1))
                return 999999

            orderedCandidates = sorted(candidates, key=plateKey)
            if not orderedCandidates:
                return None, []

            if plateIndex is not None:
                requestedIndex = max(1, plateIndex)
                expectedName = f"Metadata/plate_{requestedIndex}.gcode"
                for candidate in orderedCandidates:
                    if candidate.lower() == expectedName.lower():
                        return candidate, orderedCandidates
                return None, orderedCandidates

            preferredName = "Metadata/plate_1.gcode"
            for candidate in orderedCandidates:
                if candidate.lower() == preferredName.lower():
                    return candidate, orderedCandidates
            return orderedCandidates[0], orderedCandidates
    except zipfile.BadZipFile:
        return None, []


def startPrintViaMqtt(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    sdFileName: str,
    paramPath: Optional[str],
    useAms: bool = False,
    bedLeveling: bool = True,
    layerInspect: bool = True,
    flowCalibration: bool = False,
    vibrationCalibration: bool = False,
    insecureTls: bool = True,
    waitSeconds: int = 12,
    statusWarmupSeconds: int = 5,
    sendStartCommand: bool = True,
    initialStatus: Optional[Dict[str, Any]] = None,
    statusCallback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Start a print job via MQTT and stream status messages."""

    if mqtt is None:  # pragma: no cover - exercised when dependency missing
        raise RuntimeError("paho-mqtt is required for MQTT print control")

    port = 8883
    topicReport = f"device/{serial}/report"
    topicRequest = f"device/{serial}/request"

    connectionReady = Event()
    connectionError: Optional[str] = None
    lastStatus: Dict[str, Any] = {}
    hmsWarningIssued = False

    def emitStatus(payload: Dict[str, Any]) -> None:
        if not statusCallback:
            return
        try:
            statusCallback(dict(payload))
        except Exception:  # pragma: no cover - callback exceptions should not stop MQTT loop
            logger.exception("Status callback failed for MQTT payload")

    def handleProgress(statusPayload: Dict[str, Any]) -> None:
        nonlocal lastStatus
        trackedKeys = ("mc_percent", "gcode_state", "mc_remaining_time", "nozzle_temper", "bed_temper")
        snapshot = {key: statusPayload.get(key) for key in trackedKeys if statusPayload.get(key) is not None}
        if not snapshot or snapshot == lastStatus:
            return
        lastStatus = snapshot
        emitStatus({"status": "progress", **snapshot})

    def onConnect(client: mqtt.Client, *args, **_kwargs):  # type: ignore[no-redef]
        nonlocal connectionError
        rc: Optional[int] = None
        reasonCode: Any = None
        if len(args) >= 3:
            third = args[2]
            if isinstance(third, int):
                rc = third
            else:
                reasonCode = third
        if rc is not None:
            ok = rc == 0
        elif reasonCode is not None:
            ok = not getattr(reasonCode, "is_failure", False)
        else:
            ok = True
        if ok:
            client.subscribe(topicReport, qos=1)
        else:
            description = getattr(reasonCode, "value", reasonCode)
            connectionError = f"MQTT connection failed: rc={rc or 'n/a'} reason={description}"
        connectionReady.set()

    def onMessage(_client: mqtt.Client, _userdata, message):  # type: ignore[no-redef]
        nonlocal hmsWarningIssued
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            return

        serialized = json.dumps(payload, ensure_ascii=False)
        if not hmsWarningIssued and "HMS_07FF-2000-0002-0004" in serialized:
            hmsWarningIssued = True
            emitStatus(
                {
                    "status": "error",
                    "error": (
                        "Fullfør Unload/trekk ut filament fra verktøyhodet før AMS-jobben – "
                        "deretter prøver vi igjen"
                    ),
                }
            )

        def findKey(obj: Any, key: str) -> Any:
            if isinstance(obj, dict):
                if key in obj:
                    return obj[key]
                for value in obj.values():
                    result = findKey(value, key)
                    if result is not None:
                        return result
            elif isinstance(obj, list):
                for value in obj:
                    result = findKey(value, key)
                    if result is not None:
                        return result
            return None

        statusMap = {
            key: findKey(payload, key)
            for key in ("mc_percent", "gcode_state", "mc_remaining_time", "nozzle_temper", "bed_temper")
        }
        handleProgress(statusMap)

    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", accessCode)

    if insecureTls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    else:
        client.tls_set()
        client.tls_insecure_set(False)

    client.on_connect = onConnect
    client.on_message = onMessage

    try:
        client.connect(ip, port, keepalive=60)
    except (OSError, socket.timeout, ssl.SSLError, EOFError) as connectionProblem:
        errorMessage = (
            f"Failed to connect to Bambu printer MQTT endpoint {ip}:{port} for serial {serial}: "
            f"{connectionProblem}"
        )
        logger.error(errorMessage)
        raise RuntimeError(errorMessage) from connectionProblem

    client.loop_start()

    if not connectionReady.wait(timeout=10):
        client.loop_stop()
        client.disconnect()
        raise RuntimeError(f"Timed out waiting for MQTT connection to {serial}")

    if connectionError:
        client.loop_stop()
        client.disconnect()
        raise RuntimeError(connectionError)

    url = f"file:///sdcard/{sdFileName}"

    statusPayload = initialStatus or {
        "status": "starting",
        "url": url,
        "param": paramPath,
        "useAms": bool(useAms),
        "bedLeveling": bool(bedLeveling),
        "layerInspect": bool(layerInspect),
        "flowCalibration": bool(flowCalibration),
        "vibrationCalibration": bool(vibrationCalibration),
    }

    if statusPayload:
        emitStatus(dict(statusPayload))

    if sendStartCommand:
        sequenceId = uuid.uuid4().hex
        payload = {
            "print": {
                "command": "project_file",
                "sequence_id": sequenceId,
                "url": url,
                "use_ams": bool(useAms),
                "bed_leveling": bool(bedLeveling),
                "layer_inspect": bool(layerInspect),
                "flow_cali": bool(flowCalibration),
                "vibration_cali": bool(vibrationCalibration),
            }
        }
        if paramPath:
            payload["print"]["param"] = paramPath

        logger.info(
            "Publishing project_file: url=%s param=%s use_ams=%s bed_leveling=%s layer_inspect=%s flow_cali=%s vibration_cali=%s",
            url,
            paramPath,
            bool(useAms),
            bool(bedLeveling),
            bool(layerInspect),
            bool(flowCalibration),
            bool(vibrationCalibration),
        )
        client.publish(topicRequest, json.dumps(payload), qos=1)
        logger.info("Startkommando sendt")
    else:
        logger.info("Monitoring MQTT status etter API-start")

    timeoutDeadline = time.time() + max(waitSeconds, statusWarmupSeconds, 0)
    while time.time() < timeoutDeadline:
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()



def postStatus(status: Dict[str, Any], printerConfig: Dict[str, Any]) -> None:
    """Send the latest printer status to the configured remote endpoint."""

    url = printerConfig.get("statusBaseUrl")
    apiKey = printerConfig.get("statusApiKey")
    recipientId = printerConfig.get("statusRecipientId")
    if not url or not apiKey:
        return

    payload = {
        "apiKey": apiKey,
        "recipientId": recipientId,
        "serialNumber": printerConfig.get("serialNumber"),
        "ipAddress": printerConfig.get("ipAddress"),
        "status": status.get("status") or status.get("state"),
        "nozzleTemp": status.get("nozzle_temper") or status.get("nozzleTemp"),
        "bedTemp": status.get("bed_temper") or status.get("bedTemp"),
        "progressPercent": status.get("mc_percent")
        or status.get("progress")
        or status.get("progressPercent"),
        "remainingTimeSeconds": status.get("mc_remaining_time")
        or status.get("remainingTimeSeconds"),
        "gcodeState": status.get("gcode_state") or status.get("gcodeState"),
    }

    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:  # pragma: no cover - logging optional
        logger.debug("Failed to post status update", exc_info=True)


@dataclass(frozen=True)
class BambuPrintOptions:
    ipAddress: str
    serialNumber: str
    accessCode: str
    brand: str = "Bambu Lab"
    nickname: Optional[str] = None
    useCloud: bool = False
    cloudUrl: Optional[str] = None
    cloudTimeout: int = 180
    useAms: bool = True
    bedLeveling: bool = True
    layerInspect: bool = True
    flowCalibration: bool = False
    vibrationCalibration: bool = False
    secureConnection: bool = False
    plateIndex: Optional[int] = None
    waitSeconds: int = 8
    lanStrategy: str = "legacy"


def sanitizeThreeMfName(name: str, maxLength: int = 60) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "").strip())
    if not base:
        base = "upload"

    if base.lower().endswith(".gcode"):
        base = os.path.splitext(base)[0] or "upload"

    if not base.lower().endswith(".3mf"):
        root, _ = os.path.splitext(base)
        base = f"{root or base}.3mf"

    stem, extension = os.path.splitext(base)
    if len(base) > maxLength:
        allowedRootLength = max(1, maxLength - len(extension))
        base = f"{stem[:allowedRootLength]}{extension}"

    return base


def normalizeRemoteFileName(name: str) -> str:
    return sanitizeThreeMfName(name)


def buildRemoteFileName(localPath: Path) -> str:
    return normalizeRemoteFileName(localPath.name)


def buildPrinterTransferFileName(localPath: Path) -> str:
    trimmedName = localPath.name
    match = re.match(r"^[0-9a-fA-F-]+_[0-9a-fA-F-]+_(.+)$", trimmedName)
    if match:
        trimmedName = match.group(1)
    return normalizeRemoteFileName(trimmedName)


def _normalizeString(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extractPlateIndex(plateElement: Any) -> Optional[str]:
    if plateElement is None:
        return None
    for metadataElement in plateElement.findall("metadata"):
        if metadataElement.get("key") == "index":
            return metadataElement.get("value")
    return None


def _findObjectElement(plateElement: Any, *, identifyId: Optional[str], objectName: Optional[str]) -> Optional[Any]:
    for objectElement in plateElement.findall("object"):
        elementId = _normalizeString(objectElement.get("identify_id") or objectElement.get("object_id") or objectElement.get("id"))
        if identifyId and elementId and identifyId == elementId:
            return objectElement
        elementName = _normalizeString(objectElement.get("name"))
        if objectName and elementName and objectName == elementName:
            return objectElement
    return None


def _ensureSkippedContainer(plateElement: Any) -> Any:
    skippedContainer = plateElement.find("skipped_objects")
    if skippedContainer is None:
        skippedContainer = ET.SubElement(plateElement, "skipped_objects")
    return skippedContainer


def _updateSkippedContainer(
    skippedContainer: Any,
    *,
    orderNumber: int,
    identifyId: Optional[str],
    objectName: Optional[str],
    plateId: Optional[str],
) -> None:
    orderText = str(orderNumber)
    existingElement: Optional[Any] = None
    for candidate in skippedContainer.findall("object"):
        candidateOrder = _normalizeString(candidate.get("order"))
        if candidateOrder == orderText:
            existingElement = candidate
            break

    if existingElement is None:
        existingElement = ET.SubElement(skippedContainer, "object")

    existingElement.set("order", orderText)
    if identifyId:
        existingElement.set("identify_id", identifyId)
    if objectName:
        existingElement.set("name", objectName)
    if plateId:
        existingElement.set("plate_id", plateId)


def applySkippedObjectsToArchive(archivePath: Path, skipTargets: Sequence[Dict[str, Any]]) -> None:
    if not skipTargets:
        return

    try:
        with zipfile.ZipFile(archivePath, "r") as archive:
            try:
                sliceInfo = archive.read("Metadata/slice_info.config")
            except KeyError as error:
                raise ValueError("3MF archive is missing slicer metadata (Metadata/slice_info.config)") from error
    except zipfile.BadZipFile as error:
        raise ValueError(f"{archivePath} is not a valid 3MF archive") from error

    root = ET.fromstring(sliceInfo)

    unmatchedOrders: List[int] = []
    appliedOrders: List[int] = []

    for target in skipTargets:
        orderNumber = target.get("order")
        if not isinstance(orderNumber, int):
            continue
        identifyId = _normalizeString(target.get("identifyId"))
        objectName = _normalizeString(target.get("objectName"))
        plateId = _normalizeString(target.get("plateId"))

        matchedObject: Optional[Any] = None
        matchedPlate: Optional[Any] = None

        for plateElement in root.findall("plate"):
            plateIndex = _normalizeString(_extractPlateIndex(plateElement))
            if plateId and plateIndex and plateId != plateIndex:
                continue
            candidateObject = _findObjectElement(
                plateElement,
                identifyId=identifyId,
                objectName=objectName,
            )
            if candidateObject is not None:
                matchedObject = candidateObject
                matchedPlate = plateElement
                break

        if matchedObject is None or matchedPlate is None:
            unmatchedOrders.append(orderNumber)
            continue

        matchedObject.set("skipped", "true")
        skippedContainer = _ensureSkippedContainer(matchedPlate)
        _updateSkippedContainer(
            skippedContainer,
            orderNumber=orderNumber,
            identifyId=identifyId,
            objectName=objectName,
            plateId=plateId,
        )
        appliedOrders.append(orderNumber)

    if unmatchedOrders:
        orderSummary = ", ".join(str(number) for number in sorted(set(unmatchedOrders)))
        logging.error("Unable to locate slicer objects for order(s): %s", orderSummary)
        raise ValueError(f"Unable to locate slicer objects for order(s): {orderSummary}")

    if not appliedOrders:
        return

    updatedSliceInfo = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    with zipfile.ZipFile(archivePath, "r") as archive:
        entries: List[zipfile.ZipInfo] = []
        contents: Dict[str, bytes] = {}
        for info in archive.infolist():
            entries.append(info)
            contents[info.filename] = archive.read(info.filename)

    contents["Metadata/slice_info.config"] = updatedSliceInfo

    with zipfile.ZipFile(archivePath, "w") as archive:
        for info in entries:
            data = contents.pop(info.filename, None)
            if data is None:
                continue
            archive.writestr(info, data)
        for name, data in contents.items():
            archive.writestr(name, data)


def sendBambuPrintJob(
    *,
    filePath: Path,
    options: BambuPrintOptions,
    statusCallback: Optional[Callable[[Dict[str, Any]], None]] = None,
    skippedObjects: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Upload a file and start a Bambu print job."""

    resolvedPath = filePath.expanduser().resolve()
    if not resolvedPath.exists():
        raise FileNotFoundError(resolvedPath)

    normalizedSuffix = resolvedPath.suffix.lower()
    if normalizedSuffix not in {".gcode", ".3mf"}:
        raise ValueError("Støtter kun .3mf eller .gcode for utskrift")

    plateIndex = options.plateIndex
    lanStrategy = (options.lanStrategy or "legacy").lower()

    remoteName = buildPrinterTransferFileName(resolvedPath)
    assert remoteName.lower().endswith(".3mf"), f"remoteName må være .3mf, fikk: {remoteName}"
    logger.debug("Resolved remote file name for upload: %s", remoteName)

    with tempfile.TemporaryDirectory() as temporaryDirectory:
        paramPath: Optional[str] = None
        tempDir = Path(temporaryDirectory)
        workingPath = tempDir / remoteName

        if resolvedPath.suffix.lower() == ".gcode":
            targetPlate = max(1, plateIndex or 1)
            platePath = f"Metadata/plate_{targetPlate}.gcode"
            gcodeText = resolvedPath.read_text(encoding="utf-8", errors="ignore")
            buffer = packageGcodeToThreeMfBytes(gcodeText, platePath=platePath)
            workingPath.write_bytes(buffer.getvalue())
            paramPath = platePath
        else:
            shutil.copy2(resolvedPath, workingPath)
            try:
                with zipfile.ZipFile(workingPath, "r"):
                    pass
            except zipfile.BadZipFile as zipError:
                raise ValueError(f"{resolvedPath} is not a valid 3MF archive") from zipError

            paramPath, candidates = pickGcodeParamFrom3mf(workingPath, plateIndex)
            if paramPath is None:
                if not candidates:
                    raise ValueError(
                        "3MF-arkivet mangler G-code i Metadata/. Eksporter med innebygd G-code "
                        "eller send .gcode slik at klienten kan pakke det automatisk."
                    )
                if plateIndex is not None:
                    requestedIndex = max(1, plateIndex)
                    expectedParam = f"Metadata/plate_{requestedIndex}.gcode"
                    raise ValueError(
                        f"3MF-arkivet mangler {expectedParam}. Tilgjengelige filer: {candidates}"
                    )
                paramPath = candidates[0]

        if skippedObjects:
            applySkippedObjectsToArchive(workingPath, skippedObjects)

        if options.useCloud and options.cloudUrl:
            payload = buildCloudJobPayload(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                safeName=remoteName,
                paramPath=paramPath,
                plateIndex=plateIndex,
                useAms=options.useAms,
                bedLeveling=options.bedLeveling,
                layerInspect=options.layerInspect,
                flowCalibration=options.flowCalibration,
                vibrationCalibration=options.vibrationCalibration,
                secureConnection=options.secureConnection,
                localPath=workingPath,
            )
            response = sendPrintJobViaCloud(options.cloudUrl, payload, timeoutSeconds=options.cloudTimeout)
            if statusCallback:
                statusCallback({"status": "cloudAccepted", "response": response})
            return {"method": "cloud", "remoteFile": remoteName, "paramPath": paramPath, "response": response}

        def uploadAndStartViaBambuApi() -> str:
            initialStatusPayload: Optional[Dict[str, Any]] = None
            with uploadViaBambulabsApi(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                localPath=workingPath,
                remoteName=remoteName,
            ) as session:
                uploaded = session.remoteName
                if statusCallback:
                    statusCallback(
                        {
                            "status": "uploaded",
                            "remoteFile": uploaded,
                            "originalRemoteFile": remoteName,
                            "param": paramPath,
                        }
                    )
                initialStatusPayload = {
                    "status": "starting",
                    "url": f"file:///sdcard/{uploaded}",
                    "param": paramPath,
                    "useAms": bool(options.useAms),
                    "bedLeveling": bool(options.bedLeveling),
                    "layerInspect": bool(options.layerInspect),
                    "flowCalibration": bool(options.flowCalibration),
                    "vibrationCalibration": bool(options.vibrationCalibration),
                }
                startViaBambuapiAfterUpload(
                    session.printer,
                    uploaded,
                    paramPath,
                    plateIndex,
                    useAms=bool(options.useAms),
                    ip=options.ipAddress,
                    accessCode=options.accessCode,
                    serial=options.serialNumber,
                )
            startPrintViaMqtt(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                sdFileName=uploaded,
                paramPath=paramPath,
                useAms=options.useAms,
                bedLeveling=options.bedLeveling,
                layerInspect=options.layerInspect,
                flowCalibration=options.flowCalibration,
                vibrationCalibration=options.vibrationCalibration,
                insecureTls=not options.secureConnection,
                waitSeconds=options.waitSeconds,
                sendStartCommand=False,
                initialStatus=initialStatusPayload,
                statusCallback=statusCallback,
            )
            return uploaded

        if lanStrategy == "bambuapi":
            uploadedName = uploadAndStartViaBambuApi()
            return {
                "method": "lan",  # LAN fallback via bambulabs_api
                "remoteFile": uploadedName,
                "originalRemoteFile": remoteName,
                "paramPath": paramPath,
            }

        try:
            uploadedName = uploadViaFtps(
                ip=options.ipAddress,
                accessCode=options.accessCode,
                localPath=workingPath,
                remoteName=remoteName,
                insecureTls=not options.secureConnection,
            )
        except error_perm as ftpsError:
            if "550" not in str(ftpsError):
                raise
            logger.warning(
                "FTPS feilet med 550, forsøker upload via bambulabs_api...", exc_info=True
            )
            uploadedName = uploadAndStartViaBambuApi()
            return {
                "method": "lan",
                "remoteFile": uploadedName,
                "originalRemoteFile": remoteName,
                "paramPath": paramPath,
            }

        if statusCallback:
            statusCallback(
                {
                    "status": "uploaded",
                    "remoteFile": uploadedName,
                    "originalRemoteFile": remoteName,
                    "param": paramPath,
                }
            )

        startPrintViaMqtt(
            ip=options.ipAddress,
            serial=options.serialNumber,
            accessCode=options.accessCode,
            sdFileName=uploadedName,
            paramPath=paramPath,
            useAms=options.useAms,
            bedLeveling=options.bedLeveling,
            layerInspect=options.layerInspect,
            flowCalibration=options.flowCalibration,
            vibrationCalibration=options.vibrationCalibration,
            insecureTls=not options.secureConnection,
            waitSeconds=options.waitSeconds,
            statusCallback=statusCallback,
        )

        return {
            "method": "lan",
            "remoteFile": uploadedName,
            "originalRemoteFile": remoteName,
            "paramPath": paramPath,
        }



def summarizeStatusMessages(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Utility to normalize status events for logging or persistence."""

    return [dict(event) for event in events]


__all__ = [
    "BambuPrintOptions",
    "ImplicitFtpTls",
    "buildCloudJobPayload",
    "buildRemoteFileName",
    "buildPrinterTransferFileName",
    "encodeFileToBase64",
    "makeTlsContext",
    "applySkippedObjectsToArchive",
    "pickGcodeParamFrom3mf",
    "postStatus",
    "BambuApiUploadSession",
    "publishSpoolStart",
    "uploadViaBambulabsApi",
    "startViaBambuapiAfterUpload",
    "sendBambuPrintJob",
    "sendPrintJobViaCloud",
    "startPrintViaMqtt",
    "summarizeStatusMessages",
    "uploadViaFtps",
    "waitForMqttReady",
    "waitForStartAck",
]

