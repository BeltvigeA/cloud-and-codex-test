from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from client import client


def test_listenForFiles_performs_fetch_when_handshake_requires_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    databasePath = tmp_path / "handshake-positive.db"
    database = client.LocalDatabase(databasePath)

    pendingFiles: List[Dict[str, Any]] = [
        {
            "fetchToken": "token-positive",
            "productId": "product-positive",
            "originalFilename": "positive.gcode",
            "handshake": {"recipientId": "recipient-positive", "printJobId": "job-positive"},
        }
    ]

    monkeypatch.setattr(client, "fetchPendingFiles", lambda *_args, **_kwargs: pendingFiles)

    handshakeCalls: List[Dict[str, Any]] = []

    def fakeSendHandshake(
        baseUrl: str,
        productId: str,
        recipientId: str,
        printJobId: str | None,
        jobExists: bool,
    ) -> Dict[str, Any]:
        handshakeCalls.append(
            {
                "baseUrl": baseUrl,
                "productId": productId,
                "recipientId": recipientId,
                "printJobId": printJobId,
                "jobExists": jobExists,
            }
        )
        return {
            "decision": "full",
            "fetchMode": "full",
            "downloadRequired": True,
            "fetchToken": "token-positive",
            "originalFilename": "positive.gcode",
        }

    monkeypatch.setattr(client, "sendHandshakeResponse", fakeSendHandshake)

    fetchCalls: List[Dict[str, Any]] = []

    def fakePerformFetch(
        baseUrl: str,
        fetchToken: str,
        outputDir: str,
        *,
        requestMode: str,
        database: client.LocalDatabase,
        productId: str,
    ) -> Dict[str, Any]:
        fetchCalls.append(
            {
                "baseUrl": baseUrl,
                "fetchToken": fetchToken,
                "outputDir": outputDir,
                "requestMode": requestMode,
                "productId": productId,
            }
        )
        return {
            "savedFile": str(tmp_path / "positive.gcode"),
            "unencryptedData": {},
            "decryptedData": {},
            "timestamp": 0.0,
            "fetchToken": fetchToken,
            "source": baseUrl,
            "requestMode": requestMode,
            "fileName": "positive.gcode",
            "printJobId": "job-positive",
        }

    monkeypatch.setattr(client, "performFetch", fakePerformFetch)

    outputDir = tmp_path / "output"
    outputDir.mkdir()

    capturedEntries: List[Dict[str, Any]] = []

    client.listenForFiles(
        "https://example.com",
        "recipient-positive",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        database=database,
        onFileFetched=lambda entry: capturedEntries.append(dict(entry)),
    )

    database.close()

    assert handshakeCalls == [
        {
            "baseUrl": "https://example.com",
            "productId": "product-positive",
            "recipientId": "recipient-positive",
            "printJobId": "job-positive",
            "jobExists": False,
        }
    ]
    assert fetchCalls and fetchCalls[0]["requestMode"] == "full"
    assert fetchCalls[0]["productId"] == "product-positive"
    assert capturedEntries
    statusPayload = capturedEntries[0]["productStatus"]
    assert statusPayload["recipientId"] == "recipient-positive"
    assert statusPayload["requestedMode"] == "full"
    assert statusPayload["printJobId"] == "job-positive"
    assert statusPayload["message"] == "success"
    assert statusPayload["sent"] is False


def test_listenForFiles_triggers_force_command_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "fetchPendingFiles", lambda *_args, **_kwargs: [])

    forceCalls: List[str] = []

    def fakeForcePoll(recipientId: str) -> None:
        forceCalls.append(recipientId)

    monkeypatch.setattr(client, "_forceRecipientCommandPoll", fakeForcePoll)

    databasePath = tmp_path / "force-command.db"
    database = client.LocalDatabase(databasePath)
    try:
        client.listenForFiles(
            "https://example.com",
            "recipient-force",
            str(tmp_path),
            pollInterval=0,
            maxIterations=1,
            database=database,
        )
    finally:
        database.close()

    assert forceCalls == ["recipient-force"]


def test_listenForFiles_skips_fetch_when_handshake_opts_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    databasePath = tmp_path / "handshake-negative.db"
    database = client.LocalDatabase(databasePath)

    database.upsertProductRecord(
        "product-negative",
        fileName="negative.gcode",
        downloaded=True,
        requestTimestamp="2024-01-01T00:00:00",
        printJobId="job-negative",
    )

    pendingFiles: List[Dict[str, Any]] = [
        {
            "fetchToken": "token-negative",
            "productId": "product-negative",
            "originalFilename": "negative.gcode",
            "handshake": {"recipientId": "recipient-negative", "printJobId": "job-negative"},
        }
    ]

    monkeypatch.setattr(client, "fetchPendingFiles", lambda *_args, **_kwargs: pendingFiles)

    handshakeCalls: List[Dict[str, Any]] = []

    def fakeSendHandshake(
        baseUrl: str,
        productId: str,
        recipientId: str,
        printJobId: str | None,
        jobExists: bool,
    ) -> Dict[str, Any]:
        handshakeCalls.append(
            {
                "baseUrl": baseUrl,
                "productId": productId,
                "recipientId": recipientId,
                "printJobId": printJobId,
                "jobExists": jobExists,
            }
        )
        return {
            "decision": "metadata",
            "fetchMode": "metadata",
            "downloadRequired": False,
            "originalFilename": "negative.gcode",
            "lastRequestTimestamp": "2024-01-02T00:00:00",
        }

    monkeypatch.setattr(client, "sendHandshakeResponse", fakeSendHandshake)

    def fakePerformFetch(*_args, **_kwargs) -> Dict[str, Any]:
        raise AssertionError("performFetch should not be called when handshake opts out")

    monkeypatch.setattr(client, "performFetch", fakePerformFetch)

    outputDir = tmp_path / "negative-output"
    outputDir.mkdir()

    capturedEntries: List[Dict[str, Any]] = []

    client.listenForFiles(
        "https://example.com",
        "recipient-negative",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        database=database,
        onFileFetched=lambda entry: capturedEntries.append(dict(entry)),
    )

    database.close()

    assert handshakeCalls == [
        {
            "baseUrl": "https://example.com",
            "productId": "product-negative",
            "recipientId": "recipient-negative",
            "printJobId": "job-negative",
            "jobExists": True,
        }
    ]
    assert capturedEntries == []


