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


class FakeCameraClient:
    def __init__(self) -> None:
        self.alive = False
        self.startCalls = 0
        self.timelapseActivations: list[str] = []
        self.configuredDirectories: list[str] = []

    def start(self) -> bool:
        self.alive = True
        self.startCalls += 1
        return True

    def start_timelapse_capture(self, directory: str) -> bool:
        self.alive = True
        self.timelapseActivations.append(directory)
        return True

    def enable_timelapse(self, directory: str) -> bool:
        return self.start_timelapse_capture(directory)

    def set_timelapse_directory(self, directory: str) -> None:
        self.configuredDirectories.append(directory)


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
        self.camera_client = FakeCameraClient()
        self.cameraStartCalls = 0
        self.timelapseRequests: list[str] = []

    def mqtt_start(self) -> None:
        return None

    def camera_start(self) -> bool:
        self.cameraStartCalls += 1
        return self.camera_client.start()

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

    def start_timelapse_capture(self, directory: str) -> bool:
        self.timelapseRequests.append(directory)
        return True

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


class IdleLoopPrinter(FakeApiPrinter):
    def __init__(self) -> None:
        super().__init__()

    def get_state(self) -> Any:
        return "IDLE"

    def get_percentage(self) -> float:
        return 0.0

    def start_print(self, *args: Any, **kwargs: Any) -> None:
        super().start_print(*args, **kwargs)
        self.started = True


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


def test_startPrintViaApi_printer_stays_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    idlePrinter = IdleLoopPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: idlePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    monotonicState = {"value": 0.0}

    def monotonicStub() -> float:
        monotonicState["value"] += 1.0
        return monotonicState["value"]

    monkeypatch.setattr(bambuPrinter.time, "monotonic", monotonicStub)

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
        ack_timeout_sec=0.1,
    )

    assert result["acknowledged"] is False
    assert result["fallbackTriggered"] is True
    assert result["useAms"] is False
    assert idlePrinter.startArgs == [
        ("job.3mf", "Metadata/plate_1.gcode"),
        ("job.3mf", "Metadata/plate_1.gcode"),
    ]
    assert idlePrinter.disconnectCalls == 1


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


def test_startPrintViaApi_enables_timelapse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fakePrinter = FakeApiPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    timelapseDir = tmp_path / "timelapse"
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
        enableTimeLapse=True,
        timeLapseDirectory=timelapseDir,
    )

    bambuPrinter.startPrintViaApi(
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

    expectedDirectory = str(timelapseDir.expanduser())
    assert timelapseDir.exists()
    assert fakePrinter.camera_client.configuredDirectories == [expectedDirectory]
    assert fakePrinter.camera_client.timelapseActivations == [expectedDirectory]


def test_activateTimelapseCapture_prefers_mqtt_client(tmp_path: Path) -> None:
    fakePrinter = FakeApiPrinter()

    class FakeMqttClient:
        def __init__(self) -> None:
            self.enableCalls: list[bool] = []

        def set_onboard_printer_timelapse(self, *, enable: bool) -> bool:
            self.enableCalls.append(bool(enable))
            return True

    mqttClient = FakeMqttClient()
    fakePrinter.mqtt_client = mqttClient

    bambuPrinter._activateTimelapseCapture(fakePrinter, tmp_path)

    assert mqttClient.enableCalls == [True]
    assert fakePrinter.timelapseRequests == []


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
        enableBrakePlate=True,
        plateTemplate="smooth_plate",
    )

    result = bambuPrinter.sendBambuPrintJob(
        filePath=samplePath,
        options=options,
        statusCallback=capture,
    )

    assert result["method"] == "lan"
    assert result["startMethod"] == "api"
    assert result["api"] == apiResult
    assert result["enableBrakePlate"] is True
    assert result["plateTemplate"] == "smooth_plate"
    startingEvents = [event for event in events if event.get("status") == "starting"]
    assert startingEvents and startingEvents[0]["method"] == "api"
    assert startingEvents[0]["enableBrakePlate"] is True
    assert startingEvents[0]["plateTemplate"] == "smooth_plate"
    uploadedEvents = [event for event in events if event.get("status") == "uploaded"]
    assert uploadedEvents and uploadedEvents[0]["enableBrakePlate"] is True
    assert uploadedEvents[0]["plateTemplate"] == "smooth_plate"
    startedEvents = [event for event in events if event.get("status") == "started"]
    assert startedEvents and startedEvents[0]["enableBrakePlate"] is True
    assert startedEvents[0]["plateTemplate"] == "smooth_plate"


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


