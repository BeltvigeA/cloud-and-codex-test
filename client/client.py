"""Command-line client for interacting with the Cloud Run printer backend."""

import argparse
import json
import logging
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests

from .database import LocalDatabase
from .persistence import storePrintSummary
from .bambuPrinter import BambuPrintOptions, sendBambuPrintJob


defaultBaseUrl = "https://printer-backend-934564650450.europe-west1.run.app"
defaultFilesDirectory = Path.home() / ".printmaster" / "files"


def configureLogging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )


def parseArguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local PC client for interacting with the Cloud Run printer backend.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetchParser = subparsers.add_parser(
        "fetch",
        help="Fetch a file and metadata using a fetch token.",
    )
    fetchParser.add_argument(
        "--mode",
        choices={"remote", "offline"},
        default="remote",
        help="Select remote HTTP backend or offline file-based backend.",
    )
    fetchParser.add_argument(
        "--baseUrl",
        default=defaultBaseUrl,
        help="Base URL of the Cloud Run service.",
    )
    fetchParser.add_argument("--fetchToken", help="Fetch token provided by the web app.")
    fetchParser.add_argument(
        "--metadataFile",
        help="Path to JSON metadata when using offline mode.",
    )
    fetchParser.add_argument(
        "--dataFile",
        help="Path to the local file contents when using offline mode.",
    )
    fetchParser.add_argument(
        "--outputDir",
        default=str(defaultFilesDirectory),
        help=(
            "Directory path to save the downloaded file (default: ~/.printmaster/files)."
        ),
    )

    statusParser = subparsers.add_parser(
        "status",
        help="Send printer status updates to the Cloud Run service.",
    )
    statusParser.add_argument(
        "--baseUrl",
        default=defaultBaseUrl,
        help="Base URL of the Cloud Run service.",
    )
    statusParser.add_argument("--apiKey", required=True, help="API key for authenticating with the server.")
    statusParser.add_argument("--printerSerial", required=True, help="Unique printer serial number.")
    statusParser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Interval in seconds between status updates (default: 60).",
    )
    statusParser.add_argument(
        "--numUpdates",
        type=int,
        default=1,
        help="Number of updates to send (0 for indefinite).",
    )
    statusParser.add_argument(
        "--recipientId",
        default=None,
        help="Optional recipient identifier to associate with status updates.",
    )

    listenParser = subparsers.add_parser(
        "listen",
        help="Continuously poll for files assigned to a recipient and download them.",
    )
    listenParser.add_argument(
        "--mode",
        choices={"remote", "offline"},
        default="remote",
        help="Select remote HTTP backend or offline file-based backend.",
    )
    listenParser.add_argument(
        "--baseUrl",
        default=defaultBaseUrl,
        help="Base URL of the Cloud Run service.",
    )
    listenParser.add_argument(
        "--recipientId",
        help="Recipient identifier to filter pending files.",
    )
    listenParser.add_argument(
        "--offlineDataset",
        help="Path to JSON dataset describing files for offline mode.",
    )
    listenParser.add_argument(
        "--outputDir",
        default=str(defaultFilesDirectory),
        help=(
            "Directory path to save downloaded files (default: ~/.printmaster/files)."
        ),
    )
    listenParser.add_argument(
        "--logFile",
        default=str(Path.home() / ".printmaster" / "listener-log.json"),
        help="Path to a JSON log file for fetched files and product statuses.",
    )
    listenParser.add_argument(
        "--pollInterval",
        type=int,
        default=30,
        help="Seconds to wait between polling attempts (default: 30).",
    )
    listenParser.add_argument(
        "--maxIterations",
        type=int,
        default=0,
        help="Maximum number of polling iterations (0 for indefinite).",
    )

    return parser.parse_args()


def buildBaseUrl(baseUrl: str) -> str:
    sanitized = baseUrl.strip()
    if not sanitized:
        raise ValueError("baseUrl must not be empty")

    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        sanitized = f"https://{sanitized}"

    parsedUrl = urlparse(sanitized)
    if parsedUrl.scheme not in {"http", "https"} or not parsedUrl.netloc:
        raise ValueError("baseUrl must be a valid HTTP(S) URL")

    return sanitized.rstrip("/")


