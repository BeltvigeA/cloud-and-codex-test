import logging
import time
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


def installRequestsStub() -> None:
    requestsModule = types.ModuleType("requests")

    class DummyResponse:  # pragma: no cover - minimal stub
        def __init__(self, url: str = "", headers: dict | None = None):
            self.url = url
            self.headers = headers or {}

    class DummySession:  # pragma: no cover - minimal stub
        def get(self, *_args, **_kwargs):
            raise NotImplementedError

        def post(self, *_args, **_kwargs):
            raise NotImplementedError

    class DummyRequestException(Exception):
        pass

    requestsModule.Response = DummyResponse
    requestsModule.Session = DummySession
    requestsModule.RequestException = DummyRequestException
    requestsModule.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(NotImplementedError())

    sys.modules.setdefault("requests", requestsModule)


installRequestsStub()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import client  # noqa: E402
from client import gui  # noqa: E402


def testParseArgumentsUsesProductionBaseUrlByDefault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "fetch",
            "--mode",
            "remote",
            "--fetchToken",
            "tokenValue",
            "--outputDir",
            str(tmp_path),
        ],
    )

    arguments = client.parseArguments()

    assert arguments.baseUrl == client.defaultBaseUrl
    assert client.validateRemoteFetchArguments(arguments)


def testFetchCommandDefaultsOutputDirectory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "fetch",
            "--mode",
            "remote",
            "--fetchToken",
            "tokenValue",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.outputDir == str(client.defaultFilesDirectory)


def testStatusCommandDefaultsToProductionBaseUrl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "status",
            "--apiKey",
            "testKey",
            "--printerSerial",
            "printer123",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.baseUrl == client.defaultBaseUrl
    assert arguments.recipientId is None


def testStatusCommandAcceptsRecipientId(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "status",
            "--apiKey",
            "testKey",
            "--printerSerial",
            "printer123",
            "--recipientId",
            "recipient-42",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.recipientId == "recipient-42"


def testValidateRemoteListenAcceptsDefaultBaseUrl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "listen",
            "--mode",
            "remote",
            "--recipientId",
            "recipientValue",
            "--outputDir",
            str(tmp_path),
        ],
    )

    arguments = client.parseArguments()

    assert arguments.baseUrl == client.defaultBaseUrl
    assert client.validateRemoteListenArguments(arguments)


def testListenCommandDefaultsOutputDirectory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "listen",
            "--mode",
            "remote",
            "--recipientId",
            "recipientValue",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.outputDir == str(client.defaultFilesDirectory)


@pytest.mark.parametrize(
    ("baseUrl", "expected"),
    [
        ("example.com", "https://example.com"),
        ("https://api.example.com", "https://api.example.com"),
    ],
)
def testValidateRemoteFetchAcceptsBareAndQualifiedBaseUrls(
    baseUrl: str, expected: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "fetch",
            "--mode",
            "remote",
            "--baseUrl",
            baseUrl,
            "--fetchToken",
            "tokenValue",
            "--outputDir",
            str(tmp_path),
        ],
    )

    arguments = client.parseArguments()

    assert client.validateRemoteFetchArguments(arguments)
    assert (
        client.buildFetchUrl(arguments.baseUrl, "tokenValue")
        == f"{expected}/fetch/tokenValue"
    )


def testFetchPendingFilesForwardsBaseUrl(monkeypatch: pytest.MonkeyPatch) -> None:
    capturedCalls: Dict[str, Any] = {}

    def fakeBuildPendingUrl(baseUrl: str, recipientId: str) -> str:
        capturedCalls["builtUrl"] = (baseUrl, recipientId)
        return "https://example.com/recipients/recipient-xyz/pending"

    def fakeListPending(
        recipientId: str,
        *,
        baseUrl: str,
        apiKey: str | None = None,
    ) -> List[Dict[str, Any]]:
        capturedCalls["listPending"] = {
            "recipientId": recipientId,
            "baseUrl": baseUrl,
            "apiKey": apiKey,
        }
        return [{"jobId": "job-1"}]

    monkeypatch.setattr(client, "buildPendingUrl", fakeBuildPendingUrl)
    monkeypatch.setattr(client, "listPending", fakeListPending)

    result = client.fetchPendingFiles("https://example.com", " recipient-xyz ", statusApiKey="secret")

    assert result == [{"jobId": "job-1"}]
    assert capturedCalls == {
        "builtUrl": ("https://example.com", "recipient-xyz"),
        "listPending": {
            "recipientId": "recipient-xyz",
            "baseUrl": "https://example.com",
            "apiKey": "secret",
        },
    }


@pytest.mark.parametrize(
    ("baseUrl", "expected"),
    [
        ("listener.example.com", "https://listener.example.com"),
        ("http://listener.example.com", "http://listener.example.com"),
    ],
)
def testValidateRemoteListenAcceptsBareAndQualifiedBaseUrls(
    baseUrl: str, expected: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "listen",
            "--mode",
            "remote",
            "--baseUrl",
            baseUrl,
            "--recipientId",
            "recipientValue",
            "--outputDir",
            str(tmp_path),
        ],
    )

    arguments = client.parseArguments()

    assert client.validateRemoteListenArguments(arguments)
    assert (
        client.buildPendingUrl(arguments.baseUrl, "recipientValue")
        == f"{expected}/recipients/recipientValue/pending"
    )


