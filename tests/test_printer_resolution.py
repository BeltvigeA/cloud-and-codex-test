from client.client import resolvePrinterDetails


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