def test_listenForFiles_reports_fetch_errors_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    databasePath = tmp_path / "fetch-error.db"
    database = client.LocalDatabase(databasePath)

    pendingFiles: List[Dict[str, Any]] = [
        {
            "fetchToken": "token-error",
            "productId": "product-error",
            "printJobId": "job-error",
            "originalFilename": "error.gcode",
        }
    ]

    monkeypatch.setattr(client, "fetchPendingFiles", lambda *_args, **_kwargs: pendingFiles)

    monkeypatch.setattr(client, "sendHandshakeResponse", lambda *_args, **_kwargs: None)

    def fakePerformFetch(*_args, **_kwargs) -> Dict[str, Any]:
        raise RuntimeError("simulated transfer failure")

    monkeypatch.setattr(client, "performFetch", fakePerformFetch)

    outputDir = tmp_path / "error-output"
    outputDir.mkdir()
    logFilePath = tmp_path / "error-log.json"

    client.listenForFiles(
        "https://example.com",
        "recipient-error",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        database=database,
        logFilePath=str(logFilePath),
    )

    database.close()

    assert logFilePath.exists()
    logEntries = json.loads(logFilePath.read_text())
    assert logEntries
    statusPayload = logEntries[-1]["productStatus"]
    assert statusPayload["recipientId"] == "recipient-error"
    assert statusPayload["success"] is False
    assert statusPayload["message"] == "simulated transfer failure"
    assert statusPayload["printJobId"] == "job-error"
    assert statusPayload["sent"] is False


def testListenForFilesStoresMetadataSummary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    databasePath = tmp_path / "metadata.db"
    database = client.LocalDatabase(databasePath)

    pendingFiles: List[Dict[str, Any]] = [
        {
            "fetchToken": "token-meta",
            "productId": "product-meta",
            "originalFilename": "meta.json",
        }
    ]

    monkeypatch.setattr(client, "fetchPendingFiles", lambda *_args, **_kwargs: pendingFiles)

    monkeypatch.setattr(
        client,
        "checkProductAvailability",
        lambda *_args, **_kwargs: {
            "status": "metadataCached",
            "shouldRequestFile": False,
            "record": {},
        },
    )

    appendCalls: List[str] = []

    def fakeAppend(*_args, **_kwargs) -> Path:
        appendCalls.append("called")
        raise AssertionError("appendJsonLogEntry should not be used for metadata summaries")

    monkeypatch.setattr(client, "appendJsonLogEntry", fakeAppend)

    def fakePerformFetch(
        baseUrl: str,
        fetchToken: str,
        outputDir: str,
        *,
        requestMode: str,
        database: client.LocalDatabase,
        productId: str,
    ) -> Dict[str, Any]:
        assert requestMode == "metadata"
        return {
            "savedFile": None,
            "unencryptedData": {"summary": "details"},
            "decryptedData": {"note": "metadata"},
            "timestamp": 3.14,
            "fetchToken": fetchToken,
            "source": baseUrl,
            "requestMode": requestMode,
            "fileName": "meta.json",
            "printJobId": "job-meta",
        }

    monkeypatch.setattr(client, "performFetch", fakePerformFetch)

    monkeypatch.setattr(client, "sendHandshakeResponse", lambda *_args, **_kwargs: None)

    outputDir = tmp_path / "metadata-output"
    outputDir.mkdir()

    client.listenForFiles(
        "https://base44.com",
        "recipient-meta",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        logFilePath=str(tmp_path / "listener-log.json"),
        database=database,
    )

    database.close()

    summaryPath = tmp_path / "print-summaries.json"
    assert summaryPath.exists()
    with summaryPath.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert isinstance(payload, list)
    assert len(payload) == 1
    storedSummary = payload[0]
    assert storedSummary["fetchToken"] == "token-meta"
    assert storedSummary["fileName"] == "meta.json"
    assert storedSummary["requestMode"] == "metadata"
    assert storedSummary["printJobId"] == "job-meta"
    assert storedSummary["productId"] == "product-meta"
    assert storedSummary["unencryptedData"] == {"summary": "details"}
    assert storedSummary["decryptedData"] == {"note": "metadata"}
    assert not appendCalls