@pytest.mark.parametrize(
    ("baseUrl", "expected"),
    [
        ("status.example.com", "https://status.example.com"),
        ("https://status.example.com", "https://status.example.com"),
    ],
)
def testStatusCommandBuildsEndpointFromBaseUrl(
    baseUrl: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client",
            "status",
            "--baseUrl",
            baseUrl,
            "--apiKey",
            "testKey",
            "--printerSerial",
            "printer123",
        ],
    )

    arguments = client.parseArguments()

    assert client.buildBaseUrl(arguments.baseUrl) == expected
    assert client.getPrinterStatusEndpointUrl(arguments.baseUrl) == f"{expected}/printer-status"
    assert client.getPrinterStatusEndpointUrl(expected) == f"{expected}/printer-status"
    assert arguments.recipientId is None


def testPerformStatusUpdatesUsesProvidedBaseUrl(monkeypatch: pytest.MonkeyPatch) -> None:
    postedUrls: list[str] = []

    class DummyResponse:
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:  # pragma: no cover - no-op
            return None

    def fakePost(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> DummyResponse:
        postedUrls.append(url)
        return DummyResponse()

    monkeypatch.setattr(client.requests.Session, "post", fakePost, raising=False)
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)

    client.performStatusUpdates(
        "printer-status.example.com",
        "api-key",
        "printer-123",
        intervalSeconds=0,
        numUpdates=1,
    )

    assert postedUrls == ["https://printer-status.example.com/printer-status"]


def testGenerateStatusPayloadIncludesRecipientId() -> None:
    payload, _ = client.generateStatusPayload(
        "printer-1",
        iteration=0,
        currentJobId=None,
        recipientId="recipient-55",
    )

    assert "printerIpAddress" in payload
    assert payload["recipientId"] == "recipient-55"
    assert payload["printerSerial"] == "printer-1"
    assert payload["accessCode"] == "PCODE6789"
    timestampValue = payload["lastUpdateTimestamp"]
    assert timestampValue.endswith("Z")
    parsedTimestamp = datetime.strptime(timestampValue, "%Y-%m-%dT%H:%M:%SZ")
    assert parsedTimestamp.tzinfo is None


def testAddPrinterIdentityToPayloadInsertsSerialAndAccessCode() -> None:
    payload = {"status": "printing"}

    result = gui.addPrinterIdentityToPayload(payload, "SN-001", "AC-002")

    assert result["status"] == "printing"
    assert result["printerSerial"] == "SN-001"
    assert result["accessCode"] == "AC-002"


def testValidateBaseUrlArgumentLogsErrorForEmptyInput(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)

    assert not client.validateBaseUrlArgument("   ", "fetch")
    assert "Missing required options for remote fetch: --baseUrl" in caplog.text


class DummyDownloadResponse:
    def __init__(self, url: str, body: bytes = b"payload"):
        self.url = url
        self.headers: dict[str, str] = {}
        self._body = body

    def iter_content(self, chunk_size: int = 8192):  # pragma: no cover - simple iterator
        chunkSize = chunk_size
        if chunkSize >= 0:
            yield self._body


def testSaveDownloadedFileStripsQueryParameters(tmp_path: Path) -> None:
    downloadResponse = DummyDownloadResponse("https://example.com/files/document.pdf?token=value#section")

    savedPath = client.saveDownloadedFile(downloadResponse, tmp_path)

    assert savedPath.name == "document.pdf"
    assert savedPath.read_bytes() == b"payload"


def testListenRequestsFreshDownloadWhenCachedFileMissing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    databasePath = tmp_path / "listener.db"
    downloadsDir = tmp_path / "downloads"
    downloadsDir.mkdir()
    database = client.LocalDatabase(databasePath)

    cachedFilePath = downloadsDir / "missing.gcode"
    cachedFilePath.write_text("old-data", encoding="utf-8")
    database.upsertProductRecord(
        "product-123",
        fileName="missing.gcode",
        downloaded=True,
        downloadedFilePath=str(cachedFilePath.resolve()),
        requestTimestamp="2025-08-01T12:00:00",
    )
    cachedFilePath.unlink()

    recordedRequestModes: list[str] = []

    def fakeFetchPendingFiles(_baseUrl: str, _recipientId: str) -> list[dict[str, object]]:
        return [
            {
                "fetchToken": "token-xyz",
                "productId": "product-123",
                "originalFilename": "missing.gcode",
            }
        ]

    newFilePath = downloadsDir / "missing.gcode"

    def fakePerformFetch(
        baseUrl: str,
        fetchToken: str,
        outputDir: str,
        *,
        requestMode: str,
        database: client.LocalDatabase,
        productId: str,
    ) -> dict[str, object]:
        recordedRequestModes.append(requestMode)
        newFilePath.write_text("new-data", encoding="utf-8")
        updatedRecord = database.upsertProductRecord(
            productId,
            newFilePath.name,
            downloaded=True,
            downloadedFilePath=str(newFilePath.resolve()),
        )
        return {
            "savedFile": str(newFilePath.resolve()),
            "fileName": newFilePath.name,
            "requestMode": requestMode,
            "productRecord": updatedRecord,
            "printJobId": None,
            "timestamp": time.time(),
            "source": baseUrl,
            "fetchToken": fetchToken,
            "unencryptedData": {},
            "decryptedData": {},
        }

    monkeypatch.setattr(client, "fetchPendingFiles", fakeFetchPendingFiles)
    monkeypatch.setattr(client, "performFetch", fakePerformFetch)

    logFilePath = tmp_path / "listener-log.json"
    client.listenForFiles(
        "https://example.com",
        "recipient-1",
        str(downloadsDir),
        pollInterval=0,
        maxIterations=1,
        logFilePath=logFilePath,
        database=database,
    )

    assert recordedRequestModes == ["full"]
    updatedRecord = database.findProductById("product-123")
    assert updatedRecord is not None
    assert updatedRecord["downloaded"] is True
    assert updatedRecord["downloadedFilePath"] == str(newFilePath.resolve())

    database.close()
