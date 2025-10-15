from __future__ import annotations

import sys
import copy
import logging
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


def createSampleThreeMfWithSliceInfo(targetPath: Path) -> None:
    sliceInfo = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<config>\n  <plate>\n    <metadata key=\"index\" value=\"1\" />\n    <ordered_objects>\n      <object order=\"1\" identify_id=\"obj-1\" name=\"Object One\" />\n      <object order=\"2\" identify_id=\"obj-2\" name=\"Object Two\" />\n    </ordered_objects>\n    <object identify_id=\"obj-1\" name=\"Object One\" skipped=\"false\" />\n    <object identify_id=\"obj-2\" name=\"Object Two\" skipped=\"false\" />\n  </plate>\n</config>\n"""
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/slice_info.config", sliceInfo)
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


def test_sendBambuPrintJobUsesTemporaryCopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    originalPath = tmp_path / "sample.3mf"
    createSampleThreeMf(originalPath)
    originalBytes = originalPath.read_bytes()

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaBambulabsApi(
        *,
        ip: str,
        serial: str,
        accessCode: str,
        localPath: Path,
        remoteName: str,
        returnPrinter: bool,
        **_kwargs: object,
    ):
        temporaryLocalPath = Path(localPath)
        uploadCapture["localPath"] = temporaryLocalPath
        uploadCapture["remoteName"] = remoteName
        uploadCapture["temporaryExistsDuringUpload"] = temporaryLocalPath.exists()
        uploadCapture["bytesDuringUpload"] = temporaryLocalPath.read_bytes()
        session = bambuPrinter.BambuApiUploadSession(
            printer=object(),
            remoteName="uploaded.3mf",
            connectCamera=False,
            mqttStarted=True,
        )
        if returnPrinter:
            return session
        return session.remoteName

    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)

    startCapture: dict[str, object] = {}

    def fakeStartViaBambuapi(
        printer: object,
        remoteName: str,
        paramPath: str | None,
        plateIndex: int | None,
        **kwargs: object,
    ) -> bool:
        startCapture["startArgs"] = (
            printer,
            remoteName,
            paramPath,
            plateIndex,
            kwargs,
        )
        return True

    def fakeStartPrint(**kwargs) -> None:
        startCapture.update(kwargs)

    monkeypatch.setattr(bambuPrinter, "startViaBambuapiAfterUpload", fakeStartViaBambuapi)
    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", fakeStartPrint)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.0.10",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    result = bambuPrinter.sendBambuPrintJob(filePath=originalPath, options=options)

    assert originalPath.read_bytes() == originalBytes
    assert uploadCapture["temporaryExistsDuringUpload"] is True
    assert uploadCapture["localPath"] != originalPath
    assert Path(uploadCapture["localPath"]).parent != originalPath.parent
    assert uploadCapture["bytesDuringUpload"] == originalBytes
    assert result["remoteFile"] == "uploaded.3mf"
    assert result["originalRemoteFile"] == bambuPrinter.buildPrinterTransferFileName(
        originalPath
    )
    assert startCapture["paramPath"] == "Metadata/plate_1.gcode"
    assert startCapture["sdFileName"] == "uploaded.3mf"


def test_sendBambuPrintJobMarksSkippedObjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplePath = tmp_path / "skip.3mf"
    createSampleThreeMfWithSliceInfo(samplePath)

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaBambulabsApi(
        *,
        ip: str,
        serial: str,
        accessCode: str,
        localPath: Path,
        remoteName: str,
        returnPrinter: bool,
        **_kwargs: object,
    ):
        with zipfile.ZipFile(localPath, "r") as archive:
            uploadCapture["sliceInfo"] = archive.read("Metadata/slice_info.config")
        uploadCapture["remoteName"] = remoteName
        session = bambuPrinter.BambuApiUploadSession(
            printer=object(),
            remoteName="uploaded.3mf",
            connectCamera=False,
            mqttStarted=True,
        )
        if returnPrinter:
            return session
        return session.remoteName

    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)

    monkeypatch.setattr(
        bambuPrinter,
        "startViaBambuapiAfterUpload",
        lambda *_args, **_kwargs: True,
    )

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", lambda **_kwargs: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.0.5",
        serialNumber="SERIAL-SKIP",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    skipTargets = [{"order": 2, "plateId": "1", "identifyId": "obj-2", "objectName": "Object Two"}]

    bambuPrinter.sendBambuPrintJob(
        filePath=samplePath,
        options=options,
        skippedObjects=skipTargets,
    )

    sliceInfoBytes = uploadCapture.get("sliceInfo")
    assert isinstance(sliceInfoBytes, bytes)
    xmlRoot = ET.fromstring(sliceInfoBytes.decode("utf-8"))
    plateElement = xmlRoot.find("plate")
    assert plateElement is not None
    objectOne = None
    objectTwo = None
    for objectElement in plateElement.findall("object"):
        identifyId = objectElement.get("identify_id")
        if identifyId == "obj-1":
            objectOne = objectElement
        if identifyId == "obj-2":
            objectTwo = objectElement
    assert objectOne is not None
    assert objectTwo is not None
    assert objectOne.get("skipped") == "false"
    assert objectTwo.get("skipped") == "true"

    skippedContainer = plateElement.find("skipped_objects")
    assert skippedContainer is not None
    skippedOrders = {element.get("order") for element in skippedContainer.findall("object")}
    assert skippedOrders == {"2"}


