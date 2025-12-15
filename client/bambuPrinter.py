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

# Import config manager for reading config.json
try:
    from .config_manager import get_config_manager
    _config_manager_available = True
except ImportError:
    _config_manager_available = False

# Import event reporter for event reporting
try:
    from .event_reporter import EventReporter
    _event_reporter_available = True
except ImportError:
    _event_reporter_available = False
    EventReporter = None

import requests

from .status_reporter import StatusReporter


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


def _is_printing_state(stateName: Optional[str]) -> bool:
    """Check if printer is actively printing (not just heating/preparing)."""
    if not stateName:
        return False
    normalized = str(stateName).upper()
    # Check for states that indicate actual printing, not just warmup
    return any(
        token in normalized for token in ("PRINT", "RUN", "RUNNING")
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
    import json

    normalizedBaseUrl = baseUrl.rstrip("/") + "/"
    endpoint = urljoin(normalizedBaseUrl, "print")

    # ═══════════════════════════════════════════════════════════════════════════
    # PRINT JOB SUBMISSION - Detaljert logging av cloud API-kall
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info("═" * 80)
    logger.info("📤 SENDER PRINT JOB TIL CLOUD API")
    logger.info("─" * 80)
    logger.info(f"   Target URL: {endpoint}")
    logger.info(f"   Base URL: {baseUrl}")
    logger.info(f"   Timeout: {timeoutSeconds} sekunder")
    logger.info("─" * 80)
    logger.info("   📦 PAYLOAD DATA SOM SENDES:")
    logger.info("   ─── Printer Info ───")
    logger.info(f"   Serial Number: {jobPayload.get('serialNumber', 'N/A')}")
    logger.info(f"   IP Address: {jobPayload.get('ipAddress', 'N/A')}")
    logger.info(f"   Access Code: {'✅ Present' if jobPayload.get('accessCode') else '❌ Missing'}")
    logger.info("   ─── File Info ───")
    logger.info(f"   File Name: {jobPayload.get('fileName', 'N/A')}")
    logger.info(f"   Remote Name: {jobPayload.get('remoteName', 'N/A')}")
    logger.info(f"   Param Path: {jobPayload.get('paramPath', 'N/A')}")
    file_data = jobPayload.get('fileData', '')
    if file_data:
        logger.info(f"   File Data Size: {len(file_data)} chars (base64 encoded)")
        logger.info(f"   File Data Preview: {file_data[:100]}..." if len(file_data) > 100 else f"   File Data: {file_data}")
    else:
        logger.info("   File Data: ❌ Missing")
    logger.info("   ─── Print Settings ───")
    logger.info(f"   Use AMS: {jobPayload.get('useAms', 'N/A')}")
    logger.info(f"   Bed Leveling: {jobPayload.get('bedLeveling', 'N/A')}")
    logger.info(f"   Layer Inspect: {jobPayload.get('layerInspect', 'N/A')}")
    logger.info(f"   Flow Calibration: {jobPayload.get('flowCalibration', 'N/A')}")
    logger.info(f"   Vibration Calibration: {jobPayload.get('vibrationCalibration', 'N/A')}")
    logger.info(f"   Secure Connection: {jobPayload.get('secureConnection', 'N/A')}")
    logger.info(f"   Plate Index: {jobPayload.get('plateIndex', 'N/A')}")
    logger.info("─" * 80)
    logger.info("   📋 FULL JSON PAYLOAD (uten fileData for lesbarhet):")
    payload_for_log = {k: v for k, v in jobPayload.items() if k != 'fileData'}
    payload_for_log['fileData'] = f"<{len(file_data)} chars base64>" if file_data else "<missing>"
    logger.info(f"{json.dumps(payload_for_log, indent=2)}")
    logger.info("═" * 80)

    logger.info("🌐 Sender HTTP POST request til cloud API...")

    try:
        response = requests.post(endpoint, json=jobPayload, timeout=timeoutSeconds)

        logger.info(f"📥 Mottok respons: HTTP {response.status_code}")
        logger.info("─" * 80)

        response.raise_for_status()

        if not response.content:
            logger.warning("⚠️  Respons har ingen content body")
            logger.info("═" * 80)
            return {}

        try:
            payload = response.json()

            # ═══════════════════════════════════════════════════════════════════════════
            # RESPONS LOGGING - Detaljert logging av cloud API-respons
            # ═══════════════════════════════════════════════════════════════════════════
            logger.info("✅ PRINT JOB SENDT TIL CLOUD API - SUKSESS")
            logger.info("─" * 80)
            logger.info("   📥 RESPONS FRA CLOUD API:")
            logger.info(f"   HTTP Status: {response.status_code}")
            logger.info(f"   Response Type: {type(payload).__name__}")
            if isinstance(payload, dict):
                logger.info("   Response Data:")
                for key, value in payload.items():
                    logger.info(f"     • {key}: {value}")
            logger.info("   ─── FULL JSON RESPONS ───")
            logger.info(f"{json.dumps(payload, indent=2)}")
            logger.info("═" * 80)

            if isinstance(payload, dict):
                return payload
            else:
                logger.warning(f"⚠️  Respons er ikke en dict, men {type(payload).__name__}")
                logger.info("═" * 80)
                return {}

        except ValueError as e:
            logger.error("❌ FEIL VED PARSING AV RESPONS")
            logger.error(f"   Error: {e}")
            logger.error(f"   Response Content: {response.text[:500]}")
            logger.error("═" * 80)
            return {}

    except requests.exceptions.Timeout as e:
        logger.error("═" * 80)
        logger.error("❌ TIMEOUT VED SENDING AV PRINT JOB TIL CLOUD API")
        logger.error("─" * 80)
        logger.error(f"   Target URL: {endpoint}")
        logger.error(f"   Timeout: {timeoutSeconds} sekunder")
        logger.error(f"   Error: {e}")
        logger.error("═" * 80)
        raise

    except requests.exceptions.ConnectionError as e:
        logger.error("═" * 80)
        logger.error("❌ CONNECTION ERROR VED SENDING AV PRINT JOB TIL CLOUD API")
        logger.error("─" * 80)
        logger.error(f"   Target URL: {endpoint}")
        logger.error(f"   Error: {e}")
        if "getaddrinfo failed" in str(e):
            logger.error("   ⚠️  DNS resolution failed - kan ikke finne hostname")
            logger.error(f"   ⚠️  Sjekk at domenet '{baseUrl}' er tilgjengelig")
        logger.error("═" * 80)
        raise

    except requests.exceptions.HTTPError as e:
        logger.error("═" * 80)
        logger.error("❌ HTTP ERROR VED SENDING AV PRINT JOB TIL CLOUD API")
        logger.error("─" * 80)
        logger.error(f"   Target URL: {endpoint}")
        logger.error(f"   HTTP Status: {response.status_code}")
        logger.error(f"   Response Text: {response.text[:500]}")
        logger.error(f"   Error: {e}")
        logger.error("═" * 80)
        raise

    except Exception as e:
        logger.error("═" * 80)
        logger.error("❌ UNEXPECTED ERROR VED SENDING AV PRINT JOB TIL CLOUD API")
        logger.error("─" * 80)
        logger.error(f"   Target URL: {endpoint}")
        logger.error(f"   Error Type: {type(e).__name__}")
        logger.error(f"   Error: {e}")
        import traceback
        logger.error(f"   Traceback:\n{traceback.format_exc()}")
        logger.error("═" * 80)
        raise


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
        logger.info(
            "[PRINTER_COMM] FTPS Upload Start",
            extra={
                "method": "FTPS",
                "protocol": "FTP_TLS",
                "port": 990,
                "destination": ip,
                "serial": "N/A",
                "action": "upload_file",
                "file_name": remoteName,
                "filesize_bytes": localPath.stat().st_size if localPath.exists() else 0,
                "comm_direction": "client_to_printer"
            }
        )
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
            # Put timestamp and unique suffix at the END so original name stays recognizable
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
            logger.info(
                "[PRINTER_COMM] FTPS Sending File Data",
                extra={
                    "method": "FTPS",
                    "port": 990,
                    "action": "storbinary_command",
                    "remote_path": f"/cache/{remoteName}",
                    "comm_direction": "client_to_printer"
                }
            )
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
            logger.info(
                "[PRINTER_COMM] FTPS Upload Complete",
                extra={
                    "method": "FTPS",
                    "port": 990,
                    "action": "upload_complete",
                    "remote_filename": remoteName,
                    "success": True
                }
            )
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
        logger.info(
            "[PRINTER_COMM] Bambu API Upload Start",
            extra={
                "method": "BAMBU_API",
                "protocol": "HTTPS",
                "port": 443,
                "destination": ip,
                "serial": serial,
                "action": "api_connect",
                "comm_direction": "client_to_printer"
            }
        )

    uploadMethod = None
    for candidate in ("upload_file", "upload_project", "upload"):
        uploadMethod = getattr(printer, candidate, None)
        if uploadMethod:
            break

    if uploadMethod is None:
        raise RuntimeError("Unable to locate an upload method on bambulabs_api.Printer")

    try:
        logger.info(
            "[PRINTER_COMM] Bambu API Uploading File",
            extra={
                "method": "BAMBU_API",
                "protocol": "HTTPS",
                "port": 443,
                "serial": serial,
                "action": "api_upload",
                "file_name": remoteName,
                "filesize_bytes": localPath.stat().st_size if localPath.exists() else 0,
                "comm_direction": "client_to_printer"
            }
        )
        # bambulabs_api 2.6.x expects a binary file handle rather than a path string
        with open(localPath, "rb") as fileHandle:
            try:
                uploadMethod(fileHandle, remoteName)
            except TypeError:
                fileHandle.seek(0)
                uploadMethod(fileHandle)
        logger.info(
            "[PRINTER_COMM] Bambu API Upload Complete",
            extra={
                "method": "BAMBU_API",
                "port": 443,
                "serial": serial,
                "action": "upload_complete",
                "remote_filename": remoteName,
                "success": True
            }
        )
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

    # Extract serial number for logging if available
    serialForLogging = "N/A"
    if isinstance(printer, dict):
        serialForLogging = printer.get("serialNumber") or printer.get("serial") or "N/A"

    logger.info(
        "[PRINTER_COMM] Deleting Remote File",
        extra={
            "method": "BAMBU_API",
            "serial": serialForLogging,
            "action": "delete_file",
            "remote_path": remotePath,
            "comm_direction": "client_to_printer"
        }
    )

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
    """
    Send the latest printer status to the configured remote endpoint.

    CRITICAL: This function has been updated to send status to the PostgreSQL
    backend (printpro3d-api) instead of the legacy Firestore backend to ensure
    the web frontend displays real-time printer status correctly.
    """

    # Get API key - try config manager first, then printer config, then environment
    apiKey = None
    if _config_manager_available:
        try:
            config = get_config_manager()
            apiKey = config.get_api_key()
        except Exception:
            pass

    if not apiKey:
        apiKey = printerConfig.get("statusApiKey")

    if not apiKey:
        apiKey = os.getenv("BASE44_API_KEY", "").strip() or None

    if not apiKey:
        return

    # Get recipient ID - try config manager first, then printer config, then environment
    recipientId = None
    if _config_manager_available:
        try:
            config = get_config_manager()
            recipientId = config.get_recipient_id()
        except Exception:
            pass

    if not recipientId:
        recipientId = printerConfig.get("statusRecipientId")

    if not recipientId:
        recipientId = os.getenv("BASE44_RECIPIENT_ID", "").strip() or None

    if not recipientId:
        logger.warning("postStatus: missing recipientId; skipping status update")
        return

    # CRITICAL: Always use PostgreSQL backend for status updates, not the configured URL
    # The configured URL may point to the old Firestore backend
    # Old: https://printer-backend-934564650450.europe-west1.run.app
    # New: https://printpro3d-api-931368217793.europe-west1.run.app
    postgresBackend = "https://printpro3d-api-931368217793.europe-west1.run.app"

    # Build endpoint URL (recipientId is sent in payload)
    url = f"{postgresBackend}/api/printer-status/update"

    # Get organizationId from environment variable or printer config
    organizationId = printerConfig.get("organizationId") or os.getenv("BASE44_ORGANIZATION_ID", "").strip()

    # SIMPLIFIED: Get fields directly from status dict - they are already set by _normalizeSnapshot
    # No need for parse_print_job_data - the fields are at top level of status
    parsedStatus = {
        "status": status.get("status") or status.get("state") or "UNKNOWN",
        "state": status.get("state") or status.get("status") or "UNKNOWN",
        "gcodeState": status.get("gcodeState"),
        "progressPercent": status.get("progressPercent"),
        "jobProgress": status.get("progressPercent"),  # Alias
        "bedTemp": status.get("bedTemp"),
        "nozzleTemp": status.get("nozzleTemp"),
        "chamberTemp": status.get("chamberTemp"),
        "fanSpeed": status.get("fanSpeedPercent"),
        "remainingTimeSeconds": status.get("remainingTimeSeconds"),
        "timeRemaining": status.get("remainingTimeSeconds"),  # Alias
        # Print Job Fields - these come directly from _normalizeSnapshot
        "fileName": status.get("fileName"),
        "gcodeFile": status.get("gcodeFile"),
        "printType": status.get("printType"),
        "currentLayer": status.get("currentLayer"),
        "totalLayers": status.get("totalLayers"),
        "lightState": status.get("lightState"),
        "printErrorCode": status.get("printErrorCode"),
        "skippedObjects": status.get("skippedObjects"),
        "printSpeed": status.get("printSpeed"),
    }
    
    # Remove None values to keep payload clean
    parsedStatus = {k: v for k, v in parsedStatus.items() if v is not None}

    # Build payload with all required fields for PostgreSQL backend
    # CRITICAL: API expects FLAT structure, not nested "status" object
    payload = {
        "recipientId": recipientId,
        "printerIpAddress": printerConfig.get("ipAddress"),  # CRITICAL: Required for matching
        "printerSerial": printerConfig.get("serialNumber"),
    }

    # Merge parsedStatus fields into top-level payload (not nested)
    # The API expects: nozzleTemp, bedTemp, progressPercent, etc. at the root level
    payload.update(parsedStatus)

    # Add organizationId if available
    if organizationId:
        payload["organizationId"] = organizationId

    # CRITICAL: Ensure printerIpAddress is set - this is required for matching in PostgreSQL
    if not payload.get("printerIpAddress"):
        logger.warning(
            "Skipping status update for printer %s - printerIpAddress is required",
            printerConfig.get("serialNumber") or "unknown"
        )
        return

    # Use X-API-Key header instead of including in payload
    headers = {
        "X-API-Key": apiKey,
        "Content-Type": "application/json"
    }

    # VERBOSE LOGGING - Before sending
    logger.info("─" * 80)
    logger.info("📤 SENDING PRINTER STATUS (postStatus - Legacy) to backend")
    logger.info(f"   Printer Serial: {printerConfig.get('serialNumber')}")
    logger.info(f"   Printer IP: {printerConfig.get('ipAddress')}")
    logger.info(f"   Target URL: {url}")
    logger.info(f"   API Key: {'✅ Present (' + str(len(apiKey)) + ' chars)' if apiKey else '❌ MISSING'}")
    logger.info(f"   Recipient ID: {recipientId}")
    if organizationId:
        logger.info(f"   Organization ID: {organizationId}")
    logger.info("   ─── PAYLOAD DATA ───")
    logger.info(f"   Status: {parsedStatus.get('status')}")
    logger.info(f"   GCode State: {parsedStatus.get('gcodeState')}")
    logger.info(f"   Progress: {parsedStatus.get('progressPercent') or parsedStatus.get('jobProgress')}%")
    logger.info(f"   Nozzle Temp: {parsedStatus.get('nozzleTemp')}°C")
    logger.info(f"   Bed Temp: {parsedStatus.get('bedTemp')}°C")
    logger.info(f"   Chamber Temp: {parsedStatus.get('chamberTemp')}°C")
    logger.info(f"   Remaining Time: {parsedStatus.get('remainingTimeSeconds') or parsedStatus.get('timeRemaining')}s")
    logger.info("   ─── NEW PRINT JOB FIELDS ───")
    logger.info(f"   File Name: {parsedStatus.get('fileName')}")
    logger.info(f"   Gcode File: {parsedStatus.get('gcodeFile')}")
    logger.info(f"   Print Type: {parsedStatus.get('printType')}")
    logger.info(f"   Current Layer: {parsedStatus.get('currentLayer')}")
    logger.info(f"   Total Layers: {parsedStatus.get('totalLayers')}")
    logger.info(f"   Light State: {parsedStatus.get('lightState')}")
    logger.info(f"   Print Error Code: {parsedStatus.get('printErrorCode')}")
    logger.info(f"   Skipped Objects: {parsedStatus.get('skippedObjects')}")
    logger.info(f"   Fan Speed: {parsedStatus.get('fanSpeed')}")
    logger.info("   ─── FULL JSON PAYLOAD ───")
    import json
    logger.info(f"{json.dumps(payload, indent=2)}")
    logger.info("─" * 80)

    try:
        logger.info("🌐 Making HTTP POST request...")
        response = requests.post(url, json=payload, headers=headers, timeout=5)

        logger.info(f"📥 Got response: HTTP {response.status_code}")

        response.raise_for_status()

        logger.info("✅ PRINTER STATUS SENT SUCCESSFULLY (Legacy postStatus)")
        logger.info(f"   Printer Serial: {printerConfig.get('serialNumber')}")
        logger.info(f"   Printer IP: {printerConfig.get('ipAddress')}")
        logger.info(f"   Status: {payload.get('status')}")
        logger.info(f"   Response: {response.text[:500]}")
        logger.info("─" * 80)

    except requests.exceptions.Timeout as e:
        logger.error("❌ REQUEST TIMEOUT (Legacy postStatus)")
        logger.error(f"   Printer Serial: {printerConfig.get('serialNumber')}")
        logger.error(f"   Target: {url}")
        logger.error(f"   Error: {e}")
        logger.error("─" * 80)

    except requests.exceptions.HTTPError as e:
        logger.error("❌ HTTP ERROR (Legacy postStatus)")
        logger.error(f"   Printer Serial: {printerConfig.get('serialNumber')}")
        logger.error(f"   Status Code: {response.status_code}")
        logger.error(f"   Response: {response.text[:500]}")
        logger.error(f"   Error: {e}")
        logger.error("─" * 80)

    except requests.exceptions.RequestException as e:
        logger.error("❌ REQUEST ERROR (Legacy postStatus)")
        logger.error(f"   Printer Serial: {printerConfig.get('serialNumber')}")
        logger.error(f"   Error: {e}")
        logger.error("─" * 80)

    except Exception as e:
        logger.error("❌ UNEXPECTED ERROR (Legacy postStatus)")
        logger.error(f"   Printer Serial: {printerConfig.get('serialNumber')}")
        logger.error(f"   Error Type: {type(e).__name__}")
        logger.error(f"   Error: {e}", exc_info=True)
        logger.error("─" * 80)


def _resolveTimeLapseDirectory(
    options: "BambuPrintOptions", *, ensure: bool = False
) -> Optional[Path]:
    enableTimeLapse = getattr(options, "enableTimeLapse", False)
    logger.info("[timelapse] _resolveTimeLapseDirectory kalles - enableTimeLapse=%s", enableTimeLapse)

    # DEBUG: Logg ALLE options-verdier
    import traceback
    logger.info("[timelapse] options objekt: %s", options)
    logger.info("[timelapse] Stacktrace for å finne hvem som kalte:")
    for line in traceback.format_stack()[-5:]:
        logger.info("[timelapse] %s", line.strip())

    if not enableTimeLapse:
        logger.warning("[timelapse] enableTimeLapse er False - returnerer None")
        return None

    rawDirectory = options.timeLapseDirectory
    logger.info("[timelapse] options.timeLapseDirectory=%s", rawDirectory)

    if rawDirectory is None:
        rawDirectory = Path.home() / ".printmaster" / "timelapse"
        logger.info("[timelapse] Ingen directory spesifisert, bruker standard: %s", rawDirectory)

    directory = Path(rawDirectory).expanduser()
    if ensure:
        directory.mkdir(parents=True, exist_ok=True)
        logger.info("[timelapse] Mappe opprettet/bekreftet: %s", directory)

    return directory


def _activateTimelapseCapture(printer: Any, directory: Path) -> None:
    logger.info("[timelapse] _activateTimelapseCapture kalles med directory: %s", directory)
    directoryString = str(directory)
    cameraClient = getattr(printer, "camera_client", None)
    cameraConfigured = False

    activated = False
    mqttClient = getattr(printer, "mqtt_client", None)
    logger.info("[timelapse] mqttClient: %s", mqttClient)

    if mqttClient is not None:
        setTimelapseMethod = getattr(mqttClient, "set_onboard_printer_timelapse", None)
        logger.info("[timelapse] setTimelapseMethod funnet: %s", setTimelapseMethod is not None)

        if callable(setTimelapseMethod):
            try:
                logger.info("[timelapse] Kaller set_onboard_printer_timelapse(enable=True)...")
                mqttResult = setTimelapseMethod(enable=True)
                logger.info("[timelapse] set_onboard_printer_timelapse returnerte: %s", mqttResult)

                if mqttResult:
                    activated = True
                    logger.info("[timelapse] Timelapse AKTIVERT via MQTT!")
                else:
                    logger.warning("[timelapse] set_onboard_printer_timelapse returnerte False/None")
            except Exception as error:
                logger.error("[timelapse] set_onboard_printer_timelapse feilet: %s", error, exc_info=True)
        else:
            logger.warning("[timelapse] set_onboard_printer_timelapse er ikke callable")
    else:
        logger.warning("[timelapse] mqttClient er None - kan ikke aktivere timelapse via MQTT")

    if not activated and cameraClient is not None:
        for methodName in (
            "set_timelapse_directory",
            "set_output_directory",
            "set_save_directory",
            "set_directory",
        ):
            configureMethod = getattr(cameraClient, methodName, None)
            if callable(configureMethod):
                try:
                    configureMethod(directoryString)
                    cameraConfigured = True
                    break
                except Exception:  # pragma: no cover - diagnostic logging only
                    logger.debug("timelapse configure %s failed", methodName, exc_info=START_DEBUG)
        if not cameraConfigured:
            for attributeName in (
                "timelapseDirectory",
                "timelapse_directory",
                "outputDirectory",
                "output_directory",
            ):
                if hasattr(cameraClient, attributeName):
                    try:
                        setattr(cameraClient, attributeName, directoryString)
                        cameraConfigured = True
                        break
                    except Exception:  # pragma: no cover - diagnostic logging only
                        logger.debug(
                            "timelapse attribute %s assignment failed",
                            attributeName,
                            exc_info=START_DEBUG,
                        )
        if not bool(getattr(cameraClient, "alive", False)):
            startMethod = getattr(printer, "camera_start", None)
            if callable(startMethod):
                try:
                    startMethod()
                except Exception:  # pragma: no cover - best effort
                    logger.debug("camera_start() failed during timelapse activation", exc_info=START_DEBUG)

    if not activated:
        for candidate in (cameraClient, printer):
            if candidate is None:
                continue
            for methodName in ("start_timelapse_capture", "start_timelapse", "enable_timelapse"):
                activationMethod = getattr(candidate, methodName, None)
                if not callable(activationMethod):
                    continue
                try:
                    activationMethod(directoryString)
                    activated = True
                    break
                except TypeError:
                    try:
                        activationMethod()
                        activated = True
                        break
                    except Exception:  # pragma: no cover - diagnostic logging only
                        logger.debug(
                            "timelapse activation %s without args failed",
                            methodName,
                            exc_info=START_DEBUG,
                        )
                except Exception:  # pragma: no cover - diagnostic logging only
                    logger.debug("timelapse activation %s failed", methodName, exc_info=START_DEBUG)
            if activated:
                break

    if hasattr(printer, "timelapse_directory"):
        try:
            setattr(printer, "timelapse_directory", directoryString)
        except Exception:  # pragma: no cover - diagnostic logging only
            logger.debug("setting printer.timelapse_directory failed", exc_info=START_DEBUG)

    if START_DEBUG:
        if activated:
            logger.info("[timelapse] capture activated for %s", directoryString)
        else:
            logger.info("[timelapse] capture requested for %s but no activation hooks succeeded", directoryString)


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

    serialForLogging = getattr(options, "serialNumber", "N/A")

    autoStepMethod = getattr(printer, "set_auto_step_recovery", None)
    desiredLayerInspect = bool(getattr(options, "layerInspect", True))
    if callable(autoStepMethod):
        try:
            logger.info(
                "[PRINTER_COMM] Setting Layer Inspect",
                extra={
                    "method": "BAMBU_API",
                    "serial": serialForLogging,
                    "action": "set_layer_inspect",
                    "enabled": desiredLayerInspect,
                    "comm_direction": "client_to_printer"
                }
            )
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
            logger.info(
                "[PRINTER_COMM] Setting Calibration",
                extra={
                    "method": "BAMBU_API",
                    "serial": serialForLogging,
                    "action": "set_calibration",
                    "bed_leveling": bedEnabled,
                    "vibration_calibration": vibrationEnabled,
                    "comm_direction": "client_to_printer"
                }
            )
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


def _extract_ams_and_skips_from_metadata(job_metadata: Optional[Dict[str, Any]]) -> tuple[Optional[List[int]], Optional[List[int]]]:
    """Returner (ams_mapping, skip_objects) fra job-metadata.
    - AMS: sorter `ams_configuration.slots` på colorIndex (0,1,2,...) og bruk slotNumber/slot (0-basert) i den rekkefølgen.
    - Skip: flat liste av identify_id (ints) i `skipped_objects`.
    Aksepterer varianter av feltnavn (colorIndex/color_index/index/order og slot/slotNumber/slotNummer/tray).
    """
    ams_mapping: Optional[List[int]] = None
    skip_objects: Optional[List[int]] = None
    try:
        if not isinstance(job_metadata, dict):
            return (None, None)
        root = job_metadata
        inner = root.get("unencryptedData") or root

        # --- Skipped objects ---
        raw_skips = inner.get("skipped_objects") or root.get("skipped_objects")
        if isinstance(raw_skips, (list, tuple)):
            out: List[int] = []
            for x in raw_skips:
                try:
                    out.append(int(x))
                except Exception:
                    continue
            if out:
                skip_objects = out

        # --- AMS mapping ---
        cfg = inner.get("ams_configuration") or {}
        slots = cfg.get("slots") or []
        parsed: List[tuple[int, int]] = []
        if isinstance(slots, (list, tuple)):
            def _norm(k: str) -> str:
                return "".join(ch for ch in str(k).lower() if ch.isalnum())
            for s in slots:
                if not isinstance(s, dict):
                    continue
                kv = { _norm(k): v for k, v in s.items() }
                color_val = kv.get("colorindex", kv.get("color_index", kv.get("index", kv.get("order"))))
                slot_val  = kv.get("slot", kv.get("slotnumber", kv.get("slotnummer", kv.get("tray"))))
                try:
                    parsed.append((int(color_val), int(slot_val)))
                except Exception:
                    continue
        if parsed:
            parsed.sort(key=lambda t: t[0])
            ams_mapping = [slot for _, slot in parsed]
    except Exception:
        logger.debug("AMS/skip parse error", exc_info=True)
    return (ams_mapping, skip_objects)



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

    # Connect to printer via MQTT
    connectionMethod = getattr(printer, "mqtt_start", None) or getattr(printer, "connect", None)
    if connectionMethod:
        connectionMethod()
        logger.info(
            "[PRINTER_COMM] Bambu API Start Print Connection",
            extra={
                "method": "BAMBU_API",
                "protocol": "MQTT",
                "port": 8883,
                "destination": ip,
                "serial": serial,
                "action": "mqtt_connect",
                "comm_direction": "client_to_printer"
            }
        )
        # Wait for MQTT connection to establish and receive printer state
        max_wait = 5.0  # Maximum 5 seconds
        wait_interval = 0.5
        elapsed = 0.0
        while elapsed < max_wait:
            time.sleep(wait_interval)
            elapsed += wait_interval
            # Try to get printer state to verify connection is ready
            state = _safe_get_state(printer)
            if state is not None:
                logger.info("[PRINTER_COMM] MQTT connection established and printer state received (waited %.1fs)", elapsed)
                break
        else:
            # Timed out waiting for state, but continue anyway
            logger.warning("[PRINTER_COMM] MQTT connection timeout - proceeding anyway (waited %.1fs)", elapsed)

    resolvedUseAms = resolveUseAmsAuto(options, job_metadata, None)

    # Resolve requested timelapse directory from options first
    logger.info("[timelapse] startPrintViaApi - timelapse_directory parameter: %s", timelapse_directory)
    timelapsePath = timelapse_directory or _resolveTimeLapseDirectory(options, ensure=True)
    logger.info("[timelapse] startPrintViaApi - timelapsePath etter _resolveTimeLapseDirectory: %s", timelapsePath)

    # --- NEW: metadata fallback (covers MQTT→API fallback where options.enableTimeLapse wasn't set) ---
    if timelapsePath is None and job_metadata:
        logger.info("[timelapse] timelapsePath er None, sjekker job_metadata for fallback...")
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
            logger.info("[timelapse] FALLBACK aktivert - timelapse funnet i metadata -> %s", timelapsePath)
        else:
            logger.warning("[timelapse] job_metadata inneholder IKKE enableTimeLapse")

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

    # --- NEW: parse AMS mapping and skip_objects from job metadata -------------------------
    amsMapping: Optional[List[int]] = None
    skipObjects: Optional[List[int]] = None
    try:
        meta = job_metadata if isinstance(job_metadata, dict) else None
        if meta:
            # Some callers wrap data inside "unencryptedData". Fall back to root if not present.
            root = meta
            inner = root.get("unencryptedData") or root

            # skipped objects: a flat list of identify_id integers
            raw_skips = inner.get("skipped_objects") or root.get("skipped_objects")
            if isinstance(raw_skips, (list, tuple)):
                skipObjects = []
                for x in raw_skips:
                    try:
                        skipObjects.append(int(x))
                    except Exception:
                        continue

            # ams mapping: sort slots by colorIndex and collect slot numbers (0-based across AMS units)
            cfg = inner.get("ams_configuration") or {}
            slots = cfg.get("slots") or []
            parsed: List[tuple] = []
            for s in slots if isinstance(slots, (list, tuple)) else []:
                if not isinstance(s, dict):
                    continue
                def _norm(k: str) -> str:
                    return "".join(ch for ch in str(k).lower() if ch.isalnum())
                kv = { _norm(k): v for k, v in s.items() }
                # Accept multiple spellings: colorIndex / color_index / index / order
                color_val = kv.get("colorindex", kv.get("color_index", kv.get("index", kv.get("order"))))
                # Accept slot / slotNumber / slotNummer / tray
                slot_val = kv.get("slot", kv.get("slotnumber", kv.get("slotnummer", kv.get("tray"))))
                try:
                    color_int = int(color_val)
                    slot_int = int(slot_val)
                    parsed.append((color_int, slot_int))
                except Exception:
                    continue
            if parsed:
                parsed.sort(key=lambda t: t[0])
                amsMapping = [slot for _, slot in parsed]

    except Exception:
        logger.debug("Failed to parse AMS/skip metadata", exc_info=True)

    if skipObjects:
        startKeywordArgs["skip_objects"] = skipObjects
        logger.info("[start] skip_objects preselected in job: %s", skipObjects)
    # Only include ams_mapping when use_ams is True or None (auto-detect)
    # When use_ams=False (external spool), ams_mapping causes HMS validation errors
    if amsMapping and resolvedUseAms is not False:
        startKeywordArgs["ams_mapping"] = amsMapping
        logger.info("[start] ams_mapping requested: %s", amsMapping)
    elif amsMapping and resolvedUseAms is False:
        logger.info("[start] ams_mapping SKIPPED (use_ams=False, external spool mode): %s", amsMapping)


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
    # NEW: Always try to clear FINISH state before attempting to start
    if preflightState is not None:
        preflightStateText = _extract_gcode_state(preflightState) or extractStateText(preflightState)
        if _normalizeStateName(preflightStateText) in finishStateNames:
            logger.info("[start] Printer is in FINISH state before start - clearing...")
            try:
                stopMethod = getattr(printer, "stop_print", None)
                if callable(stopMethod):
                    stopMethod()
                    logger.info("[start] Sent stop_print to clear FINISH state proactively")
                    time.sleep(3.0)  # Give printer more time to process
                    preflightState = _safe_get_state(printer)  # Refresh state after clearing
            except Exception as error:
                logger.warning("[start] Failed to proactively clear FINISH state: %s", error)

    if _state_is_completed_like(preflightState):
        # Printer is in FINISH state - try to force it out of FINISH state first
        logger.info("[start] Printer in FINISH state, attempting to reset/clear state...")
        try:
            # Try to stop/clear the current print to get out of FINISH state
            stopMethod = getattr(printer, "stop_print", None)
            if callable(stopMethod):
                try:
                    stopMethod()
                    logger.info("[start] Sent stop_print to clear FINISH state")
                    time.sleep(3.0)  # Give printer more time to process
                except Exception as error:
                    logger.debug("[start] stop_print failed (may already be stopped): %s", error)
        except Exception as error:
            logger.debug("[start] Error trying to clear FINISH state: %s", error)
        _preselect_project_file(printer, remoteUrl, startParam, sendControlFlags)

    if timelapsePath is not None:
        logger.info("[timelapse] timelapsePath er satt, aktiverer timelapse: %s", timelapsePath)
        _activateTimelapseCapture(printer, timelapsePath)
    else:
        logger.warning("[timelapse] timelapsePath er None - timelapse vil IKKE aktiveres")

    finishStateNames = {"FINISH", "FINISHED", "COMPLETE", "COMPLETED"}

    def _normalizeStateName(stateText: Optional[str]) -> str:
        return (stateText or "").strip().upper()

    observedFinishState = False
    preflightStateText = _extract_gcode_state(preflightState) or extractStateText(preflightState)
    if _normalizeStateName(preflightStateText) in finishStateNames:
        observedFinishState = True

    def _invokeStart() -> None:
        localErrors: List[str] = []
        started = False
        startMethod = getattr(printer, "start_print", None)
        if callable(startMethod):
            try:
                logger.info(
                    "[PRINTER_COMM] Starting Print via API",
                    extra={
                        "method": "BAMBU_API",
                        "protocol": "HTTPS",
                        "port": 443,
                        "serial": serial,
                        "action": "start_print_command",
                        "file_name": uploaded_name,
                        "plate_index": plate_index,
                        "use_ams": options.useAms if options else None,
                        "comm_direction": "client_to_printer"
                    }
                )
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
                    # Only include ams_mapping in fallback path when use_ams is True or None
                    # When use_ams=False (external spool), ams_mapping causes HMS validation errors
                    if amsMapping and resolvedUseAms is not False:
                        payload["print"]["ams_mapping"] = amsMapping
                    if skipObjects:
                        payload["print"]["skip_objects"] = skipObjects
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
        nonlocal observedFinishState
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
                        if _normalizeStateName(candidateState) in finishStateNames:
                            observedFinishState = True
                    elif _normalizeStateName(extractStateText(statePayload)) in finishStateNames:
                        observedFinishState = True
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
            # Only consider printer ready when it's actually printing (not just heating)
            # This prevents premature disconnect during warmup phase
            printingState = _is_printing_state(stateIndicator)
            activeOk = printingState and not completedLike
            # Also accept if printer has transitioned from FINISH to IDLE/ready state
            # This handles the case where printer is ready but not yet actively printing
            stateNormalized = _normalizeStateName(stateIndicator)
            if stateNormalized in finishStateNames:
                observedFinishState = True
            finishTransitionReady = observedFinishState and stateNormalized not in finishStateNames
            idleOrReady = finishTransitionReady and stateNormalized in ("IDLE", "READY", "STANDBY") and not completedLike
            # Accept if printer is no longer in FINISH state (even if still at 100%)
            # This handles the case where printer has exited FINISH but hasn't started printing yet
            exitedFinish = (
                finishTransitionReady
                and completedLike
                and (percentageFloat is None or percentageFloat < 100.0)
            )
            if START_DEBUG:
                logger.info(
                    "[start] poll state=%s percent=%s completedLike=%s printingState=%s activeOk=%s pctOk=%s idleOrReady=%s exitedFinish=%s finishObserved=%s",
                    stateIndicator,
                    lastPercentage,
                    completedLike,
                    printingState,
                    activeOk,
                    pctOk,
                    idleOrReady,
                    exitedFinish,
                    observedFinishState,
                )
            if pctOk or activeOk or idleOrReady or exitedFinish:
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

        # Check if printer is in FINISH state (from ackResult, not preflight)
        currentState = ackResult.get("state")
        currentGcodeState = ackResult.get("gcodeState")
        isInFinishState = (
            currentState == "FINISH" or
            currentGcodeState == "FINISH" or
            _state_is_completed_like(preflightState) or
            _state_is_completed_like(ackResult.get("statePayload"))
        )

        # Debug logging to understand why FINISH detection might not trigger
        logger.info(
            "[start] State check: currentState=%s, currentGcodeState=%s, preflightState=%s, isInFinishState=%s, acknowledged=%s",
            currentState, currentGcodeState, preflightState, isInFinishState, acknowledged
        )

        # NEW: Check for HMS error code 07ff-2000-0002-0004 specifically
        statePayload = ackResult.get("statePayload")
        if statePayload and isinstance(statePayload, dict):
            hmsMessages = statePayload.get("messages", [])
            if any("07ff-2000-0002-0004" in str(msg).lower() for msg in hmsMessages):
                logger.warning("[start] Detected HMS code 07ff-2000-0002-0004 - file sent but not started")
                isInFinishState = True  # Treat this like a FINISH state that needs clearing

        # If printer is in FINISH state, we need to clear it first
        finishStateHandled = False
        if isInFinishState and not acknowledged:
            logger.info("[start] Printer is in FINISH state - sending stop_print to clear state...")
            try:
                stopMethod = getattr(printer, "stop_print", None)
                if callable(stopMethod):
                    stopMethod()
                    logger.info("[start] Sent stop_print to clear FINISH state")
                    time.sleep(3.0)  # Give printer more time to process
                    # Try starting again
                    _invokeStart()
                    ackResult = _pollForAcknowledgement(ack_timeout_sec)
                    acknowledged = bool(ackResult.get("acknowledged"))
                    finishStateHandled = True
                    logger.info("[start] After FINISH state clear: acknowledged=%s", acknowledged)
            except Exception as error:
                logger.warning("[start] Failed to clear FINISH state: %s", error)
        conflictDetected = _looksLikeAmsFilamentConflict(ackResult.get("statePayload"))
        # Only trigger AMS fallback if we haven't already handled FINISH state
        if (resolvedUseAms is None) and (conflictDetected or not acknowledged) and not finishStateHandled:
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
            "[PRINTER_COMM] Print Start Acknowledged",
            extra={
                "method": "BAMBU_API",
                "port": 443,
                "serial": serial,
                "action": "print_start_ack",
                "acknowledged": ackResult.get("acknowledged", False),
                "gcode_state": ackResult.get("gcodeState"),
                "success": True
            }
        )
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


