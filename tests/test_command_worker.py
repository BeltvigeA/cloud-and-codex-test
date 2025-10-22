from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

import pytest
from requests import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.command_controller import CommandWorker, _normalizeCommandMetadata
from client.gui import ListenerGuiApp


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


def testCommandWorkerReceivesControlApiKey(monkeypatch: pytest.MonkeyPatch) -> None:
    app = ListenerGuiApp.__new__(ListenerGuiApp)

    class DummyVar:
        def __init__(self, value: bool) -> None:
            self._value = value

        def get(self) -> bool:
            return self._value

    class DummyValue:
        def __init__(self, value: str) -> None:
            self._value = value

        def get(self) -> str:
            return self._value

    class DummyThread:
        def is_alive(self) -> bool:
            return True

    app.liveStatusEnabledVar = DummyVar(True)
    app.listenerThread = DummyThread()
    app.listenerStatusApiKey = "status-key"
    app.listenerControlApiKey = "control-key"
    app.listenerRecipientId = "recipient-123"
    app.baseUrlVar = DummyValue("https://example.com")
    app.pollIntervalVar = DummyValue("5")
    app.commandWorkers = {}
    app.log = lambda message: None
    app._applyBase44Environment = lambda: None
    app._collectActiveLanPrinters = lambda: [
        {
            "serialNumber": "SERIAL123",
            "ipAddress": "192.168.1.10",
            "accessCode": "abcd",
            "nickname": "Printer",
        }
    ]

    captured: Dict[str, Any] = {}

    class FakeWorker:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.serial = kwargs.get("serial", "")

        def start(self) -> None:  # pragma: no cover - trivial behavior
            captured["started"] = True

        def stop(self) -> None:  # pragma: no cover - not used in test
            pass

    monkeypatch.setattr("client.gui.CommandWorker", FakeWorker)

    app._startCommandWorkers()

    assert captured["apiKey"] == "control-key"
    assert captured["recipientId"] == "recipient-123"


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


def testCollectAndReportBambuError(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = CommandWorker(
        serial="SERIAL-ERR",
        ipAddress="10.0.0.30",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-err",
        baseUrl="https://example.com",
    )

    class DummyClient:
        def print_error_code(self) -> str:
            return "HMS_0102-0001"

    class DummyPrinter:
        def __init__(self) -> None:
            self.mqtt_client = DummyClient()

        def print_error_code(self) -> str:
            return "HMS_0102-0001"

    captured: Dict[str, Any] = {}

    def fakeReport(payload: Dict[str, Any]) -> Dict[str, Any]:
        captured.update(payload)
        return payload

    monkeypatch.setattr("client.command_controller.postReportError", fakeReport)

    worker._lastStatus = {"mc_percent": 42, "gcode_state": "printing"}
    worker._lastRawStatus = {"gcode_state": "printing"}

    worker._collectAndReportBambuError(DummyPrinter(), {"event": {"source": "test"}})

    assert captured["printerSerial"] == "SERIAL-ERR"
    assert captured["errorCode"] == "HMS_0102-0001"
    assert captured["gcodeState"] == "printing"


def testHandlePrinterStatusTracksProgressAndCompletion(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = CommandWorker(
        serial="SERIAL-PROG",
        ipAddress="10.0.0.31",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-prog",
        baseUrl="https://example.com",
    )

    deleted: List[str] = []

    def fakeDelete(_printer: Any, remotePath: str) -> bool:
        deleted.append(remotePath)
        return True

    monkeypatch.setattr("client.command_controller.bambuPrinter.deleteRemoteFile", fakeDelete)

    statuses = [
        {"mc_percent": 0, "gcode_state": "prepare"},
        {"mc_percent": 12, "gcode_state": "printing"},
        {"mc_percent": 47, "gcode_state": "printing"},
        {"mc_percent": 100, "gcode_state": "finish", "remoteFile": "sdcard/test.3mf"},
    ]

    worker._printerInstance = object()

    for payload in statuses:
        worker._handlePrinterStatus(payload)

    assert worker._copyLastStatus().get("mc_percent") == 100
    assert worker._jobActive is False
    assert deleted == ["sdcard/test.3mf"]


def testRecipientModeProcessesEnqueuedCommands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_POLL_MODE", "recipient")

    processed: List[Dict[str, Any]] = []

    class FakeRouter:
        def __init__(self) -> None:
            self.registered: Optional[CommandWorker] = None
            self.unregistered: List[str] = []

        def registerWorker(self, worker: CommandWorker) -> None:
            self.registered = worker

        def unregisterWorker(self, serial: str) -> None:
            self.unregistered.append(serial)

    fakeRouter = FakeRouter()
    monkeypatch.setattr(
        "client.command_controller._registerRecipientRouter",
        lambda recipientId, pollInterval: fakeRouter,
    )

    worker = CommandWorker(
        serial="SERIAL-R",
        ipAddress="10.0.0.30",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-R",
        baseUrl="https://example.com",
        pollInterval=0.05,
    )

    def fakeProcess(self: CommandWorker, command: Dict[str, Any]) -> None:
        processed.append(command)
        self._stopEvent.set()

    monkeypatch.setattr(CommandWorker, "_processCommand", fakeProcess)

    worker.start()
    worker.enqueueCommand({"commandId": "cmd-recipient"})
    time.sleep(0.2)
    worker.stop()

    assert processed and processed[0]["commandId"] == "cmd-recipient"
    assert fakeRouter.registered is worker
    assert "SERIAL-R" in fakeRouter.unregistered


def testNormalizeCommandMetadataParsesJson() -> None:
    command = {"commandId": "cmd-json", "metadata": "{\"printerSerial\": \"SER-JSON\"}"}
    metadata = _normalizeCommandMetadata(command)
    assert isinstance(metadata, dict)
    assert metadata.get("printerSerial") == "SER-JSON"
    assert isinstance(command["metadata"], dict)


def testRecipientModeResultMapsSuccessStatuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_POLL_MODE", "recipient")

    captured: List[tuple[str, str, Optional[str], Optional[str]]] = []

    def fakePostResult(
        commandId: str,
        status: str,
        message: Optional[str] = None,
        errorMessage: Optional[str] = None,
    ) -> None:
        captured.append((commandId, status, message, errorMessage))

    monkeypatch.setattr("client.command_controller.postCommandResult", fakePostResult)

    worker = CommandWorker(
        serial="SER-SUCCESS",
        ipAddress="10.0.0.40",
        accessCode="code",
        apiKey="api-key",
        recipientId="recipient-success",
        baseUrl="https://example.com",
    )

    for status in ["completed", "success", "ok", "done", "failed"]:
        worker._sendCommandResult(f"cmd-{status}", status, message="details")

    worker._sendCommandResult("cmd-error", "error", errorMessage="boom")

    assert captured[:4] == [
        ("cmd-completed", "completed", "details", None),
        ("cmd-success", "completed", "details", None),
        ("cmd-ok", "completed", "details", None),
        ("cmd-done", "completed", "details", None),
    ]
    assert captured[4] == ("cmd-failed", "failed", "details", None)
    assert captured[5] == ("cmd-error", "failed", None, "boom")

