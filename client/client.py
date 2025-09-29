"""Command-line client for interacting with the Cloud Run printer backend."""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


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
        "--baseUrl",
        required=False,
        help=(
            "Base URL of the Cloud Run service. If omitted, the CLIENT_BASE_URL "
            "environment variable will be used."
        ),
    )
    fetchParser.add_argument("--fetchToken", required=True, help="Fetch token provided by the web app.")
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
        required=False,
        help=(
            "Base URL of the Cloud Run service. Defaults to CLIENT_BASE_URL environment variable if unset."
        ),
    )
    statusParser.add_argument(
        "--apiKey",
        required=False,
        help="API key for authenticating with the server (or set CLIENT_API_KEY).",
    )
    statusParser.add_argument(
        "--printerSerial",
        required=False,
        help="Unique printer serial number (or set CLIENT_PRINTER_SERIAL).",
    )
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
        "--baseUrl",
        required=False,
        help=(
            "Base URL of the Cloud Run service. Defaults to CLIENT_BASE_URL environment variable if unset."
        ),
    )
    listenParser.add_argument(
        "--recipientId",
        required=False,
        help=(
            "Recipient identifier to filter pending files. Provide via flag, the "
            "CLIENT_RECIPIENT_ID environment variable, or interactively when prompted."
        ),
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

    return parser.parse_args()


def buildBaseUrl(baseUrl: str) -> str:
    sanitized = baseUrl.strip().rstrip("/")
    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        raise ValueError("baseUrl must include the protocol, e.g., https://")
    return sanitized


def resolveBaseUrl(argumentValue: Optional[str]) -> str:
    candidate = argumentValue or os.environ.get("CLIENT_BASE_URL")
    if not candidate:
        raise ValueError(
            "A base URL is required. Provide --baseUrl or set the CLIENT_BASE_URL environment variable."
        )
    return buildBaseUrl(candidate)


def resolveApiKey(argumentValue: Optional[str]) -> str:
    candidate = argumentValue or os.environ.get("CLIENT_API_KEY")
    if not candidate:
        raise ValueError(
            "An API key is required. Provide --apiKey or set the CLIENT_API_KEY environment variable."
        )
    return candidate


def resolvePrinterSerial(argumentValue: Optional[str]) -> str:
    candidate = argumentValue or os.environ.get("CLIENT_PRINTER_SERIAL")
    if not candidate:
        raise ValueError(
            "A printer serial is required. Provide --printerSerial or set the CLIENT_PRINTER_SERIAL environment variable."
        )
    return candidate


def resolveRecipientId(argumentValue: Optional[str]) -> str:
    candidate = argumentValue or os.environ.get("CLIENT_RECIPIENT_ID")
    if candidate:
        sanitized = candidate.strip()
        if sanitized:
            return sanitized

    if sys.stdin.isatty():
        while True:
            try:
                userInput = input("Enter recipient ID to monitor: ").strip()
            except EOFError as error:
                raise ValueError("recipientId is required but was not provided.") from error
            if userInput:
                return userInput

    raise ValueError(
        "A recipient ID is required. Provide --recipientId, set CLIENT_RECIPIENT_ID, or run interactively to input it."
    )


def buildFetchUrl(baseUrl: str, fetchToken: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    return f"{sanitizedBase}/fetch/{fetchToken}"


def buildPendingUrl(baseUrl: str, recipientId: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    sanitizedRecipient = recipientId.strip()
    if not sanitizedRecipient:
        raise ValueError("recipientId must not be empty")
    return f"{sanitizedBase}/recipients/{sanitizedRecipient}/pending"


def ensureOutputDirectory(outputDir: str) -> Path:
    outputPath = Path(outputDir).expanduser().resolve()
    outputPath.mkdir(parents=True, exist_ok=True)
    return outputPath


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
) -> None:
    iteration = 0
    while True:
        pendingFiles = fetchPendingFiles(baseUrl, recipientId)
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


def main() -> None:
    configureLogging()
    arguments = parseArguments()

    try:
        if arguments.command == "fetch":
            baseUrl = resolveBaseUrl(arguments.baseUrl)
            performFetch(baseUrl, arguments.fetchToken, arguments.outputDir)
        elif arguments.command == "status":
            baseUrl = resolveBaseUrl(arguments.baseUrl)
            apiKey = resolveApiKey(arguments.apiKey)
            printerSerial = resolvePrinterSerial(arguments.printerSerial)
            performStatusUpdates(
                baseUrl,
                apiKey,
                printerSerial,
                arguments.interval,
                arguments.numUpdates,
            )
        elif arguments.command == "listen":
            baseUrl = resolveBaseUrl(arguments.baseUrl)
            recipientId = resolveRecipientId(arguments.recipientId)
            listenForFiles(
                baseUrl,
                recipientId,
                arguments.outputDir,
                arguments.pollInterval,
                arguments.maxIterations,
            )
        else:
            logging.error("Unknown command: %s", arguments.command)
    except ValueError as error:
        logging.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