def extractOrderedObjectsFromArchive(archivePath: Path) -> List[Dict[str, Any]]:
    """
    Extract ordered objects from a 3MF archive's slice_info.config.
    
    This function reads object information from the Metadata/slice_info.config
    file, which contains the correct identify_id values used in G-code
    and during printing.
    
    Args:
        archivePath: Path to the 3MF archive file
        
    Returns:
        List of objects, each containing:
        - identify_id: The object's unique identifier used in G-code
        - name: Object name (e.g., "Model.stl 7")
        - plate_id: Which plate the object belongs to (1-indexed)
        - order: Sequential print order (1-indexed)
        - skipped: Whether the object is marked as skipped
        
    Raises:
        ValueError: If the archive is invalid or missing slice_info.config
    """
    try:
        with zipfile.ZipFile(archivePath, "r") as archive:
            try:
                sliceInfo = archive.read("Metadata/slice_info.config")
            except KeyError as error:
                raise ValueError("3MF archive is missing slicer metadata (Metadata/slice_info.config)") from error
    except zipfile.BadZipFile as error:
        raise ValueError(f"{archivePath} is not a valid 3MF archive") from error

    root = ET.fromstring(sliceInfo)
    orderedObjects: List[Dict[str, Any]] = []
    objectOrder = 1

    for plateElement in root.findall("plate"):
        plateIndex = _extractPlateIndex(plateElement)
        
        for objectElement in plateElement.findall("object"):
            identifyId = objectElement.get("identify_id") or objectElement.get("object_id") or objectElement.get("id")
            objectName = objectElement.get("name")
            skipped = objectElement.get("skipped", "false").lower() == "true"
            
            objectData: Dict[str, Any] = {
                "order": objectOrder,
                "identify_id": identifyId,
                "name": objectName,
                "plate_id": plateIndex,
                "skipped": skipped,
            }
            orderedObjects.append(objectData)
            objectOrder += 1

    return orderedObjects


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


