"""Utilities for dispatching print jobs to Bambu Lab printers."""

from __future__ import annotations

import base64
import importlib
import io
import webbrowser
import sys
import urllib.parse
import os
import re
import shutil
import socket
import ssl
import tempfile
import time
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Iterable, List, Optional, Sequence, Literal

from urllib.parse import urljoin

from ftplib import FTP_TLS, error_perm

import requests


_bambulabsApiModule = importlib.util.find_spec("bambulabs_api")
if _bambulabsApiModule is not None:
    bambulabsApi = importlib.import_module("bambulabs_api")
else:
    bambulabsApi = None


logger = logging.getLogger(__name__)
START_DEBUG = (
    str(os.getenv("PRINTMASTER_START_DEBUG", "")).strip().lower()
    not in ("", "0", "false", "off")
)


def _find_key(obj: Any, keys: set[str]) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                return value
            nested = _find_key(value, keys)
            if nested is not None:
                return nested
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            nested = _find_key(item, keys)
            if nested is not None:
                return nested
    return None


def _extract_gcode_state(state: Any) -> Optional[str]:
    keys = {"gcode_state", "sub_state", "state", "printer_state", "job_state"}
    value = _find_key(state, keys)
    return str(value) if value is not None else None


def _is_active_state(stateName: Optional[str]) -> bool:
    if not stateName:
        return False
    normalized = str(stateName).upper()
    return any(token in normalized for token in ("PRINT", "RUN", "HEAT", "PREP", "BUSY", "WORK"))



# Persistent dir for handing 3MF to Bambu Connect
PERSISTENT_FILES_DIR = Path.home() / ".printmaster" / "files"

def _ensure_dir(path):
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p
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
        ftps.prot_p()
        ftps.set_pasv(True)
        ftps.voidcmd("TYPE I")
        try:
            ftps.sendcmd("SITE ENABLE_STOR")
            logger.debug("FTPS: SITE ENABLE_STOR succeeded before upload")
        except Exception as enableStorError:
            logger.debug("FTPS: SITE ENABLE_STOR not required or failed: %s", enableStorError)

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

        def performUpload() -> str:
            if dataStream is not None:
                try:
                    dataStream.seek(0)
                except Exception:
                    pass
                response = ftps.storbinary(storageCommand, dataStream, blocksize=64 * 1024)
            else:
                with open(localPath, "rb") as handle:
                    response = ftps.storbinary(storageCommand, handle, blocksize=64 * 1024)
            if not response or not response.startswith("226"):
                raise RuntimeError(f"FTPS transfer did not complete successfully for {fileName}: {response}")
            return response

        try:
            performUpload()
        except RuntimeError as incompleteError:
            logger.error("%s", incompleteError)
            generatedName = buildFallbackFileName(fallbackSeedName)
            activateFallbackName(generatedName, source="stor")
            reactivateStor(ftps)
            tryReenterSavedDirectory()
            performUpload()
        except error_perm as uploadError:
            if "550" not in str(uploadError):
                raise
            logger.warning(
                "FTPS 550 on STOR %s; reactivating STOR and retrying: %s",
                remoteName,
                uploadError,
            )
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

        return fileName
    finally:
        try:
            ftps.quit()
        except Exception:
            pass


def uploadViaBambulabsApi(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    localPath: Path,
    remoteName: str,
) -> str:
    """Upload a file using the official bambulabs_api client."""

    if bambulabsApi is None:
        raise RuntimeError("bambulabs_api is required for this upload strategy")

    printerClass = getattr(bambulabsApi, "Printer", None)
    if printerClass is None:
        raise RuntimeError("bambulabs_api.Printer is not available")

    printer = printerClass(ip, accessCode, serial)
    connectionMethod = getattr(printer, "mqtt_start", None) or getattr(printer, "connect", None)
    if connectionMethod:
        connectionMethod()

    uploadMethod = None
    for candidate in ("upload_file", "upload_project", "upload"):
        uploadMethod = getattr(printer, candidate, None)
        if uploadMethod:
            break

    if uploadMethod is None:
        raise RuntimeError("Unable to locate an upload method on bambulabs_api.Printer")

    try:
        # bambulabs_api 2.6.x expects a binary file handle rather than a path string
        with open(localPath, "rb") as fileHandle:
            try:
                uploadMethod(fileHandle, remoteName)
            except TypeError:
                fileHandle.seek(0)
                uploadMethod(fileHandle)
    finally:
        disconnectMethod = getattr(printer, "disconnect", None)
        if disconnectMethod:
            try:
                disconnectMethod()
            except Exception:
                # Camera thread may not have been started; ignore spurious disconnect errors
                logger.debug("bambulabs_api disconnect raised (ignored)", exc_info=True)

    return remoteName


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