def buildFetchUrl(baseUrl: str, fetchToken: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    return f"{sanitizedBase}/fetch/{fetchToken}"


def buildPendingUrl(baseUrl: str, recipientId: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    sanitizedRecipient = recipientId.strip()
    if not sanitizedRecipient:
        raise ValueError("recipientId must not be empty")
    return f"{sanitizedBase}/recipients/{sanitizedRecipient}/pending"


def interpretBoolean(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in {"true", "1", "yes", "y", "on"}:
            return True
        if candidate in {"false", "0", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def interpretInteger(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return int(candidate)
        except ValueError:
            return None
    return None


def normalizeTextValue(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
    else:
        candidate = str(value).strip()
    return candidate or None


def normalizePrinterDetails(details: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for rawKey, rawValue in details.items():
        if rawValue is None:
            continue
        key = rawKey.replace("-", "").replace("_", "").lower()
        if key in {"brand", "printerbrand"}:
            brandValue = normalizeTextValue(rawValue)
            if brandValue is not None:
                normalized["brand"] = brandValue
        elif key in {"serial", "serialnumber", "printersn", "printerserial", "serialno"}:
            serialValue = normalizeTextValue(rawValue)
            if serialValue is not None:
                normalized["serialNumber"] = serialValue
        elif key in {"ip", "ipaddress", "printerip", "host", "hostname"}:
            ipValue = normalizeTextValue(rawValue)
            if ipValue is not None:
                normalized["ipAddress"] = ipValue
        elif key in {"accesscode", "lanaccesscode", "password"}:
            accessValue = normalizeTextValue(rawValue)
            if accessValue is not None:
                normalized["accessCode"] = accessValue
        elif key in {"nickname", "printername", "name", "label"}:
            nicknameValue = normalizeTextValue(rawValue)
            if nicknameValue is not None:
                normalized["nickname"] = nicknameValue
        elif key in {"printerid", "printeridentifier"}:
            nicknameValue = normalizeTextValue(rawValue)
            if nicknameValue is not None:
                normalized.setdefault("nickname", nicknameValue)
        elif key == "useams":
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["useAms"] = interpreted
        elif key == "bedleveling":
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["bedLeveling"] = interpreted
        elif key == "layerinspect":
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["layerInspect"] = interpreted
        elif key in {"flowcalibration", "flowcali"}:
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["flowCalibration"] = interpreted
        elif key in {"vibrationcalibration", "vibrationcali"}:
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["vibrationCalibration"] = interpreted
        elif key in {"usecloud", "cloudprint"}:
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["useCloud"] = interpreted
        elif key in {"secureconnection", "secure"}:
            interpreted = interpretBoolean(rawValue)
            if interpreted is not None:
                normalized["secureConnection"] = interpreted
        elif key == "cloudurl":
            normalized["cloudUrl"] = str(rawValue)
        elif key == "cloudtimeout":
            interpretedInt = interpretInteger(rawValue)
            if interpretedInt is not None:
                normalized["cloudTimeout"] = interpretedInt
        elif key in {"plateindex", "plate"}:
            interpretedInt = interpretInteger(rawValue)
            if interpretedInt is not None:
                normalized["plateIndex"] = interpretedInt
        elif key in {"waitseconds", "waittime", "mqttwait"}:
            interpretedInt = interpretInteger(rawValue)
            if interpretedInt is not None:
                normalized["waitSeconds"] = interpretedInt
    return normalized


def extractPrinterAssignment(*candidates: Any) -> Optional[Dict[str, Any]]:
    def search(value: Any) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        if isinstance(value, dict):
            normalized = normalizePrinterDetails(value)
            if normalized:
                collected.append(dict(normalized))
            for nestedValue in value.values():
                if isinstance(nestedValue, (dict, list)):
                    collected.extend(search(nestedValue))
        elif isinstance(value, list):
            for item in value:
                collected.extend(search(item))
        return collected

    merged: Dict[str, Any] = {}
    for candidate in candidates:
        if candidate is None:
            continue
        for details in search(candidate):
            if not details:
                continue
            if not merged:
                merged.update(details)
                continue

            existingSerial = merged.get("serialNumber")
            existingSerialNormalized = (
                str(existingSerial).lower() if isinstance(existingSerial, str) else None
            )
            newSerial = details.get("serialNumber")
            newSerialNormalized = (
                str(newSerial).lower() if isinstance(newSerial, str) else None
            )

            if (
                existingSerialNormalized
                and newSerialNormalized
                and existingSerialNormalized != newSerialNormalized
            ):
                continue

            if newSerial is not None and existingSerialNormalized != newSerialNormalized:
                merged["serialNumber"] = newSerial
                existingSerial = newSerial
                existingSerialNormalized = newSerialNormalized

            canOverride = existingSerial is None or newSerialNormalized is not None

            for key, value in details.items():
                if value is None:
                    continue
                if key not in merged:
                    merged[key] = value
                elif canOverride:
                    merged[key] = value

    return merged or None


def loadConfiguredPrinters(configPath: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    path = Path(configPath).expanduser() if configPath else Path.home() / ".printmaster" / "printers.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Unable to load printers from %s: %s", path, error)
        return []
    if not isinstance(payload, list):
        logging.warning("Printer configuration in %s is not a list", path)
        return []
    printers: List[Dict[str, Any]] = []
    for entry in payload:
        if isinstance(entry, dict):
            printers.append(entry)
    return printers


def resolvePrinterDetails(
    metadata: Dict[str, Any],
    configuredPrinters: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    normalizedMetadata = normalizePrinterDetails(metadata)
    candidates = [normalizePrinterDetails(printer) for printer in configuredPrinters if isinstance(printer, dict)]

    def matchBy(predicate: Callable[[Dict[str, Any]], bool]) -> Optional[Dict[str, Any]]:
        for candidate in candidates:
            if predicate(candidate):
                return candidate
        return None

    match: Optional[Dict[str, Any]] = None
    serial = normalizedMetadata.get("serialNumber")
    nickname = normalizedMetadata.get("nickname")
    ipAddress = normalizedMetadata.get("ipAddress")

    if serial:
        loweredSerial = serial.lower()
        match = matchBy(lambda item: str(item.get("serialNumber", "")).lower() == loweredSerial)
    if match is None and nickname:
        loweredNickname = nickname.lower()
        match = matchBy(lambda item: str(item.get("nickname", "")).lower() == loweredNickname)
    if match is None and ipAddress:
        loweredIp = ipAddress.lower()
        match = matchBy(lambda item: str(item.get("ipAddress", "")).lower() == loweredIp)

    resolved: Dict[str, Any] = {}
    if match:
        for key, value in match.items():
            if value is not None:
                resolved[key] = value

    for key, value in normalizedMetadata.items():
        if value is not None:
            resolved[key] = value

    return resolved or None


def createPrinterStatusReporter(
    baseUrl: str,
    productId: str,
    recipientId: str,
    statusTemplate: Dict[str, Any],
    printerDetails: Dict[str, Any],
) -> Callable[[Dict[str, Any]], None]:
    def reporter(event: Dict[str, Any]) -> None:
        message = event.get("message")
        eventType = event.get("event")
        if not message:
            if eventType == "uploadComplete":
                remoteFile = event.get("remoteFile")
                message = f"Uploaded to printer storage as {remoteFile}" if remoteFile else "Uploaded to printer"
            elif eventType == "cloudAccepted":
                message = "Print job accepted by Bambu Cloud"
            elif eventType == "starting":
                message = "Starting print on printer"
            elif eventType == "progress":
                status = event.get("status") or {}
                segments: List[str] = []
                percent = status.get("mc_percent")
                if percent is not None:
                    segments.append(f"{percent}%")
                state = status.get("gcode_state")
                if state:
                    segments.append(str(state))
                remaining = status.get("mc_remaining_time")
                if remaining is not None:
                    segments.append(f"ETA {remaining}s")
                message = " | ".join(segments) if segments else "Printer status update"
            elif eventType == "error":
                message = f"Printer error: {event.get('error')}"
            else:
                message = f"Printer event: {eventType}" if eventType else "Printer update"

        payload = dict(statusTemplate)
        payload["message"] = message
        payload["printerDetails"] = printerDetails
        payload["printerEvent"] = event
        payload.setdefault("success", statusTemplate.get("success", True))
        payload.setdefault("recipientId", recipientId)
        sendProductStatusUpdate(baseUrl, productId, recipientId, payload)

    return reporter


def _normalizeMetadataKey(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def _extractListFromMetadata(
    sources: List[Dict[str, Any]],
    *,
    keyAliases: List[str],
) -> Optional[List[Any]]:
    normalizedAliases = {alias.lower() for alias in keyAliases}
    queue: List[Any] = [source for source in sources if isinstance(source, dict)]
    seen: set[int] = set()

    while queue:
        current = queue.pop()
        identifier = id(current)
        if identifier in seen:
            continue
        seen.add(identifier)
        if not isinstance(current, dict):
            continue
        for key, value in current.items():
            normalizedKey = _normalizeMetadataKey(str(key))
            if normalizedKey in normalizedAliases and isinstance(value, list):
                return value
            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        queue.append(item)
    return None


def _parseOrderValue(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
        if stripped.startswith("-") and stripped[1:].isdigit():
            return int(stripped)
    return None


def _extractMetadataValue(entry: Dict[str, Any], aliases: List[str]) -> Optional[Any]:
    normalizedAliases = {alias.lower() for alias in aliases}
    for key, value in entry.items():
        normalizedKey = _normalizeMetadataKey(str(key))
        if normalizedKey in normalizedAliases:
            return value
    return None


def _parseOrderedObject(entry: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict):
        return None

    orderValue = _extractMetadataValue(entry, ["order", "objectorder", "sequence", "index"])
    parsedOrder = _parseOrderValue(orderValue)
    if parsedOrder is None:
        return None

    identifyValue = _extractMetadataValue(entry, ["identifyid", "objectid", "id", "modelid"])
    plateValue = _extractMetadataValue(entry, ["plate", "plateid", "plateindex", "plateorder"])
    nameValue = _extractMetadataValue(entry, ["name", "objectname", "displayname"])

    parsedEntry: Dict[str, Any] = {"order": parsedOrder}
    if identifyValue is not None:
        parsedEntry["identifyId"] = str(identifyValue)
    if plateValue is not None:
        parsedEntry["plateId"] = str(plateValue)
    if nameValue is not None:
        parsedEntry["objectName"] = str(nameValue)
    return parsedEntry


def _parseSkippedObject(entry: Any) -> Optional[Dict[str, Any]]:
    if isinstance(entry, dict):
        orderValue = _extractMetadataValue(entry, ["order", "objectorder", "sequence", "index"])
        parsedOrder = _parseOrderValue(orderValue)
        if parsedOrder is None:
            return None
        parsedEntry: Dict[str, Any] = {"order": parsedOrder}
        identifyValue = _extractMetadataValue(entry, ["identifyid", "objectid", "id", "modelid"])
        plateValue = _extractMetadataValue(entry, ["plate", "plateid", "plateindex", "plateorder"])
        nameValue = _extractMetadataValue(entry, ["name", "objectname", "displayname"])
        if identifyValue is not None:
            parsedEntry["identifyId"] = str(identifyValue)
        if plateValue is not None:
            parsedEntry["plateId"] = str(plateValue)
        if nameValue is not None:
            parsedEntry["objectName"] = str(nameValue)
        return parsedEntry

    parsedOrder = _parseOrderValue(entry)
    if parsedOrder is None:
        return None
    return {"order": parsedOrder}


def extractSkippedObjectTargets(
    entryData: Dict[str, Any],
    statusPayload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    metadataSources: List[Dict[str, Any]] = []
    for source in (
        statusPayload,
        entryData.get("unencryptedData"),
        entryData.get("decryptedData"),
    ):
        if isinstance(source, dict):
            metadataSources.append(source)

    orderedObjects = _extractListFromMetadata(metadataSources, keyAliases=["ordered_objects", "orderedObjects"])
    skippedObjects = _extractListFromMetadata(metadataSources, keyAliases=["skipped_objects", "skippedObjects"])

    if not orderedObjects or not skippedObjects:
        return []

    orderMapping: Dict[int, Dict[str, Any]] = {}
    for item in orderedObjects:
        parsed = _parseOrderedObject(item)
        if not parsed:
            continue
        orderMapping.setdefault(parsed["order"], parsed)

    skipTargets: List[Dict[str, Any]] = []
    invalidOrders: List[int] = []

    for item in skippedObjects:
        parsedSkip = _parseSkippedObject(item)
        if not parsedSkip:
            continue
        orderNumber = parsedSkip["order"]
        orderedDetails = orderMapping.get(orderNumber)
        if not orderedDetails:
            invalidOrders.append(orderNumber)
            continue
        merged = dict(orderedDetails)
        for key in ("identifyId", "plateId", "objectName"):
            value = parsedSkip.get(key)
            if value is not None:
                merged[key] = value
        skipTargets.append(merged)

    if invalidOrders:
        invalidSummary = ", ".join(str(order) for order in sorted(set(invalidOrders)))
        raise ValueError(f"Unknown slicer order number(s): {invalidSummary}")

    return skipTargets


def dispatchBambuPrintIfPossible(
    *,
    baseUrl: str,
    productId: str,
    recipientId: str,
    entryData: Dict[str, Any],
    statusPayload: Dict[str, Any],
    configuredPrinters: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    savedFile = entryData.get("savedFile")
    if not savedFile:
        return None

    printerAssignment = extractPrinterAssignment(entryData.get("unencryptedData"), entryData.get("decryptedData"))
    if not printerAssignment:
        return None

    printers = configuredPrinters if configuredPrinters is not None else loadConfiguredPrinters()
    resolvedDetails = resolvePrinterDetails(printerAssignment, printers)
    if not resolvedDetails:
        logging.info("No matching printer configuration for product %s", productId)
        return None

    brand = str(resolvedDetails.get("brand", "")).strip()
    if brand and "bambu" not in brand.lower():
        logging.info("Skipping printer dispatch for non-Bambu brand %s", brand)
        return None

    ipAddress = resolvedDetails.get("ipAddress")
    accessCode = resolvedDetails.get("accessCode")
    serialNumber = resolvedDetails.get("serialNumber")

    if not ipAddress or not accessCode or not serialNumber:
        logging.warning(
            "Incomplete printer credentials for product %s (ip=%s, accessCode=%s, serial=%s)",
            productId,
            ipAddress,
            bool(accessCode),
            serialNumber,
        )
        return None

    try:
        filePath = Path(savedFile)
    except Exception:
        logging.warning("Invalid file path for printer dispatch: %s", savedFile)
        return None
    if not filePath.exists():
        logging.warning("Downloaded file missing for printer dispatch: %s", filePath)
        return None

    options = BambuPrintOptions(
        ipAddress=str(ipAddress),
        serialNumber=str(serialNumber),
        accessCode=str(accessCode),
        brand=brand or "Bambu Lab",
        nickname=resolvedDetails.get("nickname") or resolvedDetails.get("printerName"),
        useCloud=bool(resolvedDetails.get("useCloud", False)),
        cloudUrl=resolvedDetails.get("cloudUrl"),
        cloudTimeout=resolvedDetails.get("cloudTimeout", 180),
        useAms=bool(resolvedDetails.get("useAms", False)),
        bedLeveling=resolvedDetails.get("bedLeveling", True),
        layerInspect=resolvedDetails.get("layerInspect", True),
        flowCalibration=resolvedDetails.get("flowCalibration", False),
        vibrationCalibration=resolvedDetails.get("vibrationCalibration", False),
        secureConnection=bool(resolvedDetails.get("secureConnection", False)),
        plateIndex=resolvedDetails.get("plateIndex"),
        waitSeconds=resolvedDetails.get("waitSeconds", 12),
    )

    printerDetails = {
        "serialNumber": options.serialNumber,
        "ipAddress": options.ipAddress,
        "brand": options.brand,
        "nickname": options.nickname,
        "useCloud": options.useCloud,
    }

    statusTemplate = dict(statusPayload)
    statusTemplate.pop("sent", None)
    statusTemplate.setdefault("recipientId", recipientId)

    requiredStatusKeys = ("fileName", "lastRequestedAt", "requestedMode", "success")
    for requiredKey in requiredStatusKeys:
        if statusTemplate.get(requiredKey) in {None, ""}:
            logging.info(
                "Skipping printer dispatch for product %s due to missing %s in status payload",
                productId,
                requiredKey,
            )
            return None

    reporter = createPrinterStatusReporter(baseUrl, productId, recipientId, statusTemplate, printerDetails)

    statusEvents: List[Dict[str, Any]] = []

    def capture(event: Dict[str, Any]) -> None:
        statusEvents.append(dict(event))
        reporter(event)

    capture({"event": "dispatching", "message": f"Dispatching print job to {options.nickname or options.serialNumber}"})

    try:
        skippedTargets = extractSkippedObjectTargets(entryData, statusPayload)
    except ValueError as error:
        logging.error("Invalid skipped object metadata for product %s: %s", productId, error)
        capture({"event": "error", "error": str(error)})
        return {"success": False, "details": printerDetails, "error": str(error), "events": statusEvents}

    try:
        result = sendBambuPrintJob(
            filePath=filePath,
            options=options,
            statusCallback=capture,
            skippedObjects=skippedTargets,
        )
        capture(
            {
                "event": "dispatched",
                "message": "Print job sent to printer",
                "result": result,
            }
        )
        return {"success": True, "details": printerDetails, "result": result, "events": statusEvents}
    except Exception as error:  # noqa: BLE001 - propagate via status
        printerIdentity = options.nickname or options.serialNumber or "printer"
        if options.ipAddress and options.ipAddress not in printerIdentity:
            printerIdentity = f"{printerIdentity} ({options.ipAddress})"
        elif options.ipAddress and not options.nickname and not options.serialNumber:
            printerIdentity = options.ipAddress
        enrichedMessage = f"{printerIdentity}: {error}"
        logging.exception(
            "Failed to dispatch print job for product %s to %s: %s",
            productId,
            printerIdentity,
            error,
        )
        capture({"event": "error", "error": enrichedMessage})
        return {"success": False, "details": printerDetails, "error": enrichedMessage, "events": statusEvents}

def validateBaseUrlArgument(baseUrl: Optional[str], commandName: str) -> bool:
    if not isinstance(baseUrl, str) or not baseUrl.strip():
        logging.error("Missing required options for remote %s: --baseUrl", commandName)
        return False

    try:
        buildBaseUrl(baseUrl)
    except ValueError as error:
        logging.error("Invalid --baseUrl for remote %s: %s", commandName, error)
        return False

    return True


def checkProductAvailability(
    database: LocalDatabase,
    productId: str,
    fileName: Optional[str] = None,
    *,
    requestTimestamp: Optional[str] = None,
) -> Dict[str, Any]:
    existingRecord = database.findProductById(productId)
    resolvedTimestamp = requestTimestamp or datetime.utcnow().isoformat()

    if existingRecord is None:
        status = "notFound"
        shouldRequestFile = True
        updatedRecord = database.upsertProductRecord(
            productId,
            None,
            downloaded=False,
            downloadedFilePath=None,
            requestTimestamp=resolvedTimestamp,
        )
    else:
        cachedPath = existingRecord.get("downloadedFilePath")
        cachedFileExists = bool(cachedPath) and Path(cachedPath).is_file()
        if existingRecord.get("downloaded") and cachedFileExists:
            status = "fileCached"
            shouldRequestFile = False
            updatedRecord = database.upsertProductRecord(
                productId,
                existingRecord.get("fileName"),
                downloaded=True,
                downloadedFilePath=cachedPath,
                requestTimestamp=resolvedTimestamp,
            )
        else:
            status = "metadataCached"
            shouldRequestFile = True
            updatedRecord = database.upsertProductRecord(
                productId,
                fileName or existingRecord.get("fileName"),
                downloaded=False,
                downloadedFilePath=None,
                requestTimestamp=resolvedTimestamp,
            )

    logging.info(
        "Product %s availability: %s (downloaded=%s)",
        productId,
        status,
        updatedRecord.get("downloaded"),
    )
    return {
        "productId": productId,
        "status": status,
        "shouldRequestFile": shouldRequestFile,
        "record": updatedRecord,
        "timestamp": resolvedTimestamp,
    }


def ensureOutputDirectory(outputDir: str) -> Path:
    outputPath = Path(outputDir).expanduser().resolve()
    outputPath.mkdir(parents=True, exist_ok=True)
    return outputPath


def extractPrintJobId(*sources: Optional[Dict[str, Any]]) -> Optional[str]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("printJobId", "printJob"):
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                candidate = value.strip()
                if candidate:
                    return candidate
            else:
                return str(value)
    return None


def loadOfflineMetadata(metadataSource: Union[str, Path, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(metadataSource, dict):
        metadata = metadataSource
    else:
        if not metadataSource:
            logging.error("Offline metadata source must be provided.")
            return None
        metadataPath = Path(metadataSource).expanduser().resolve()
        try:
            metadataText = metadataPath.read_text(encoding="utf-8")
        except OSError as error:
            logging.error("Unable to read offline metadata file %s: %s", metadataPath, error)
            return None
        try:
            metadata = json.loads(metadataText)
        except json.JSONDecodeError as error:
            logging.error("Offline metadata file %s is not valid JSON: %s", metadataPath, error)
            return None

    if not isinstance(metadata, dict):
        logging.error("Offline metadata must be a JSON object, received %s", type(metadata).__name__)
        return None

    if "unencryptedData" not in metadata:
        metadata["unencryptedData"] = {}
    if "decryptedData" not in metadata:
        metadata["decryptedData"] = {}

    return metadata


def copyOfflineFile(
    dataFile: Union[str, Path], outputPath: Path, preferredName: Optional[str]
) -> Optional[Path]:
    if not dataFile:
        logging.error("Offline file path must be provided when using offline mode.")
        return None

    sourcePath = Path(dataFile).expanduser()
    if not sourcePath.is_file():
        logging.error("Offline data file %s does not exist or is not a file.", sourcePath)
        return None

    filename = preferredName.strip() if isinstance(preferredName, str) else ""
    if not filename:
        filename = sourcePath.name

    destinationPath = outputPath / filename
    try:
        shutil.copy2(sourcePath, destinationPath)
    except OSError as error:
        logging.error("Failed to copy offline file from %s to %s: %s", sourcePath, destinationPath, error)
        return None

    logging.info("Saved offline file to %s", destinationPath)
    return destinationPath


def performOfflineFetch(
    metadataSource: Union[str, Path, Dict[str, Any]],
    dataFile: Optional[str],
    outputDir: str,
    onMetadata: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Path]:
    metadata = loadOfflineMetadata(metadataSource)
    if metadata is None:
        return None

    outputPath = ensureOutputDirectory(outputDir)
    preferredName = metadata.get("originalFilename") or metadata.get("fileName")
    savedFile = copyOfflineFile(dataFile, outputPath, preferredName)
    if savedFile is None:
        return None

    logging.info("Unencrypted data:\n%s", json.dumps(metadata.get("unencryptedData"), indent=2))
    logging.info("Decrypted data:\n%s", json.dumps(metadata.get("decryptedData"), indent=2))
    if onMetadata is not None:
        onMetadata(
            {
                "savedFile": str(savedFile),
                "unencryptedData": metadata.get("unencryptedData", {}),
                "decryptedData": metadata.get("decryptedData", {}),
                "timestamp": time.time(),
                "source": "offline",
            }
        )
    return savedFile


def loadOfflineDataset(datasetPath: str) -> Optional[List[Dict[str, Any]]]:
    if not datasetPath:
        logging.error("Offline dataset path must be provided when using offline listen mode.")
        return None

    resolvedPath = Path(datasetPath).expanduser().resolve()
    try:
        datasetText = resolvedPath.read_text(encoding="utf-8")
    except OSError as error:
        logging.error("Unable to read offline dataset file %s: %s", resolvedPath, error)
        return None

    try:
        loadedDataset = json.loads(datasetText)
    except json.JSONDecodeError as error:
        logging.error("Offline dataset file %s is not valid JSON: %s", resolvedPath, error)
        return None

    if isinstance(loadedDataset, dict):
        pendingEntries = loadedDataset.get("pendingFiles")
        if pendingEntries is None:
            pendingEntries = loadedDataset.get("files")
        if pendingEntries is None:
            pendingEntries = [loadedDataset]
    else:
        pendingEntries = loadedDataset

    if not isinstance(pendingEntries, list):
        logging.error(
            "Offline dataset must be a list or object containing a list of pending files. Received %s.",
            type(pendingEntries).__name__,
        )
        return None

    normalizedEntries: List[Dict[str, Any]] = []
    for entry in pendingEntries:
        if isinstance(entry, dict):
            normalizedEntries.append(entry)
        else:
            logging.warning(
                "Skipping offline dataset entry because it is not an object: %r", entry
            )

    return normalizedEntries


def listenOffline(datasetPath: str, outputDir: str) -> None:
    entries = loadOfflineDataset(datasetPath)
    if entries is None:
        return

    if not entries:
        logging.info("No offline files to process; dataset is empty.")
        return

    datasetDirectory = Path(datasetPath).expanduser().resolve().parent

    processedCount = 0
    for entry in entries:
        dataFile = entry.get("dataFile")
        if not dataFile:
            logging.warning("Skipping offline entry without dataFile: %s", entry)
            continue

        dataFilePath = Path(dataFile).expanduser()
        if not dataFilePath.is_absolute():
            dataFilePath = (datasetDirectory / dataFilePath).resolve()

        if entry.get("metadataFile"):
            metadataSource: Union[str, Path, Dict[str, Any]] = entry["metadataFile"]
        elif isinstance(entry.get("metadata"), dict):
            metadataSource = entry["metadata"]
        else:
            metadataSource = {
                key: value
                for key, value in entry.items()
                if key not in {"dataFile", "metadataFile"}
            }

        if isinstance(metadataSource, (str, Path)):
            metadataPath = Path(metadataSource).expanduser()
            if not metadataPath.is_absolute():
                metadataPath = (datasetDirectory / metadataPath).resolve()
            metadataSource = str(metadataPath)

        savedFile = performOfflineFetch(metadataSource, str(dataFilePath), outputDir)
        if savedFile is not None:
            processedCount += 1

    logging.info("Offline processing complete. Files saved: %d", processedCount)


def determineFilename(response: requests.Response, fallbackName: str = "downloaded_file.bin") -> str:
    contentDisposition = response.headers.get("Content-Disposition")
    if contentDisposition:
        parts = contentDisposition.split(";")
        for part in parts:
            part = part.strip()
            if part.lower().startswith("filename="):
                filename = part.split("=", 1)[1].strip('"')
                if filename:
                    return filename
    urlPath = response.url or ""
    parsedUrl = urlparse(urlPath)
    cleanPath = parsedUrl.path.rstrip("/")
    if cleanPath:
        candidate = Path(cleanPath).name
        if candidate:
            return candidate
    return fallbackName


def saveDownloadedFile(response: requests.Response, outputDir: Path) -> Path:
    filename = determineFilename(response)
    outputPath = outputDir / filename
    with open(outputPath, "wb") as fileHandle:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                fileHandle.write(chunk)
    logging.info("Saved file to %s", outputPath)
    return outputPath


def performFetch(
    baseUrl: str,
    fetchToken: str,
    outputDir: str,
    *,
    requestMode: str = "full",
    database: Optional[LocalDatabase] = None,
    productId: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalizedMode = requestMode if requestMode in {"full", "metadata"} else "full"
    fetchUrl = buildFetchUrl(baseUrl, fetchToken)
    logging.info(
        "Fetching metadata from %s (mode=%s) for product %s",
        fetchUrl,
        normalizedMode,
        productId or "unknown",
    )
    session = requests.Session()
    try:
        metadataResponse = session.get(
            fetchUrl,
            timeout=30,
            params={"mode": normalizedMode} if normalizedMode != "full" else None,
        )
        metadataResponse.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to fetch metadata: %s", error)
        return None

    try:
        payload = metadataResponse.json()
    except json.JSONDecodeError as error:
        logging.error("Invalid JSON response: %s", error)
        return None

    unencryptedData = payload.get("unencryptedData") or {}
    decryptedData = payload.get("decryptedData") or {}
    metadataFileName = payload.get("originalFilename") or payload.get("fileName")
    printJobId = extractPrintJobId(payload, unencryptedData, decryptedData)

    result: Dict[str, Any] = {
        "savedFile": None,
        "unencryptedData": unencryptedData,
        "decryptedData": decryptedData,
        "timestamp": time.time(),
        "fetchToken": fetchToken,
        "source": baseUrl,
        "requestMode": normalizedMode,
        "fileName": metadataFileName,
        "printJobId": printJobId,
    }

    if normalizedMode == "metadata":
        cachedSavedFile: Optional[Path] = None
        existingRecord: Optional[Dict[str, Any]] = None
        if database and productId:
            try:
                existingRecord = database.findProductById(productId)
            except Exception as error:
                logging.warning(
                    "Unable to load existing product record for %s: %s",
                    productId,
                    error,
                )
            if existingRecord:
                cachedPathValue = existingRecord.get("downloadedFilePath")
                if (
                    existingRecord.get("downloaded")
                    and isinstance(cachedPathValue, str)
                    and cachedPathValue
                ):
                    candidatePath = Path(cachedPathValue).expanduser()
                    if candidatePath.exists():
                        cachedSavedFile = candidatePath.resolve()
                        result["savedFile"] = str(cachedSavedFile)
                        logging.info(
                            "Reusing cached download for product %s at %s",
                            productId,
                            cachedSavedFile,
                        )
        if database and productId:
            updatedRecord = database.upsertProductRecord(
                productId,
                metadataFileName,
                downloaded=None,
                downloadedFilePath=None,
                printJobId=printJobId,
            )
            result["productRecord"] = updatedRecord
            if not result.get("savedFile"):
                cachedPathValue = updatedRecord.get("downloadedFilePath")
                if (
                    updatedRecord.get("downloaded")
                    and isinstance(cachedPathValue, str)
                    and cachedPathValue
                ):
                    candidatePath = Path(cachedPathValue).expanduser()
                    if candidatePath.exists():
                        cachedSavedFile = candidatePath.resolve()
                        result["savedFile"] = str(cachedSavedFile)
        if cachedSavedFile and not result.get("fileName"):
            result["fileName"] = cachedSavedFile.name
        logging.info("Metadata retrieved for product %s without downloading file.", productId)
        return result

    signedUrl = payload.get("signedUrl")
    if not signedUrl:
        logging.error("No signedUrl returned from server.")
        if database and productId:
            result["productRecord"] = database.upsertProductRecord(
                productId,
                metadataFileName,
                downloaded=None,
                downloadedFilePath=None,
                printJobId=printJobId,
            )
        return None

    outputPath = ensureOutputDirectory(outputDir)
    logging.info("Downloading file from signed URL for product %s.", productId)
    try:
        downloadResponse = session.get(signedUrl, stream=True, timeout=60)
        downloadResponse.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to download file: %s", error)
        if database and productId:
            result["productRecord"] = database.upsertProductRecord(
                productId,
                metadataFileName,
                downloaded=None,
                printJobId=printJobId,
            )
        return None

    savedFile = saveDownloadedFile(downloadResponse, outputPath)
    logging.info("Unencrypted data:\n%s", json.dumps(unencryptedData, indent=2))
    logging.info("Decrypted data:\n%s", json.dumps(decryptedData, indent=2))
    logging.info("Fetch completed successfully. File saved at %s", savedFile)

    result["savedFile"] = str(savedFile)
    result["fileName"] = savedFile.name

    if database and productId:
        result["productRecord"] = database.upsertProductRecord(
            productId,
            savedFile.name,
            downloaded=True,
            downloadedFilePath=str(savedFile),
            printJobId=printJobId,
        )

    return result


def appendJsonLogEntry(logFilePath: Union[str, Path], entry: Dict[str, Any]) -> Path:
    logPath = Path(logFilePath).expanduser().resolve()
    logPath.parent.mkdir(parents=True, exist_ok=True)
    serializedEntry = {
        "savedFile": entry.get("savedFile"),
        "unencryptedData": entry.get("unencryptedData", {}),
        "decryptedData": entry.get("decryptedData", {}),
        "timestamp": entry.get("timestamp", time.time()),
        "fetchToken": entry.get("fetchToken"),
        "source": entry.get("source"),
    }
    if entry.get("fileName") is not None:
        serializedEntry["fileName"] = entry.get("fileName")
    if entry.get("requestMode") is not None:
        serializedEntry["requestMode"] = entry.get("requestMode")
    if entry.get("productStatus") is not None:
        serializedEntry["productStatus"] = entry.get("productStatus")
    productRecord = entry.get("productRecord")
    if isinstance(productRecord, dict):
        downloadedFilePath = productRecord.get("downloadedFilePath")
        if downloadedFilePath is not None:
            serializedEntry["downloadedFilePath"] = downloadedFilePath

    existingEntries: List[Dict[str, Any]] = []
    if logPath.exists():
        try:
            with logPath.open("r", encoding="utf-8") as logFile:
                loaded = json.load(logFile)
            if isinstance(loaded, list):
                existingEntries = loaded
        except (OSError, json.JSONDecodeError) as error:
            logging.warning("Unable to load existing log file %s: %s", logPath, error)

    existingEntries.append(serializedEntry)
    with logPath.open("w", encoding="utf-8") as logFile:
        json.dump(existingEntries, logFile, indent=2, ensure_ascii=False)
    logging.info("Appended fetch metadata to %s", logPath)
    return logPath


def sendProductStatusUpdate(
    baseUrl: str,
    productId: str,
    recipientId: str,
    statusPayload: Dict[str, Any],
) -> bool:
    statusUrl = f"{buildBaseUrl(baseUrl)}/products/{productId}/status"
    payloadToSend = dict(statusPayload)
    if "recipientId" not in payloadToSend or not payloadToSend.get("recipientId"):
        payloadToSend["recipientId"] = recipientId
    try:
        response = requests.post(statusUrl, json=payloadToSend, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to send product status update for %s: %s", productId, error)
        return False

    logging.info(
        "Sent product status update for %s: %s",
        productId,
        response.text.strip() or response.status_code,
    )
    return True


def sendHandshakeResponse(
    baseUrl: str,
    productId: str,
    recipientId: str,
    printJobId: Optional[str],
    jobExists: bool,
) -> Optional[Dict[str, Any]]:
    handshakeUrl = f"{buildBaseUrl(baseUrl)}/products/{productId}/handshake"
    handshakeMessage = "printJobId found" if jobExists else "printJobId not found"
    payload = {
        "status": "hasFile" if jobExists else "needsFile",
        "recipientId": recipientId,
        "printJobId": printJobId,
        "handshakeMessage": handshakeMessage,
    }

    try:
        response = requests.post(handshakeUrl, json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        logging.error(
            "Failed to send handshake response for %s (job %s): %s",
            productId,
            printJobId,
            error,
        )
        return None

    try:
        return response.json()
    except json.JSONDecodeError as error:
        logging.error(
            "Invalid JSON handshake response for %s (job %s): %s",
            productId,
            printJobId,
            error,
        )
        return None


def fetchPendingFiles(baseUrl: str, recipientId: str) -> Optional[List[Dict[str, Any]]]:
    pendingUrl = buildPendingUrl(baseUrl, recipientId)
    logging.info("Checking for pending files for recipient %s", recipientId)
    try:
        response = requests.get(pendingUrl, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to fetch pending files: %s", error)
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        logging.error("Invalid JSON response when listing pending files: %s", error)
        return None

    pendingFiles = payload.get("pendingFiles")
    if not isinstance(pendingFiles, list):
        logging.error("Unexpected response format when listing pending files: %s", payload)
        return None

    return pendingFiles


def generateStatusPayload(
    printerSerial: str,
    iteration: int,
    currentJobId: Optional[str],
    recipientId: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    statuses = ["idle", "printing", "pausing", "error", "finished"]
    status = statuses[iteration % len(statuses)]

    if status == "printing":
        jobId = currentJobId or str(uuid.uuid4())
        jobProgress = min(100, (iteration * 20) % 110)
    elif status == "finished":
        jobId = currentJobId or str(uuid.uuid4())
        jobProgress = 100
    else:
        jobId = currentJobId
        jobProgress = 0

    materialLevel = {
        "filamentA": max(0, 80 - iteration * 5),
        "filamentB": max(0, 65 - iteration * 3),
    }

    accessCodeValue = "PCODE6789"
    credentialMetadata = {
        "printerSerial": printerSerial,
        "accessCode": accessCodeValue,
    }

    payload: Dict[str, Any] = {
        "printerIp": f"192.168.1.{10 + (iteration % 10)}",
        "publicKey": "ABCDEFG12345",
        "objectName": f"dummy_print_object_v{1 + (iteration % 3)}",
        "useAms": iteration % 2 == 0,
        "printJobId": jobId or str(uuid.uuid4()),
        "productName": f"widget_v{1 + (iteration % 4)}",
        "platesRequested": 1 + (iteration % 2),
        "status": status,
        "jobProgress": jobProgress,
        "materialLevel": materialLevel,
    }

    if recipientId:
        payload["recipientId"] = recipientId

    if status == "error":
        payload["errorCode"] = "E123"
        payload["errorMessage"] = "Simulated error condition"
    if status in {"finished", "idle"}:
        payload["lastFilePrinted"] = f"file_{iteration:03d}.gcode"

    nextJobId = jobId
    if status in {"finished", "error", "idle"}:
        nextJobId = None

    _ = credentialMetadata

    return payload, nextJobId


def listenForFiles(
    baseUrl: str,
    recipientId: str,
    outputDir: str,
    pollInterval: int,
    maxIterations: int,
    onFileFetched: Optional[Callable[[Dict[str, Any]], None]] = None,
    stopEvent: Optional[Event] = None,
    *,
    logFilePath: Optional[Union[str, Path]] = None,
    database: Optional[LocalDatabase] = None,
) -> None:
    iteration = 0
    ownDatabase = False
    resolvedLogPath: Optional[Path] = None
    if logFilePath:
        resolvedLogPath = Path(logFilePath).expanduser().resolve()
    if database is None:
        database = LocalDatabase()
        ownDatabase = True

    try:
        while True:
            if stopEvent and stopEvent.is_set():
                break
            pendingFiles = fetchPendingFiles(baseUrl, recipientId)
            if pendingFiles is None:
                logging.warning("Unable to retrieve pending files; will retry after delay.")
            elif not pendingFiles:
                logging.info("No pending files for recipient %s.", recipientId)
            else:
                logging.info(
                    "Found %d pending file(s) for recipient %s.", len(pendingFiles), recipientId
                )
                for pendingFile in pendingFiles:
                    fetchToken = pendingFile.get("fetchToken")
                    if not fetchToken:
                        logging.warning("Skipping pending entry without fetchToken: %s", pendingFile)
                        continue

                    productId = str(
                        pendingFile.get("productId")
                        or pendingFile.get("fileId")
                        or fetchToken
                    )
                    availability = checkProductAvailability(
                        database,
                        productId,
                        pendingFile.get("originalFilename"),
                    )
                    requestMode = "full" if availability["shouldRequestFile"] else "metadata"
                    handshakeInfo = pendingFile.get("handshake")
                    handshakeRecipientId = recipientId
                    handshakePrintJobId: Optional[str] = None
                    if isinstance(handshakeInfo, dict):
                        handshakeRecipient = handshakeInfo.get("recipientId")
                        if isinstance(handshakeRecipient, str) and handshakeRecipient.strip():
                            handshakeRecipientId = handshakeRecipient
                        rawHandshakeJobId = handshakeInfo.get("printJobId")
                        if isinstance(rawHandshakeJobId, str) and rawHandshakeJobId.strip():
                            handshakePrintJobId = rawHandshakeJobId

                    if handshakePrintJobId is None:
                        rawPendingPrintJobId = pendingFile.get("printJobId")
                        if isinstance(rawPendingPrintJobId, str) and rawPendingPrintJobId.strip():
                            handshakePrintJobId = rawPendingPrintJobId

                    handshakeResponse: Optional[Dict[str, Any]] = None
                    jobExists = False
                    handshakeMessage: Optional[str] = None
                    if handshakePrintJobId and database is not None:
                        existingJob = database.findPrintJobInProductLog(handshakePrintJobId)
                        jobExists = existingJob is not None
                        handshakeMessage = "printJobId found" if jobExists else "printJobId not found"
                        handshakeResponse = sendHandshakeResponse(
                            baseUrl,
                            productId,
                            handshakeRecipientId,
                            handshakePrintJobId,
                            jobExists,
                        )
                        if handshakeResponse:
                            fetchTokenOverride = handshakeResponse.get("fetchToken")
                            if isinstance(fetchTokenOverride, str) and fetchTokenOverride.strip():
                                fetchToken = fetchTokenOverride

                    shouldFetch = True
                    if handshakeResponse:
                        handshakeDecision = handshakeResponse.get("fetchMode") or handshakeResponse.get(
                            "decision"
                        )
                        if isinstance(handshakeDecision, str) and handshakeDecision in {"full", "metadata"}:
                            requestMode = handshakeDecision
                        downloadRequired = handshakeResponse.get("downloadRequired")
                        if downloadRequired is False or handshakeDecision == "metadata":
                            shouldFetch = False

                    if not shouldFetch:
                        logging.info(
                            "Skipping fetch for product %s after handshake decision.",
                            productId,
                        )
                        if database is not None and handshakePrintJobId:
                            availabilityRecord = (
                                availability.get("record") if isinstance(availability.get("record"), dict) else {}
                            )
                            resolvedFileName = (
                                handshakeResponse.get("originalFilename")
                                if handshakeResponse and isinstance(handshakeResponse.get("originalFilename"), str)
                                else pendingFile.get("originalFilename")
                            )
                            database.upsertProductRecord(
                                productId,
                                resolvedFileName,
                                downloaded=jobExists if handshakeResponse else bool(availabilityRecord.get("downloaded")),
                                printJobId=handshakePrintJobId,
                                requestTimestamp=(
                                    handshakeResponse.get("lastRequestTimestamp")
                                    if handshakeResponse
                                    and isinstance(handshakeResponse.get("lastRequestTimestamp"), str)
                                    else None
                                ),
                            )

                        if resolvedLogPath is not None:
                            handshakeEntry: Dict[str, Any] = {
                                "savedFile": None,
                                "unencryptedData": {},
                                "decryptedData": {},
                                "timestamp": time.time(),
                                "fetchToken": fetchToken,
                                "source": baseUrl,
                                "fileName": (
                                    (handshakeResponse or {}).get("originalFilename")
                                    or pendingFile.get("originalFilename")
                                ),
                                "requestMode": requestMode,
                                "printJobId": handshakePrintJobId,
                                "handshakeResponse": handshakeResponse,
                                "handshakeMessage": handshakeMessage,
                                "handshakeSkippedFetch": True,
                            }
                            loggedPath = appendJsonLogEntry(resolvedLogPath, handshakeEntry)
                            handshakeEntry["logFilePath"] = str(loggedPath)
                        continue
                    logging.info(
                        "Processing pending product %s with fetch token %s (mode=%s)",
                        productId,
                        fetchToken,
                        requestMode,
                    )
                    fetchErrorMessage: Optional[str] = None
                    try:
                        fetchResult = performFetch(
                            baseUrl,
                            fetchToken,
                            outputDir,
                            requestMode=requestMode,
                            database=database,
                            productId=productId,
                        )
                    except Exception as error:  # noqa: BLE001
                        logging.exception(
                            "Unexpected error while fetching product %s with token %s",
                            productId,
                            fetchToken,
                        )
                        fetchResult = None
                        fetchErrorMessage = str(error)

                    updatedRecord = database.findProductById(productId)
                    resolvedPrintJobId = (
                        (fetchResult or {}).get("printJobId")
                        or handshakePrintJobId
                        or ((updatedRecord or {}).get("printJobId") if updatedRecord else None)
                    )
                    statusMessage = (
                        "success"
                        if fetchResult is not None
                        else fetchErrorMessage
                        or handshakeMessage
                        or "File transfer failed"
                    )
                    statusPayload = {
                        "productId": productId,
                        "requestedMode": requestMode,
                        "availabilityStatus": availability["status"],
                        "downloaded": bool((updatedRecord or {}).get("downloaded", False)),
                        "fileName": (updatedRecord or {}).get("fileName"),
                        "lastRequestedAt": (updatedRecord or {}).get("lastRequestedAt"),
                        "timestamp": time.time(),
                        "fetchToken": fetchToken,
                        "success": fetchResult is not None,
                        "recipientId": handshakeRecipientId,
                        "printJobId": resolvedPrintJobId,
                        "message": statusMessage,
                    }
                    statusSent = sendProductStatusUpdate(
                        baseUrl,
                        productId,
                        handshakeRecipientId,
                        statusPayload,
                    )
                    statusPayload["sent"] = statusSent

                    if fetchResult is not None:
                        entryData: Dict[str, Any] = dict(fetchResult)
                    else:
                        entryData = {
                            "savedFile": None,
                            "unencryptedData": {},
                            "decryptedData": {},
                            "timestamp": time.time(),
                            "fetchToken": fetchToken,
                            "source": baseUrl,
                            "fileName": (updatedRecord or {}).get("fileName"),
                            "requestMode": requestMode,
                        }
                    entryData["productId"] = productId
                    entryData["productStatus"] = statusPayload
                    entryData["productRecord"] = updatedRecord

                    if fetchResult is not None:
                        dispatchResult = dispatchBambuPrintIfPossible(
                            baseUrl=baseUrl,
                            productId=productId,
                            recipientId=handshakeRecipientId,
                            entryData=entryData,
                            statusPayload=statusPayload,
                        )
                        if dispatchResult:
                            entryData["printerDispatch"] = dispatchResult

                    summaryDirectory: Optional[Path] = None
                    if database is not None:
                        summaryDirectory = database.productRecordsPath.parent

                    if (
                        entryData.get("requestMode") == "metadata"
                        and not entryData.get("savedFile")
                    ):
                        summaryPath = storePrintSummary(entryData, summaryDirectory)
                        entryData["logFilePath"] = str(summaryPath)
                    elif resolvedLogPath is not None:
                        loggedPath = appendJsonLogEntry(resolvedLogPath, entryData)
                        entryData["logFilePath"] = str(loggedPath)

                    if fetchResult is not None and onFileFetched:
                        onFileFetched(entryData)

            iteration += 1
            if maxIterations and iteration >= maxIterations:
                break

            if stopEvent:
                if stopEvent.wait(timeout=pollInterval):
                    break
            else:
                time.sleep(pollInterval)
    finally:
        if ownDatabase and database is not None:
            database.close()


def performStatusUpdates(
    baseUrl: str,
    apiKey: str,
    printerSerial: str,
    intervalSeconds: int,
    numUpdates: int,
    recipientId: Optional[str] = None,
) -> None:
    statusUrl = f"{buildBaseUrl(baseUrl)}/printer-status"
    session = requests.Session()
    headers = {"X-API-Key": apiKey, "Content-Type": "application/json"}

    iteration = 0
    currentJobId: Optional[str] = None
    while True:
        payload, currentJobId = generateStatusPayload(
            printerSerial,
            iteration,
            currentJobId,
            recipientId=recipientId,
        )
        logging.info(
            "Status payload %d: %s",
            iteration + 1,
            json.dumps(payload, ensure_ascii=False),
        )
        try:
            response = session.post(statusUrl, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logging.info(
                "Sent status update %d: %s", iteration + 1, response.text.strip() or response.status_code
            )
        except requests.RequestException as error:
            logging.error("Failed to send status update %d: %s", iteration + 1, error)

        iteration += 1
        if numUpdates and iteration >= numUpdates:
            break
        time.sleep(intervalSeconds)


def validateRemoteFetchArguments(arguments: argparse.Namespace) -> bool:
    if not validateBaseUrlArgument(arguments.baseUrl, "fetch"):
        return False

    if not arguments.fetchToken:
        logging.error("Missing required options for remote fetch: --fetchToken")
        return False

    return True


def validateRemoteListenArguments(arguments: argparse.Namespace) -> bool:
    if not validateBaseUrlArgument(arguments.baseUrl, "listen"):
        return False

    if not arguments.recipientId:
        logging.error("Missing required options for remote listen: --recipientId")
        return False

    return True


def main() -> None:
    configureLogging()
    arguments = parseArguments()

    if arguments.command == "fetch":
        if arguments.mode == "offline":
            if not arguments.dataFile:
                logging.error("Offline fetch requires --dataFile to specify the local file to copy.")
                return
            if not arguments.metadataFile:
                logging.info(
                    "Offline fetch without metadata file will proceed with default metadata."
                )
            metadataSource: Union[str, Path, Dict[str, Any]]
            if arguments.metadataFile:
                metadataSource = arguments.metadataFile
            else:
                metadataSource = {
                    "fetchToken": arguments.fetchToken,
                    "unencryptedData": {},
                    "decryptedData": {},
                }

            savedFile = performOfflineFetch(metadataSource, arguments.dataFile, arguments.outputDir)
            if savedFile is None:
                logging.error("Offline fetch failed.")
        else:
            if not validateRemoteFetchArguments(arguments):
                return
            performFetch(arguments.baseUrl, arguments.fetchToken, arguments.outputDir)
    elif arguments.command == "status":
        performStatusUpdates(
            arguments.baseUrl,
            arguments.apiKey,
            arguments.printerSerial,
            arguments.interval,
            arguments.numUpdates,
            arguments.recipientId,
        )
    elif arguments.command == "listen":
        if arguments.mode == "offline":
            if not arguments.offlineDataset:
                logging.error(
                    "Offline listen requires --offlineDataset to reference a JSON description of files."
                )
                return
            listenOffline(arguments.offlineDataset, arguments.outputDir)
        else:
            if not validateRemoteListenArguments(arguments):
                return
            listenForFiles(
                arguments.baseUrl,
                arguments.recipientId,
                arguments.outputDir,
                arguments.pollInterval,
                arguments.maxIterations,
                logFilePath=arguments.logFile,
            )
    else:
        logging.error("Unknown command: %s", arguments.command)


if __name__ == "__main__":
    main()
