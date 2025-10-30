from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import bambuPrinter, client  # noqa: E402


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


def patchRequestsPost(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        status_code = 200

        def raise_for_status(self) -> None:  # noqa: D401 - simple stub
            """Pretend the request succeeded."""

        @property
        def text(self) -> str:
            return ""

        def json(self) -> dict[str, Any]:
            return {}

    def fakePost(*_args: Any, **_kwargs: Any) -> DummyResponse:
        return DummyResponse()

    monkeypatch.setattr(client.requests, "post", fakePost)
    monkeypatch.setattr(bambuPrinter.requests, "post", fakePost)


def test_dispatchBambuPrintExtractsSkippedObjects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    orderedObjects = [
        {"order": 1, "identify_id": "obj-1", "plate_id": "1", "name": "Object One"},
        {"order": 2, "identify_id": "obj-2", "plate_id": "1", "name": "Object Two"},
    ]
    skippedObjects = [{"order": 2}]

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL123",
                "ipAddress": "192.168.0.8",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            },
            "slicer": {
                "ordered_objects": orderedObjects,
                "skipped_objects": skippedObjects,
            },
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL123",
            "ipAddress": "192.168.0.8",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
        }
    ]

    capturedSkipped: dict[str, Any] = {}

    def fakeSendBambuPrintJob(**kwargs: Any) -> dict[str, Any]:
        capturedSkipped["skippedObjects"] = kwargs.get("skippedObjects")
        return {"remoteFile": "uploaded.3mf", "method": "lan"}

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    patchRequestsPost(monkeypatch)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-1",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    expectedSkipped = [{"order": 2, "identifyId": "obj-2", "plateId": "1", "objectName": "Object Two"}]
    assert capturedSkipped["skippedObjects"] == expectedSkipped


def test_dispatchBambuPrintPropagatesTimelapseFlag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIALTL",
                "ipAddress": "192.168.1.10",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
        "enable_time_lapse": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIALTL",
            "ipAddress": "192.168.1.10",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
        }
    ]

    capturedOptions: dict[str, bambuPrinter.BambuPrintOptions] = {}

    def fakeSendBambuPrintJob(**kwargs: Any) -> dict[str, Any]:
        capturedOptions["options"] = kwargs.get("options")
        return {"remoteFile": "uploaded.3mf", "method": "lan"}

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    patchRequestsPost(monkeypatch)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-1",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    selectedOptions = capturedOptions.get("options")
    assert isinstance(selectedOptions, bambuPrinter.BambuPrintOptions)
    assert selectedOptions.enableTimeLapse is True
    assert selectedOptions.timeLapseDirectory is None


def test_dispatchBambuPrintIncludesPrinterDetailsInErrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL123",
                "ipAddress": "192.168.0.8",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
                "nickname": "Studio Printer",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL123",
            "ipAddress": "192.168.0.8",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "nickname": "Studio Printer",
        }
    ]

    def fakeSendBambuPrintJob(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Connection failed for endpoint 192.168.0.8:990")

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    patchRequestsPost(monkeypatch)

    caplog.set_level(logging.ERROR)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-1",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    assert result["success"] is False
    errorMessage = result["error"]
    assert "Studio Printer" in errorMessage
    assert "192.168.0.8" in errorMessage
    assert "Connection failed" in errorMessage
    errorEvents = [event for event in result["events"] if event.get("event") == "error"]
    assert errorEvents and errorEvents[0]["error"] == errorMessage
    assert any("Studio Printer" in record.message and "192.168.0.8" in record.message for record in caplog.records)


def test_dispatchDeletesRemoteFileOnCompletion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplePath = tmp_path / "sample.3mf"
    createSampleThreeMf(samplePath)

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIALDEL",
                "ipAddress": "192.168.0.50",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIALDEL",
            "ipAddress": "192.168.0.50",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
        }
    ]

    deletionCalls: List[tuple[Dict[str, str], str]] = []

    def fakeDelete(credentials: Dict[str, str], remotePath: str) -> bool:
        deletionCalls.append((dict(credentials), remotePath))
        return True

    def fakeSendBambuPrintJob(**kwargs: Any) -> Dict[str, Any]:
        statusCallback = kwargs["statusCallback"]
        statusCallback({"status": "uploaded", "remoteFile": "sdcard/sample.3mf"})
        statusCallback({"status": "progress", "mc_percent": 50, "gcode_state": "printing"})
        statusCallback({"status": "completed", "remoteFile": "sdcard/sample.3mf"})
        return {"remoteFile": "sdcard/sample.3mf"}

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    monkeypatch.setattr(client, "postStatus", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "deleteRemoteFile", fakeDelete)
    patchRequestsPost(monkeypatch)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-del",
        recipientId="recipient-del",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result and result["success"] is True
    assert deletionCalls
    credentials, remotePath = deletionCalls[0]
    assert remotePath == "sdcard/sample.3mf"
    assert credentials["serialNumber"] == "SERIALDEL"


