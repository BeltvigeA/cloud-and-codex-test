import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import gui  # noqa: E402


def testParseFieldValueReturnsNoneForEmptyString() -> None:
    assert gui.parseFieldValue("filamentColor", "   ") is None


def testParseFieldValueParsesIntegerField() -> None:
    assert gui.parseFieldValue("infillDensity", "42") == 42


@pytest.mark.parametrize("value", ["3.0", "0.4", " 1.75 "])
def testParseFieldValueParsesFloatField(value: str) -> None:
    assert gui.parseFieldValue("filamentDiameter", value) == float(value)


@pytest.mark.parametrize(
    "fieldName,rawValue,errorMessage",
    [
        ("infillDensity", "not-a-number", "infillDensity must be an integer"),
        ("filamentDiameter", "not-a-number", "filamentDiameter must be a number"),
    ],
)
def testParseFieldValueRaisesForInvalidNumbers(fieldName: str, rawValue: str, errorMessage: str) -> None:
    with pytest.raises(ValueError) as errorInfo:
        gui.parseFieldValue(fieldName, rawValue)

    assert errorMessage in str(errorInfo.value)
