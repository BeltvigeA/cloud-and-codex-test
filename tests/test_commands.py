from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
import logging
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import commands  # noqa: E402
from client.base44Status import Base44StatusReporter  # noqa: E402
from client.gui import ListenerGuiApp  # noqa: E402


def test_list_pending_commands_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    capturedRequests: List[Tuple[str, Dict[str, Any]]] = []

    class FakeResponse:
        def __init__(self, payload: Dict[str, Any]):
            self._payload = payload
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return self._payload

    def fakePost(url: str, *, json: Dict[str, Any], headers: Dict[str, Any], timeout: int) -> FakeResponse:  # type: ignore[override]
        capturedRequests.append((url, json))
        return FakeResponse(
            {
                "ok": True,
                "commands": [
                    {"commandId": "cmd-123", "commandType": "poke", "printerIpAddress": "10.0.0.1"}
                ],
            }
        )

    monkeypatch.setattr(commands.requests, "post", fakePost)
    monkeypatch.setenv("BASE44_BASE", "https://example.com/api")
    monkeypatch.setenv("BASE44_RECIPIENT_ID", "recipient-xyz")

    result = commands.listPendingCommands()

    assert isinstance(result, list)
    assert result and result[0]["commandId"] == "cmd-123"
    assert capturedRequests[0][0] == "https://example.com/api/listPendingCommands"
    assert capturedRequests[0][1]["recipientId"] == "recipient-xyz"


def test_list_pending_commands_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fakePost(*_args: Any, **_kwargs: Any) -> Any:
        raise commands.requests.RequestException("boom")

    monkeypatch.setattr(commands.requests, "post", fakePost)
    monkeypatch.setenv("BASE44_BASE", "https://example.com/api")
    monkeypatch.setenv("BASE44_RECIPIENT_ID", "recipient-xyz")

    result = commands.listPendingCommands()

    assert result is None


def test_complete_command_marks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    capturedPayloads: List[Dict[str, Any]] = []

    class FakeResponse:
        def __init__(self, payload: Dict[str, Any]):
            self._payload = payload
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return self._payload

    def fakePost(url: str, *, json: Dict[str, Any], headers: Dict[str, Any], timeout: int) -> FakeResponse:  # type: ignore[override]
        capturedPayloads.append(json)
        return FakeResponse({"ok": True, "status": "completed"})

    monkeypatch.setattr(commands.requests, "post", fakePost)
    monkeypatch.setenv("BASE44_BASE", "https://example.com/api")

    assert commands.completeCommand("cmd-99", True, recipientId="recipient-1") is True
    assert capturedPayloads[0]["commandId"] == "cmd-99"
    assert capturedPayloads[0]["success"] is True
    assert capturedPayloads[0]["error"] is None


