from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

import pytest

projectRoot = Path(__file__).resolve().parents[1]
projectRootPath = str(projectRoot)
if projectRootPath not in sys.path:
    sys.path.append(projectRootPath)

from client.autoprint import plate_reference


def _createSnapshot(tmpPath: Path, serial: str, index: int) -> Path:
    snapshotPath = tmpPath / f"{serial}_{index}.jpg"
    snapshotPath.write_text("snapshot")
    return snapshotPath


def test_capture_reference_sequence_prefers_control_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    controlCalls: List[dict] = []
    sleepCalls: List[float] = []

    class ApiPrinter:
        def __init__(self) -> None:
            self._stateCalls = 0

        def send_control(self, payload: dict) -> None:
            controlCalls.append(payload)

        def get_state(self) -> str:
            sequence = ["homing", "homing", "idle"]
            index = min(self._stateCalls, len(sequence) - 1)
            self._stateCalls += 1
            return sequence[index]

    apiPrinter = ApiPrinter()
    captureCount = 0

    def captureFunc(printer: Any, serial: str) -> Path:
        nonlocal captureCount
        snapshotPath = _createSnapshot(tmp_path, serial, captureCount)
        captureCount += 1
        return snapshotPath

    def fakeSleep(seconds: float) -> None:
        sleepCalls.append(seconds)

    monkeypatch.setattr(plate_reference.time, "sleep", fakeSleep)

    capturedPaths = plate_reference.captureReferenceSequence(
        apiPrinter,
        "serial-789",
        captureFunc,
        frameCount=3,
        delaySeconds=0.2,
    )

    assert len(capturedPaths) == 3
    assert controlCalls == [
        {"motion": {"command": "home_all"}},
        {
            "motion": {
                "command": "move",
                "mode": "absolute",
                "position": {"x": 0.0, "y": 250.0},
                "feedrate": 6000,
            }
        },
        {
            "motion": {
                "command": "move",
                "axis": "z",
                "distance": 5.0,
                "feedrate": 600,
            }
        },
        {
            "motion": {
                "command": "move",
                "axis": "z",
                "distance": 5.0,
                "feedrate": 600,
            }
        },
    ]
    assert sleepCalls == [
        pytest.approx(0.25, rel=1e-3),
        pytest.approx(0.25, rel=1e-3),
        pytest.approx(2.0, rel=1e-3),
        pytest.approx(0.5, rel=1e-3),
        pytest.approx(0.5, rel=1e-3),
    ]


def test_capture_reference_sequence_lowers_plate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sendCalls: List[str] = []

    class FakePrinter:
        def send_gcode(self, command: str) -> None:
            sendCalls.append(command)

    fakePrinter = FakePrinter()
    captureCount = 0

    def captureFunc(printer: Any, serial: str) -> Path:
        nonlocal captureCount
        snapshotPath = _createSnapshot(tmp_path, serial, captureCount)
        captureCount += 1
        return snapshotPath

    sleepCalls: List[float] = []

    def fakeSleep(seconds: float) -> None:
        sleepCalls.append(seconds)

    monkeypatch.setattr(plate_reference.time, "sleep", fakeSleep)

    capturedPaths = plate_reference.captureReferenceSequence(
        fakePrinter,
        "serial-123",
        captureFunc,
        frameCount=3,
        delaySeconds=0.2,
    )

    assert len(capturedPaths) == 3
    assert sendCalls == [
        "G28",
        "G90",
        "G1 X0 Y250 F6000",
        "G91",
        "G1 Z5 F600",
        "G90",
        "G91",
        "G1 Z5 F600",
        "G90",
    ]
    assert sleepCalls == [
        pytest.approx(2.0, rel=1e-3),
        pytest.approx(0.5, rel=1e-3),
        pytest.approx(0.5, rel=1e-3),
    ]


def test_capture_reference_sequence_without_gcode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePrinter:
        pass

    fakePrinter = FakePrinter()
    captureCount = 0

    def captureFunc(printer: Any, serial: str) -> Path:
        nonlocal captureCount
        snapshotPath = _createSnapshot(tmp_path, serial, captureCount)
        captureCount += 1
        return snapshotPath

    sleepCalls: List[float] = []

    def fakeSleep(seconds: float) -> None:
        sleepCalls.append(seconds)

    monkeypatch.setattr(plate_reference.time, "sleep", fakeSleep)

    capturedPaths = plate_reference.captureReferenceSequence(
        fakePrinter,
        "serial-456",
        captureFunc,
        frameCount=2,
        delaySeconds=0.3,
    )

    assert len(capturedPaths) == 2
    assert sleepCalls == [
        pytest.approx(0.3, rel=1e-3),
        pytest.approx(0.3, rel=1e-3),
    ]
