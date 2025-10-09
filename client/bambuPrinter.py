"""Utilities for dispatching print jobs to Bambu Lab printers."""

from __future__ import annotations

import base64
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
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from urllib.parse import urljoin

from ftplib import FTP_TLS, error_perm

try:  # pragma: no cover - optional dependency in tests
    import paho.mqtt.client as mqtt  # type: ignore
except ImportError:  # pragma: no cover - handled gracefully by callers
    mqtt = None  # type: ignore

import requests


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
) -> str:
    """Upload a file to the printer SD card using FTPS."""

    tlsContext = makeTlsContext(insecure=insecureTls)
    ftps = ImplicitFtpTls(context=tlsContext)
    ftps.connect(ip, 990, timeout=timeout)
    ftps.timeout = timeout
    try:
        ftps.login("bblp", accessCode)
        ftps.prot_p()
        ftps.set_pasv(True)
        ftps.voidcmd("TYPE I")

        fileName = os.path.basename(remoteName)
        fallbackSeedName = fileName

        storageCommand = f"STOR {fileName}"
        remoteDeleteTargets = []

        def deleteRemotePath(remotePath: str) -> None:
            try:
                ftps.delete(remotePath)
            except Exception as deleteError:  # pragma: no cover - exercised via specific tests
                errorMessage = str(deleteError).lower()
                if "not found" in errorMessage or "no such file" in errorMessage:
                    return
                raise

        def buildFallbackFileName(originalName: str) -> str:
            baseName, extension = os.path.splitext(originalName)
            safeBase = baseName or "upload"
            timestampPart = str(int(time.time()))
            uniquePart = uuid.uuid4().hex[:8]
            return f"{safeBase}_{timestampPart}_{uniquePart}{extension}"

        savedDirectory: Optional[str] = None

        try:
            ftps.cwd("/sdcard")
            savedDirectory = "/sdcard"
            remoteDeleteTargets.append(fileName)
        except Exception:
            try:
                ftps.cwd("sdcard")
                savedDirectory = "sdcard"
                remoteDeleteTargets.append(fileName)
            except Exception:
                storageCommand = f"STOR sdcard/{fileName}"
                remoteDeleteTargets.append(f"sdcard/{fileName}")

        def tryReenterSavedDirectory() -> None:
            if not savedDirectory:
                return
            try:
                ftps.cwd(savedDirectory)
            except Exception:
                return

        fallbackActive = False
        fallbackSource: Optional[str] = None
        fallbackRetriesAfterDelete = 0
        maxFallbackRetriesAfterDelete = 1

        def activateFallbackName(generatedName: str, *, source: str) -> None:
            nonlocal storageCommand, fileName, fallbackActive, fallbackSource, fallbackRetriesAfterDelete

            _, remoteStoragePath = storageCommand.split(" ", 1)
            remoteDirectory, _ = os.path.split(remoteStoragePath)
            if remoteDirectory:
                newRemotePath = f"{remoteDirectory}/{generatedName}"
            else:
                newRemotePath = generatedName
            storageCommand = f"STOR {newRemotePath}"
            fileName = generatedName
            fallbackActive = True
            fallbackSource = source
            if source == "delete":
                fallbackRetriesAfterDelete = 0
        for remoteTarget in remoteDeleteTargets:
            try:
                deleteRemotePath(remoteTarget)
            except error_perm as deleteError:
                if "550" in str(deleteError):
                    generatedName = buildFallbackFileName(fallbackSeedName)
                    activateFallbackName(generatedName, source="delete")
                    break
                raise

        def performUpload() -> None:
            with open(localPath, "rb") as handle:
                ftps.storbinary(storageCommand, handle, blocksize=64 * 1024)

        try:
            performUpload()
        except error_perm as uploadError:
            if "550" not in str(uploadError):
                raise
            reactivateStor(ftps)
            tryReenterSavedDirectory()
            try:
                performUpload()
            except error_perm as secondError:
                if "550" not in str(secondError):
                    raise
                allowExtraFallback = (
                    fallbackActive
                    and fallbackSource == "delete"
                    and fallbackRetriesAfterDelete < maxFallbackRetriesAfterDelete
                )
                if fallbackActive and not allowExtraFallback:
                    raise
                if allowExtraFallback:
                    fallbackRetriesAfterDelete += 1
                generatedName = buildFallbackFileName(fallbackSeedName)
                activateFallbackName(generatedName, source="stor")
                reactivateStor(ftps)
                tryReenterSavedDirectory()
                try:
                    performUpload()
                except error_perm as thirdError:
                    if "550" in str(thirdError):
                        raise
                    raise

        ftps.voidresp()
        return fileName
    finally:
        try:
            ftps.quit()
        except Exception:
            pass