def _create_event_reporter_if_available() -> Optional[Any]:
    """
    Create an EventReporter instance if credentials are available from config file.

    Credentials are read from ~/.printmaster/config.json:
    - backend_url: Backend API URL
    - api_key: API authentication key
    - recipient_id: Unique recipient identifier

    Returns:
        EventReporter instance or None if not available/configured
    """
    if not _event_reporter_available or EventReporter is None:
        return None

    # Get configuration from config manager (preferred)
    base_url = None
    api_key = None
    recipient_id = None

    if _config_manager_available:
        try:
            config = get_config_manager()
            base_url = config.get_backend_url()
            api_key = config.get_api_key()
            recipient_id = config.get_recipient_id()
        except Exception as e:
            logger.debug(f"Could not load config from config manager: {e}")

    # Fallback to environment variables (not recommended, use config file instead)
    if not base_url:
        base_url = os.getenv("BASE44_API_URL", "").strip()
    if not api_key:
        api_key = os.getenv("BASE44_API_KEY", "").strip() or os.getenv("BASE44_FUNCTIONS_API_KEY", "").strip()
    if not recipient_id:
        recipient_id = os.getenv("BASE44_RECIPIENT_ID", "").strip()

    if not base_url or not api_key or not recipient_id:
        missing = []
        if not base_url:
            missing.append("backend_url")
        if not api_key:
            missing.append("api_key")
        if not recipient_id:
            missing.append("recipient_id")
        logger.debug(f"Event reporting not configured (missing: {', '.join(missing)})")
        return None

    try:
        logger.debug("Creating event reporter with credentials from config file")
        return EventReporter(
            base_url=base_url,
            api_key=api_key,
            recipient_id=recipient_id
        )
    except Exception as e:
        logger.debug(f"Failed to create event reporter: {e}")
        return None


