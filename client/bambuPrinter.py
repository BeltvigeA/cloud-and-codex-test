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
from dataclasses import dataclass, replace
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
    return any(
        token in normalized for token in ("PRINT", "RUN", "HEAT", "PREP", "BUSY", "WORK", "HOM", "HOME")
    )


def _safe_get_state(printer: Any) -> Optional[Dict[str, Any]]:
    getStateMethod = getattr(printer, "get_state", None)
    if not callable(getStateMethod):
        return None
    try:
        statePayload = getStateMethod()
    except Exception as error:  # pragma: no cover - dependent on printer state
        if START_DEBUG:
            logger.info("[start] preflight get_state() failed: %s", error, exc_info=True)
        return None
    if isinstance(statePayload, dict):
        return statePayload
    if START_DEBUG and statePayload is not None:
        logger.info(
            "[start] preflight get_state() returned %s", type(statePayload).__name__
        )
    return None


def _extract_mc_percent(payload: Any) -> Optional[int]:
    percentValue = _find_key(payload, {"mc_percent", "percentage", "percent"})
    if percentValue is None:
        return None
    try:
        percentFloat = float(percentValue)
    except (TypeError, ValueError):
        return None
    try:
        return int(percentFloat)
    except (TypeError, ValueError):  # pragma: no cover - float coercion safeguard
        return None


def _state_is_completed_like(payload: Any) -> bool:
    if payload is None:
        return False
    percentValue = _extract_mc_percent(payload)
    if percentValue == 100:
        return True
    stateText = _extract_gcode_state(payload)
    if not stateText:
        return False
    normalized = stateText.strip().lower()
    completedStates = {"finish", "finished", "completed", "idle", "complete"}
    return normalized in completedStates


def _preselect_project_file(
    printer: Any,
    remoteUrl: str,
    startParam: Optional[str],
    sendControlFlags: Dict[str, Any],
) -> bool:
    controlMethod = getattr(printer, "send_control", None)
    if not callable(controlMethod):
        return False
    payload: Dict[str, Any] = {"print": {"command": "project_file", "url": remoteUrl}}
    if startParam:
        payload["print"]["param"] = startParam
    payload["print"].update({key: value for key, value in sendControlFlags.items() if value is not None})
    if START_DEBUG:
        logger.info("[start] preselect send_control payload=%s", payload)
    try:
        controlMethod(payload)
        logger.info("[start] preselected project_file before start_print")
        return True
    except Exception as error:  # pragma: no cover - dependent on printer state
        if START_DEBUG:
            logger.info("[start] preselect send_control failed: %s", error, exc_info=True)
        return False


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


def _resolveTimeLapseDirectory(
    options: "BambuPrintOptions", *, ensure: bool = False
) -> Optional[Path]:
    if not getattr(options, "enableTimeLapse", False):
        return None

    rawDirectory = options.timeLapseDirectory
    if rawDirectory is None:
        rawDirectory = Path.home() / ".printmaster" / "timelapse"

    directory = Path(rawDirectory).expanduser()
    if ensure:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _activateOnboardTimelapse(printer: Any, directory: Path) -> bool:
    """
    Enable the printer's built-in timelapse feature using the BambuLabs API.

    Args:
        printer: The printer instance from bambulabs_api.Printer
        directory: Path where timelapse files will be stored locally (for reference)

    Returns:
        bool: True if timelapse was enabled successfully
    """
    # Try to enable the onboard printer timelapse feature
    setTimelapseMethod = getattr(printer, "set_onboard_printer_timelapse", None)

    if not callable(setTimelapseMethod):
        logger.warning("[timelapse] set_onboard_printer_timelapse method not available on printer")
        return False

    try:
        # Enable the onboard timelapse - returns bool indicating success
        result = setTimelapseMethod(enable=True)

        if START_DEBUG:
            logger.info("[timelapse] set_onboard_printer_timelapse(enable=True) returned: %s", result)

        if result:
            logger.info("[timelapse] Onboard timelapse enabled successfully, files will be saved to printer")
            return True
        else:
            logger.warning("[timelapse] Failed to enable onboard timelapse")
            return False

    except Exception as error:
        logger.warning("[timelapse] Error enabling onboard timelapse: %s", error, exc_info=START_DEBUG)
        return False