def pickGcodeParamFrom3mf(path: Path, plateIndex: Optional[int]) -> tuple[Optional[str], List[str]]:
    """Inspect a .3mf archive and determine the gcode metadata path."""

    if path.suffix.lower() != ".3mf" and not path.suffix.lower().endswith(".3mf"):
        return None, []

    try:
        with zipfile.ZipFile(path, "r") as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith(".gcode")]

            def plateKey(name: str) -> int:
                match = re.search(r"plate[_\-]?(\d+)\.gcode$", name, re.IGNORECASE)
                if match:
                    return int(match.group(1))
                return 999999

            orderedCandidates = sorted(candidates, key=plateKey)
            if not orderedCandidates:
                return None, []

            if plateIndex:
                requestedIndex = max(1, plateIndex)
                explicit = [
                    item
                    for item in orderedCandidates
                    if re.search(fr"plate[_\-]?{requestedIndex}\.gcode$", item, re.IGNORECASE)
                ]
                if explicit:
                    chosen = explicit[0]
                else:
                    zeroBased = requestedIndex - 1
                    if zeroBased < len(orderedCandidates):
                        chosen = orderedCandidates[zeroBased]
                    else:
                        chosen = orderedCandidates[0]
            else:
                chosen = orderedCandidates[0]
            return chosen, orderedCandidates
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
    statusCallback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Start a print job via MQTT and stream status messages."""

    if mqtt is None:  # pragma: no cover - exercised when dependency missing
        raise RuntimeError("paho-mqtt is required for MQTT print control")

    port = 8883
    topicReport = f"device/{serial}/report"
    topicRequest = f"device/{serial}/request"

    lastStatus: Dict[str, Any] = {}

    connectionReady = Event()
    connectionError: Optional[str] = None
    initialStatus = Event()

    def handleStatus(statusPayload: Dict[str, Any]) -> None:
        nonlocal lastStatus
        filteredKeys = ("mc_percent", "gcode_state", "mc_remaining_time", "nozzle_temper", "bed_temper")
        statusSnapshot = {key: statusPayload.get(key) for key in filteredKeys if statusPayload.get(key) is not None}
        if statusSnapshot and statusSnapshot != lastStatus:
            lastStatus = statusSnapshot
            if statusCallback:
                statusCallback({"event": "progress", "status": statusSnapshot})
            initialStatus.set()

    def onConnect(client: mqtt.Client, _userdata, _flags, reasonCode, _properties):  # type: ignore[no-redef]
        nonlocal connectionError
        if getattr(reasonCode, "is_failure", False):
            connectionError = f"MQTT connection failed: {reasonCode}"
        else:
            client.subscribe(topicReport, qos=1)
        connectionReady.set()

    def onMessage(_client: mqtt.Client, _userdata, message):  # type: ignore[no-redef]
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            return

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

        statusMap = {key: findKey(payload, key) for key in ("mc_percent", "gcode_state", "mc_remaining_time", "nozzle_temper", "bed_temper")}
        handleStatus(statusMap)

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", accessCode)

    if insecureTls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    else:
        client.tls_set()

    client.on_connect = onConnect
    client.on_message = onMessage
    client.connect(ip, port, keepalive=60)
    client.loop_start()

    if not connectionReady.wait(timeout=10):
        client.loop_stop()
        client.disconnect()
        raise TimeoutError("Timed out waiting for MQTT connection")

    if connectionError:
        client.loop_stop()
        client.disconnect()
        raise RuntimeError(connectionError)

    sequenceBase = str(int(time.time()))
    statusSequenceId = f"{sequenceBase}-status"
    statusRequestPayload = {"pushing": {"command": "pushall", "sequence_id": statusSequenceId}}
    client.publish(topicRequest, json.dumps(statusRequestPayload), qos=1)

    if not initialStatus.wait(timeout=max(0, statusWarmupSeconds)) and statusCallback:
        statusCallback({"event": "statusWarmupTimeout"})

    sequenceId = f"{sequenceBase}-print"
    url = f"file:///sdcard/{sdFileName}"
    payload: Dict[str, Any] = {
        "print": {
            "command": "project_file",
            "sequence_id": sequenceId,
            "url": url,
            "use_ams": bool(useAms),
            "bed_leveling": bool(bedLeveling),
            "layer_inspect": bool(layerInspect),
            "flow_cali": bool(flowCalibration),
            "vibration_cali": bool(vibrationCalibration),
            "subtask_id": "0",
        }
    }
    if paramPath:
        payload["print"]["param"] = paramPath

    if statusCallback:
        statusCallback(
            {
                "event": "starting",
                "status": {
                    "url": url,
                    "param": paramPath,
                    "useAms": bool(useAms),
                    "bedLeveling": bool(bedLeveling),
                    "layerInspect": bool(layerInspect),
                    "flowCalibration": bool(flowCalibration),
                    "vibrationCalibration": bool(vibrationCalibration),
                },
            }
        )

    client.publish(topicRequest, json.dumps(payload), qos=1)

    timeout = time.time() + waitSeconds
    while time.time() < timeout:
        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()


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
    useAms: bool = False
    bedLeveling: bool = True
    layerInspect: bool = True
    flowCalibration: bool = False
    vibrationCalibration: bool = False
    secureConnection: bool = False
    plateIndex: Optional[int] = None
    waitSeconds: int = 12


def normalizeRemoteFileName(name: str) -> str:
    safeName = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    if not safeName.lower().endswith(".3mf"):
        safeName += ".3mf"
    return safeName


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

    remoteName = buildRemoteFileName(resolvedPath)
    printerFileName = buildPrinterTransferFileName(resolvedPath)

    with tempfile.TemporaryDirectory() as temporaryDirectory:
        temporaryPath = Path(temporaryDirectory) / resolvedPath.name
        shutil.copy2(resolvedPath, temporaryPath)

        if temporaryPath.suffix.lower().endswith(".3mf"):
            try:
                with zipfile.ZipFile(temporaryPath, "a"):
                    pass
            except zipfile.BadZipFile as zipError:
                raise ValueError(f"{resolvedPath} is not a valid 3MF archive") from zipError

        applySkippedObjectsToArchive(temporaryPath, skippedObjects or [])

        paramPath, _ = pickGcodeParamFrom3mf(temporaryPath, options.plateIndex)

        if options.useCloud and options.cloudUrl:
            payload = buildCloudJobPayload(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                safeName=remoteName,
                paramPath=paramPath,
                plateIndex=options.plateIndex,
                useAms=options.useAms,
                bedLeveling=options.bedLeveling,
                layerInspect=options.layerInspect,
                flowCalibration=options.flowCalibration,
                vibrationCalibration=options.vibrationCalibration,
                secureConnection=options.secureConnection,
                localPath=temporaryPath,
            )
            response = sendPrintJobViaCloud(options.cloudUrl, payload, timeoutSeconds=options.cloudTimeout)
            if statusCallback:
                statusCallback({"event": "cloudAccepted", "response": response})
            return {"method": "cloud", "remoteFile": remoteName, "paramPath": paramPath, "response": response}

        uploadedName = uploadViaFtps(
            ip=options.ipAddress,
            accessCode=options.accessCode,
            localPath=temporaryPath,
            remoteName=printerFileName,
            insecureTls=not options.secureConnection,
        )
        if statusCallback:
            statusCallback(
                {
                    "event": "uploadComplete",
                    "remoteFile": uploadedName,
                    "originalRemoteFile": remoteName,
                    "paramPath": paramPath,
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
    "sendBambuPrintJob",
    "sendPrintJobViaCloud",
    "startPrintViaMqtt",
    "summarizeStatusMessages",
    "uploadViaFtps",
]