def sendBambuPrintJob(
    *,
    filePath: Path,
    options: BambuPrintOptions,
    statusCallback: Optional[Callable[[Dict[str, Any]], None]] = None,
    skippedObjects: Optional[Sequence[Dict[str, Any]]] = None,
    jobMetadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Upload a file and start a Bambu print job."""

    printJobId = None
    if jobMetadata:
        printJobId = (
            jobMetadata.get("printJobId")
            or jobMetadata.get("print_job_id")
            or jobMetadata.get("jobId")
        )

    logger.info("=" * 80)
    logger.info("🖨️  STARTING PRINT JOB")
    logger.info(f"   Printer Serial: {options.serialNumber}")
    logger.info(f"   Printer IP: {options.ipAddress}")
    logger.info(f"   File: {filePath.name}")
    logger.info(f"   Print Job ID: {printJobId or 'None'}")


    logger.info(
        "[PRINT_JOB] Dispatch Started",
        extra={
            "serial": options.serialNumber,
            "ip": options.ipAddress,
            "nickname": options.nickname,
            "transport": options.transport,
            "use_cloud": options.useCloud,
            "lan_strategy": options.lanStrategy,
            "start_strategy": options.startStrategy,
            "file_name": filePath.name,
            "action": "dispatch_begin"
        }
    )

    resolvedPath = filePath.expanduser().resolve()
    if not resolvedPath.exists():
        raise FileNotFoundError(resolvedPath)

    plateIndex = options.plateIndex
    lanStrategy = (options.lanStrategy or "legacy").lower()

    # --- NEW: løft timelapse fra jobMetadata → options før vi lager/mappe ---
    try:
        logger.info("[timelapse] sendBambuPrintJob - jobMetadata mottatt: %s", jobMetadata)
        logger.info("[timelapse] sendBambuPrintJob - options.enableTimeLapse FØR: %s", getattr(options, "enableTimeLapse", False))

        if jobMetadata:
            tl_hint = _findMetadataValue(jobMetadata, {"enable_timelapse", "enabletimelapse", "timelapseenabled"})
            logger.info("[timelapse] sendBambuPrintJob - _findMetadataValue returnerte: %s", tl_hint)

            tl_bool = _interpretFlexibleBoolean(tl_hint) if tl_hint is not None else None
            logger.info("[timelapse] sendBambuPrintJob - _interpretFlexibleBoolean returnerte: %s", tl_bool)

            tl_dir  = _findMetadataValue(jobMetadata, {"timelapse_directory", "timelapsedirectory", "timelapsepath"})
            logger.info("[timelapse] sendBambuPrintJob - timelapse directory funnet: %s", tl_dir)

            if tl_bool and not getattr(options, "enableTimeLapse", False):
                logger.info("[timelapse] sendBambuPrintJob - Setter enableTimeLapse=True på options!")
                options = replace(options, enableTimeLapse=True)
            if tl_dir and not getattr(options, "timeLapseDirectory", None):
                try:
                    options = replace(options, timeLapseDirectory=Path(str(tl_dir)).expanduser())
                    logger.info("[timelapse] sendBambuPrintJob - Satte timeLapseDirectory=%s", tl_dir)
                except Exception as ex:
                    logger.warning("[timelapse] sendBambuPrintJob - Kunne ikke sette timeLapseDirectory: %s", ex)
        else:
            logger.warning("[timelapse] sendBambuPrintJob - jobMetadata er None eller tomt!")

        logger.info("[timelapse] sendBambuPrintJob - options.enableTimeLapse ETTER: %s", getattr(options, "enableTimeLapse", False))
    except Exception as ex:
        logger.warning("[timelapse] timelapse metadata merge failed: %s", ex, exc_info=START_DEBUG)

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

        # DEBUG: Log transport decision
        logger.info("=" * 80)
        logger.info("🔍 TRANSPORT BESLUTNING")
        logger.info(f"   options.useCloud = {options.useCloud}")
        logger.info(f"   options.cloudUrl = {options.cloudUrl}")
        logger.info(f"   Vil bruke cloud? {options.useCloud and options.cloudUrl}")
        logger.info("=" * 80)

        if options.useCloud and options.cloudUrl:
            logger.info("═" * 80)
            logger.info("🌐 CLOUD TRANSPORT VALGT")
            logger.info(
                "[PRINT_JOB] Using Cloud Transport",
                extra={
                    "serial": options.serialNumber,
                    "transport": "cloud",
                    "cloud_url": options.cloudUrl,
                    "action": "transport_selected"
                }
            )
            logger.info(f"   Serial Number: {options.serialNumber}")
            logger.info(f"   IP Address: {options.ipAddress}")
            logger.info(f"   Cloud URL: {options.cloudUrl}")
            logger.info(f"   Cloud Timeout: {options.cloudTimeout}s")
            logger.info("═" * 80)

            useAmsForCloud = resolvedUseAms if resolvedUseAms is not None else True

            logger.info("📦 Bygger cloud job payload...")
            try:
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
                logger.info("✅ Payload bygget vellykket")
            except Exception as e:
                logger.error("═" * 80)
                logger.error("❌ FEIL VED BYGGING AV CLOUD JOB PAYLOAD")
                logger.error(f"   Error Type: {type(e).__name__}")
                logger.error(f"   Error: {e}")
                import traceback
                logger.error(f"   Traceback:\n{traceback.format_exc()}")
                logger.error("═" * 80)
                raise

            logger.info("🚀 Kaller sendPrintJobViaCloud()...")
            try:
                response = sendPrintJobViaCloud(options.cloudUrl, payload, timeoutSeconds=options.cloudTimeout)
                logger.info("✅ sendPrintJobViaCloud() returnerte vellykket")
                logger.info(f"   Response: {response}")
            except Exception as e:
                logger.error("═" * 80)
                logger.error("❌ EXCEPTION FRA sendPrintJobViaCloud()")
                logger.error(f"   Error Type: {type(e).__name__}")
                logger.error(f"   Error: {e}")
                import traceback
                logger.error(f"   Traceback:\n{traceback.format_exc()}")
                logger.error("═" * 80)
                raise

            if statusCallback:
                statusCallback(_with_plate_options({"status": "cloudAccepted", "response": response}))
            resultPayload = {
                "method": "cloud",
                "remoteFile": remoteName,
                "paramPath": paramPath,
                "response": response,
            }
            logger.info("✅ Cloud print job fullført, returnerer resultPayload")
            return _with_plate_options(resultPayload)

        uploadedName: Optional[str] = None

        logger.info(
            "[PRINT_JOB] Using LAN Transport",
            extra={
                "serial": options.serialNumber,
                "ip": options.ipAddress,
                "transport": "lan",
                "upload_method": lanStrategy,
                "start_method": options.startStrategy,
                "action": "transport_selected"
            }
        )

        if lanStrategy == "bambuapi":
            logger.info(
                "[PRINT_JOB] Upload Method Selected",
                extra={
                    "serial": options.serialNumber,
                    "upload_method": "bambu_api",
                    "will_use_protocol": "HTTPS",
                    "will_use_port": 443,
                    "action": "upload_method_selected"
                }
            )
            uploadedName = uploadViaBambulabsApi(
                ip=options.ipAddress,
                serial=options.serialNumber,
                accessCode=options.accessCode,
                localPath=workingPath,
                remoteName=printerFileName,
            )
        else:
            logger.info(
                "[PRINT_JOB] Upload Method Selected",
                extra={
                    "serial": options.serialNumber,
                    "upload_method": "ftps",
                    "will_use_protocol": "FTP_TLS",
                    "will_use_port": 990,
                    "action": "upload_method_selected"
                }
            )
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

        logger.info(
            "[PRINT_JOB] Start Method Selected",
            extra={
                "serial": options.serialNumber,
                "start_method": "api",
                "will_use_protocol": "HTTPS",
                "will_use_port": 443,
                "wait_seconds": options.waitSeconds,
                "action": "start_method_selected"
            }
        )

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
            logger.error("❌ PRINT JOB FAILED")
            logger.error(f"   Error: {error}")
            logger.warning("API start failed for %s: %s", options.serialNumber, error, exc_info=True)

            logger.info("=" * 80)

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

        logger.info("🎉 PRINT JOB STARTED SUCCESSFULLY")
        logger.info(f"   Serial: {options.serialNumber}")
        logger.info(f"   File: {uploadedName}")
        logger.info(f"   Start Method: {startMethodResult}")
        logger.info("=" * 80)

        logger.info(
            "[PRINT_JOB] Dispatch Complete",
            extra={
                "serial": options.serialNumber,
                "success": True,
                "remote_file": uploadedName,
                "start_method": startMethodResult,
                "action": "dispatch_complete"
            }
        )

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

