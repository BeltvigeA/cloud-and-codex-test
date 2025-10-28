import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


class StuckAtHundredPrinter:
    def __init__(self) -> None:
        self.started = False
        self.controlCount = 0

    def mqtt_start(self) -> None:
        return None

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def start_print(self, *_args, **_kwargs) -> None:
        self.started = True

    def send_control(self, _payload):
        self.controlCount += 1
        self.started = True

    def get_state(self):
        return "FINISH" if self.started else "IDLE"

    def get_percentage(self):
        return 100.0 if self.started else 0.0


class ApiModuleStub:
    def __init__(self, printerFactory):
        self.Printer = printerFactory


class DualPhasePrinter:
    def __init__(self) -> None:
        self.startPrintCalls = 0
        self.sendControlCalls = 0
        self.currentState = "IDLE"
        self.currentPercentage = 0.0
        self.lastPayload = None

    def mqtt_start(self) -> None:
        return None

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def start_print(self, *_args, **_kwargs) -> None:
        self.startPrintCalls += 1
        self.currentState = "FINISH"
        self.currentPercentage = 100.0

    def send_control(self, payload):
        self.sendControlCalls += 1
        self.lastPayload = payload
        self.currentState = "PRINTING"
        self.currentPercentage = 10.0

    def get_state(self):
        return self.currentState

    def get_percentage(self):
        return self.currentPercentage


def test_pollForAcknowledgement_rejects_finish_at_hundred(monkeypatch: pytest.MonkeyPatch) -> None:
    stuckPrinter = StuckAtHundredPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: stuckPrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        useAms=True,
    )

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=None,
        param_path=None,
        options=options,
        job_metadata=None,
        ack_timeout_sec=0.2,
    )

    assert result["acknowledged"] is False
    assert result.get("percentage") == 100.0


def test_startPrintViaApi_retries_with_send_control_when_state_is_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    dualPhasePrinter = DualPhasePrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: dualPhasePrinter))
    monkeypatch.setattr(bambuPrinter, "_waitForMqttReady", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        useAms=True,
    )

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=None,
        param_path=None,
        options=options,
        job_metadata=None,
        ack_timeout_sec=0.2,
    )

    assert dualPhasePrinter.startPrintCalls == 1
    assert dualPhasePrinter.sendControlCalls == 1
    assert result["acknowledged"] is True
