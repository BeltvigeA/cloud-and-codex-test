import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


class StuckAtHundredPrinter:
    def __init__(self) -> None:
        self.started = False

    def mqtt_start(self) -> None:
        return None

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def start_print(self, *_args, **_kwargs) -> None:
        self.started = True

    def get_state(self):
        return "FINISH" if self.started else "IDLE"

    def get_percentage(self):
        return 100.0 if self.started else 0.0


class ApiModuleStub:
    def __init__(self, printerFactory):
        self.Printer = printerFactory


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
