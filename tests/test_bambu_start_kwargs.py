from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.bambuPrinter import _normalizeStartKwargs


class FakePrinterAll:
    def start_print(self, *, bed_leveling=False, use_ams=False, timelapse=None):
        return {"ok": True}


class FakePrinterLimited:
    def start_print(self, *, bed_leveling=False):
        return {"ok": True}


@pytest.fixture
def samplePrinter():
    return FakePrinterAll()


def test_normalize_maps_bed_levelling_and_drops_unknown(samplePrinter):
    rawOptions = {
        "bed_levelling": True,
        "useAms": True,
        "timelapse_enabled": False,
        "totally_unknown": 123,
        "none_field": None,
    }
    output = _normalizeStartKwargs(samplePrinter, rawOptions)
    assert output.get("bed_leveling") is True
    assert output.get("use_ams") is True
    assert output.get("timelapse") is False
    assert "totally_unknown" not in output
    assert "none_field" not in output


def test_normalize_respects_sdk_signature():
    printer = FakePrinterLimited()
    rawOptions = {
        "bed_levelling": True,
        "use_ams": True,
        "timelapse": True,
    }
    output = _normalizeStartKwargs(printer, rawOptions)
    assert output == {"bed_leveling": True}
