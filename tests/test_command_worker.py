from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

import pytest
from requests import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.command_controller import CommandWorker


class DummyResponse:
    def __init__(self, payload: Any, statusCode: int = 200) -> None:
        self._payload = payload
        self.status_code = statusCode
        self.content = b"" if payload is None else b"{}"
        if isinstance(payload, (dict, list)):
            self.content = b"{}"

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(response=self)


def testPollCommandsUsesGet(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    def fakeGet(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 0.0):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return DummyResponse({"commands": [{"commandId": "cmd-1"}]})

    monkeypatch.setattr("client.command_controller.requests.get", fakeGet)

    worker = CommandWorker(
        serial="SERIAL123",
        ipAddress="192.168.1.10",
        accessCode="abcd",
        apiKey="api-key",
        recipientId="recipient-1",
        baseUrl="https://example.com",
    )

    commands = worker._pollCommands()

    assert commands == [{"commandId": "cmd-1"}]
    assert captured["url"] == "https://example.com/control"
    assert captured["params"] == {
        "recipientId": "recipient-1",
        "printerSerial": "SERIAL123",
        "printerIpAddress": "192.168.1.10",
    }
    assert captured["headers"]["X-API-Key"] == "api-key"


def testProcessHeatCommandAcknowledgesAndReports(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = CommandWorker(
        serial="SERIAL321",
        ipAddress="10.0.0.9",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-2",
        baseUrl="https://example.com",
    )

    monkeypatch.setattr("client.command_controller._reserveCommand", lambda commandId: True)
    finalized: List[tuple[str, str]] = []
    monkeypatch.setattr("client.command_controller._finalizeCommand", lambda commandId, status: finalized.append((commandId, status)))

    ackCalls: List[tuple[str, str]] = []
    resultCalls: List[Dict[str, Any]] = []

    def fakeAck(self: CommandWorker, commandId: str, status: str) -> bool:
        ackCalls.append((commandId, status))
        return True

    def fakeResult(
        self: CommandWorker,
        commandId: str,
        status: str,
        *,
        message: Optional[str] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        resultCalls.append(
            {
                "commandId": commandId,
                "status": status,
                "message": message,
                "errorMessage": errorMessage,
            }
        )

    monkeypatch.setattr(CommandWorker, "_sendCommandAck", fakeAck)
    monkeypatch.setattr(CommandWorker, "_sendCommandResult", fakeResult)

    class FakePrinter:
        def __init__(self) -> None:
            self.nozzleCalls: List[float] = []
            self.bedCalls: List[float] = []

        def set_nozzle_temperature(self, value: float) -> None:
            self.nozzleCalls.append(value)

        def set_bed_temperature(self, value: float) -> None:
            self.bedCalls.append(value)

    fakePrinter = FakePrinter()
    monkeypatch.setattr(CommandWorker, "_connectPrinter", lambda self: fakePrinter)

    worker._processCommand(
        {
            "commandId": "heat-1",
            "commandType": "heat",
            "metadata": {"nozzleTemp": 215, "bedTemp": 65},
        }
    )

    assert fakePrinter.nozzleCalls == [215]
    assert fakePrinter.bedCalls == [65]
    assert ackCalls == [("heat-1", "processing")]
    assert finalized == [("heat-1", "completed")]
    assert resultCalls[0]["status"] == "completed"
    assert "Heating" in (resultCalls[0]["message"] or "")


def testProcessUnsupportedCommandReportsFailure(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = CommandWorker(
        serial="SERIAL999",
        ipAddress="10.0.0.20",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-3",
        baseUrl="https://example.com",
    )

    monkeypatch.setattr("client.command_controller._reserveCommand", lambda commandId: True)
    finalized: List[tuple[str, str]] = []
    monkeypatch.setattr("client.command_controller._finalizeCommand", lambda commandId, status: finalized.append((commandId, status)))

    monkeypatch.setattr(CommandWorker, "_connectPrinter", lambda self: object())
    monkeypatch.setattr(CommandWorker, "_sendCommandAck", lambda self, commandId, status: True)

    resultCalls: List[Dict[str, Any]] = []

    def fakeResult(
        self: CommandWorker,
        commandId: str,
        status: str,
        *,
        message: Optional[str] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        resultCalls.append(
            {
                "commandId": commandId,
                "status": status,
                "message": message,
                "errorMessage": errorMessage,
            }
        )

    monkeypatch.setattr(CommandWorker, "_sendCommandResult", fakeResult)

    worker._processCommand({"commandId": "bad", "commandType": "unsupported"})

    assert finalized == [("bad", "failed")]
    assert resultCalls and resultCalls[0]["status"] == "failed"
    assert "Unsupported" in (resultCalls[0]["errorMessage"] or "")

