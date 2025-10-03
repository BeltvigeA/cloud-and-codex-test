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
    )

    productDir = tmp_path / "products" / "product-7"
    metadataPath = productDir / "metadata.json"
    requestsPath = productDir / "requests.json"

    assert metadataPath.exists()
    assert requestsPath.exists()

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
    )

    metadata = json.loads(metadataPath.read_text(encoding="utf-8"))
    assert metadata["productId"] == "product-7"
    assert metadata["createdAt"] == firstTimestamp
    assert metadata["lastRequestedAt"] == secondTimestamp
    assert metadata["fileLocation"] == "finalized.gcode"

    requests = json.loads(requestsPath.read_text(encoding="utf-8"))
    assert requests == [firstTimestamp, secondTimestamp]

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

    productDir = tmp_path / "products" / "product-9"
    metadataPath = productDir / "metadata.json"
    requestsPath = productDir / "requests.json"

    metadata = json.loads(metadataPath.read_text(encoding="utf-8"))
    assert metadata["lastRequestedAt"] == initialTimestamp

    requests = json.loads(requestsPath.read_text(encoding="utf-8"))
    assert requests == [initialTimestamp]

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
