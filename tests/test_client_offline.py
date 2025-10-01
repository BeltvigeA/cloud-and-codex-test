import json
import logging
import sys
import types
from pathlib import Path

import pytest


def installRequestStub() -> None:
    requestsModule = types.ModuleType("requests")

    class DummyResponse:  # pragma: no cover - minimal stub
        def __init__(self, url: str = "", headers: dict | None = None):
            self.url = url
            self.headers = headers or {}

    class DummySession:  # pragma: no cover - minimal stub
        def get(self, *_args, **_kwargs):
            raise NotImplementedError

        def post(self, *_args, **_kwargs):
            raise NotImplementedError

    class DummyRequestException(Exception):
        pass

    requestsModule.Response = DummyResponse
    requestsModule.Session = DummySession
    requestsModule.RequestException = DummyRequestException
    requestsModule.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(NotImplementedError())

    sys.modules.setdefault("requests", requestsModule)


installRequestStub()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import client


def createMetadataFile(tmpPath: Path, filename: str, metadata: dict) -> Path:
    metadataPath = tmpPath / filename
    metadataPath.write_text(json.dumps(metadata), encoding="utf-8")
    return metadataPath


def createDataFile(tmpPath: Path, filename: str, content: bytes) -> Path:
    dataPath = tmpPath / filename
    dataPath.write_bytes(content)
    return dataPath


def test_performOfflineFetch_copies_file_with_metadata(tmp_path: Path) -> None:
    metadata = {
        "originalFilename": "customFile.gcode",
        "unencryptedData": {"job": "demo"},
        "decryptedData": {"secret": "value"},
    }
    metadataPath = createMetadataFile(tmp_path, "metadata.json", metadata)
    dataPath = createDataFile(tmp_path, "sourceFile.gcode", b"file-bytes")

    outputDirectory = tmp_path / "output"

    savedFile = client.performOfflineFetch(str(metadataPath), str(dataPath), str(outputDirectory))

    assert savedFile is not None
    assert savedFile.name == "customFile.gcode"
    assert savedFile.exists()
    assert savedFile.read_bytes() == b"file-bytes"


