import unittest
import json
import tempfile
from pathlib import Path
from client.commands.update_config import PrinterConfigUpdater, handle_update_printer_config

class TestPrinterConfigUpdater(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / 'printers.json'

        # Create initial config
        initial_printers = [{
            'serialNumber': '01P00A381200434',
            'nickname': 'Old Name',
            'ipAddress': '192.168.1.100',
            'accessCode': 'OLD_CODE'
        }]

        with open(self.config_path, 'w') as f:
            json.dump(initial_printers, f)

    def test_update_existing_printer(self):
        updater = PrinterConfigUpdater(self.config_path)

        success, changes, error = updater.apply_changes(
            '01P00A381200434',
            {
                'ip_address': '192.168.1.105',
                'name': 'New Name'
            }
        )

        self.assertTrue(success)
        self.assertEqual(len(changes), 2)
        self.assertIsNone(error)

        # Verify changes were saved
        printers = updater.load_printers()
        self.assertEqual(printers[0]['ipAddress'], '192.168.1.105')
        self.assertEqual(printers[0]['nickname'], 'New Name')

    def test_create_new_printer(self):
        updater = PrinterConfigUpdater(self.config_path)

        success, changes, error = updater.apply_changes(
            '01P00A381200999',  # New serial
            {
                'ip_address': '192.168.1.200',
                'name': 'New Printer',
                'access_code': 'NEW_CODE'
            }
        )

        self.assertTrue(success)
        self.assertIsNone(error)

        printers = updater.load_printers()
        self.assertEqual(len(printers), 2)

    def test_handle_update_printer_config_success(self):
        command_data = {
            'printerSerial': '01P00A381200434',
            'payload': {
                'changes': {
                    'ip_address': '192.168.1.110',
                    'name': 'Updated Printer'
                }
            }
        }

        response = handle_update_printer_config(command_data, self.config_path)

        self.assertEqual(response['status'], 'completed')
        self.assertEqual(len(response['appliedChanges']), 2)

    def test_handle_update_printer_config_missing_serial(self):
        command_data = {
            'payload': {
                'changes': {
                    'ip_address': '192.168.1.110'
                }
            }
        }

        response = handle_update_printer_config(command_data, self.config_path)

        self.assertEqual(response['status'], 'error')
        self.assertIn('Missing printerSerial', response['error'])

if __name__ == '__main__':
    unittest.main()
