import logging
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
