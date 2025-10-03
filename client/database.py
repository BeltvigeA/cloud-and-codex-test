import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class StoredJob:
    jobId: str
    source: str
    jobNumber: str
    filename: str
    targetPrinter: str
    status: str
    material: str
    duration: str
    uploadedAt: Optional[str]
    fetchToken: Optional[str]


@dataclass
class StoredJobMetadata:
    jobId: str
    fetchToken: Optional[str]
    unencryptedData: Dict[str, Any]
    decryptedData: Dict[str, Any]
    signedUrl: Optional[str]
    downloadedFilePath: Optional[str]


class LocalDatabase:
    def __init__(self, databasePath: Optional[Path | str] = None) -> None:
        defaultPath = Path.home() / ".printmaster" / "printmaster.db"
        self.databasePath = Path(databasePath).expanduser() if databasePath else defaultPath
        self.databasePath.parent.mkdir(parents=True, exist_ok=True)
        self.productStorageDir = self.databasePath.parent / "products"
        self.productStorageDir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.databasePath)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS printers (
                    serialNumber TEXT PRIMARY KEY,
                    printerName TEXT NOT NULL,
                    modelName TEXT NOT NULL,
                    ipAddress TEXT NOT NULL,
                    status TEXT NOT NULL,
                    statusDetail TEXT NOT NULL,
                    statusColor TEXT NOT NULL,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    jobId TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    jobNumber TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    targetPrinter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    material TEXT NOT NULL,
                    duration TEXT NOT NULL,
                    uploadedAt TEXT,
                    fetchToken TEXT,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobMetadata (
                    jobId TEXT PRIMARY KEY,
                    fetchToken TEXT,
                    unencryptedData TEXT,
                    decryptedData TEXT,
                    signedUrl TEXT,
                    downloadedFilePath TEXT,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    productId TEXT PRIMARY KEY,
                    fileName TEXT,
                    downloaded INTEGER NOT NULL,
                    lastRequestedAt TEXT NOT NULL,
                    updatedAt TEXT NOT NULL
                )
                """
            )

    def close(self) -> None:
        self.connection.close()

    def upsertPrinter(
        self,
        serialNumber: str,
        printerName: str,
        modelName: str,
        ipAddress: str,
        status: str,
        statusDetail: str,
        statusColor: str,
    ) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO printers (
                    serialNumber,
                    printerName,
                    modelName,
                    ipAddress,
                    status,
                    statusDetail,
                    statusColor,
                    updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(serialNumber) DO UPDATE SET
                    printerName = excluded.printerName,
                    modelName = excluded.modelName,
                    ipAddress = excluded.ipAddress,
                    status = excluded.status,
                    statusDetail = excluded.statusDetail,
                    statusColor = excluded.statusColor,
                    updatedAt = excluded.updatedAt
                """,
                (
                    serialNumber,
                    printerName,
                    modelName,
                    ipAddress,
                    status,
                    statusDetail,
                    statusColor,
                    timestamp,
                ),
            )

    def deletePrinter(self, serialNumber: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM printers WHERE serialNumber = ?", (serialNumber,))

    def loadPrinters(self) -> List[dict[str, str]]:
        cursor = self.connection.execute(
            "SELECT serialNumber, printerName, modelName, ipAddress, status, statusDetail, statusColor FROM printers"
        )
        printers: List[dict[str, str]] = []
        for row in cursor.fetchall():
            printers.append(
                {
                    "serialNumber": row["serialNumber"],
                    "printerName": row["printerName"],
                    "modelName": row["modelName"],
                    "ipAddress": row["ipAddress"],
                    "status": row["status"],
                    "statusDetail": row["statusDetail"],
                    "statusColor": row["statusColor"],
                }
            )
        return printers

    def upsertJob(self, job: StoredJob) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobs (
                    jobId,
                    source,
                    jobNumber,
                    filename,
                    targetPrinter,
                    status,
                    material,
                    duration,
                    uploadedAt,
                    fetchToken,
                    updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jobId) DO UPDATE SET
                    source = excluded.source,
                    jobNumber = excluded.jobNumber,
                    filename = excluded.filename,
                    targetPrinter = excluded.targetPrinter,
                    status = excluded.status,
                    material = excluded.material,
                    duration = excluded.duration,
                    uploadedAt = excluded.uploadedAt,
                    fetchToken = excluded.fetchToken,
                    updatedAt = excluded.updatedAt
                """,
                (
                    job.jobId,
                    job.source,
                    job.jobNumber,
                    job.filename,
                    job.targetPrinter,
                    job.status,
                    job.material,
                    job.duration,
                    job.uploadedAt,
                    job.fetchToken,
                    timestamp,
                ),
            )

    def loadJobs(self) -> List[StoredJob]:
        cursor = self.connection.execute(
            """
            SELECT jobId, source, jobNumber, filename, targetPrinter, status, material, duration, uploadedAt, fetchToken
            FROM jobs
            ORDER BY updatedAt DESC
            """
        )
        jobs: List[StoredJob] = []
        for row in cursor.fetchall():
            jobs.append(
                StoredJob(
                    jobId=row["jobId"],
                    source=row["source"],
                    jobNumber=row["jobNumber"],
                    filename=row["filename"],
                    targetPrinter=row["targetPrinter"],
                    status=row["status"],
                    material=row["material"],
                    duration=row["duration"],
                    uploadedAt=row["uploadedAt"],
                    fetchToken=row["fetchToken"],
                )
            )
        return jobs

    def deleteJob(self, jobId: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM jobs WHERE jobId = ?", (jobId,))
            self.connection.execute("DELETE FROM jobMetadata WHERE jobId = ?", (jobId,))

    def pruneJobs(self, source: str, validJobIds: Iterable[str]) -> None:
        validIds = list(validJobIds)
        with self.connection:
            if validIds:
                placeholders = ",".join("?" for _ in validIds)
                self.connection.execute(
                    f"DELETE FROM jobs WHERE source = ? AND jobId NOT IN ({placeholders})",
                    (source, *validIds),
                )
            else:
                self.connection.execute("DELETE FROM jobs WHERE source = ?", (source,))
            self.connection.execute(
                "DELETE FROM jobMetadata WHERE jobId NOT IN (SELECT jobId FROM jobs)"
            )

    def saveJobMetadata(
        self,
        jobId: str,
        fetchToken: Optional[str],
        unencryptedData: Dict[str, Any],
        decryptedData: Dict[str, Any],
        signedUrl: Optional[str],
        downloadedFilePath: Optional[str] = None,
    ) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobMetadata (
                    jobId,
                    fetchToken,
                    unencryptedData,
                    decryptedData,
                    signedUrl,
                    downloadedFilePath,
                    updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jobId) DO UPDATE SET
                    fetchToken = excluded.fetchToken,
                    unencryptedData = excluded.unencryptedData,
                    decryptedData = excluded.decryptedData,
                    signedUrl = excluded.signedUrl,
                    downloadedFilePath = excluded.downloadedFilePath,
                    updatedAt = excluded.updatedAt
                """,
                (
                    jobId,
                    fetchToken,
                    json.dumps(unencryptedData or {}),
                    json.dumps(decryptedData or {}),
                    signedUrl,
                    downloadedFilePath,
                    timestamp,
                ),
            )

    def loadJobMetadata(self, jobId: Optional[str], fetchToken: Optional[str]) -> Optional[StoredJobMetadata]:
        if jobId:
            cursor = self.connection.execute(
                "SELECT jobId, fetchToken, unencryptedData, decryptedData, signedUrl, downloadedFilePath FROM jobMetadata WHERE jobId = ?",
                (jobId,),
            )
            row = cursor.fetchone()
            if row:
                return self._buildMetadataFromRow(row)
        if fetchToken:
            cursor = self.connection.execute(
                "SELECT jobId, fetchToken, unencryptedData, decryptedData, signedUrl, downloadedFilePath FROM jobMetadata WHERE fetchToken = ?",
                (fetchToken,),
            )
            row = cursor.fetchone()
            if row:
                return self._buildMetadataFromRow(row)
        return None

    def _buildMetadataFromRow(self, row: sqlite3.Row) -> StoredJobMetadata:
        def parseJson(raw: Optional[str]) -> Dict[str, Any]:
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}

        return StoredJobMetadata(
            jobId=row["jobId"],
            fetchToken=row["fetchToken"],
            unencryptedData=parseJson(row["unencryptedData"]),
            decryptedData=parseJson(row["decryptedData"]),
            signedUrl=row["signedUrl"],
            downloadedFilePath=row["downloadedFilePath"],
        )

    def upsertProductRecord(
        self,
        productId: str,
        fileName: Optional[str] = None,
        *,
        downloaded: Optional[bool] = None,
        requestTimestamp: Optional[str] = None,
        printJobId: Optional[str] = None,
    ) -> Dict[str, Any]:
        existingRecord = self.findProductById(productId)
        if requestTimestamp is not None:
            resolvedTimestamp = requestTimestamp
        elif existingRecord and existingRecord.get("lastRequestedAt"):
            resolvedTimestamp = existingRecord["lastRequestedAt"]
        else:
            resolvedTimestamp = datetime.utcnow().isoformat()
        resolvedFileName = fileName if fileName is not None else (existingRecord or {}).get("fileName")
        if downloaded is None:
            resolvedDownloaded = bool((existingRecord or {}).get("downloaded", False))
        else:
            resolvedDownloaded = downloaded

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO products (
                    productId,
                    fileName,
                    downloaded,
                    lastRequestedAt,
                    updatedAt
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(productId) DO UPDATE SET
                    fileName = CASE WHEN excluded.fileName IS NOT NULL THEN excluded.fileName ELSE fileName END,
                    downloaded = excluded.downloaded,
                    lastRequestedAt = excluded.lastRequestedAt,
                    updatedAt = excluded.updatedAt
                """,
                (
                    productId,
                    resolvedFileName,
                    1 if resolvedDownloaded else 0,
                    resolvedTimestamp,
                    resolvedTimestamp,
                ),
            )

        updatedRecord = self.findProductById(productId)
        if updatedRecord is None:
            raise RuntimeError(f"Failed to persist product record for {productId}")

        self._persistProductFiles(updatedRecord, printJobId)
        return updatedRecord

    def findProductById(self, productId: str) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT productId, fileName, downloaded, lastRequestedAt, updatedAt FROM products WHERE productId = ?",
            (productId,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "productId": row["productId"],
            "fileName": row["fileName"],
            "downloaded": bool(row["downloaded"]),
            "lastRequestedAt": row["lastRequestedAt"],
            "updatedAt": row["updatedAt"],
        }

    def _persistProductFiles(
        self, record: Dict[str, Any], printJobId: Optional[str]
    ) -> None:
        productId = record["productId"]
        productDir = self.productStorageDir / productId
        productDir.mkdir(parents=True, exist_ok=True)

        metadataPath = productDir / "metadata.json"
        requestsPath = productDir / "requests.json"
        activityPath = productDir / "print-activity.json"

        existingMetadata: Dict[str, Any] = {}
        if metadataPath.exists():
            try:
                with metadataPath.open("r", encoding="utf-8") as metadataFile:
                    loadedMetadata = json.load(metadataFile)
                if isinstance(loadedMetadata, dict):
                    existingMetadata = loadedMetadata
            except (OSError, json.JSONDecodeError):
                existingMetadata = {}

        createdAt = (
            existingMetadata.get("createdAt")
            or record.get("updatedAt")
            or datetime.utcnow().isoformat()
        )
        fileLocation = record.get("fileName") or existingMetadata.get("fileLocation")

        metadataContent: Dict[str, Any] = {
            "productId": productId,
            "createdAt": createdAt,
            "lastRequestedAt": record.get("lastRequestedAt"),
        }

        if fileLocation:
            metadataContent["fileLocation"] = str(fileLocation)

        with metadataPath.open("w", encoding="utf-8") as metadataFile:
            json.dump(metadataContent, metadataFile, indent=2, ensure_ascii=False)

        requestEntries: List[str] = []
        if requestsPath.exists():
            try:
                with requestsPath.open("r", encoding="utf-8") as requestsFile:
                    loadedRequests = json.load(requestsFile)
                if isinstance(loadedRequests, list):
                    requestEntries = [
                        entry for entry in loadedRequests if isinstance(entry, str)
                    ]
            except (OSError, json.JSONDecodeError):
                requestEntries = []

        latestTimestamp = record.get("lastRequestedAt")
        if latestTimestamp:
            latestTimestampStr = str(latestTimestamp)
            if not requestEntries or requestEntries[-1] != latestTimestampStr:
                requestEntries.append(latestTimestampStr)

        with requestsPath.open("w", encoding="utf-8") as requestsFile:
            json.dump(requestEntries, requestsFile, indent=2, ensure_ascii=False)

        lastRequestedAt = record.get("lastRequestedAt")
        resolvedLastRequestedAt = (
            str(lastRequestedAt) if lastRequestedAt is not None else None
        )

        self._persistProductActivityFile(
            activityPath,
            record["productId"],
            printJobId,
            resolvedLastRequestedAt,
        )

    def _persistProductActivityFile(
        self,
        activityPath: Path,
        productId: str,
        printJobId: Optional[str],
        lastPrintedAt: Optional[str],
    ) -> None:
        existingContent: Dict[str, Any] = {}
        if activityPath.exists():
            try:
                with activityPath.open("r", encoding="utf-8") as activityFile:
                    loadedContent = json.load(activityFile)
                if isinstance(loadedContent, dict):
                    existingContent = loadedContent
            except (OSError, json.JSONDecodeError):
                existingContent = {}

        existingPrintJobs = existingContent.get("printJobs")
        if isinstance(existingPrintJobs, dict):
            updatedPrintJobs: Dict[str, Dict[str, Any]] = dict(existingPrintJobs)
        else:
            updatedPrintJobs = {}

        updatedContent: Dict[str, Any] = {
            "productId": productId,
            "printJobs": updatedPrintJobs,
        }

        existingLatestPrintJobId = existingContent.get("latestPrintJobId")
        existingLatestPrintedAt = existingContent.get("latestPrintedAt")

        if printJobId is not None and lastPrintedAt is not None:
            jobKey = str(printJobId)
            updatedPrintJobs[jobKey] = {"lastPrintedAt": lastPrintedAt}
            updatedContent["latestPrintJobId"] = jobKey
            updatedContent["latestPrintedAt"] = lastPrintedAt
        else:
            if isinstance(existingLatestPrintJobId, str):
                updatedContent["latestPrintJobId"] = existingLatestPrintJobId
            if lastPrintedAt is not None:
                updatedContent["latestPrintedAt"] = lastPrintedAt
            elif isinstance(existingLatestPrintedAt, str):
                updatedContent["latestPrintedAt"] = existingLatestPrintedAt

        with activityPath.open("w", encoding="utf-8") as activityFile:
            json.dump(updatedContent, activityFile, indent=2, ensure_ascii=False)

