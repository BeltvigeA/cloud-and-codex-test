import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import status_subscriber
from client.status_subscriber import BambuStatusSubscriber


def testStalledPrintForcesIdle(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyPrinter:
        instances: List["DummyPrinter"] = []

        def __init__(self) -> None:
            self.stopInvocations = 0
            self.sendControlPayloads: List[Dict[str, Any]] = []
            DummyPrinter.instances.append(self)

        def stop_print(self) -> None:  # pragma: no cover - simple counter
            self.stopInvocations += 1

        def send_control(self, payload: Dict[str, Any]) -> None:  # pragma: no cover - fallback not used
            self.sendControlPayloads.append(payload)

    monotonicState = {"value": 0.0}

    def fakeMonotonic() -> float:
        return monotonicState["value"]

    monkeypatch.setattr(status_subscriber.time, "monotonic", fakeMonotonic)

    dummyPrinter = DummyPrinter()

    subscriber = BambuStatusSubscriber(lambda *_args: None, lambda *_args: None, pollInterval=0.0)

    statusPayload: Dict[str, Any] = {
        "status": "update",
        "state": "Printing",
        "gcodeState": "",
        "progressPercent": 100.0,
        "rawStatePayload": {},
        "rawGcodePayload": {},
        "rawPercentagePayload": {},
    }

    stallStart: Any = None
    for step in range(3):
        monotonicState["value"] += 3.0
        derivedStatus, _, _ = subscriber._deriveStatusAttributes(statusPayload)
        progressValue = subscriber._coerceFloat(statusPayload.get("progressPercent"))
        stallStart = subscriber._maybeForceIdleForStall(
            dummyPrinter,
            statusPayload,
            derivedStatus,
            progressValue,
            stallStart,
        )
        if step < 2:
            assert stallStart is not None
        else:
            assert stallStart is None

    assert statusPayload["status"] == "idle"
    assert statusPayload["progressPercent"] == 0.0
    assert statusPayload["gcodeState"] == "idle"

    assert dummyPrinter.stopInvocations == 1
    assert dummyPrinter.sendControlPayloads == []