def _downloadTimelapseFromPrinter(printer: Any, serial: str, directory: Path) -> Optional[Path]:
    """
    Download the timelapse video from the printer's SD card via FTP.

    Args:
        printer: The printer instance from bambulabs_api.Printer
        serial: Printer serial number
        directory: Local directory to save the timelapse file

    Returns:
        Path to the downloaded file, or None if download failed
    """
    try:
        # Get the FTP client from the printer
        ftpClient = getattr(printer, "ftp_client", None)
        if ftpClient is None:
            logger.warning("[timelapse] FTP client not available on printer")
            return None

        # List files in the timelapse directory
        listMethod = getattr(ftpClient, "list_timelapse_dir", None)
        if not callable(listMethod):
            logger.warning("[timelapse] list_timelapse_dir method not available")
            return None

        ftpResult, fileList = listMethod()

        if START_DEBUG:
            logger.info("[timelapse] FTP result: %s, Files found: %s", ftpResult, fileList)

        if not fileList:
            logger.info("[timelapse] No timelapse files found on printer")
            return None

        # Get the most recent timelapse file (last in the list)
        latestFile = fileList[-1] if isinstance(fileList, list) else None
        if not latestFile:
            logger.warning("[timelapse] Could not identify latest timelapse file")
            return None

        # Construct the remote path
        remoteFilePath = f"timelapse/{latestFile}" if not latestFile.startswith("/") else latestFile

        # Generate local filename with timestamp
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        localFilename = f"{serial}_{timestamp}_{Path(latestFile).name}"
        localPath = directory / localFilename

        # Ensure directory exists
        directory.mkdir(parents=True, exist_ok=True)

        # Download the file using FTP
        downloadMethod = getattr(ftpClient, "download", None)
        if not callable(downloadMethod):
            logger.warning("[timelapse] download method not available on FTP client")
            return None

        logger.info("[timelapse] Downloading %s to %s", remoteFilePath, localPath)
        downloadMethod(remoteFilePath, str(localPath))

        if localPath.exists():
            logger.info("[timelapse] Successfully downloaded timelapse to %s", localPath)
            return localPath
        else:
            logger.warning("[timelapse] Download completed but file not found at %s", localPath)
            return None

    except Exception as error:
        logger.error("[timelapse] Error downloading timelapse from printer: %s", error, exc_info=True)
        return None


def _storeTimelapseReference(printer: Any, serial: str, directory: Path) -> None:
    """
    Store a reference to where the timelapse will be retrieved from.
    This is called when a print job starts with timelapse enabled.
    """
    if not hasattr(printer, "_printmaster_timelapse_info"):
        try:
            printer._printmaster_timelapse_info = {
                "serial": serial,
                "directory": directory,
                "enabled": True,
            }
        except Exception:
            logger.debug("[timelapse] Could not store timelapse reference", exc_info=START_DEBUG)


def _retrieveTimelapseIfEnabled(printer: Any) -> Optional[Path]:
    """
    Check if timelapse was enabled for this printer and download it if so.
    This should be called when a print job completes.
    """
    timelapseInfo = getattr(printer, "_printmaster_timelapse_info", None)
    if not timelapseInfo or not timelapseInfo.get("enabled"):
        return None

    try:
        serial = timelapseInfo.get("serial")
        directory = timelapseInfo.get("directory")

        if not serial or not directory:
            return None

        # Download the timelapse from the printer
        downloadedPath = _downloadTimelapseFromPrinter(printer, serial, directory)

        # Clear the reference
        try:
            del printer._printmaster_timelapse_info
        except Exception:
            pass

        return downloadedPath

    except Exception as error:
        logger.error("[timelapse] Error retrieving timelapse: %s", error, exc_info=True)
        return None


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
    enableTimeLapse: bool = False
    timeLapseDirectory: Optional[Path] = None
    enableBrakePlate: Optional[bool] = None
    plateTemplate: Optional[str] = None


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


