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
