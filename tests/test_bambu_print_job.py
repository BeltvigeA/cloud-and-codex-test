import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


def createSampleGcode(targetPath: Path) -> None:
    targetPath.write_text("G1 X0 Y0\n", encoding="utf-8")


class FakeApiPrinter:
    def __init__(self, *, conflictFirst: bool = False) -> None:
        self.started = False
        self.stopCalls = 0
        self.disconnectCalls = 0
        self.startRequests: list[Optional[bool]] = []
        self.conflictFirst = conflictFirst
        self.startCount = 0
        self._statePollsBeforeStart = 0
        self._statePollsAfterStart = 0
        self.startPayloads: list[Dict[str, Any]] = []
        self.startArgs: list[tuple[Any, ...]] = []
        self.startKwargs: list[Dict[str, Any]] = []
        self.autoStepRecovery: list[bool] = []
        self.calibrationRequests: list[Dict[str, Any]] = []

    def mqtt_start(self) -> None:
        return None

    def get_state(self) -> Any:
        if not self.started:
            self._statePollsBeforeStart += 1
            return "IDLE"
        self._statePollsAfterStart += 1
        if self.conflictFirst and self.startCount == 1:
            return {"messages": ["HMS_07FF-2000-0002-0004"]}
        if self._statePollsAfterStart == 1:
            return "PREPARE"
        return "PRINTING"

    def get_percentage(self) -> float:
        if not self.started:
            return 0.0
        return 12.5 if self._statePollsAfterStart > 1 else 0.0

    def get_gcode_state(self) -> str:
        return "PRINTING" if self.started else "IDLE"

    def start_print(self, *args: Any, **kwargs: Any) -> None:
        self.startCount += 1
        self.started = True
        self._statePollsAfterStart = 0
        self.startArgs.append(tuple(args))
        self.startPayloads.append(dict(kwargs))
        self.startKwargs.append(dict(kwargs))
        self.startRequests.append(kwargs.get("use_ams"))
        if self.startCount >= 2:
            self.conflictFirst = False

    def stop_print(self) -> None:
        self.stopCalls += 1

    def disconnect(self) -> None:
        self.disconnectCalls += 1

    def set_auto_step_recovery(self, enabled: bool) -> None:
        self.autoStepRecovery.append(bool(enabled))

    def calibrate_printer(
        self,
        *,
        bed_level: bool = True,
        motor_noise_calibration: bool = True,
        vibration_compensation: bool = True,
    ) -> bool:
        self.calibrationRequests.append(
            {
                "bed_level": bool(bed_level),
                "motor_noise_calibration": bool(motor_noise_calibration),
                "vibration_compensation": bool(vibration_compensation),
            }
        )
        return True


class ApiModuleStub:
    def __init__(self, printerFactory):
        self.Printer = printerFactory


def test_resolveUseAmsAuto_respects_explicit_option() -> None:
    optionsTrue = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        useAms=True,
    )
    optionsFalse = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        useAms=False,
    )
    assert bambuPrinter.resolveUseAmsAuto(optionsTrue, None, None) is True
    assert bambuPrinter.resolveUseAmsAuto(optionsFalse, None, None) is False


def test_resolveUseAmsAuto_detects_gcode_path(tmp_path: Path) -> None:
    gcodePath = tmp_path / "model.gcode"
    createSampleGcode(gcodePath)
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
    )
    assert bambuPrinter.resolveUseAmsAuto(options, None, gcodePath) is False


def test_resolveUseAmsAuto_considers_metadata() -> None:
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
    )
    metadata: Dict[str, Any] = {"unencryptedData": {"ams_configuration": {"enabled": True}}}
    quickMetadata: Dict[str, Any] = {"unencryptedData": {"is_quick_print": "true"}}
    assert bambuPrinter.resolveUseAmsAuto(options, metadata, None) is True
    assert bambuPrinter.resolveUseAmsAuto(options, quickMetadata, None) is False