def test_sendBambuPrintJobWrapsGcodeInThreeMf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcodePath = tmp_path / "model.gcode"
    gcodeContent = "G1 X5 Y5\nM400\n"
    gcodePath.write_text(gcodeContent)

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaBambulabsApi(
        *,
        ip: str,
        serial: str,
        accessCode: str,
        localPath: Path,
        remoteName: str,
        returnPrinter: bool,
        **_kwargs: object,
    ):
        uploadCapture["remoteName"] = remoteName
        uploadCapture["localPath"] = localPath
        with zipfile.ZipFile(localPath, "r") as archive:
            with archive.open("Metadata/plate_1.gcode") as handle:
                uploadCapture["gcodeBytes"] = handle.read()
        session = bambuPrinter.BambuApiUploadSession(
            printer=object(),
            remoteName="model.3mf",
            connectCamera=False,
            mqttStarted=True,
        )
        if returnPrinter:
            return session
        return session.remoteName

    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)

    monkeypatch.setattr(bambuPrinter, "startViaBambuapiAfterUpload", lambda *_args, **_kwargs: True)

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", lambda **_kwargs: None)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.1.5",
        serialNumber="SERIAL-GCODE",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    bambuPrinter.sendBambuPrintJob(filePath=gcodePath, options=options)

    capturedRemote = uploadCapture.get("remoteName")
    assert capturedRemote == "model.3mf"
    localPath = uploadCapture.get("localPath")
    assert isinstance(localPath, Path)
    assert localPath.suffix == ".3mf"
    capturedBytes = uploadCapture.get("gcodeBytes")
    assert capturedBytes == gcodeContent.encode("utf-8")


@pytest.mark.parametrize(
    (
        "metadataInput",
        "optionsUseAms",
        "expectedUseAms",
    ),
    [
        pytest.param(
            {"unencryptedData": {"ams_configuration": None}},
            True,
            False,
            id="null-config",
        ),
        pytest.param(
            {
                "unencryptedData": {
                    "ams_configuration": {
                        "enabled": True,
                        "slots": [{"slot": 1}],
                    }
                }
            },
            True,
            True,
            id="enabled-with-slots",
        ),
        pytest.param(
            {
                "unencryptedData": {
                    "ams_configuration": {
                        "enabled": True,
                        "slots": [{"slot": 1}],
                    },
                    "is_quick_print": "true",
                }
            },
            True,
            False,
            id="quick-print",
        ),
        pytest.param(
            {
                "unencryptedData": {
                    "ams_configuration": {
                        "enabled": True,
                        "slots": [{"slot": 1}],
                    }
                }
            },
            False,
            False,
            id="forced-spool",
        ),
    ],
)
def test_sendBambuPrintJobDecidesUseAmsBasedOnMetadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadataInput: Dict[str, Any],
    optionsUseAms: bool,
    expectedUseAms: bool,
) -> None:
    samplePath = tmp_path / "metadata.3mf"
    createSampleThreeMf(samplePath)

    def fakeUploadViaBambulabsApi(
        *,
        ip: str,
        serial: str,
        accessCode: str,
        localPath: Path,
        remoteName: str,
        returnPrinter: bool,
        **_kwargs: object,
    ):
        session = bambuPrinter.BambuApiUploadSession(
            printer=object(),
            remoteName="uploaded.3mf",
            connectCamera=False,
            mqttStarted=True,
        )
        if returnPrinter:
            return session
        return session.remoteName

    startRecorder: Dict[str, Any] = {}

    def fakeStartViaBambuapi(
        printer: object,
        remoteName: str,
        paramPath: str | None,
        plateIndex: int | None,
        **kwargs: object,
    ) -> bool:
        startRecorder["apiUseAms"] = kwargs.get("useAms")
        return True

    def fakeStartPrint(**kwargs: object) -> None:
        startRecorder["mqtt"] = kwargs

    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)
    monkeypatch.setattr(bambuPrinter, "startViaBambuapiAfterUpload", fakeStartViaBambuapi)
    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", fakeStartPrint)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.0.10",
        serialNumber="SERIAL-META",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
        useAms=optionsUseAms,
    )

    metadata = copy.deepcopy(metadataInput)

    bambuPrinter.sendBambuPrintJob(
        filePath=samplePath,
        options=options,
        jobMetadata=metadata,
    )

    assert startRecorder.get("apiUseAms") is expectedUseAms
    mqttPayload = startRecorder.get("mqtt")
    assert isinstance(mqttPayload, dict)
    assert mqttPayload.get("useAms") is expectedUseAms
    initialStatus = mqttPayload.get("initialStatus")
    assert isinstance(initialStatus, dict)
    assert initialStatus.get("useAms") is expectedUseAms


