import json
from datetime import datetime
from pathlib import Path

from client.database import LocalDatabase
from client.client import checkProductAvailability


def test_upsertProductRecord_updates_lastRequested(tmp_path: Path) -> None:
    databasePath = tmp_path / "products.db"
    database = LocalDatabase(databasePath)

    firstTimestamp = "2023-01-01T00:00:00"
    firstRecord = database.upsertProductRecord(
        "product-1",
        requestTimestamp=firstTimestamp,
    )

    assert firstRecord["productId"] == "product-1"
    assert firstRecord["fileName"] is None
    assert firstRecord["downloaded"] is False
    assert firstRecord["lastRequestedAt"] == firstTimestamp

    secondTimestamp = "2024-02-02T12:30:45"
    secondRecord = database.upsertProductRecord(
        "product-1",
        fileName="widget-v2.gcode",
        downloaded=True,
        requestTimestamp=secondTimestamp,
    )

    assert secondRecord["fileName"] == "widget-v2.gcode"
    assert secondRecord["downloaded"] is True
    assert secondRecord["lastRequestedAt"] == secondTimestamp
    assert database.findProductById("product-1") == secondRecord

    database.close()


def test_upsertProductRecord_creates_single_log_entry(tmp_path: Path) -> None:
    databasePath = tmp_path / "records.db"
    database = LocalDatabase(databasePath)

    firstTimestamp = "2023-05-05T05:05:05"
    database.upsertProductRecord(
        "product-7",
        fileName="initial.gcode",
        downloaded=False,
        requestTimestamp=firstTimestamp,
        printJobId="job-1",
    )

    productLogPath = tmp_path / "product-records.json"
    assert productLogPath.exists()

    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    productEntry = productLog["products"]["product-7"]

    assert productEntry["productId"] == "product-7"
    assert productEntry["createdAt"] == firstTimestamp
    assert productEntry["lastRequestedAt"] == firstTimestamp
    assert productEntry["fileLocation"] == "initial.gcode"
    assert productEntry["requestHistory"] == [firstTimestamp]

    secondTimestamp = "2024-06-06T06:06:06"
    database.upsertProductRecord(
        "product-7",
        fileName="finalized.gcode",
        downloaded=True,
        requestTimestamp=secondTimestamp,
        printJobId="job-2",
    )

    updatedLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    updatedEntry = updatedLog["products"]["product-7"]

    assert updatedEntry["productId"] == "product-7"
    assert updatedEntry["createdAt"] == firstTimestamp
    assert updatedEntry["lastRequestedAt"] == secondTimestamp
    assert updatedEntry["fileLocation"] == "finalized.gcode"
    assert updatedEntry["requestHistory"] == [firstTimestamp, secondTimestamp]

    activity = updatedEntry["printActivity"]
    assert activity["productId"] == "product-7"
    assert activity["latestPrintJobId"] == "job-2"
    assert activity["latestPrintedAt"] == secondTimestamp
    assert activity["printJobs"] == {
        "job-1": {"lastPrintedAt": firstTimestamp},
        "job-2": {"lastPrintedAt": secondTimestamp},
    }

    database.close()


def test_upsertProductRecord_preserves_lastRequested_without_new_request(tmp_path: Path) -> None:
    databasePath = tmp_path / "preserve.db"
    database = LocalDatabase(databasePath)

    initialTimestamp = "2023-07-07T07:07:07"
    database.upsertProductRecord(
        "product-9",
        fileName="draft.gcode",
        downloaded=False,
        requestTimestamp=initialTimestamp,
    )

    updatedRecord = database.upsertProductRecord(
        "product-9",
        downloaded=True,
    )

    assert updatedRecord["downloaded"] is True
    assert updatedRecord["lastRequestedAt"] == initialTimestamp

    productLogPath = tmp_path / "product-records.json"
    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    entry = productLog["products"]["product-9"]

    assert entry["lastRequestedAt"] == initialTimestamp
    assert entry["requestHistory"] == [initialTimestamp]
    assert entry["printActivity"]["latestPrintedAt"] == initialTimestamp

    database.close()


def test_checkProductAvailability_tracks_transitions(tmp_path: Path) -> None:
    databasePath = tmp_path / "availability.db"
    database = LocalDatabase(databasePath)

    initialStatus = checkProductAvailability(
        database,
        "product-42",
        fileName="alpha.gcode",
        requestTimestamp="2025-03-01T10:00:00",
    )

    assert initialStatus["status"] == "notFound"
    assert initialStatus["shouldRequestFile"] is True
    assert initialStatus["record"]["downloaded"] is False
    assert initialStatus["record"]["lastRequestedAt"] == "2025-03-01T10:00:00"

    secondStatus = checkProductAvailability(
        database,
        "product-42",
        fileName="alpha.gcode",
        requestTimestamp="2025-03-01T10:05:00",
    )

    assert secondStatus["status"] == "metadataCached"
    assert secondStatus["shouldRequestFile"] is True
    assert secondStatus["record"]["lastRequestedAt"] == "2025-03-01T10:05:00"

    database.upsertProductRecord(
        "product-42",
        fileName="alpha.gcode",
        downloaded=True,
        requestTimestamp="2025-03-01T10:10:00",
    )

    finalStatus = checkProductAvailability(
        database,
        "product-42",
        requestTimestamp="2025-03-01T10:15:00",
    )

    assert finalStatus["status"] == "fileCached"
    assert finalStatus["shouldRequestFile"] is False
    assert finalStatus["record"]["downloaded"] is True
    assert finalStatus["record"]["lastRequestedAt"] == "2025-03-01T10:15:00"

    database.close()


