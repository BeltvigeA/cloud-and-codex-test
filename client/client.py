"""Command-line client for interacting with the Cloud Run printer backend."""

import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    fetchParser.add_argument("--baseUrl", required=True, help="Base URL of the Cloud Run service.")
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
    statusParser.add_argument("--baseUrl", required=True, help="Base URL of the Cloud Run service.")
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

    return parser.parse_args()


def buildBaseUrl(baseUrl: str) -> str:
    sanitized = baseUrl.strip().rstrip("/")
    if not sanitized.startswith("http://") and not sanitized.startswith("https://"):
        raise ValueError("baseUrl must include the protocol, e.g., https://")
    return sanitized


def buildFetchUrl(baseUrl: str, fetchToken: str) -> str:
    sanitizedBase = buildBaseUrl(baseUrl)
    return f"{sanitizedBase}/fetch/{fetchToken}"


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

    if arguments.command == "fetch":
        performFetch(arguments.baseUrl, arguments.fetchToken, arguments.outputDir)
    elif arguments.command == "status":
        performStatusUpdates(
            arguments.baseUrl,
            arguments.apiKey,
            arguments.printerSerial,
            arguments.interval,
            arguments.numUpdates,
        )
    else:
        logging.error("Unknown command: %s", arguments.command)


if __name__ == "__main__":
    main()
