import json
from pathlib import Path
from typing import Dict

import pytest

from client.persistence import storePrintSummary


def testStorePrintSummaryCreatesFile(tmp_path: Path) -> None:
    summaryData: Dict[str, object] = {
        "timestamp": 123.456,
        "fetchToken": "token-one",
        "source": "https://base44.com",
        "fileName": "metadata.json",
        "requestMode": "metadata",
        "printJobId": "job-one",
        "productId": "product-one",
        "unencryptedData": {"printer": "alpha"},
        "decryptedData": {"status": "queued"},
        "unexpected": "ignored",
    }

    summaryPath = storePrintSummary(summaryData, baseDirectory=tmp_path)

    assert summaryPath.exists()
    with summaryPath.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert isinstance(payload, list)
    assert len(payload) == 1
    storedSummary = payload[0]
    assert storedSummary["fetchToken"] == "token-one"
    assert storedSummary["source"] == "https://base44.com"
    assert storedSummary["fileName"] == "metadata.json"
    assert storedSummary["requestMode"] == "metadata"
    assert storedSummary["printJobId"] == "job-one"
    assert storedSummary["productId"] == "product-one"
    assert storedSummary["unencryptedData"] == {"printer": "alpha"}
    assert storedSummary["decryptedData"] == {"status": "queued"}
    assert "unexpected" not in storedSummary
    assert storedSummary["timestamp"] == pytest.approx(123.456)


def testStorePrintSummaryAppendsSafely(tmp_path: Path) -> None:
    firstSummary: Dict[str, object] = {
        "timestamp": 1.0,
        "fetchToken": "token-first",
        "source": "https://base44.com",
        "requestMode": "metadata",
        "fileName": "first.json",
    }
    secondSummary: Dict[str, object] = {
        "timestamp": 2.0,
        "fetchToken": "token-second",
        "source": "https://base44.com",
        "requestMode": "metadata",
        "fileName": "second.json",
    }

    summaryPath = storePrintSummary(firstSummary, baseDirectory=tmp_path)
    summaryPath = storePrintSummary(secondSummary, baseDirectory=tmp_path)

    with summaryPath.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert isinstance(payload, list)
    assert [entry["fetchToken"] for entry in payload] == [
        "token-first",
        "token-second",
    ]