def startPrintViaMqtt(*_args, **_kwargs) -> None:
    """Disabled legacy MQTT start helper."""

    raise NotImplementedError("Disabled by policy: raw MQTT is not allowed (API-only).")




def _buildRemoteDeleteCandidates(remotePath: str) -> List[str]:
    normalized = remotePath.strip().replace("\\", "/")
    if not normalized:
        return []
    stripped = normalized.lstrip("/")
    candidates: List[str] = []
    seen: set[str] = set()

    def addCandidate(value: str) -> None:
        if not value:
            return
        if value not in seen:
            candidates.append(value)
            seen.add(value)

    addCandidate(normalized)
    addCandidate(stripped)
    addCandidate(f"/{stripped}")
    if not stripped.startswith("sdcard/"):
        addCandidate(f"sdcard/{stripped}")
        addCandidate(f"/sdcard/{stripped}")
    return candidates


def deleteRemoteFile(printer: Any, remotePath: str) -> bool:
    """Attempt to delete a remote file from the printer."""

    if not remotePath:
        return False

    def tryDelete(target: Any, path: str) -> bool:
        if target is None:
            return False
        deleteMethod = getattr(target, "delete_file", None)
        if not callable(deleteMethod):
            return False
        try:
            deleteMethod(path)
            return True
        except Exception as deleteError:
            message = str(deleteError).lower()
            if "not found" in message or "no such" in message:
                return False
            raise

    candidates = _buildRemoteDeleteCandidates(str(remotePath))
    if not candidates:
        return False

    for candidate in candidates:
        try:
            if tryDelete(printer, candidate):
                return True
        except Exception:
            logger.debug("delete_file failed for %s on printer", candidate, exc_info=True)

    if isinstance(printer, dict):
        ipAddress = printer.get("ipAddress") or printer.get("ip")
        accessCode = printer.get("accessCode") or printer.get("password")
        serialNumber = printer.get("serialNumber") or printer.get("serial") or ipAddress
        if not ipAddress or not accessCode:
            logger.info("Skip delete: missing credentials for printer %s", printer)
            return False
        if bambulabsApi is None:
            logger.info("Skip delete: bambulabs_api unavailable; cannot delete %s", remotePath)
            return False
        printerInstance = bambulabsApi.Printer(str(ipAddress), str(accessCode), str(serialNumber or ""))
        connectMethod = getattr(printerInstance, "mqtt_start", None) or getattr(printerInstance, "connect", None)
        try:
            if callable(connectMethod):
                connectMethod()
        except Exception:
            logger.info("Skip delete: unable to connect to printer %s", serialNumber, exc_info=True)
            safeDisconnectPrinter(printerInstance)
            return False
        try:
            result = deleteRemoteFile(printerInstance, remotePath)
            return result
        finally:
            safeDisconnectPrinter(printerInstance)

    logger.info("Skip delete: delete_file not supported for remote path %s", remotePath)
    return False


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
    useAms: Optional[bool] = None
    bedLeveling: bool = True
    layerInspect: bool = True
    flowCalibration: bool = False
    vibrationCalibration: bool = False
    secureConnection: bool = False
    plateIndex: Optional[int] = None
    waitSeconds: int = 8
    lanStrategy: str = "legacy"
    transport: str = "lan"
    spoolMode: bool = False
    startStrategy: Literal["api"] = "api"


