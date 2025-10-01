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
    assert arguments.channel is None
    assert arguments.jobLogFile == "pendingJobs.log"
    assert client.validateRemoteListenArguments(arguments)


def testListenCommandAllowsChannelAndJobLog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jobLogPath = tmp_path / "jobs" / "custom.log"
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
            "--channel",
            "alpha",
            "--jobLogFile",
            str(jobLogPath),
        ],
    )

    arguments = client.parseArguments()

    assert arguments.channel == "alpha"
    assert Path(arguments.jobLogFile) == jobLogPath
