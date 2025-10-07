import sys
from pathlib import Path

projectRoot = Path(__file__).resolve().parents[1]
projectRootPath = str(projectRoot)
if projectRootPath not in sys.path:
    sys.path.append(projectRootPath)

from client.client import extractPrinterAssignment, resolvePrinterDetails


def test_resolvePrinterDetails_matches_serial_with_whitespace_in_config():
    configuredPrinters = [
        {
            "serialNumber": " SN-12345 ",
            "nickname": "Workhorse",
            "ipAddress": " 192.168.0.5 ",
            "accessCode": " 0000 ",
        }
    ]
    metadata = {"serialNumber": "SN-12345"}

    resolved = resolvePrinterDetails(metadata, configuredPrinters)

    assert resolved is not None
    assert resolved["serialNumber"] == "SN-12345"
    assert resolved["ipAddress"] == "192.168.0.5"
    assert resolved["accessCode"] == "0000"
    assert resolved["nickname"] == "Workhorse"


def test_resolvePrinterDetails_trims_metadata_values_before_matching():
    configuredPrinters = [{"nickname": "Speedy", "serialNumber": "ABC-999"}]
    metadata = {"nickname": "  Speedy  "}

    resolved = resolvePrinterDetails(metadata, configuredPrinters)

    assert resolved is not None
    assert resolved["nickname"] == "Speedy"
    assert resolved["serialNumber"] == "ABC-999"


def test_extractPrinterAssignment_prefers_decrypted_access_code_for_matching_serial():
    unencryptedData = {
        "printer": {
            "serialNumber": "SN-0001",
            "accessCode": "1111",
        }
    }
    decryptedData = {
        "printer": {
            "serialNumber": "sn-0001",
            "accessCode": "2222",
        }
    }

    assignment = extractPrinterAssignment(unencryptedData, decryptedData)

    assert assignment is not None
    assert assignment["serialNumber"].lower() == "sn-0001"
    assert assignment["accessCode"] == "2222"
