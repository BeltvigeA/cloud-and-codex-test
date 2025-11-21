"""
Handler for update_printer_config commands from backend.
Applies configuration changes to local printers.json file.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

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

    # Map backend field names to client config field names
    FIELD_MAPPING = {
        'ip_address': 'ipAddress',
        'access_code': 'accessCode',
        'name': 'nickname',
        'model': 'model',
        'use_ams': 'useAms',
        'bed_leveling': 'bedLeveling',
        'status': 'status'
    }

    def __init__(self, config_path: Path):
        """
        Initialize config updater.

        Args:
            config_path: Path to printers.json file
        """
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load_printers(self) -> List[Dict[str, Any]]:
        """Load printers from configuration file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            return []

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    logger.error(f"Invalid config format, expected list but got {type(data)}")
                    return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse printers.json: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load printers: {e}")
            raise

    def save_printers(self, printers: List[Dict[str, Any]]) -> None:
        """Save printers to configuration file with backup."""
        try:
            # Create backup of existing file
            if self.config_path.exists():
                backup_path = self.config_path.with_suffix(
                    f'.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                )
                self.config_path.rename(backup_path)
                logger.info(f"Created backup: {backup_path}")

                # Keep only last 5 backups
                backups = sorted(
                    self.config_path.parent.glob('printers.backup.*.json'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                for old_backup in backups[5:]:
                    old_backup.unlink()
                    logger.debug(f"Removed old backup: {old_backup}")

            # Write new configuration
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(printers, f, indent=2, ensure_ascii=False)

            logger.info(f"✅ Saved {len(printers)} printers to {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to save printers: {e}")
            # Try to restore from backup if save failed
            if self.config_path.exists():
                backups = sorted(
                    self.config_path.parent.glob('printers.backup.*.json'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                if backups:
                    logger.info(f"Restoring from backup: {backups[0]}")
                    backups[0].rename(self.config_path)
            raise

    def find_printer_by_serial(
        self,
        printers: List[Dict[str, Any]],
        serial_number: str
    ) -> Optional[int]:
        """
        Find printer index by serial number.

        Returns:
            Index of printer in list, or None if not found
        """
        for idx, printer in enumerate(printers):
            if printer.get('serialNumber') == serial_number:
                return idx
        return None

    def apply_changes(
        self,
        serial_number: str,
        changes: Dict[str, Any]
    ) -> Tuple[bool, List[ConfigChange], Optional[str]]:
        """
        Apply configuration changes to a printer.

        Args:
            serial_number: Printer serial number
            changes: Dictionary of field changes from backend

        Returns:
            Tuple of (success, applied_changes, error_message)
        """
        try:
            logger.info(f"🔄 Applying config changes for printer {serial_number}")
            logger.debug(f"Changes to apply: {changes}")

            printers = self.load_printers()
            printer_idx = self.find_printer_by_serial(printers, serial_number)

            if printer_idx is None:
                # Create new printer entry from fullConfig if provided
                if 'fullConfig' in changes:
                    logger.info(f"🆕 Creating new printer: {serial_number}")
                    new_printer = changes['fullConfig']
                    printers.append(new_printer)
                    self.save_printers(printers)

                    return (
                        True,
                        [ConfigChange('*', None, 'created')],
                        None
                    )
                else:
                    logger.warning(f"Printer {serial_number} not found and no fullConfig provided")
                    return (False, [], "Printer not found and no fullConfig to create from")

            # Update existing printer
            printer = printers[printer_idx]
            applied_changes = []

            for backend_field, new_value in changes.items():
                if backend_field == 'fullConfig':
                    # Skip fullConfig as it's only used for new printers
                    continue

                if backend_field not in self.FIELD_MAPPING:
                    logger.warning(f"Skipping unknown field: {backend_field}")
                    continue

                client_field = self.FIELD_MAPPING[backend_field]
                old_value = printer.get(client_field)

                # Only update if value actually changed
                if old_value != new_value:
                    printer[client_field] = new_value
                    applied_changes.append(
                        ConfigChange(client_field, old_value, new_value)
                    )
                    logger.info(f"  ✏️ {client_field}: {old_value} → {new_value}")

            if applied_changes:
                printers[printer_idx] = printer
                self.save_printers(printers)
                logger.info(
                    f"✅ Applied {len(applied_changes)} changes to {serial_number}"
                )
            else:
                logger.info(f"→ No changes needed for {serial_number}")

            return (True, applied_changes, None)

        except Exception as e:
            logger.exception(f"❌ Failed to apply changes to {serial_number}")
            return (False, [], str(e))


def handle_update_printer_config(
    command_data: Dict[str, Any],
    config_path: Path
) -> Dict[str, Any]:
    """
    Handle update_printer_config command from Firestore.

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

        logger.info(f"📥 Received update_printer_config command for {serial_number}")

        if not serial_number:
            return {
                'status': 'error',
                'error': 'Missing printerSerial in command'
            }

        # If fullConfig is provided, include it in changes
        if 'fullConfig' in payload:
            changes['fullConfig'] = payload['fullConfig']

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
                'changesCount': len(applied_changes),
                'message': f'Applied {len(applied_changes)} changes to {serial_number}'
            }
        else:
            return {
                'status': 'failed',
                'error': error
            }

    except Exception as e:
        logger.exception("❌ Error handling update_printer_config command")
        return {
            'status': 'error',
            'error': str(e)
        }
