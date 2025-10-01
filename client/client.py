"""Command-line client for interacting with the Cloud Run printer backend."""

import argparse
import json
import logging
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests


defaultBaseUrl = "https://printer-backend-934564650450.europe-west1.run.app"


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
        required=True,
        help="Directory path to save the downloaded file.",
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
        required=True,
        help="Directory path to save downloaded files.",
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
    listenParser.add_argument(
        "--channel",
        help="Optional channel name used to scope pending file lookups.",
    )
    listenParser.add_argument(
        "--jobLogFile",
        default="pendingJobs.log",
        help="Filename (or path) used to record received job metadata.",
    )

    return parser.parse_args()


def buildBaseUrl(baseUrl: str) -> str:
    sanitized = baseUrl.strip().rstrip("/")
    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        raise ValueError("baseUrl must include the protocol, e.g., https://")
    return sanitized


def buildFetchUrl(baseUrl: str, fetchToken: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    return f"{sanitizedBase}/fetch/{fetchToken}"


def buildPendingUrl(baseUrl: str, recipientId: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    sanitizedRecipient = recipientId.strip()
    if not sanitizedRecipient:
        raise ValueError("recipientId must not be empty")
    return f"{sanitizedBase}/recipients/{sanitizedRecipient}/pending"


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


def ensureOutputDirectory(outputDir: str) -> Path:
    outputPath = Path(outputDir).expanduser().resolve()
    outputPath.mkdir(parents=True, exist_ok=True)
    return outputPath


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


def listenOffline(
    datasetPath: str,
    outputDir: str,
    channel: Optional[str] = None,
    jobLogFile: Optional[str] = None,
) -> None:
    entries = loadOfflineDataset(datasetPath)
    if entries is None:
        return

    if not entries:
        logging.info("No offline files to process; dataset is empty.")
        return

    datasetDirectory = Path(datasetPath).expanduser().resolve().parent
    outputPath = ensureOutputDirectory(outputDir)
    jobLogPath = resolveJobLogPath(jobLogFile, outputPath)

    processedCount = 0
    for entry in entries:
        dataFile = entry.get("dataFile")
        if not dataFile:
            logging.warning("Skipping offline entry without dataFile: %s", entry)
            continue

        entryChannel = entry.get("channel")
        if channel and entryChannel != channel:
            logging.info(
                "Skipping offline entry for channel %s (active channel %s).",
                entryChannel,
                channel,
            )
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
            jobMetadata = {
                "filePath": str(savedFile),
                "channel": entryChannel,
                "metadataSource": metadataSource,
            }
            recordJobLogEntry(jobLogPath, jobMetadata, "offline")

    logging.info("Offline processing complete. Files saved: %d", processedCount)
    logging.info("Job metadata recorded in %s", jobLogPath)


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
    urlPath = response.url
    if urlPath:
        candidate = urlPath.rstrip("/").split("/")[-1]
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


def performFetch(baseUrl: str, fetchToken: str, outputDir: str) -> None:
    fetchUrl = buildFetchUrl(baseUrl, fetchToken)
    logging.info("Fetching metadata from %s", fetchUrl)
    session = requests.Session()
    try:
        metadataResponse = session.get(fetchUrl, timeout=30)
        metadataResponse.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to fetch metadata: %s", error)
        return

    try:
        payload = metadataResponse.json()
    except json.JSONDecodeError as error:
        logging.error("Invalid JSON response: %s", error)
        return

    signedUrl = payload.get("signedUrl")
    unencryptedData = payload.get("unencryptedData")
    decryptedData = payload.get("decryptedData")

    if not signedUrl:
        logging.error("No signedUrl returned from server.")
        return

    outputPath = ensureOutputDirectory(outputDir)
    logging.info("Downloading file from signed URL.")
    try:
        downloadResponse = session.get(signedUrl, stream=True, timeout=60)
        downloadResponse.raise_for_status()
    except requests.RequestException as error:
        logging.error("Failed to download file: %s", error)
        return

    savedFile = saveDownloadedFile(downloadResponse, outputPath)

    logging.info("Unencrypted data:\n%s", json.dumps(unencryptedData, indent=2))
    logging.info("Decrypted data:\n%s", json.dumps(decryptedData, indent=2))
    logging.info("Fetch completed successfully. File saved at %s", savedFile)


def fetchPendingFiles(
    baseUrl: str, recipientId: str, channel: Optional[str]
) -> Optional[List[Dict[str, Any]]]:
    pendingUrl = buildPendingUrl(baseUrl, recipientId)
    logging.info("Checking for pending files for recipient %s", recipientId)
    try:
        params = {"channel": channel} if channel else None
        response = requests.get(pendingUrl, params=params, timeout=30)
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


def resolveJobLogPath(jobLogFile: Optional[str], outputPath: Path) -> Path:
    if jobLogFile:
        jobLogPath = Path(jobLogFile).expanduser()
        if not jobLogPath.is_absolute():
            jobLogPath = outputPath / jobLogPath
    else:
        jobLogPath = outputPath / "pendingJobs.log"

    jobLogPath.parent.mkdir(parents=True, exist_ok=True)
    return jobLogPath


def recordJobLogEntry(jobLogPath: Path, jobMetadata: Dict[str, Any], source: str) -> None:
    logEntry = {
        "loggedAt": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "job": jobMetadata,
    }
    with jobLogPath.open("a", encoding="utf-8") as logFile:
        logFile.write(json.dumps(logEntry, ensure_ascii=False))
        logFile.write("\n")


def generateStatusPayload(
    printerSerial: str,
    iteration: int,
    currentJobId: Optional[str],
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

    payload: Dict[str, Any] = {
        "printerIp": f"192.168.1.{10 + (iteration % 10)}",
        "publicKey": "ABCDEFG12345",
        "accessCode": "PCODE6789",
        "printerSerial": printerSerial,
        "objectName": f"dummy_print_object_v{1 + (iteration % 3)}",
        "useAms": iteration % 2 == 0,
        "printJobId": jobId or str(uuid.uuid4()),
        "productName": f"widget_v{1 + (iteration % 4)}",
        "platesRequested": 1 + (iteration % 2),
        "status": status,
        "jobProgress": jobProgress,
        "materialLevel": materialLevel,
    }

    if status == "error":
        payload["errorCode"] = "E123"
        payload["errorMessage"] = "Simulated error condition"
    if status in {"finished", "idle"}:
        payload["lastFilePrinted"] = f"file_{iteration:03d}.gcode"

    nextJobId = jobId
    if status in {"finished", "error", "idle"}:
        nextJobId = None

    return payload, nextJobId


def listenForFiles(
    baseUrl: str,
    recipientId: str,
    outputDir: str,
    pollInterval: int,
    maxIterations: int,
    channel: Optional[str],
    jobLogFile: Optional[str],
) -> None:
    outputPath = ensureOutputDirectory(outputDir)
    jobLogPath = resolveJobLogPath(jobLogFile, outputPath)
    iteration = 0
    while True:
        pendingFiles = fetchPendingFiles(baseUrl, recipientId, channel)
        if pendingFiles is None:
            logging.warning("Unable to retrieve pending files; will retry after delay.")
        elif not pendingFiles:
            logging.info("No pending files for recipient %s.", recipientId)
        else:
            logging.info("Found %d pending file(s) for recipient %s.", len(pendingFiles), recipientId)
            for pendingFile in pendingFiles:
                fetchToken = pendingFile.get("fetchToken")
                if not fetchToken:
                    logging.warning("Skipping pending entry without fetchToken: %s", pendingFile)
                    continue

                filename = pendingFile.get("originalFilename") or pendingFile.get("fileId")
                logging.info(
                    "Fetching pending file %s with token %s.",
                    filename,
                    fetchToken,
                )
                jobMetadata = {
                    **pendingFile,
                    "channel": pendingFile.get("channel") or channel,
                }
                recordJobLogEntry(jobLogPath, jobMetadata, "remote")
                performFetch(baseUrl, fetchToken, outputDir)

        iteration += 1
        if maxIterations and iteration >= maxIterations:
            break

        time.sleep(pollInterval)


def performStatusUpdates(
    baseUrl: str,
    apiKey: str,
    printerSerial: str,
    intervalSeconds: int,
    numUpdates: int,
) -> None:
    statusUrl = f"{buildBaseUrl(baseUrl)}/printer-status"
    session = requests.Session()
    headers = {"X-API-Key": apiKey, "Content-Type": "application/json"}

    iteration = 0
    currentJobId: Optional[str] = None
    while True:
        payload, currentJobId = generateStatusPayload(printerSerial, iteration, currentJobId)
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
        )
    elif arguments.command == "listen":
        if arguments.mode == "offline":
            if not arguments.offlineDataset:
                logging.error(
                    "Offline listen requires --offlineDataset to reference a JSON description of files."
                )
                return
            listenOffline(
                arguments.offlineDataset,
                arguments.outputDir,
                arguments.channel,
                arguments.jobLogFile,
            )
        else:
            if not validateRemoteListenArguments(arguments):
                return
            listenForFiles(
                arguments.baseUrl,
                arguments.recipientId,
                arguments.outputDir,
                arguments.pollInterval,
                arguments.maxIterations,
                arguments.channel,
                arguments.jobLogFile,
            )
    else:
        logging.error("Unknown command: %s", arguments.command)


if __name__ == "__main__":
    main()
