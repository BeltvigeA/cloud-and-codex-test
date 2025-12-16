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
        self.productRecordsPath = self.databasePath.parent / "product-records.json"
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
                    downloadedFilePath TEXT,
                    lastRequestedAt TEXT NOT NULL,
                    updatedAt TEXT NOT NULL
                )
                """
            )
            cursor = self.connection.execute("PRAGMA table_info(products)")
            existingColumns = {row["name"] for row in cursor.fetchall()}
            if "downloadedFilePath" not in existingColumns:
                self.connection.execute(
                    "ALTER TABLE products ADD COLUMN downloadedFilePath TEXT"
                )
            
            # Active print jobs table for persistent job tracking
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS active_print_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    printer_serial TEXT NOT NULL,
                    print_job_id TEXT,
                    product_id TEXT,
                    product_name TEXT,
                    file_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    status TEXT DEFAULT 'printing',
                    finished_at TEXT,
                    sent_to_backend INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Create indexes for efficient lookup
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_active_jobs_serial 
                ON active_print_jobs(printer_serial)
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_active_jobs_status 
                ON active_print_jobs(status)
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
        downloadedFilePath: Optional[str] = None,
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

        if downloadedFilePath is not None:
            resolvedDownloadedFilePath = downloadedFilePath
        elif resolvedDownloaded:
            resolvedDownloadedFilePath = (existingRecord or {}).get("downloadedFilePath")
        else:
            resolvedDownloadedFilePath = None

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO products (
                    productId,
                    fileName,
                    downloaded,
                    downloadedFilePath,
                    lastRequestedAt,
                    updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(productId) DO UPDATE SET
                    fileName = CASE WHEN excluded.fileName IS NOT NULL THEN excluded.fileName ELSE fileName END,
                    downloaded = excluded.downloaded,
                    downloadedFilePath = excluded.downloadedFilePath,
                    lastRequestedAt = excluded.lastRequestedAt,
                    updatedAt = excluded.updatedAt
                """,
                (
                    productId,
                    resolvedFileName,
                    1 if resolvedDownloaded else 0,
                    resolvedDownloadedFilePath,
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
            """
            SELECT productId, fileName, downloaded, downloadedFilePath, lastRequestedAt, updatedAt
            FROM products WHERE productId = ?
            """,
            (productId,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "productId": row["productId"],
            "fileName": row["fileName"],
            "downloaded": bool(row["downloaded"]),
            "downloadedFilePath": row["downloadedFilePath"],
            "lastRequestedAt": row["lastRequestedAt"],
            "updatedAt": row["updatedAt"],
        }

    def _loadProductLog(self) -> Dict[str, Any]:
        if self.productRecordsPath.exists():
            try:
                with self.productRecordsPath.open("r", encoding="utf-8") as logFile:
                    loaded = json.load(logFile)
                if isinstance(loaded, dict):
                    productLog: Dict[str, Any] = dict(loaded)
                else:
                    productLog = {}
            except (OSError, json.JSONDecodeError):
                productLog = {}
        else:
            productLog = {}

        products = productLog.get("products")
        if not isinstance(products, dict):
            products = {}
        productLog["products"] = products
        return productLog

    def _writeProductLog(self, productLog: Dict[str, Any]) -> None:
        with self.productRecordsPath.open("w", encoding="utf-8") as logFile:
            json.dump(productLog, logFile, indent=2, ensure_ascii=False)

    def _persistProductFiles(
        self, record: Dict[str, Any], printJobId: Optional[str]
    ) -> None:
        productLog = self._loadProductLog()
        products: Dict[str, Any] = productLog["products"]

        productId = record["productId"]
        existingEntry = products.get(productId)
        if isinstance(existingEntry, dict):
            productEntry: Dict[str, Any] = dict(existingEntry)
        else:
            productEntry = {}

        if not isinstance(productEntry.get("printActivity"), dict):
            productEntry["printActivity"] = {}

        if not isinstance(productEntry.get("requestHistory"), list):
            productEntry["requestHistory"] = []

        createdAt = productEntry.get("createdAt")
        if not isinstance(createdAt, str):
            createdAt = (
                record.get("lastRequestedAt")
                or record.get("updatedAt")
                or datetime.utcnow().isoformat()
            )

        lastRequestedAt = record.get("lastRequestedAt")
        requestHistory: List[str] = [
            entry for entry in productEntry["requestHistory"] if isinstance(entry, str)
        ]
        if isinstance(lastRequestedAt, str) and (
            not requestHistory or requestHistory[-1] != lastRequestedAt
        ):
            requestHistory.append(lastRequestedAt)

        printActivityRaw = productEntry["printActivity"]
        if not isinstance(printActivityRaw.get("printJobs"), dict):
            printActivityRaw["printJobs"] = {}

        latestPrintedAt = printActivityRaw.get("latestPrintedAt")
        if not isinstance(latestPrintedAt, str):
            latestPrintedAt = None

        latestPrintJobId = printActivityRaw.get("latestPrintJobId")
        if not isinstance(latestPrintJobId, str):
            latestPrintJobId = None

        if printJobId is not None:
            jobKey = str(printJobId)
            existingJob = printActivityRaw["printJobs"].get(jobKey)
            if isinstance(existingJob, dict):
                jobEntry = dict(existingJob)
            else:
                jobEntry = {}

            resolvedPrintedAt = (
                lastRequestedAt if isinstance(lastRequestedAt, str) else datetime.utcnow().isoformat()
            )
            jobEntry["lastPrintedAt"] = resolvedPrintedAt
            printActivityRaw["printJobs"][jobKey] = jobEntry
            latestPrintJobId = jobKey
            latestPrintedAt = resolvedPrintedAt
        elif isinstance(lastRequestedAt, str):
            latestPrintedAt = lastRequestedAt

        # Extract product name from fileName by removing UUID prefix and file extension
        productName = None
        fileLocation = record.get("fileName")
        if fileLocation:
            # Remove directory path and file extension
            baseName = Path(fileLocation).stem  # Gets filename without extension
            # Check if filename has UUID prefix (format: uuid_productname)
            if "_" in baseName:
                # Find the first underscore after the UUID pattern (36 chars: 8-4-4-4-12)
                parts = baseName.split("_", 1)
                if len(parts) > 1 and len(parts[0]) == 36:
                    # UUID is exactly 36 chars (with dashes), so keep everything after first underscore
                    productName = parts[1]
                else:
                    productName = baseName
            else:
                productName = baseName

        productEntry.update(
            {
                "productId": productId,
                "productName": productName,
                "createdAt": createdAt,
                "lastRequestedAt": lastRequestedAt,
                "fileLocation": fileLocation,
                "filePath": record.get("downloadedFilePath"),
                "downloaded": bool(record.get("downloaded")),
                "requestHistory": requestHistory,
                "printActivity": {
                    "productId": productId,
                    "latestPrintJobId": latestPrintJobId,
                    "latestPrintedAt": latestPrintedAt,
                    "printJobs": printActivityRaw["printJobs"],
                },
            }
        )

        products[productId] = productEntry
        productLog["products"] = products
        self._writeProductLog(productLog)

    def findPrintJobInProductLog(self, printJobId: str) -> Optional[Dict[str, Any]]:
        if not isinstance(printJobId, str) or not printJobId:
            return None

        productLog = self._loadProductLog()
        products = productLog.get("products")
        if not isinstance(products, dict):
            return None

        jobKey = str(printJobId)
        for productId, productEntry in products.items():
            if not isinstance(productEntry, dict):
                continue

            printActivity = productEntry.get("printActivity")
            if not isinstance(printActivity, dict):
                continue

            printJobs = printActivity.get("printJobs")
            if not isinstance(printJobs, dict):
                continue

            jobEntry = printJobs.get(jobKey)
            if isinstance(jobEntry, dict):
                return {
                    "productId": productId,
                    "productEntry": dict(productEntry),
                    "printJob": dict(jobEntry),
                }

        return None

    # ============================================
    # ACTIVE PRINT JOBS PERSISTENCE METHODS
    # ============================================

    def save_active_job(
        self,
        printer_serial: str,
        file_name: str,
        print_job_id: Optional[str] = None,
        product_id: Optional[str] = None,
        product_name: Optional[str] = None,
    ) -> int:
        """
        Save an active print job to the database.
        
        Returns the row ID of the inserted job.
        """
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO active_print_jobs (
                    printer_serial, print_job_id, product_id, product_name,
                    file_name, started_at, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'printing', ?)
                """,
                (
                    printer_serial,
                    print_job_id,
                    product_id,
                    product_name,
                    file_name,
                    timestamp,
                    timestamp,
                ),
            )
            return cursor.lastrowid or 0

    def get_active_job_by_serial(self, printer_serial: str) -> Optional[Dict[str, Any]]:
        """Get the currently printing job for a printer (status='printing')."""
        cursor = self.connection.execute(
            """
            SELECT id, printer_serial, print_job_id, product_id, product_name,
                   file_name, started_at, status, finished_at, sent_to_backend
            FROM active_print_jobs
            WHERE printer_serial = ? AND status = 'printing'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (printer_serial,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_active_job(row)

    def get_active_job_by_file(
        self, printer_serial: str, file_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find an active job by file name (for matching jobs without job ID)."""
        cursor = self.connection.execute(
            """
            SELECT id, printer_serial, print_job_id, product_id, product_name,
                   file_name, started_at, status, finished_at, sent_to_backend
            FROM active_print_jobs
            WHERE printer_serial = ? AND file_name = ? AND status = 'printing'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (printer_serial, file_name),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_active_job(row)

    def finish_active_job(
        self,
        printer_serial: str,
        print_job_id: Optional[str] = None,
        status: str = "finished",
    ) -> bool:
        """Mark an active job as finished or cancelled."""
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            if print_job_id:
                cursor = self.connection.execute(
                    """
                    UPDATE active_print_jobs
                    SET status = ?, finished_at = ?, updated_at = ?
                    WHERE printer_serial = ? AND print_job_id = ? AND status = 'printing'
                    """,
                    (status, timestamp, timestamp, printer_serial, print_job_id),
                )
            else:
                cursor = self.connection.execute(
                    """
                    UPDATE active_print_jobs
                    SET status = ?, finished_at = ?, updated_at = ?
                    WHERE printer_serial = ? AND status = 'printing'
                    """,
                    (status, timestamp, timestamp, printer_serial),
                )
            return cursor.rowcount > 0

    def mark_active_job_sent(self, printer_serial: str, print_job_id: Optional[str]) -> bool:
        """Mark a job as sent to backend."""
        timestamp = datetime.utcnow().isoformat()
        with self.connection:
            if print_job_id:
                cursor = self.connection.execute(
                    """
                    UPDATE active_print_jobs
                    SET sent_to_backend = 1, updated_at = ?
                    WHERE printer_serial = ? AND print_job_id = ?
                    """,
                    (timestamp, printer_serial, print_job_id),
                )
            else:
                cursor = self.connection.execute(
                    """
                    UPDATE active_print_jobs
                    SET sent_to_backend = 1, updated_at = ?
                    WHERE printer_serial = ? AND print_job_id IS NULL
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (timestamp, printer_serial),
                )
            return cursor.rowcount > 0

    def get_printing_jobs(self) -> List[Dict[str, Any]]:
        """Get all jobs with status='printing' (for recovery after restart)."""
        cursor = self.connection.execute(
            """
            SELECT id, printer_serial, print_job_id, product_id, product_name,
                   file_name, started_at, status, finished_at, sent_to_backend
            FROM active_print_jobs
            WHERE status = 'printing'
            ORDER BY started_at DESC
            """
        )
        return [self._row_to_active_job(row) for row in cursor.fetchall()]

    def get_unsent_finished_jobs(self) -> List[Dict[str, Any]]:
        """Get finished jobs that haven't been sent to backend."""
        cursor = self.connection.execute(
            """
            SELECT id, printer_serial, print_job_id, product_id, product_name,
                   file_name, started_at, status, finished_at, sent_to_backend
            FROM active_print_jobs
            WHERE status IN ('finished', 'cancelled') AND sent_to_backend = 0
            ORDER BY finished_at DESC
            """
        )
        return [self._row_to_active_job(row) for row in cursor.fetchall()]

    def cleanup_old_active_jobs(self, days: int = 7) -> int:
        """Remove old finished jobs from the database."""
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                """
                DELETE FROM active_print_jobs
                WHERE status IN ('finished', 'cancelled') AND finished_at < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def _row_to_active_job(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to an active job dictionary."""
        return {
            "id": row["id"],
            "printer_serial": row["printer_serial"],
            "print_job_id": row["print_job_id"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "file_name": row["file_name"],
            "started_at": row["started_at"],
            "status": row["status"],
            "finished_at": row["finished_at"],
            "sent_to_backend": bool(row["sent_to_backend"]),
        }


