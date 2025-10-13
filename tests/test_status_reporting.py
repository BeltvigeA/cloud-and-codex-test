import logging
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


class DummyResponse:
    def __init__(self, statusCode: int = 200, text: str = "ok") -> None:
        self.status_code = statusCode
        self.text = text


def testBuildBase44StatusPayloadMapsFields(monkeypatch: pytest.MonkeyPatch) -> None:
    status: Dict[str, Any] = {
        "status": "Printing",
        "progress": 42.6,
        "bedTemp": 60.4,
        "nozzleTemp": 200.9,
        "fanSpeed": 80,
        "printSpeed": 150,
        "filamentUsed": 12.3,
        "timeRemaining": 900,
        "firmware": "1.2.3",
        "ip": "192.168.0.55",
    }
    printerConfig = {
        "statusRecipientId": "recipient-1",
        "printerId": "printer-xyz",
    }

    payload = bambuPrinter.buildBase44StatusPayload(status, printerConfig)

    assert payload is not None
    assert payload["recipientId"] == "recipient-1"
    assert payload["printerIpAddress"] == "192.168.0.55"
    assert payload["printerId"] == "printer-xyz"
    assert payload["status"] == "printing"
    assert payload["jobProgress"] == 43
    assert payload["bedTemp"] == 60
    assert payload["nozzleTemp"] == 200
    assert payload["firmwareVersion"] == "1.2.3"
    assert payload["fanSpeed"] == pytest.approx(80.0)
    assert payload["printSpeed"] == pytest.approx(150.0)
    assert payload["filamentUsed"] == pytest.approx(12.3)
    assert payload["timeRemaining"] == 900
    assert payload["lastUpdateTimestamp"].endswith("Z")


def testPostStatusSendsRequestWithHeaders(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured: Dict[str, Any] = {}

    def fakePost(url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> DummyResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return DummyResponse(statusCode=202, text="accepted")

    monkeypatch.setattr(bambuPrinter.requests, "post", fakePost)

    status = {"status": "idle", "ip": "10.0.0.12"}
    printerConfig = {
        "statusBaseUrl": "https://example.com/status",
        "statusApiKey": "secret-token",
        "statusRecipientId": "recipient-2",
    }

    caplog.set_level(logging.INFO)
    bambuPrinter.postStatus(status, printerConfig)

    assert captured["url"] == "https://example.com/status"
    assert captured["headers"]["X-API-Key"] == "secret-token"
    assert captured["json"]["recipientId"] == "recipient-2"
    assert captured["json"]["status"] == "idle"
    assert captured["json"]["printerIpAddress"] == "10.0.0.12"
    assert any("[POST]" in record.message for record in caplog.records)


def testPostStatusFallsBackToEnvironment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def fakePost(url: str, headers: Dict[str, str], json: Dict[str, Any], timeout: int) -> DummyResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr(bambuPrinter.requests, "post", fakePost)
    monkeypatch.setenv("BASE44_STATUS_URL", "https://env.example.com/status")
    monkeypatch.setenv("PRINTER_API_TOKEN", "env-token")
    monkeypatch.setenv("RECIPIENT_ID", "env-recipient")

    bambuPrinter.postStatus({}, {})

    assert captured["url"] == "https://env.example.com/status"
    assert captured["headers"]["X-API-Key"] == "env-token"
    assert captured["json"]["recipientId"] == "env-recipient"
    assert captured["json"]["status"] == "idle"