def _applyPostStartControls(printer: Any, options: "BambuPrintOptions") -> None:
    """Apply print-time toggles that are not supported directly by start_print."""

    autoStepMethod = getattr(printer, "set_auto_step_recovery", None)
    desiredLayerInspect = bool(getattr(options, "layerInspect", True))
    if callable(autoStepMethod):
        try:
            autoStepMethod(desiredLayerInspect)
            if START_DEBUG:
                logger.info("[start] set_auto_step_recovery(%s) invoked", desiredLayerInspect)
        except Exception:  # pragma: no cover - robustness
            logger.debug("set_auto_step_recovery failed", exc_info=True)

    bedEnabled = bool(getattr(options, "bedLeveling", True))
    vibrationEnabled = bool(getattr(options, "vibrationCalibration", False))
    if not bedEnabled and not vibrationEnabled:
        return

    calibrateMethod = getattr(printer, "calibrate_printer", None)
    if callable(calibrateMethod):
        try:
            calibrateMethod(
                bed_level=bedEnabled,
                motor_noise_calibration=vibrationEnabled,
                vibration_compensation=vibrationEnabled,
            )
            if START_DEBUG:
                logger.info(
                    "[start] calibrate_printer(bed_level=%s, vibration=%s) invoked",
                    bedEnabled,
                    vibrationEnabled,
                )
        except Exception:  # pragma: no cover - robustness
            logger.debug("calibrate_printer failed", exc_info=True)


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
    timelapse_directory: Optional[Path] = None,
) -> Dict[str, Any]:
    """Start a print using bambulabs_api.Printer with acknowledgement handling."""

    if bambulabsApi is None:
        raise RuntimeError("bambulabs_api is required for API start strategy")

    printerClass = getattr(bambulabsApi, "Printer", None)
    if printerClass is None:
        raise RuntimeError("bambulabs_api.Printer class is unavailable")

    printer = printerClass(ip, accessCode, serial)
    resolvedUseAms = resolveUseAmsAuto(options, job_metadata, None)

    # Resolve requested timelapse directory from options first
    timelapsePath = timelapse_directory or _resolveTimeLapseDirectory(options, ensure=True)

    # --- NEW: metadata fallback (covers MQTT→API fallback where options.enableTimeLapse wasn't set) ---
    if timelapsePath is None and job_metadata:
        def _norm_key(s):
            try:
                return "".join(ch for ch in str(s).lower() if ch.isalnum())
            except Exception:
                return ""
        def _any_enable(obj) -> bool:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    nk = _norm_key(k)
                    if nk in ("enabletimelapse", "timelapseenabled"):
                        try:
                            return bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("1","true","yes","on")
                        except Exception:
                            return False
                    if _any_enable(v):
                        return True
            elif isinstance(obj, list):
                return any(_any_enable(x) for x in obj)
            return False
        if _any_enable(job_metadata):
            # default: ~/.printmaster/timelapse
            timelapsePath = Path.home() / ".printmaster" / "timelapse"
            timelapsePath.mkdir(parents=True, exist_ok=True)
            if START_DEBUG:
                logger.info("[start] timelapse enabled by metadata -> %s", timelapsePath)

    startKeywordArgs: Dict[str, Any] = {}
    if resolvedUseAms is not None:
        startKeywordArgs["use_ams"] = resolvedUseAms
    startKeywordArgs["flow_calibration"] = bool(options.flowCalibration)

    sendControlFlags: Dict[str, Any] = {
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
        startParam = param_path
    elif isinstance(plate_index, int) and plate_index >= 0:
        startParam = f"Metadata/plate_{plate_index}.gcode"
    else:
        startParam = None

    if param_path:
        plateArgument: Any = param_path
    elif isinstance(plate_index, int):
        plateArgument = plate_index
    else:
        plateArgument = 1

    if START_DEBUG:
        logger.info(
            "[start] prepared file=%s param=%s flags=%s",
            uploaded_name,
            startParam,
            {key: value for key, value in sendControlFlags.items() if value is not None},
        )

    preflightState = _safe_get_state(printer)
    if START_DEBUG and preflightState is not None:
        logger.info(
            "[start] preflight state percent=%s state=%s",
            _extract_mc_percent(preflightState),
            _extract_gcode_state(preflightState),
        )
    if _state_is_completed_like(preflightState):
        _preselect_project_file(printer, remoteUrl, startParam, sendControlFlags)

    if timelapsePath is not None:
        timelapseEnabled = _activateOnboardTimelapse(printer, timelapsePath)
        if timelapseEnabled:
            _storeTimelapseReference(printer, serial, timelapsePath)
        else:
            logger.warning("[start] Failed to enable onboard timelapse feature")
            if START_DEBUG:
                logger.info("[start] Timelapse was requested but could not be activated")

    def _invokeStart() -> None:
        localErrors: List[str] = []
        started = False
        startMethod = getattr(printer, "start_print", None)
        if callable(startMethod):
            try:
                positionalArgs = [uploaded_name, plateArgument]
                keywordArgs = dict(startKeywordArgs)
                startMethod(*positionalArgs, **keywordArgs)
                started = True
                logger.info(
                    "[start] start_print() invoked (file=%s, plate=%s, kwargs=%s)",
                    uploaded_name,
                    plateArgument,
                    keywordArgs,
                )
                _applyPostStartControls(printer, options)
            except Exception as error:
                localErrors.append(f"start_print:{error}")
                if START_DEBUG:
                    logger.info("[start] start_print failed: %s", error, exc_info=True)
        if not started:
            controlMethod = getattr(printer, "send_control", None)
            if callable(controlMethod):
                try:
                    payload: Dict[str, Any] = {"print": {"command": "project_file", "url": remoteUrl}}
                    if startParam:
                        payload["print"]["param"] = startParam
                    payload["print"].update({key: value for key, value in sendControlFlags.items() if value is not None})
                    if START_DEBUG:
                        logger.info("[start] send_control payload=%s", payload)
                    controlMethod(payload)
                    started = True
                    logger.info("[start] send_control(project_file) invoked")
                    _applyPostStartControls(printer, options)
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
            if percentageFloat is None and lastStatePayload is not None:
                embeddedPercent = _extract_mc_percent(lastStatePayload)
                if embeddedPercent is not None:
                    percentageFloat = float(embeddedPercent)
            completedLike = _state_is_completed_like(lastStatePayload)
            if not completedLike and percentageFloat is not None and percentageFloat >= 100.0:
                completedLike = True
            pctOk = percentageFloat is not None and 0.0 < percentageFloat < 100.0
            activeOk = _is_active_state(stateIndicator) and not completedLike
            if START_DEBUG:
                logger.info(
                    "[start] poll state=%s percent=%s completedLike=%s",
                    stateIndicator,
                    lastPercentage,
                    completedLike,
                )
            if pctOk or activeOk:
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
        if timeoutPercentage is None and lastStatePayload is not None:
            fallbackPercent = _extract_mc_percent(lastStatePayload)
            if fallbackPercent is not None:
                timeoutPercentage = float(fallbackPercent)
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
            sendControlFlags["use_ams"] = False
            startKeywordArgs["use_ams"] = False
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
        if not acknowledged:
            logger.warning("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.warning("⚠️  PRINT START NOT ACKNOWLEDGED by %s", serial)
            logger.warning("   Printer state: %s", ackResult.get("state") or "unknown")
            logger.warning("   Gcode state: %s", ackResult.get("gcodeState") or "unknown")
            logger.warning("   Percentage: %s", ackResult.get("percentage") or "unknown")
            logger.warning("   Fallback triggered: %s", fallbackTriggered)
            logger.warning("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
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

    # --- NEW: løft timelapse fra jobMetadata → options før vi lager/mappe ---
    try:
        if jobMetadata:
            tl_hint = _findMetadataValue(jobMetadata, {"enable_timelapse", "enabletimelapse", "timelapseenabled"})
            tl_bool = _interpretFlexibleBoolean(tl_hint) if tl_hint is not None else None
            tl_dir  = _findMetadataValue(jobMetadata, {"timelapse_directory", "timelapsedirectory", "timelapsepath"})
            if tl_bool and not getattr(options, "enableTimeLapse", False):
                options = replace(options, enableTimeLapse=True)
            if tl_dir and not getattr(options, "timeLapseDirectory", None):
                try:
                    options = replace(options, timeLapseDirectory=Path(str(tl_dir)).expanduser())
                except Exception:
                    pass
    except Exception:
        logger.debug("timelapse metadata merge failed (ignored)", exc_info=START_DEBUG)

    timelapsePath = _resolveTimeLapseDirectory(options, ensure=True)

    def _with_plate_options(payload: Dict[str, Any]) -> Dict[str, Any]:
        if options.enableBrakePlate is not None:
            payload.setdefault("enableBrakePlate", options.enableBrakePlate)
        if options.plateTemplate is not None:
            payload.setdefault("plateTemplate", options.plateTemplate)
        return payload

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
                        _with_plate_options(
                            {
                                "status": "bambuConnectOpened",
                                "uri": uri,
                                "persistentPath": str(persistentPath),
                                "name": connectName,
                            }
                        )
                    )
                connectPayload = {
                    "method": "bambu_connect",
                    "uri": uri,
                    "remoteFile": remoteName,
                    "localFile": str(persistentPath),
                    "paramPath": paramPath,
                }
                return _with_plate_options(connectPayload)
            except Exception as error:
                if statusCallback:
                    statusCallback(_with_plate_options({"status": "error", "error": str(error)}))
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
                statusCallback(_with_plate_options({"status": "cloudAccepted", "response": response}))
            resultPayload = {
                "method": "cloud",
                "remoteFile": remoteName,
                "paramPath": paramPath,
                "response": response,
            }
            return _with_plate_options(resultPayload)

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
                _with_plate_options(
                    {
                        "status": "uploaded",
                        "remoteFile": uploadedName,
                        "originalRemoteFile": remoteName,
                        "param": paramPath,
                    }
                )
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
            statusCallback(_with_plate_options(startingEvent))

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
                timelapse_directory=timelapsePath,
            )
            if statusCallback:
                statusCallback(
                    _with_plate_options(
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
                )
        except Exception as error:
            logger.warning("API start failed for %s: %s", options.serialNumber, error, exc_info=True)
            if statusCallback:
                statusCallback(_with_plate_options({"status": "apiStartFailed", "error": str(error)}))
            raise RuntimeError(
                "API print start failed and MQTT fallback is disabled by policy"
            ) from error

        if apiResult is None:
            raise RuntimeError("API print start failed and MQTT fallback is disabled by policy")

        startMethodResult = "api"

        resultPayload = {
            "method": "lan",
            "remoteFile": uploadedName,
            "originalRemoteFile": remoteName,
            "paramPath": paramPath,
            "useAms": apiResult.get("useAms") if apiResult else (resolvedUseAms if isinstance(resolvedUseAms, bool) else None),
            "api": apiResult,
            "startMethod": startMethodResult,
        }
        return _with_plate_options(resultPayload)



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

