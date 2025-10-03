import json
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


def test_upsertProductRecord_creates_product_files(tmp_path: Path) -> None:
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

    productDir = tmp_path / "products" / "product-7"
    metadataPath = productDir / "metadata.json"
    requestsPath = productDir / "requests.json"
    activityPath = productDir / "print-activity.json"

    assert metadataPath.exists()
    assert requestsPath.exists()
    assert activityPath.exists()

    metadata = json.loads(metadataPath.read_text(encoding="utf-8"))
    assert metadata["productId"] == "product-7"
    assert metadata["createdAt"] == firstTimestamp
    assert metadata["lastRequestedAt"] == firstTimestamp
    assert metadata["fileLocation"] == "initial.gcode"

    secondTimestamp = "2024-06-06T06:06:06"
    database.upsertProductRecord(
        "product-7",
        fileName="finalized.gcode",
        downloaded=True,
        requestTimestamp=secondTimestamp,
        printJobId="job-2",
    )

    metadata = json.loads(metadataPath.read_text(encoding="utf-8"))
    assert metadata["productId"] == "product-7"
    assert metadata["createdAt"] == firstTimestamp
    assert metadata["lastRequestedAt"] == secondTimestamp
    assert metadata["fileLocation"] == "finalized.gcode"

    requests = json.loads(requestsPath.read_text(encoding="utf-8"))
    assert requests == [firstTimestamp, secondTimestamp]

    activity = json.loads(activityPath.read_text(encoding="utf-8"))
    assert activity["productId"] == "product-7"
    assert activity["latestPrintJobId"] == "job-2"
    assert activity["latestPrintedAt"] == secondTimestamp
    assert activity["printJobs"] == {
        "job-1": {"lastPrintedAt": firstTimestamp},
        "job-2": {"lastPrintedAt": secondTimestamp},
    }

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

    productDir = tmp_path / "products" / "product-88"
    activityPath = productDir / "print-activity.json"

    activity = json.loads(activityPath.read_text(encoding="utf-8"))
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

    updatedActivity = json.loads(activityPath.read_text(encoding="utf-8"))
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

    activityPath = tmp_path / "products" / "product-100" / "print-activity.json"
    assert activityPath.exists()

    activity = json.loads(activityPath.read_text(encoding="utf-8"))
    assert activity["productId"] == "product-100"
    assert activity["printJobs"] == {}
    assert activity["latestPrintedAt"] == requestTimestamp
    assert "latestPrintJobId" not in activity

    database.close()


def testUpsertProductRecordExtendsActivityHistory(tmp_path: Path) -> None:
    databasePath = tmp_path / "history.db"
    database = LocalDatabase(databasePath)

    initialTimestamp = "2027-02-02T02:02:02"
    database.upsertProductRecord(
        "product-200",
        requestTimestamp=initialTimestamp,
        printJobId=None,
    )

    secondTimestamp = "2027-02-02T03:03:03"
    database.upsertProductRecord(
        "product-200",
        requestTimestamp=secondTimestamp,
        printJobId="job-xyz",
    )

    activityPath = tmp_path / "products" / "product-200" / "print-activity.json"
    activity = json.loads(activityPath.read_text(encoding="utf-8"))

    assert activity["productId"] == "product-200"
    assert activity["latestPrintJobId"] == "job-xyz"
    assert activity["latestPrintedAt"] == secondTimestamp
    assert activity["printJobs"] == {
        "job-xyz": {"lastPrintedAt": secondTimestamp}
    }

    database.close()