def test_startPrintViaApi_acknowledges(monkeypatch: pytest.MonkeyPatch) -> None:
    fakePrinter = FakeApiPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
    )

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=1,
        param_path="Metadata/plate_1.gcode",
        options=options,
        job_metadata=None,
        ack_timeout_sec=0.2,
    )

    assert result["acknowledged"] is True
    assert result["fallbackTriggered"] is False
    assert fakePrinter.startRequests == [None]
    assert fakePrinter.disconnectCalls == 1
    assert fakePrinter.startArgs == [("job.3mf", "Metadata/plate_1.gcode")]
    assert fakePrinter.autoStepRecovery == [True]
    assert fakePrinter.calibrationRequests == [
        {
            "bed_level": True,
            "motor_noise_calibration": False,
            "vibration_compensation": False,
        }
    ]


def test_startPrintViaApi_retries_on_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    fakePrinter = FakeApiPrinter(conflictFirst=True)
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
    )

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=None,
        param_path="Metadata/plate_1.gcode",
        options=options,
        job_metadata=None,
        ack_timeout_sec=0.2,
    )

    assert result["fallbackTriggered"] is True
    assert result["useAms"] is False
    assert fakePrinter.startRequests == [None, False]
    assert fakePrinter.stopCalls == 1
    assert fakePrinter.startArgs == [
        ("job.3mf", "Metadata/plate_1.gcode"),
        ("job.3mf", "Metadata/plate_1.gcode"),
    ]
    assert fakePrinter.autoStepRecovery == [True, True]
    assert fakePrinter.calibrationRequests == [
        {
            "bed_level": True,
            "motor_noise_calibration": False,
            "vibration_compensation": False,
        },
        {
            "bed_level": True,
            "motor_noise_calibration": False,
            "vibration_compensation": False,
        },
    ]


def test_sendBambuPrintJob_uses_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    samplePath = tmp_path / "model.3mf"
    createSampleThreeMf(samplePath)

    uploadCalls: Dict[str, Any] = {}
    monkeypatch.setattr(
        bambuPrinter,
        "uploadViaFtps",
        lambda **kwargs: uploadCalls.setdefault("sdFileName", kwargs["remoteName"]),
    )
    monkeypatch.setattr(
        bambuPrinter,
        "uploadViaBambulabsApi",
        lambda **kwargs: kwargs["remoteName"],
    )
    apiResult = {
        "acknowledged": True,
        "state": "PRINTING",
        "gcodeState": "PRINTING",
        "percentage": 5.0,
        "useAms": True,
        "fallbackTriggered": False,
    }
    monkeypatch.setattr(bambuPrinter, "startPrintViaApi", lambda **_kwargs: dict(apiResult))
    def forbidMqtt(**_kwargs: Any) -> None:
        raise AssertionError("startPrintViaMqtt should not be used under API-only policy")

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", forbidMqtt)

    events: list[Dict[str, Any]] = []

    def capture(event: Dict[str, Any]) -> None:
        events.append(dict(event))

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=0,
        startStrategy="api",
    )

    result = bambuPrinter.sendBambuPrintJob(
        filePath=samplePath,
        options=options,
        statusCallback=capture,
    )

    assert result["method"] == "lan"
    assert result["startMethod"] == "api"
    assert result["api"] == apiResult
    startingEvents = [event for event in events if event.get("status") == "starting"]
    assert startingEvents and startingEvents[0]["method"] == "api"


def test_sendBambuPrintJob_raises_when_api_start_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    samplePath = tmp_path / "model.3mf"
    createSampleThreeMf(samplePath)

    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", lambda **kwargs: kwargs["remoteName"])
    monkeypatch.setattr(
        bambuPrinter,
        "uploadViaBambulabsApi",
        lambda **kwargs: kwargs["remoteName"],
    )
    monkeypatch.setattr(bambuPrinter, "startPrintViaApi", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("api failure")))

    monkeypatch.setattr(
        bambuPrinter,
        "startPrintViaMqtt",
        lambda **_kwargs: pytest.fail("startPrintViaMqtt should not be invoked"),
    )

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=0,
        startStrategy="api",
    )

    with pytest.raises(RuntimeError) as errorInfo:
        bambuPrinter.sendBambuPrintJob(
            filePath=samplePath,
            options=options,
            jobMetadata={"unencryptedData": {"ams_configuration": None}},
        )

    assert "API print start failed and MQTT fallback is disabled by policy" in str(errorInfo.value)

