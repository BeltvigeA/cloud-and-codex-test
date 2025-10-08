import json
import logging
import time
import sys
import types
from pathlib import Path

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


def testStatusCommandParsesStatusSequence(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--statusSequence",
            "idle,printing",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.statusSequence == ["idle", "printing"]


def testStatusCommandAcceptsStatusValue(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--statusValue",
            "offline",
        ],
    )

    arguments = client.parseArguments()

    assert arguments.statusValue == "offline"


def testStatusCommandRejectsEmptyStatusSequence(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--statusSequence",
            "idle, ",
        ],
    )

    with pytest.raises(SystemExit):
        client.parseArguments()


def testStatusCommandRejectsEmptyStatusValue(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "--statusValue",
            "  ",
        ],
    )

    with pytest.raises(SystemExit):
        client.parseArguments()


def testStatusCommandAcceptsPayloadFile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payloadFile = tmp_path / "payload.json"
    payloadFile.write_text("{}", encoding="utf-8")

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
            "--payloadFile",
            str(payloadFile),
        ],
    )

    arguments = client.parseArguments()

    assert arguments.payloadFile == payloadFile


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
def testStatusCommandBuildsUrlsWithBareAndQualifiedBaseUrls(
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
    assert f"{expected}/printer-status" == f"{client.buildBaseUrl(arguments.baseUrl)}/printer-status"
    assert arguments.recipientId is None


def testGenerateStatusPayloadIncludesRecipientId() -> None:
    payload, _ = client.generateStatusPayload(
        "printer-1",
        iteration=0,
        currentJobId=None,
        recipientId="recipient-55",
    )

    assert payload["recipientId"] == "recipient-55"


def testGenerateStatusPayloadRespectsManualOverrides(monkeypatch: pytest.MonkeyPatch) -> None:
    manualPayload = {"printJobId": "manual-job", "status": "printing", "custom": True}
    originalPayload = dict(manualPayload)
    monkeypatch.setattr(client.time, "time", lambda: 123.456)

    payload, nextJobId = client.generateStatusPayload(
        "printer-override",
        iteration=3,
        currentJobId=None,
        recipientId="recipient-override",
        forcedStatus="finished",
        manualPayload=manualPayload,
    )

    assert manualPayload == originalPayload
    assert payload["printerSerial"] == "printer-override"
    assert payload["status"] == "finished"
    assert payload["printJobId"] == "manual-job"
    assert payload["recipientId"] == "recipient-override"
    assert payload["timestamp"] == 123.456
    assert payload["custom"] is True
    assert "materialLevel" not in payload
    assert nextJobId is None


def testGenerateStatusPayloadFillsMissingPrintJobId(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client.time, "time", lambda: 321.0)

    payload, nextJobId = client.generateStatusPayload(
        "printer-missing-job",
        iteration=1,
        currentJobId=None,
        manualPayload={"status": "idle"},
    )

    assert payload["printJobId"]
    assert nextJobId is None


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

    def fakeSendProductStatusUpdate(
        _baseUrl: str,
        _productId: str,
        _recipientId: str,
        _payload: dict[str, object],
    ) -> bool:
        return True

    monkeypatch.setattr(client, "fetchPendingFiles", fakeFetchPendingFiles)
    monkeypatch.setattr(client, "performFetch", fakePerformFetch)
    monkeypatch.setattr(client, "sendProductStatusUpdate", fakeSendProductStatusUpdate)

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


def testPerformStatusUpdatesUsesStatusSequence(monkeypatch: pytest.MonkeyPatch) -> None:
    forcedStatuses: list[str | None] = []

    class DummyResponse:
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class DummySession:
        def post(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            return DummyResponse()

    def fakeGenerateStatusPayload(
        printerSerial: str,
        iteration: int,
        currentJobId: str | None,
        recipientId: str | None = None,
        *,
        forcedStatus: str | None = None,
        manualPayload: dict | None = None,
    ) -> tuple[dict, str | None]:
        forcedStatuses.append(forcedStatus)
        nextJobId = currentJobId or "job-sequence"
        payload = {"printJobId": nextJobId, "status": forcedStatus or "default"}
        return payload, nextJobId

    monkeypatch.setattr(client.requests, "Session", lambda: DummySession())
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "generateStatusPayload", fakeGenerateStatusPayload)

    client.performStatusUpdates(
        "https://status.example.com",
        "api-key",
        "printer-sequence",
        intervalSeconds=0,
        numUpdates=3,
        statusSequence=["ready", "printing"],
    )

    assert forcedStatuses == ["ready", "printing", "ready"]


def testPerformStatusUpdatesUsesStatusValue(monkeypatch: pytest.MonkeyPatch) -> None:
    forcedStatuses: list[str | None] = []

    class DummyResponse:
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class DummySession:
        def post(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            return DummyResponse()

    def fakeGenerateStatusPayload(
        printerSerial: str,
        iteration: int,
        currentJobId: str | None,
        recipientId: str | None = None,
        *,
        forcedStatus: str | None = None,
        manualPayload: dict | None = None,
    ) -> tuple[dict, str | None]:
        forcedStatuses.append(forcedStatus)
        payload = {"printJobId": currentJobId or "job-value", "status": forcedStatus or "default"}
        return payload, None

    monkeypatch.setattr(client.requests, "Session", lambda: DummySession())
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "generateStatusPayload", fakeGenerateStatusPayload)

    client.performStatusUpdates(
        "https://status.example.com",
        "api-key",
        "printer-value",
        intervalSeconds=0,
        numUpdates=2,
        statusValue="offline",
    )

    assert forcedStatuses == ["offline", "offline"]


def testPerformStatusUpdatesLoadsPayloadTemplate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payloadTemplate = {"status": "printing", "printJobId": "manual-job", "custom": "value"}
    payloadPath = tmp_path / "payload.json"
    payloadPath.write_text(json.dumps(payloadTemplate), encoding="utf-8")

    capturedPayloads: list[dict] = []

    class DummyResponse:
        text = ""
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class DummySession:
        def post(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            return DummyResponse()

    def fakeGenerateStatusPayload(
        printerSerial: str,
        iteration: int,
        currentJobId: str | None,
        recipientId: str | None = None,
        *,
        forcedStatus: str | None = None,
        manualPayload: dict | None = None,
    ) -> tuple[dict, str | None]:
        capturedPayloads.append(manualPayload or {})
        payload = {
            "printJobId": (manualPayload or {}).get("printJobId", "job-template"),
            "status": (manualPayload or {}).get("status", forcedStatus or "idle"),
        }
        return payload, None

    monkeypatch.setattr(client.requests, "Session", lambda: DummySession())
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "generateStatusPayload", fakeGenerateStatusPayload)

    client.performStatusUpdates(
        "https://status.example.com",
        "api-key",
        "printer-template",
        intervalSeconds=0,
        numUpdates=2,
        payloadFile=payloadPath,
    )

    assert len(capturedPayloads) == 2
    assert capturedPayloads[0] == payloadTemplate
    assert capturedPayloads[1] == payloadTemplate
    assert capturedPayloads[0] is not capturedPayloads[1]


def testPerformStatusUpdatesRaisesForMalformedPayload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payloadPath = tmp_path / "payload.json"
    payloadPath.write_text("{invalid", encoding="utf-8")

    class DummySession:
        def post(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            raise AssertionError("post should not be called when payload is invalid")

    monkeypatch.setattr(client.requests, "Session", lambda: DummySession())

    with pytest.raises(ValueError):
        client.performStatusUpdates(
            "https://status.example.com",
            "api-key",
            "printer-template",
            intervalSeconds=0,
            numUpdates=1,
            payloadFile=payloadPath,
        )
