"""
Handler for update_printer_config commands from backend.
Applies configuration changes to local printers.json file.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ConfigChange:
    """Represents a single configuration change."""
    field: str
    old_value: Any
    new_value: Any

    def __str__(self):
        return f"{self.field}: {self.old_value} → {self.new_value}"

class PrinterConfigUpdater:
    """Manages updates to printer configuration."""

    ALLOWED_FIELDS = {
        'ip_address': 'ipAddress',
        'access_code': 'accessCode',
        'name': 'nickname',
        'model': 'model',
        'use_ams': 'useAms',
        'bed_leveling': 'bedLeveling'
    }

    def __init__(self, config_path: Path):
        self.config_path = config_path

    def load_printers(self) -> List[Dict[str, Any]]:
        """Load printers from configuration file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            return []

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load printers: {e}")
            raise

    def save_printers(self, printers: List[Dict[str, Any]]) -> None:
        """Save printers to configuration file."""
        try:
            # Backup existing file
            if self.config_path.exists():
                backup_path = self.config_path.with_suffix('.json.backup')
                self.config_path.rename(backup_path)

            # Write new configuration
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(printers, f, indent=2, ensure_ascii=False)

            logger.info(f"Saved {len(printers)} printers to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save printers: {e}")
            raise

    def find_printer_by_serial(
        self,
        printers: List[Dict[str, Any]],
        serial_number: str
    ) -> Optional[int]:
        """Find printer index by serial number."""
        for idx, printer in enumerate(printers):
            if printer.get('serialNumber') == serial_number:
                return idx
        return None

    def apply_changes(
        self,
        serial_number: str,
        changes: Dict[str, Any]
    ) -> tuple[bool, List[ConfigChange], Optional[str]]:
        """
        Apply configuration changes to a printer.

        Returns:
            (success, applied_changes, error_message)
        """
        try:
            printers = self.load_printers()
            printer_idx = self.find_printer_by_serial(printers, serial_number)

            if printer_idx is None:
                # Create new printer entry
                new_printer = self._create_printer_entry(serial_number, changes)
                printers.append(new_printer)
                self.save_printers(printers)

                return (
                    True,
                    [ConfigChange('*', None, 'created')],
                    None
                )

            # Update existing printer
            printer = printers[printer_idx]
            applied_changes = []

            for backend_field, new_value in changes.items():
                if backend_field not in self.ALLOWED_FIELDS:
                    logger.warning(f"Skipping unknown field: {backend_field}")
                    continue

                client_field = self.ALLOWED_FIELDS[backend_field]
                old_value = printer.get(client_field)

                if old_value != new_value:
                    printer[client_field] = new_value
                    applied_changes.append(
                        ConfigChange(client_field, old_value, new_value)
                    )

            if applied_changes:
                printers[printer_idx] = printer
                self.save_printers(printers)
                logger.info(
                    f"Applied {len(applied_changes)} changes to {serial_number}"
                )

            return (True, applied_changes, None)

        except Exception as e:
            logger.error(f"Failed to apply changes: {e}")
            return (False, [], str(e))

    def _create_printer_entry(
        self,
        serial_number: str,
        fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create new printer configuration entry."""
        return {
            'serialNumber': serial_number,
            'nickname': fields.get('name', f'Printer {serial_number[-4:]}'),
            'brand': 'Bambu Lab',
            'ipAddress': fields.get('ip_address', ''),
            'accessCode': fields.get('access_code', ''),
            'transport': 'lan',
            'useCloud': False,
            'useAms': fields.get('use_ams', True),
            'bedLeveling': fields.get('bed_leveling', True),
            'layerInspect': False
        }

def handle_update_printer_config(
    command_data: Dict[str, Any],
    config_path: Path
) -> Dict[str, Any]:
    """
    Handle update_printer_config command.

    Args:
        command_data: Command payload from Firestore
        config_path: Path to printers.json

    Returns:
        Response dictionary with status and details
    """
    try:
        payload = command_data.get('payload', {})
        serial_number = command_data.get('printerSerial')
        changes = payload.get('changes', {})

        if not serial_number:
            return {
                'status': 'error',
                'error': 'Missing printerSerial'
            }

        updater = PrinterConfigUpdater(config_path)
        success, applied_changes, error = updater.apply_changes(
            serial_number,
            changes
        )

        if success:
            return {
                'status': 'completed',
                'appliedChanges': [
                    {
                        'field': change.field,
                        'oldValue': change.old_value,
                        'newValue': change.new_value
                    }
                    for change in applied_changes
                ],
                'message': f'Applied {len(applied_changes)} changes'
            }
        else:
            return {
                'status': 'failed',
                'error': error
            }

    except Exception as e:
        logger.exception("Error handling update_printer_config command")
        return {
            'status': 'error',
            'error': str(e)
        }