def test_status_reporter_handles_poke_command(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots: List[Dict[str, Any]] = [
        {
            "ip": "10.0.0.7",
            "serial": "SN-42",
            "status": "Printing",
            "online": True,
            "progress": 25,
            "bed": 60.0,
            "nozzle": 200.0,
            "timeRemaining": 120,
            "mqttReady": True,
        }
    ]

    postedPayloads: List[Dict[str, Any]] = []
    commandLog: List[Tuple[str, bool, Optional[str]]] = []

    def fakeListPendingCommands(_recipientId: str) -> List[Dict[str, Any]]:
        return [
            {
                "commandId": "cmd-555",
                "commandType": "poke",
                "printerIpAddress": "10.0.0.7",
            }
        ]

    def fakeCompleteCommand(
        commandId: str,
        success: bool,
        *,
        recipientId: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        commandLog.append((commandId, success, error))
        return True

    def fakeCallFunction(
        functionName: str,
        payload: Dict[str, Any],
        *,
        apiKey: Optional[str] = None,
        timeoutSeconds: float = 0,
    ) -> Dict[str, Any]:
        postedPayloads.append(payload)
        return {"ok": True}

    monkeypatch.setattr(commands, "listPendingCommands", fakeListPendingCommands)
    monkeypatch.setattr(commands, "completeCommand", fakeCompleteCommand)
    monkeypatch.setattr("client.base44Status.listPendingCommands", fakeListPendingCommands)
    monkeypatch.setattr("client.base44Status.completeCommand", fakeCompleteCommand)
    monkeypatch.setattr("client.base44Status.callFunction", fakeCallFunction)
    monkeypatch.setattr("client.base44Status.tcpCheck", lambda _ip: True)

    reporter = Base44StatusReporter(lambda: snapshots, intervalSec=60, commandPollIntervalSec=1)
    reporter._recipientId = "recipient-xyz"
    reporter._statusFunctionName = "updatePrinterStatus"
    reporter._commandBackoffSeconds = 1.0
    reporter._nextCommandPollTimestamp = 0.0

    reporter._pollPendingCommands(list(snapshots))

    assert postedPayloads, "Status payload should be posted for poke"
    assert postedPayloads[0]["printerIpAddress"] == "10.0.0.7"
    assert commandLog == [("cmd-555", True, None)]


def test_status_reporter_handles_control_command(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots: List[Dict[str, Any]] = [
        {
            "ip": "10.0.0.7",
            "serial": "SN-42",
            "status": "Idle",
            "online": True,
            "progress": 0,
            "bed": 0.0,
            "nozzle": 0.0,
            "timeRemaining": 0,
            "mqttReady": True,
            "accessCode": "abcd1234",
        }
    ]

    commandLog: List[Tuple[str, bool, Optional[str]]] = []
    executed: List[Tuple[str, str]] = []

    def fakeListPendingCommands(_recipientId: str) -> List[Dict[str, Any]]:
        return [
            {
                "commandId": "cmd-777",
                "commandType": "set_bed_temp",
                "printerIpAddress": "10.0.0.7",
                "metadata": {"target": 60},
            }
        ]

    def fakeCompleteCommand(
        commandId: str,
        success: bool,
        *,
        recipientId: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        commandLog.append((commandId, success, error))
        return True

    class FakeClient:
        def __init__(self, ip: str, accessCode: str, serial: Optional[str]) -> None:
            self.ipAddress = ip
            self.accessCode = accessCode
            self.serialNumber = serial

        def disconnect(self) -> None:
            return None

    def fakeExecute(client: Any, command: Dict[str, Any]) -> None:
        executed.append((client.ipAddress, command.get("commandType", "")))

    monkeypatch.setattr(commands, "listPendingCommands", fakeListPendingCommands)
    monkeypatch.setattr(commands, "completeCommand", fakeCompleteCommand)
    monkeypatch.setattr("client.base44Status.listPendingCommands", fakeListPendingCommands)
    monkeypatch.setattr("client.base44Status.completeCommand", fakeCompleteCommand)
    monkeypatch.setattr("client.base44Status.BambuLanClient", FakeClient)
    monkeypatch.setattr("client.base44Status.executeCommand", fakeExecute)
    monkeypatch.setattr("client.base44Status.tcpCheck", lambda _ip: True)

    reporter = Base44StatusReporter(lambda: snapshots, intervalSec=60, commandPollIntervalSec=1)
    reporter._recipientId = "recipient-xyz"
    reporter._statusFunctionName = "updatePrinterStatus"
    reporter._commandBackoffSeconds = 1.0
    reporter._nextCommandPollTimestamp = 0.0

    reporter._pollPendingCommands(list(snapshots))

    assert executed == [("10.0.0.7", "set_bed_temp")]
    assert commandLog == [("cmd-777", True, None)]


def test_collect_telemetry_marks_printer_online(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrinter:
        def mqtt_start(self) -> None:
            return None

        def mqtt_stop(self) -> None:
            return None

        def get_percentage(self) -> float:
            return 48.0

        def get_time(self) -> int:
            return 600

        def get_nozzle_temperature(self) -> float:
            return 205.0

        def get_bed_temperature(self) -> float:
            return 60.0

        def get_gcode_state(self) -> str:
            return "PRINTING"

    fakePrinter = FakePrinter()

    class FakeBambuApi:
        def Printer(self, _ip: str, _accessCode: str, _serialNumber: str) -> FakePrinter:  # noqa: N802
            return fakePrinter

    def fakeWaitForMqttReady(_printer: FakePrinter) -> bool:
        return True

    monkeypatch.setattr("client.gui.bambuApi", FakeBambuApi())
    monkeypatch.setattr("client.gui.waitForMqttReady", fakeWaitForMqttReady)

    app = ListenerGuiApp.__new__(ListenerGuiApp)
    app._parseOptionalFloat = ListenerGuiApp._parseOptionalFloat.__get__(app, ListenerGuiApp)
    app._parseOptionalInt = ListenerGuiApp._parseOptionalInt.__get__(app, ListenerGuiApp)
    app._parseOptionalString = ListenerGuiApp._parseOptionalString.__get__(app, ListenerGuiApp)
    app._telemetryIndicatesReadiness = ListenerGuiApp._telemetryIndicatesReadiness.__get__(app, ListenerGuiApp)
    app._fetchBambuTelemetry = lambda *_args, **_kwargs: {}
    app._probePrinterAvailability = lambda _ip: "Online"

    printerDetails = {
        "ipAddress": "192.168.0.50",
        "serialNumber": "SN-12345",
        "accessCode": "CODE-123",
        "brand": "Bambu",
    }

    printerTelemetry = ListenerGuiApp._collectPrinterTelemetry(app, printerDetails)

    assert printerTelemetry["online"] is True
    assert printerTelemetry["mqttReady"] is True

    printerSnapshot = {
        "ip": printerDetails["ipAddress"],
        "serial": printerDetails["serialNumber"],
        "status": printerTelemetry["status"],
        "online": printerTelemetry["online"],
        "mqttReady": printerTelemetry["mqttReady"],
        "progress": printerTelemetry["progressPercent"],
        "bed": printerTelemetry["bedTemp"],
        "nozzle": printerTelemetry["nozzleTemp"],
        "timeRemaining": printerTelemetry["remainingTimeSeconds"],
    }

    reporter = Base44StatusReporter(lambda: [printerSnapshot], intervalSec=60, commandPollIntervalSec=60)
    reporter._recipientId = "recipient-123"

    statusPayload = reporter._buildPayload(printerSnapshot)

    assert statusPayload["online"] is True
    assert statusPayload["mqttReady"] is True
