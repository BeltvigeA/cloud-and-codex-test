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

    statusCalls: List[Dict[str, Any]] = []

    def fakeSendStatus(_baseUrl: str, _productId: str, payload: Dict[str, Any]) -> bool:
        statusCalls.append(json.loads(json.dumps(payload)))
        return True

    monkeypatch.setattr(client, "sendProductStatusUpdate", fakeSendStatus)

    outputDir = tmp_path / "output"
    outputDir.mkdir()

    client.listenForFiles(
        "https://example.com",
        "recipient-positive",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        database=database,
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
    assert statusCalls and statusCalls[0]["requestedMode"] == "full"


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

    statusCalls: List[Dict[str, Any]] = []

    def fakeSendStatus(_baseUrl: str, _productId: str, payload: Dict[str, Any]) -> bool:
        statusCalls.append(payload)
        return True

    monkeypatch.setattr(client, "sendProductStatusUpdate", fakeSendStatus)

    outputDir = tmp_path / "negative-output"
    outputDir.mkdir()

    client.listenForFiles(
        "https://example.com",
        "recipient-negative",
        str(outputDir),
        pollInterval=0,
        maxIterations=1,
        database=database,
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
    assert statusCalls == []
