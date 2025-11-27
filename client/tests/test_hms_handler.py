import unittest
from client.hms_handler import parse_hms_error, HMS_ERROR_DESCRIPTIONS


class TestHmsHandler(unittest.TestCase):
    def test_parse_known_hms_error(self):
        """Test parsing of known HMS error code"""
        result = parse_hms_error("0300_0300_0002_0003")

        self.assertEqual(result["hmsCode"], "0300_0300_0002_0003")
        self.assertEqual(result["module"], "hotbed")
        self.assertEqual(result["severity"], "critical")
        self.assertIn("Hotbed heating", result["description"])

    def test_parse_extruder_error(self):
        """Test parsing of extruder error code"""
        result = parse_hms_error("0500_0200_0001_0001")

        self.assertEqual(result["hmsCode"], "0500_0200_0001_0001")
        self.assertEqual(result["module"], "extruder")
        self.assertEqual(result["severity"], "error")
        self.assertIn("Nozzle temperature", result["description"])

    def test_parse_motion_error(self):
        """Test parsing of motion system error code"""
        result = parse_hms_error("0700_0300_0001_0002")

        self.assertEqual(result["hmsCode"], "0700_0300_0001_0002")
        self.assertEqual(result["module"], "motion")
        self.assertEqual(result["severity"], "error")  # error_type 0001 = error level
        self.assertIn("Homing failed", result["description"])

    def test_parse_ams_error(self):
        """Test parsing of AMS error code"""
        result = parse_hms_error("0C00_0100_0001_0001")

        self.assertEqual(result["hmsCode"], "0C00_0100_0001_0001")
        self.assertEqual(result["module"], "ams")
        self.assertEqual(result["severity"], "error")  # error_type 0001 = error level
        self.assertIn("AMS communication", result["description"])

    def test_parse_filament_error(self):
        """Test parsing of filament error code"""
        result = parse_hms_error("0D00_0200_0001_0001")

        self.assertEqual(result["hmsCode"], "0D00_0200_0001_0001")
        self.assertEqual(result["module"], "filament")
        self.assertEqual(result["severity"], "error")  # error_type 0001 = error level
        self.assertIn("Filament runout", result["description"])

    def test_parse_unknown_hms_error(self):
        """Test parsing of unknown HMS error code"""
        result = parse_hms_error("9999_9999_9999_9999")

        self.assertEqual(result["hmsCode"], "9999_9999_9999_9999")
        self.assertEqual(result["module"], "unknown")
        self.assertIn("9999", result["description"])

    def test_parse_malformed_hms_code_too_few_parts(self):
        """Test handling of malformed HMS code with too few parts"""
        result = parse_hms_error("invalid")

        self.assertEqual(result["hmsCode"], "invalid")
        self.assertEqual(result["severity"], "unknown")
        self.assertIn("Malformed", result["description"])

    def test_parse_malformed_hms_code_too_many_parts(self):
        """Test handling of malformed HMS code with too many parts"""
        result = parse_hms_error("0300_0300_0002_0003_extra")

        self.assertEqual(result["hmsCode"], "0300_0300_0002_0003_extra")
        self.assertEqual(result["severity"], "unknown")
        self.assertIn("Malformed", result["description"])

    def test_parse_none_hms_code(self):
        """Test handling of None HMS code"""
        result = parse_hms_error(None)

        self.assertEqual(result["hmsCode"], "None")
        self.assertEqual(result["severity"], "unknown")
        self.assertIn("Invalid", result["description"])

    def test_parse_empty_string(self):
        """Test handling of empty string HMS code"""
        result = parse_hms_error("")

        self.assertEqual(result["hmsCode"], "")
        self.assertEqual(result["severity"], "unknown")
        self.assertIn("Invalid", result["description"])

    def test_parse_non_string_hms_code(self):
        """Test handling of non-string HMS code"""
        result = parse_hms_error(12345)

        self.assertEqual(result["hmsCode"], "12345")
        self.assertEqual(result["severity"], "unknown")
        self.assertIn("Invalid", result["description"])

    def test_raw_data_structure(self):
        """Test that raw data structure is included"""
        result = parse_hms_error("0300_0300_0002_0003")

        self.assertIn("raw", result)
        self.assertEqual(result["raw"]["module_code"], "0300")
        self.assertEqual(result["raw"]["error_category"], "0300")
        self.assertEqual(result["raw"]["error_type"], "0002")
        self.assertEqual(result["raw"]["error_detail"], "0003")

    def test_severity_determination_critical(self):
        """Test severity determination for critical errors"""
        # Error type 0002 should be critical
        result = parse_hms_error("0300_0300_0002_0003")
        self.assertEqual(result["severity"], "critical")

        # Error type 0003 should be critical
        result = parse_hms_error("0500_0300_0003_0001")
        self.assertEqual(result["severity"], "critical")

        # Error type 0004 should be critical
        result = parse_hms_error("0700_0100_0004_0001")
        self.assertEqual(result["severity"], "critical")

    def test_severity_determination_error(self):
        """Test severity determination for error level"""
        # Error type 0001 should be error
        result = parse_hms_error("0500_0200_0001_0001")
        self.assertEqual(result["severity"], "error")

    def test_severity_determination_warning(self):
        """Test severity determination for warning level"""
        # Unknown error types should default to warning
        result = parse_hms_error("0C00_0100_0005_0001")
        self.assertEqual(result["severity"], "warning")

    def test_module_mapping_hotbed(self):
        """Test module mapping for hotbed"""
        result = parse_hms_error("0300_0000_0000_0000")
        self.assertEqual(result["module"], "hotbed")

    def test_module_mapping_extruder(self):
        """Test module mapping for extruder"""
        result = parse_hms_error("0500_0000_0000_0000")
        self.assertEqual(result["module"], "extruder")

    def test_module_mapping_motion(self):
        """Test module mapping for motion"""
        result = parse_hms_error("0700_0000_0000_0000")
        self.assertEqual(result["module"], "motion")

    def test_module_mapping_ams(self):
        """Test module mapping for AMS"""
        result = parse_hms_error("0C00_0000_0000_0000")
        self.assertEqual(result["module"], "ams")

    def test_module_mapping_filament(self):
        """Test module mapping for filament"""
        result = parse_hms_error("0D00_0000_0000_0000")
        self.assertEqual(result["module"], "filament")

    def test_module_mapping_chamber(self):
        """Test module mapping for chamber"""
        result = parse_hms_error("1200_0000_0000_0000")
        self.assertEqual(result["module"], "chamber")

    def test_module_mapping_unknown(self):
        """Test module mapping for unknown module code"""
        result = parse_hms_error("FFFF_0000_0000_0000")
        self.assertEqual(result["module"], "unknown")

    def test_hms_error_descriptions_completeness(self):
        """Test that HMS_ERROR_DESCRIPTIONS contains expected codes"""
        expected_codes = [
            "0300_0300_0002_0003",  # Hotbed
            "0500_0200_0001_0001",  # Nozzle temp abnormal
            "0500_0300_0002_0001",  # Nozzle heating failed
            "0700_0300_0001_0002",  # Homing failed
            "0C00_0100_0001_0001",  # AMS communication
            "0D00_0200_0001_0001",  # Filament runout
        ]

        for code in expected_codes:
            self.assertIn(code, HMS_ERROR_DESCRIPTIONS,
                         f"HMS code {code} should be in HMS_ERROR_DESCRIPTIONS")

    def test_all_described_errors_have_valid_format(self):
        """Test that all HMS error codes in the database have valid format"""
        for code in HMS_ERROR_DESCRIPTIONS.keys():
            # Should have 4 parts separated by underscores
            parts = code.split('_')
            self.assertEqual(len(parts), 4,
                           f"HMS code {code} should have 4 parts")

            # Each part should be 4 characters
            for i, part in enumerate(parts):
                self.assertEqual(len(part), 4,
                               f"Part {i} of HMS code {code} should be 4 characters")


if __name__ == '__main__':
    unittest.main()