def test_listenOffline_processes_dataset_entries(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    firstMetadata = {
        "originalFilename": "firstFile.gcode",
        "unencryptedData": {"job": "first"},
    }
    secondMetadata = {
        "originalFilename": "secondFile.gcode",
        "decryptedData": {"secret": "second"},
    }

    firstMetadataPath = createMetadataFile(tmp_path, "firstMetadata.json", firstMetadata)
    secondMetadataPath = createMetadataFile(tmp_path, "secondMetadata.json", secondMetadata)

    firstDataPath = createDataFile(tmp_path, "firstData.gcode", b"first")
    secondDataPath = createDataFile(tmp_path, "secondData.gcode", b"second")

    dataset = {
        "pendingFiles": [
            {"metadataFile": str(firstMetadataPath), "dataFile": str(firstDataPath)},
            {
                "metadata": {
                    "originalFilename": "inlineMetadataFile.gcode",
                    "unencryptedData": {"job": "inline"},
                },
                "dataFile": str(secondDataPath),
            },
            {
                "dataFile": str(secondDataPath),
                "metadataFile": str(secondMetadataPath),
            },
        ]
    }

    datasetPath = tmp_path / "dataset.json"
    datasetPath.write_text(json.dumps(dataset), encoding="utf-8")

    outputDirectory = tmp_path / "offlineOutput"
    client.listenOffline(str(datasetPath), str(outputDirectory))

    savedDataFiles = sorted(
        path.name for path in outputDirectory.iterdir() if path.name != "pendingJobs.log"
    )
    assert savedDataFiles == [
        "firstFile.gcode",
        "inlineMetadataFile.gcode",
        "secondFile.gcode",
    ]

    assert any("Offline processing complete" in message for message in caplog.messages)

    jobLogPath = outputDirectory / "pendingJobs.log"
    assert jobLogPath.exists()
    logEntries = [json.loads(line) for line in jobLogPath.read_text(encoding="utf-8").splitlines()]
    assert len(logEntries) == 3
    assert {Path(entry["job"]["filePath"]).name for entry in logEntries} == {
        "firstFile.gcode",
        "inlineMetadataFile.gcode",
        "secondFile.gcode",
    }


def test_listenOffline_supports_relative_paths(tmp_path: Path) -> None:
    datasetDirectory = tmp_path / "datasetBundle"
    datasetDirectory.mkdir()
    dataDirectory = datasetDirectory / "data"
    dataDirectory.mkdir()
    metadataDirectory = datasetDirectory / "metadata"
    metadataDirectory.mkdir()

    relativeMetadataPath = metadataDirectory / "relativeMetadata.json"
    relativeMetadataPath.write_text(
        json.dumps(
            {
                "originalFilename": "relativeFile.gcode",
                "unencryptedData": {"job": "relative"},
            }
        ),
        encoding="utf-8",
    )
    (dataDirectory / "relativeData.gcode").write_bytes(b"relative-bytes")
    (dataDirectory / "inlineData.gcode").write_bytes(b"inline-bytes")

    dataset = {
        "pendingFiles": [
            {
                "dataFile": "data/relativeData.gcode",
                "metadataFile": "metadata/relativeMetadata.json",
            },
            {
                "dataFile": "data/inlineData.gcode",
                "metadata": {
                    "originalFilename": "inlineRelative.gcode",
                    "decryptedData": {"job": "inline-relative"},
                },
            },
        ]
    }

    datasetPath = datasetDirectory / "dataset.json"
    datasetPath.write_text(json.dumps(dataset), encoding="utf-8")

    outputDirectory = tmp_path / "relativeOutput"
    client.listenOffline(str(datasetPath), str(outputDirectory))

    savedFiles = sorted(
        path.name for path in outputDirectory.iterdir() if path.name != "pendingJobs.log"
    )
    assert savedFiles == ["inlineRelative.gcode", "relativeFile.gcode"]


def test_listenOffline_relative_paths_from_different_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datasetDirectory = tmp_path / "offlineDataset"
    datasetDirectory.mkdir()

    dataDirectory = datasetDirectory / "data"
    metadataDirectory = datasetDirectory / "metadata"
    dataDirectory.mkdir()
    metadataDirectory.mkdir()

    (dataDirectory / "jobData.gcode").write_bytes(b"job-data")
    metadataPath = metadataDirectory / "jobMetadata.json"
    metadataPath.write_text(
        json.dumps(
            {
                "originalFilename": "jobData.gcode",
                "unencryptedData": {"job": "relative-from-alt-cwd"},
            }
        ),
        encoding="utf-8",
    )

    dataset = {
        "pendingFiles": [
            {
                "dataFile": "data/jobData.gcode",
                "metadataFile": "metadata/jobMetadata.json",
            }
        ]
    }

    datasetPath = datasetDirectory / "dataset.json"
    datasetPath.write_text(json.dumps(dataset), encoding="utf-8")

    alternateWorkingDirectory = tmp_path / "alternateCwd"
    alternateWorkingDirectory.mkdir()

    outputDirectory = tmp_path / "offlineOutput"

    monkeypatch.chdir(alternateWorkingDirectory)
    client.listenOffline(str(datasetPath), str(outputDirectory))

    savedFiles = sorted(
        path.name for path in outputDirectory.iterdir() if path.name != "pendingJobs.log"
    )
    assert savedFiles == ["jobData.gcode"]


def test_listenOffline_filters_by_channel(tmp_path: Path) -> None:
    metadataAlpha = {"originalFilename": "alphaFile.gcode"}
    metadataBeta = {"originalFilename": "betaFile.gcode"}

    alphaMetadataPath = createMetadataFile(tmp_path, "alphaMetadata.json", metadataAlpha)
    betaMetadataPath = createMetadataFile(tmp_path, "betaMetadata.json", metadataBeta)

    alphaDataPath = createDataFile(tmp_path, "alphaData.gcode", b"alpha")
    betaDataPath = createDataFile(tmp_path, "betaData.gcode", b"beta")

    dataset = {
        "pendingFiles": [
            {
                "metadataFile": str(alphaMetadataPath),
                "dataFile": str(alphaDataPath),
                "channel": "alpha",
            },
            {
                "metadataFile": str(betaMetadataPath),
                "dataFile": str(betaDataPath),
                "channel": "beta",
            },
        ]
    }

    datasetPath = tmp_path / "channelDataset.json"
    datasetPath.write_text(json.dumps(dataset), encoding="utf-8")

    outputDirectory = tmp_path / "channelOutput"
    client.listenOffline(str(datasetPath), str(outputDirectory), channel="beta")

    savedFiles = sorted(
        path.name for path in outputDirectory.iterdir() if path.name != "pendingJobs.log"
    )
    assert savedFiles == ["betaFile.gcode"]

    jobLogPath = outputDirectory / "pendingJobs.log"
    logEntries = [json.loads(line) for line in jobLogPath.read_text(encoding="utf-8").splitlines()]
    assert len(logEntries) == 1
    assert logEntries[0]["job"]["channel"] == "beta"