def test_dispatchBambuPrintUsesBambulabsApiWhenConfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplePath = tmp_path / "sample.3mf"
    createSampleThreeMf(samplePath)

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "serialNumber": "SERIAL999",
                "ipAddress": "192.168.0.9",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL999",
            "ipAddress": "192.168.0.9",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "lan_mode": "bambuApi",
        }
    ]

    uploadCapture: dict[str, Any] = {}

    class DummyUploadSession:
        def __init__(self, *args: Any, **_kwargs: Any) -> None:
            self.remoteName = "uploaded.3mf"

        def close(self) -> None:
            return None

    def fakeUploadViaBambulabsApi(
        *,
        ip: str,
        serial: str,
        accessCode: str,
        localPath: Path,
        remoteName: str,
        connectCamera: bool = False,
        returnPrinter: bool = False,
    ):
        uploadCapture["ip"] = ip
        uploadCapture["serial"] = serial
        uploadCapture["accessCode"] = accessCode
        uploadCapture["localPathSuffix"] = Path(localPath).suffix
        uploadCapture["remoteName"] = remoteName
        uploadCapture["startArgs"] = (remoteName, {"connectCamera": connectCamera})

        session = DummyUploadSession(
            printer=object(),
            remoteName="uploaded.3mf",
            connectCamera=connectCamera,
            mqttStarted=True,
        )
        if returnPrinter:
            return session
        session.close()
        return session.remoteName

    def failUploadViaFtps(**_kwargs: Any) -> str:
        raise AssertionError("uploadViaFtps should not be used when lanStrategy=bambuApi")

    monkeypatch.setattr(bambuPrinter, "BambuApiUploadSession", DummyUploadSession, raising=False)
    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)
    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", failUploadViaFtps)
    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", lambda **_kwargs: None)
    patchRequestsPost(monkeypatch)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-2",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    assert result["success"] is True
    assert uploadCapture["ip"] == "192.168.0.9"
    assert uploadCapture["serial"] == "SERIAL999"
    assert uploadCapture["accessCode"] == "ACCESS"
    assert uploadCapture["localPathSuffix"] == ".3mf"
    assert uploadCapture["remoteName"].endswith(".3mf")
    assert uploadCapture.get("startArgs") is not None


def test_dispatchBambuPrintRejectsTransportMismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "printJobId": "job-123",
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL555",
                "ipAddress": "192.168.0.55",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
                "connectionMethod": "lan",
            },
            "job": {"transport": "bambu_connect"},
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
        "printJobId": "job-123",
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL555",
            "ipAddress": "192.168.0.55",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "connectionMethod": "lan",
        }
    ]

    patchRequestsPost(monkeypatch)
    caplog.set_level(logging.WARNING)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-3",
        recipientId="recipient-3",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is None
    assert any(
        "reason=transport_mismatch" in record.message
        and "job-123" in record.message
        and "SERIAL555" in record.message
        for record in caplog.records
    )


def test_dispatchBambuPrintRejectsUnsupportedTransport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "printJobId": "job-124",
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL556",
                "ipAddress": "192.168.0.56",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
                "connectionMethod": "lan",
            },
            "job": {"transport": "octoprint"},
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
        "printJobId": "job-124",
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL556",
            "ipAddress": "192.168.0.56",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "connectionMethod": "lan",
        }
    ]

    patchRequestsPost(monkeypatch)
    caplog.set_level(logging.WARNING)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-4",
        recipientId="recipient-4",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is None
    assert any(
        "reason=unsupported_transport" in record.message
        and "transport=octoprint" in record.message
        and "job-124" in record.message
        for record in caplog.records
    )


def test_dispatchBambuPrintRoutesBambuConnectViaCloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "printJobId": "job-200",
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL600",
                "ipAddress": "192.168.0.60",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
                "transport": "bambu_connect",
                "cloudUrl": "https://cloud.example.com",
            },
            "job": {"transport": "bambu_connect"},
        },
        "decryptedData": {"job": {"transport": "bambu_connect"}},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
        "printJobId": "job-200",
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL600",
            "ipAddress": "192.168.0.60",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "transport": "bambu_connect",
            "cloudUrl": "https://cloud.example.com",
            "useCloud": True,
        }
    ]

    callCapture: dict[str, Any] = {}

    def fakeSendBambuPrintJob(*, options: bambuPrinter.BambuPrintOptions, **kwargs: Any) -> dict[str, Any]:
        callCapture["useCloud"] = options.useCloud
        callCapture["transport"] = options.transport
        callCapture["cloudUrl"] = options.cloudUrl
        return {"remoteFile": "uploaded.3mf", "method": "cloud"}

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    patchRequestsPost(monkeypatch)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-5",
        recipientId="recipient-5",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    assert callCapture.get("useCloud") is True
    assert callCapture.get("transport") == "bambu_connect"
    assert callCapture.get("cloudUrl") == "https://cloud.example.com"
    assert result["details"]["transport"] == "bambu_connect"