def test_upsertProductRecord_updates_print_activity(tmp_path: Path) -> None:
    databasePath = tmp_path / "activity.db"
    database = LocalDatabase(databasePath)

    firstTimestamp = "2025-07-01T08:00:00"
    database.upsertProductRecord(
        "product-88",
        fileName="alpha.gcode",
        downloaded=True,
        requestTimestamp=firstTimestamp,
        printJobId="print-001",
    )

    productLogPath = tmp_path / "product-records.json"
    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    activity = productLog["products"]["product-88"]["printActivity"]
    assert activity["latestPrintJobId"] == "print-001"
    assert activity["latestPrintedAt"] == firstTimestamp
    assert activity["printJobs"]["print-001"] == {"lastPrintedAt": firstTimestamp}

    secondTimestamp = "2025-07-01T09:30:00"
    database.upsertProductRecord(
        "product-88",
        fileName="alpha.gcode",
        downloaded=True,
        requestTimestamp=secondTimestamp,
        printJobId="print-001",
    )

    updatedLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    updatedActivity = updatedLog["products"]["product-88"]["printActivity"]
    assert updatedActivity["latestPrintJobId"] == "print-001"
    assert updatedActivity["latestPrintedAt"] == secondTimestamp
    assert updatedActivity["printJobs"]["print-001"] == {
        "lastPrintedAt": secondTimestamp
    }

    database.close()


def testUpsertProductRecordCreatesActivityWithoutJobId(tmp_path: Path) -> None:
    databasePath = tmp_path / "no-job.db"
    database = LocalDatabase(databasePath)

    requestTimestamp = "2026-01-01T01:01:01"
    database.upsertProductRecord(
        "product-100",
        requestTimestamp=requestTimestamp,
        printJobId=None,
    )

    productLogPath = tmp_path / "product-records.json"
    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    activity = productLog["products"]["product-100"]["printActivity"]
    assert activity["productId"] == "product-100"
    assert activity["printJobs"] == {}
    assert activity["latestPrintJobId"] is None
    assert activity["latestPrintedAt"] == requestTimestamp

    database.close()


def testUpsertProductRecordExtendsActivityHistory(tmp_path: Path) -> None:
    databasePath = tmp_path / "history.db"
    database = LocalDatabase(databasePath)

    initialTimestamp = "2027-02-02T02:02:02"
    productId = "product-200"
    database.upsertProductRecord(
        productId,
        requestTimestamp=initialTimestamp,
        printJobId=None,
    )

    productLogPath = tmp_path / "product-records.json"
    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    baselineActivity = productLog["products"][productId]["printActivity"]
    assert baselineActivity["productId"] == productId
    assert baselineActivity["printJobs"] == {}
    assert baselineActivity["latestPrintJobId"] is None
    assert baselineActivity["latestPrintedAt"] == initialTimestamp

    firstJobTimestamp = "2027-02-02T03:03:03"
    database.upsertProductRecord(
        productId,
        requestTimestamp=firstJobTimestamp,
        printJobId="job-xyz",
    )

    secondJobTimestamp = "2027-02-02T04:04:04"
    database.upsertProductRecord(
        productId,
        requestTimestamp=secondJobTimestamp,
        printJobId="job-abc",
    )

    updatedLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    activity = updatedLog["products"][productId]["printActivity"]

    assert activity["productId"] == productId
    assert activity["latestPrintJobId"] == "job-abc"
    assert activity["latestPrintedAt"] == secondJobTimestamp
    assert activity["printJobs"] == {
        "job-xyz": {"lastPrintedAt": firstJobTimestamp},
        "job-abc": {"lastPrintedAt": secondJobTimestamp},
    }

    database.close()


def testUpsertProductRecordGeneratesTimestampForMissingRequest(
    tmp_path: Path,
) -> None:
    databasePath = tmp_path / "auto.db"
    database = LocalDatabase(databasePath)

    productId = "product-auto"
    printJobId = "job-auto"

    record = database.upsertProductRecord(
        productId,
        printJobId=printJobId,
    )

    assert "lastRequestedAt" in record
    generatedTimestamp = record["lastRequestedAt"]
    datetime.fromisoformat(generatedTimestamp)

    productLogPath = tmp_path / "product-records.json"
    productLog = json.loads(productLogPath.read_text(encoding="utf-8"))
    activity = productLog["products"][productId]["printActivity"]
    assert activity["latestPrintJobId"] == printJobId

    jobEntry = activity["printJobs"][printJobId]
    assert jobEntry["lastPrintedAt"] == generatedTimestamp
    assert activity["latestPrintedAt"] == generatedTimestamp
    datetime.fromisoformat(activity["latestPrintedAt"])

    database.close()