def test_startPrintViaApi_excludes_ams_mapping_when_use_ams_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ams_mapping is NOT sent when use_ams=False (external spool mode)."""
    fakePrinter = FakeApiPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
        useAms=False,  # External spool mode
    )

    # Job metadata with ams_configuration that should be IGNORED when use_ams=False
    job_metadata = {
        "unencryptedData": {
            "ams_configuration": {
                "slots": [
                    {"colorIndex": 0, "slot": 0},
                    {"colorIndex": 1, "slot": 1},
                ]
            }
        }
    }

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=1,
        param_path="Metadata/plate_1.gcode",
        options=options,
        job_metadata=job_metadata,
        ack_timeout_sec=0.2,
    )

    assert result["acknowledged"] is True
    # Verify that use_ams is False
    assert fakePrinter.startRequests == [False]
    # Verify that ams_mapping was NOT included in startKeywordArgs
    for payload in fakePrinter.startPayloads:
        assert "ams_mapping" not in payload, (
            f"ams_mapping should NOT be sent when use_ams=False, but got: {payload}"
        )


def test_startPrintViaApi_includes_ams_mapping_when_use_ams_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ams_mapping IS sent when use_ams=True."""
    fakePrinter = FakeApiPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
        useAms=True,  # AMS mode
    )

    # Job metadata with ams_configuration that SHOULD be included when use_ams=True
    job_metadata = {
        "unencryptedData": {
            "ams_configuration": {
                "slots": [
                    {"colorIndex": 0, "slot": 0},
                    {"colorIndex": 1, "slot": 1},
                ]
            }
        }
    }

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=1,
        param_path="Metadata/plate_1.gcode",
        options=options,
        job_metadata=job_metadata,
        ack_timeout_sec=0.2,
    )

    assert result["acknowledged"] is True
    # Verify that use_ams is True
    assert fakePrinter.startRequests == [True]
    # Verify that ams_mapping WAS included in startKeywordArgs
    assert len(fakePrinter.startPayloads) == 1
    assert "ams_mapping" in fakePrinter.startPayloads[0], (
        f"ams_mapping should be sent when use_ams=True, but got: {fakePrinter.startPayloads[0]}"
    )
    assert fakePrinter.startPayloads[0]["ams_mapping"] == [0, 1]


def test_startPrintViaApi_excludes_ams_mapping_when_use_ams_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ams_mapping is NOT sent when use_ams=None (auto-detect mode).
    
    This is the fix for the AMS mapping error when no AMS config is specified -
    ams_mapping should only be sent when use_ams is explicitly True.
    
    Note: When ams_configuration is present in metadata, resolveUseAmsAuto()
    will return True (not None). So to test the None case, we must NOT include
    ams_configuration in the metadata.
    """
    fakePrinter = FakeApiPrinter()
    monkeypatch.setattr(bambuPrinter, "bambulabsApi", ApiModuleStub(lambda *_args, **_kwargs: fakePrinter))
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    # No useAms specified - should default to None (auto-detect)
    options = bambuPrinter.BambuPrintOptions(
        ipAddress="1.2.3.4",
        serialNumber="SERIAL",
        accessCode="CODE",
        waitSeconds=1,
        # useAms not specified - defaults to None
    )

    # Job metadata WITHOUT ams_configuration - this will keep use_ams as None
    # This simulates the user's case: no AMS config specified in payload
    job_metadata = {
        "unencryptedData": {
            "printJobId": "test-job-123",
            # No ams_configuration here!
        }
    }

    result = bambuPrinter.startPrintViaApi(
        ip="1.2.3.4",
        serial="SERIAL",
        accessCode="CODE",
        uploaded_name="job.3mf",
        plate_index=1,
        param_path="Metadata/plate_1.gcode",
        options=options,
        job_metadata=job_metadata,
        ack_timeout_sec=0.2,
    )

    assert result["acknowledged"] is True
    # Verify that use_ams is None (auto-detect) initially
    # Note: The first request will have None, but may retry with False on conflict
    assert fakePrinter.startRequests[0] is None or fakePrinter.startRequests[0] is False
    # Verify that ams_mapping was NOT included in any startKeywordArgs
    for payload in fakePrinter.startPayloads:
        assert "ams_mapping" not in payload, (
            f"ams_mapping should NOT be sent when use_ams=None (auto-detect), but got: {payload}"
        )


def test_extractOrderedObjectsFromArchive_returns_identify_id(tmp_path: Path) -> None:
    """Test that extractOrderedObjectsFromArchive returns identify_id from slice_info.config."""
    samplePath = tmp_path / "model.3mf"
    
    sliceInfoXml = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <object identify_id="482" name="Model.stl 1" skipped="false" />
    <object identify_id="493" name="Model.stl 2" skipped="false" />
    <object identify_id="515" name="Model.stl 3" skipped="true" />
  </plate>
</config>
"""
    
    with zipfile.ZipFile(samplePath, "w") as archive:
        archive.writestr("Metadata/slice_info.config", sliceInfoXml)
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")
    
    objects = bambuPrinter.extractOrderedObjectsFromArchive(samplePath)
    
    assert len(objects) == 3
    assert objects[0]["identify_id"] == "482"
    assert objects[0]["name"] == "Model.stl 1"
    assert objects[0]["plate_id"] == "1"
    assert objects[0]["order"] == 1
    assert objects[0]["skipped"] is False
    
    assert objects[1]["identify_id"] == "493"
    assert objects[1]["order"] == 2
    
    assert objects[2]["identify_id"] == "515"
    assert objects[2]["skipped"] is True