def _waitForMqttReady(apiPrinter: Any, timeout: float = 30.0, poll: float = 0.5) -> tuple[Any, Any]:
    """Wait until the printer reports a stable MQTT state."""

    deadline = time.monotonic() + max(timeout, 0.0)
    lastSnapshot: Optional[tuple[Any, Any]] = None
    consecutiveStable = 0
    lastError: Optional[BaseException] = None

    while time.monotonic() < deadline:
        try:
            state = apiPrinter.get_state()
            percentage = apiPrinter.get_percentage()
            lastError = None
        except Exception as error:  # pragma: no cover - depends on SDK behaviour
            logger.debug("Waiting for MQTT readiness failed: %s", error)
            lastError = error
            consecutiveStable = 0
            time.sleep(max(poll, 0.05))
            continue

        snapshot = (state, percentage)
        if snapshot == lastSnapshot:
            consecutiveStable += 1
        else:
            consecutiveStable = 1
            lastSnapshot = snapshot

        if consecutiveStable >= 2:
            return snapshot

        time.sleep(max(poll, 0.05))

    message = "Timed out waiting for printer MQTT readiness"
    if lastError:
        message = f"{message}: {lastError}"
    raise TimeoutError(message)


def _normalizeMetadataKey(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def _findMetadataValue(container: Any, keyNames: Iterable[str]) -> Any:
    normalizedTargets = { _normalizeMetadataKey(name) for name in keyNames }

    def _search(value: Any) -> Any:
        if isinstance(value, dict):
            for itemKey, itemValue in value.items():
                normalizedKey = _normalizeMetadataKey(str(itemKey))
                if normalizedKey in normalizedTargets:
                    return itemValue
                result = _search(itemValue)
                if result is not None:
                    return result
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                result = _search(item)
                if result is not None:
                    return result
        return None

    return _search(container)


def _metadataContainsKey(container: Any, keyNames: Iterable[str]) -> bool:
    normalizedTargets = {_normalizeMetadataKey(name) for name in keyNames}

    def _search(value: Any) -> bool:
        if isinstance(value, dict):
            for itemKey, itemValue in value.items():
                normalizedKey = _normalizeMetadataKey(str(itemKey))
                if normalizedKey in normalizedTargets:
                    return True
                if _search(itemValue):
                    return True
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if _search(item):
                    return True
        return False

    return _search(container)


def _interpretFlexibleBoolean(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"auto", "", "none", "null"}:
            return None
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return None


def resolveUseAmsAuto(
    options: BambuPrintOptions,
    jobMetadata: Optional[Dict[str, Any]],
    localPath: Optional[Path],
) -> Optional[bool]:
    """Determine the effective use_ams flag based on options and metadata."""

    if isinstance(options.useAms, bool):
        return options.useAms

    if options.spoolMode:
        return False

    if localPath and localPath.suffix.lower() == ".gcode":
        return False

    if jobMetadata:
        quickPrint = _findMetadataValue(jobMetadata, {"isquickprint"})
        quickPrintBool = _interpretFlexibleBoolean(quickPrint) if quickPrint is not None else None
        if quickPrintBool:
            return False

        hasAmsConfigurationKey = _metadataContainsKey(jobMetadata, {"amsconfiguration", "amsconfig"})
        amsConfiguration = _findMetadataValue(jobMetadata, {"amsconfiguration", "amsconfig"})
        if hasAmsConfigurationKey and amsConfiguration is None:
            return False
        if isinstance(amsConfiguration, dict):
            if amsConfiguration.get("enabled") is False:
                return False
            if amsConfiguration:
                return True
        elif amsConfiguration:
            return True

        useAmsHint = _findMetadataValue(jobMetadata, {"useams"})
        interpretedHint = _interpretFlexibleBoolean(useAmsHint) if useAmsHint is not None else None
        if interpretedHint is not None:
            return interpretedHint

    return None


def _stringifyStatusFragment(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - defensive
            return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(_stringifyStatusFragment(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringifyStatusFragment(item) for item in value)
    return str(value)


def _looksLikeAmsFilamentConflict(statusPayload: Any) -> bool:
    text = _stringifyStatusFragment(statusPayload).lower()
    if not text:
        return False
    conflictMarkers = (
        "hms_07ff-2000-0002-0004",
        "pull out filament",
        "remove filament",
        "filament in hotend",
    )
    return any(marker in text for marker in conflictMarkers)


def _extractStateText(statePayload: Any) -> Optional[str]:
    if statePayload is None:
        return None
    if isinstance(statePayload, str):
        return statePayload
    if isinstance(statePayload, dict):
        for key in ("state", "gcode_state", "sub_state", "printer_state"):
            if key in statePayload:
                nested = statePayload[key]
                if isinstance(nested, (dict, list, tuple)):
                    extracted = _extractStateText(nested)
                    if extracted:
                        return extracted
                elif nested:
                    return str(nested)
        return None
    return str(statePayload)


def _stateSuggestsPrinting(stateText: Optional[str]) -> bool:
    if not stateText:
        return False
    normalized = stateText.lower()
    return any(keyword in normalized for keyword in ("heat", "warm", "print", "run", "prepare", "busy"))


def looksLikeAmsFilamentConflict(statusPayload: Any) -> bool:
    """Public helper for detecting AMS filament conflicts in status payloads."""

    return _looksLikeAmsFilamentConflict(statusPayload)


def extractStateText(statePayload: Any) -> Optional[str]:
    """Public helper mirroring the internal state text extraction logic."""

    return _extractStateText(statePayload)


def safeDisconnectPrinter(printer: Any) -> None:
    """Disconnect from the printer while ignoring benign SDK exceptions."""

    disconnectMethod = getattr(printer, "disconnect", None)
    if disconnectMethod:
        try:
            disconnectMethod()
        except Exception:  # pragma: no cover - defensive against camera thread state
            logger.debug("bambulabs_api disconnect raised (ignored)", exc_info=True)


def startPrintViaApi(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    uploaded_name: str,
    plate_index: Optional[int],
    param_path: Optional[str],
    options: BambuPrintOptions,
    job_metadata: Optional[Dict[str, Any]] = None,
    ack_timeout_sec: float = 15.0,
) -> Dict[str, Any]:
    """Start a print using bambulabs_api.Printer with acknowledgement handling."""

    if bambulabsApi is None:
        raise RuntimeError("bambulabs_api is required for API start strategy")

    printerClass = getattr(bambulabsApi, "Printer", None)
    if printerClass is None:
        raise RuntimeError("bambulabs_api.Printer class is unavailable")

    printer = printerClass(ip, accessCode, serial)
    resolvedUseAms = resolveUseAmsAuto(options, job_metadata, None)

    flags: Dict[str, Any] = {
        "use_ams": resolvedUseAms,
        "bed_levelling": bool(options.bedLeveling),
        "layer_inspect": bool(options.layerInspect),
        "flow_cali": bool(options.flowCalibration),
        "vibration_cali": bool(options.vibrationCalibration),
    }

    if START_DEBUG:
        logger.info(
            "[start] prepared printer=%s use_ams=%s ack_timeout=%.1fs",
            serial,
            resolvedUseAms,
            ack_timeout_sec,
        )

    if hasattr(printer, "mqtt_start"):
        try:
            printer.mqtt_start()
            if START_DEBUG:
                logger.info("[start] mqtt_start() ok")
        except Exception as error:  # pragma: no cover - best effort diagnostics
            logger.warning("[start] mqtt_start() failed: %s", error, exc_info=START_DEBUG)
    if hasattr(printer, "connect"):
        try:
            printer.connect()
            if START_DEBUG:
                logger.info("[start] connect() ok")
        except Exception as error:  # pragma: no cover - best effort diagnostics
            logger.warning("[start] connect() failed: %s", error, exc_info=START_DEBUG)
    try:
        _waitForMqttReady(printer, timeout=max(8.0, min(30.0, float(ack_timeout_sec))))
    except Exception as error:  # pragma: no cover - readiness is best effort
        logger.info("[start] readiness wait failed (continuing): %s", error, exc_info=START_DEBUG)

    remoteUrl = f"file:///sdcard/{uploaded_name}"
    if param_path:
        startArgument: Optional[Any] = param_path
    elif isinstance(plate_index, int):
        startArgument = plate_index
    else:
        startArgument = None

    if START_DEBUG:
        logger.info("[start] url=%s param=%s flags=%s", remoteUrl, startArgument, flags)

    def _invokeStart() -> None:
        localErrors: List[str] = []
        started = False
        startMethod = getattr(printer, "start_print", None)
        if callable(startMethod):
            try:
                positionalArgs: List[Any] = [uploaded_name, startArgument]
                keywordFlags = {key: value for key, value in flags.items() if value is not None}
                startMethod(*positionalArgs, **keywordFlags)
                started = True
                if START_DEBUG:
                    logger.info("[start] start_print() invoked")
            except Exception as error:
                localErrors.append(f"start_print:{error}")
                if START_DEBUG:
                    logger.info("[start] start_print failed: %s", error, exc_info=True)
        if not started:
            controlMethod = getattr(printer, "send_control", None)
            if callable(controlMethod):
                try:
                    payload: Dict[str, Any] = {"print": {"command": "project_file", "url": remoteUrl}}
                    if isinstance(startArgument, str):
                        payload["print"]["param"] = startArgument
                    elif isinstance(startArgument, int):
                        payload["print"]["plate"] = startArgument
                    for key, value in flags.items():
                        if value is not None:
                            payload["print"][key] = value
                    if START_DEBUG:
                        logger.info("[start] send_control payload=%s", payload)
                    controlMethod(payload)
                    started = True
                    if START_DEBUG:
                        logger.info("[start] send_control(project_file) invoked")
                except Exception as error:
                    localErrors.append(f"send_control(project_file):{error}")
                    if START_DEBUG:
                        logger.info("[start] send_control failed: %s", error, exc_info=True)
        if not started:
            summary = " | ".join(localErrors or ["no method"])
            raise RuntimeError(f"Unable to start print via API: {summary}")

    def _pollForAcknowledgement(timeoutSeconds: float) -> Dict[str, Any]:
        deadline = time.monotonic() + max(5.0, float(timeoutSeconds))
        lastStatePayload: Any = None
        lastPercentage: Any = None
        lastGcodeState: Optional[str] = None
        while time.monotonic() < deadline:
            try:
                statePayload = printer.get_state()
                if statePayload is not None:
                    lastStatePayload = statePayload
                    candidateState = _extract_gcode_state(statePayload)
                    if candidateState is not None:
                        lastGcodeState = candidateState
            except Exception as error:
                if START_DEBUG:
                    logger.info("[start] poll get_state failed: %s", error)
            try:
                percentagePayload = printer.get_percentage()
                if percentagePayload is not None:
                    lastPercentage = percentagePayload
            except Exception as error:
                if START_DEBUG:
                    logger.info("[start] poll get_percentage failed: %s", error)
            stateIndicator = lastGcodeState or extractStateText(lastStatePayload)
            percentageFloat: Optional[float]
            try:
                percentageFloat = float(lastPercentage) if lastPercentage is not None else None
            except Exception:
                percentageFloat = None
            if START_DEBUG:
                logger.info("[start] poll state=%s percent=%s", stateIndicator, lastPercentage)
            if (percentageFloat is not None and percentageFloat > 0.0) or _is_active_state(stateIndicator):
                return {
                    "acknowledged": True,
                    "statePayload": lastStatePayload,
                    "state": extractStateText(lastStatePayload) or stateIndicator,
                    "gcodeState": lastGcodeState or stateIndicator,
                    "percentage": percentageFloat,
                }
            time.sleep(0.5)
        try:
            timeoutPercentage = float(lastPercentage) if lastPercentage is not None else None
        except Exception:
            timeoutPercentage = None
        return {
            "acknowledged": False,
            "statePayload": lastStatePayload,
            "state": extractStateText(lastStatePayload) or lastGcodeState,
            "gcodeState": lastGcodeState,
            "percentage": timeoutPercentage,
        }

    fallbackTriggered = False
    finalUseAms = resolvedUseAms

    try:
        _invokeStart()
        ackResult = _pollForAcknowledgement(ack_timeout_sec)
        acknowledged = bool(ackResult.get("acknowledged"))
        conflictDetected = _looksLikeAmsFilamentConflict(ackResult.get("statePayload"))
        if (resolvedUseAms is None) and (conflictDetected or not acknowledged):
            logger.warning(
                "API start detected possible AMS filament conflict for %s – retrying with use_ams=False",
                serial,
            )
            fallbackTriggered = True
            finalUseAms = False
            flags["use_ams"] = False
            try:
                stopMethod = getattr(printer, "stop_print", None)
                if callable(stopMethod):
                    stopMethod()
            except Exception:  # pragma: no cover - best effort stop
                logger.debug("stop_print failed during AMS retry", exc_info=True)
            time.sleep(0.5)
            _invokeStart()
            ackResult = _pollForAcknowledgement(ack_timeout_sec)
            acknowledged = bool(ackResult.get("acknowledged"))
        logger.info(
            "API start acknowledgement for %s: acknowledged=%s state=%s gcodeState=%s pct=%s",
            serial,
            acknowledged,
            ackResult.get("state"),
            ackResult.get("gcodeState"),
            ackResult.get("percentage"),
        )
        ackResult.pop("statePayload", None)
        ackResult["useAms"] = finalUseAms
        ackResult["fallbackTriggered"] = fallbackTriggered
        return ackResult
    finally:
        safeDisconnectPrinter(printer)


def normalizeRemoteFileName(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    asciiName = normalized.encode("ascii", "ignore").decode("ascii")
    safeName = re.sub(r"[^A-Za-z0-9._-]+", "_", asciiName)
    if safeName.lower().endswith(".gcode"):
        safeName = safeName[: -len(".gcode")]
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


def buildBambuConnectName(localPath: Path) -> str:
    base = localPath.name
    suffix = ""
    if base.lower().endswith(".3mf"):
        base, suffix = base[:-4], ".3mf"
    m = re.match(r"^[0-9a-fA-F-]+_[0-9a-fA-F-]+_(.+)$", base)
    if m:
        base = m.group(1)
    base = re.sub(r"[^A-Za-z0-9]+", "", base) or "Model"
    return base + (suffix or ".3mf")


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
    jobMetadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Upload a file and start a Bambu print job."""

    resolvedPath = filePath.expanduser().resolve()
    if not resolvedPath.exists():
        raise FileNotFoundError(resolvedPath)

    plateIndex = options.plateIndex
    lanStrategy = (options.lanStrategy or "legacy").lower()

    with tempfile.TemporaryDirectory() as temporaryDirectory:
        paramPath: Optional[str] = None
        tempDir = Path(temporaryDirectory)

        if resolvedPath.suffix.lower() == ".gcode":
            targetPlate = max(1, plateIndex or 1)
            platePath = f"Metadata/plate_{targetPlate}.gcode"
            gcodeText = resolvedPath.read_text(encoding="utf-8", errors="ignore")
            buffer = packageGcodeToThreeMfBytes(gcodeText, platePath=platePath)
            workingPath = tempDir / f"{resolvedPath.stem}.3mf"
            workingPath.write_bytes(buffer.getvalue())
            paramPath = platePath
        else:
            workingPath = tempDir / resolvedPath.name
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
                paramPath = candidates[0]

        remoteName = buildRemoteFileName(workingPath)
        resolvedUseAms = resolveUseAmsAuto(options, jobMetadata, resolvedPath)

        # Bambu Connect hand-off (local client). Copy to persistent dir so temp isn't deleted.
        if options.transport == "bambu_connect" and not options.cloudUrl:
            destDir = _ensure_dir(PERSISTENT_FILES_DIR)
            persistentPath = destDir / workingPath.name
            try:
                shutil.copy2(workingPath, persistentPath)
            except Exception:
                persistentPath.write_bytes(workingPath.read_bytes())
            connectName = buildBambuConnectName(persistentPath)
            uri = (
                "bambu-connect://import-file?"
                "path=" + urllib.parse.quote(str(persistentPath))
                + "&name=" + urllib.parse.quote(connectName)
                + "&version=1.0.0"
            )
            try:
                if sys.platform.startswith("win"):
                    os.startfile(uri)  # type: ignore[attr-defined]
                else:
                    webbrowser.open(uri)
                if statusCallback:
                    statusCallback(
                        {
                            "status": "bambuConnectOpened",
                            "uri": uri,
                            "persistentPath": str(persistentPath),
                            "name": connectName,
                        }
                    )
                return {
                    "method": "bambu_connect",
                    "uri": uri,
                    "remoteFile": remoteName,
                    "localFile": str(persistentPath),
                    "paramPath": paramPath,
                }
            except Exception as error:
                if statusCallback:
                    statusCallback({"status": "error", "error": str(error)})
                raise

        printerFileName = buildPrinterTransferFileName(workingPath)

        if skippedObjects:
            applySkippedObjectsToArchive(workingPath, skippedObjects)

        startStrategy = (options.startStrategy or "api").lower()
        if startStrategy != "api":
            raise RuntimeError("API-only policy requires startStrategy='api'")
        if bambulabsApi is None:
            raise RuntimeError("bambulabs_api is required for API-only policy")

        if lanStrategy != "bambuapi":
            logger.info("Forcing bambulabs_api upload because startStrategy=api")
            lanStrategy = "bambuapi"

        if options.useCloud and options.cloudUrl:
            useAmsForCloud = resolvedUseAms if resolvedUseAms is not None else True
            payload = buildCloudJobPayload(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                safeName=remoteName,
                paramPath=paramPath,
                plateIndex=plateIndex,
                useAms=useAmsForCloud,
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

        uploadedName: Optional[str] = None

        if lanStrategy == "bambuapi":
            uploadedName = uploadViaBambulabsApi(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                localPath=workingPath,
                remoteName=printerFileName,
            )
        else:
            try:
                uploadedName = uploadViaFtps(
                    ip=options.ipAddress,
                    accessCode=options.accessCode,
                    localPath=workingPath,
                    remoteName=printerFileName,
                    insecureTls=not options.secureConnection,
                )
            except Exception as error:
                is550 = (isinstance(error, error_perm) and "550" in str(error)) or (" 550" in str(error))
                if is550 and bambulabsApi is not None:
                    logger.info(
                        "FTPS 550 under opplasting – faller tilbake til bambulabs_api for %s",
                        options.serialNumber,
                    )
                    uploadedName = uploadViaBambulabsApi(
                        ip=options.ipAddress,
                        serial=options.serialNumber,
                        accessCode=options.accessCode,
                        localPath=workingPath,
                        remoteName=printerFileName,
                    )
                else:
                    raise

        if statusCallback:
            statusCallback(
                {
                    "status": "uploaded",
                    "remoteFile": uploadedName,
                    "originalRemoteFile": remoteName,
                    "param": paramPath,
                }
            )

        startStrategy = (options.startStrategy or "api").lower()
        if startStrategy != "api":
            raise RuntimeError("API-only policy requires startStrategy='api'")
        if bambulabsApi is None:
            raise RuntimeError("bambulabs_api is required for API-only policy")
        startingEvent = {
            "status": "starting",
            "param": paramPath,
            "remoteFile": uploadedName,
            "useAms": resolvedUseAms,
            "method": "api",
        }
        if statusCallback:
            statusCallback(startingEvent)

        apiResult: Optional[Dict[str, Any]] = None
        try:
            apiResult = startPrintViaApi(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                uploaded_name=uploadedName,
                plate_index=plateIndex,
                param_path=paramPath,
                options=options,
                job_metadata=jobMetadata,
                ack_timeout_sec=max(float(options.waitSeconds), 1.0),
            )
            if statusCallback:
                statusCallback(
                    {
                        "status": "started",
                        "method": "api",
                        "acknowledged": apiResult.get("acknowledged") if apiResult else False,
                        "state": apiResult.get("state") if apiResult else None,
                        "gcodeState": apiResult.get("gcodeState") if apiResult else None,
                        "percentage": apiResult.get("percentage") if apiResult else None,
                        "useAms": apiResult.get("useAms") if apiResult else resolvedUseAms,
                        "fallback": apiResult.get("fallbackTriggered") if apiResult else False,
                    }
                )
        except Exception as error:
            logger.warning("API start failed for %s: %s", options.serialNumber, error, exc_info=True)
            if statusCallback:
                statusCallback({"status": "apiStartFailed", "error": str(error)})
            raise RuntimeError(
                "API print start failed and MQTT fallback is disabled by policy"
            ) from error

        if apiResult is None:
            raise RuntimeError("API print start failed and MQTT fallback is disabled by policy")

        startMethodResult = "api"

        return {
            "method": "lan",
            "remoteFile": uploadedName,
            "originalRemoteFile": remoteName,
            "paramPath": paramPath,
            "useAms": apiResult.get("useAms") if apiResult else (resolvedUseAms if isinstance(resolvedUseAms, bool) else None),
            "api": apiResult,
            "startMethod": startMethodResult,
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
    "deleteRemoteFile",
    "sendBambuPrintJob",
    "sendPrintJobViaCloud",
    "startPrintViaApi",
    "startPrintViaMqtt",
    "summarizeStatusMessages",
    "uploadViaFtps",
]