def test_applySkippedObjectsToArchiveRejectsUnknownOrder(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "invalid.3mf"
    createSampleThreeMfWithSliceInfo(samplePath)

    caplog.set_level("ERROR")

    with pytest.raises(ValueError) as errorInfo:
        bambuPrinter.applySkippedObjectsToArchive(
            samplePath,
            [{"order": 99, "identifyId": "missing"}],
        )

    assert "Unable to locate slicer objects" in str(errorInfo.value)
    assert "Unable to locate slicer objects" in caplog.text


def test_waitForPrinterStartTreatsPrepareAsActive() -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.stateCalls = 0

        def get_state(self) -> str:
            self.stateCalls += 1
            if self.stateCalls == 1:
                return "IDLE"
            return "PREPARE"

        def get_gcode_state(self) -> str:
            if self.stateCalls == 1:
                return "IDLE"
            return "PREPARE"

        def get_percentage(self) -> float:
            return 0.0

    fakePrinter = FakePrinter()

    started, stateValue, progressValue, gcodeStateValue = bambuPrinter.waitForPrinterStart(
        fakePrinter,
        timeoutSeconds=1,
        pollIntervalSeconds=0,
    )

    assert started is True
    assert stateValue == "PREPARE"
    assert progressValue == 0.0
    assert gcodeStateValue == "PREPARE"


def test_failedSerialResponseAllowsJobStart(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.serial = "SERIAL123"
            self.serialNumber = "SERIAL123"
            self.infoCalls = 0
            self.stateCalls = 0
            self.mqttStarted = False
            self.startedPrint = False
            self.startArguments = None

        def get_printer_info(self) -> Dict[str, Any]:
            self.infoCalls += 1
            if self.infoCalls == 1:
                return {"serial": "FAILED"}
            return {"serial": "SERIAL123"}

        def get_state(self) -> Dict[str, Any]:
            self.stateCalls += 1
            return {"serial": "FAILED" if self.stateCalls == 1 else "SERIAL123"}

        def mqtt_start(self) -> None:
            self.mqttStarted = True

        def start_print(self, uploadName: str, startParam: Any | None = None) -> None:
            self.startedPrint = True
            self.startArguments = (uploadName, startParam)

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.50",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )
    printerSession.client = fakePrinter

    ensureCalls: list[float] = []

    def fakeEnsureMqttConnected(
        printer: Any, *, timeoutSeconds: float = 25.0, **_kwargs: Any
    ) -> bool:
        ensureCalls.append(timeoutSeconds)
        return True

    monkeypatch.setattr(bambuPrinter, "ensureMqttConnected", fakeEnsureMqttConnected)

    returnedPrinter = bambuPrinter.ensurePrinterSessionReady(
        printerSession, expectedSerial="SERIAL123"
    )

    assert returnedPrinter is fakePrinter
    assert printerSession.mqttReady is True
    assert ensureCalls

    bambuPrinter.startWithLibrary(fakePrinter, "job.3mf", None, expectedSerial="SERIAL123")

    assert fakePrinter.mqttStarted is True
    assert fakePrinter.startedPrint is True
    assert fakePrinter.startArguments == ("job.3mf", None)


def test_ensurePrinterSessionReadyWaitsForRealSerial(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.responses = ["IDLE", "IDLE", "SERIAL123"]
            self.mqttStarted = False
            self.startedPrint = False
            self.startArguments: tuple[str, Any | None] | None = None
            self.requestCalls = 0

        def get_printer_info(self) -> Dict[str, Any]:
            return {"serial": self.responses[0]}

        def request_device_info(self) -> None:
            self.requestCalls += 1
            if len(self.responses) > 1:
                self.responses.pop(0)

        def mqtt_start(self) -> None:
            self.mqttStarted = True

        def start_print(self, uploadName: str, startParam: Any | None = None) -> None:
            self.startedPrint = True
            self.startArguments = (uploadName, startParam)

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.55",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )
    printerSession.client = fakePrinter

    def fakeEnsureMqttConnected(printer: Any, *, timeoutSeconds: float = 25.0, **_kwargs: Any) -> bool:
        assert timeoutSeconds == 25.0
        return True

    monkeypatch.setattr(bambuPrinter, "ensureMqttConnected", fakeEnsureMqttConnected)

    returnedPrinter = bambuPrinter.ensurePrinterSessionReady(
        printerSession, expectedSerial="SERIAL123"
    )

    assert returnedPrinter is fakePrinter
    assert printerSession.mqttReady is True
    assert printerSession.serialNumber == "SERIAL123"
    assert fakePrinter.requestCalls >= 1

    bambuPrinter.startWithLibrary(fakePrinter, "job.3mf", None, expectedSerial="SERIAL123")

    assert fakePrinter.mqttStarted is True
    assert fakePrinter.startedPrint is True
    assert fakePrinter.startArguments == ("job.3mf", None)


def test_ensurePrinterSessionReadyRaisesOnMismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.serial = "WRONG"
            self.serialNumber = "WRONG"

        def get_printer_info(self) -> Dict[str, Any]:
            return {"serial": "WRONG"}

        def disconnect(self) -> None:
            return None

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.99",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )

    def fakeAcquire(self: bambuPrinter.PrinterSession) -> Any:
        if self.client is None:
            self.client = fakePrinter
        return self.client

    monkeypatch.setattr(bambuPrinter.PrinterSession, "acquireClient", fakeAcquire)

    monkeypatch.setattr(bambuPrinter, "ensureMqttConnected", lambda *_args, **_kwargs: True)

    with pytest.raises(RuntimeError) as errorInfo:
        bambuPrinter.ensurePrinterSessionReady(
            printerSession, expectedSerial="SERIAL123", serialWaitSeconds=0.5
        )

    assert "Connected to feil printer" in str(errorInfo.value)


def test_ensurePrinterSessionReadyWarnsWhenSerialMissing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FakePrinter:
        def get_printer_info(self) -> Dict[str, Any]:
            return {}

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.66",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )
    printerSession.client = fakePrinter

    monkeypatch.setattr(
        bambuPrinter,
        "ensureMqttConnected",
        lambda _printer, **_kwargs: True,
    )

    with caplog.at_level(logging.WARNING):
        returnedPrinter = bambuPrinter.ensurePrinterSessionReady(
            printerSession,
            expectedSerial="SERIAL123",
            serialWaitSeconds=0.1,
            serialPollIntervalSeconds=0.01,
        )

    assert returnedPrinter is fakePrinter
    assert any("Kunne ikke bekrefte printer-SN" in message for message in caplog.messages)


def test_waitForMqttAndStatusReturnsTrue(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.stateCalls = 0
            self.connected = False

        def mqtt_connected(self) -> bool:
            return self.connected

        def get_state(self) -> Dict[str, Any]:
            self.stateCalls += 1
            if self.stateCalls < 3:
                raise RuntimeError("Not ready yet")
            return {"state": {"state": "IDLE"}}

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.77",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )
    printerSession.client = fakePrinter

    def fakeEnsureMqttConnected(printer: Any, *, timeoutSeconds: float = 25.0, **_kwargs: Any) -> bool:
        assert printer is fakePrinter
        fakePrinter.connected = True
        return True

    monkeypatch.setattr(bambuPrinter, "ensureMqttConnected", fakeEnsureMqttConnected)
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    result = bambuPrinter.waitForMqttAndStatus(
        printerSession,
        maxWaitSeconds=1.0,
        staleAfterSeconds=1.0,
        pollIntervalSeconds=0.01,
    )

    assert result is True
    assert printerSession.mqttReady is True
    assert printerSession.lastStatusTimestamp > 0
    assert fakePrinter.stateCalls >= 3


def test_waitForMqttAndStatusTimesOut(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FakePrinter:
        def mqtt_connected(self) -> bool:
            return True

        def get_state(self) -> Dict[str, Any]:
            raise RuntimeError("Still warming up")

    fakePrinter = FakePrinter()

    printerSession = bambuPrinter.PrinterSession(
        ipAddress="192.168.2.88",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
    )
    printerSession.client = fakePrinter

    monkeypatch.setattr(bambuPrinter, "ensureMqttConnected", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bambuPrinter.time, "sleep", lambda _seconds: None)

    with caplog.at_level(logging.WARNING):
        result = bambuPrinter.waitForMqttAndStatus(
            printerSession,
            maxWaitSeconds=0.2,
            staleAfterSeconds=0.05,
            pollIntervalSeconds=0.01,
        )

    assert result is False
    assert any("MQTT-status ble ikke fersk" in message for message in caplog.messages)
