"""Configuration manager for persistent client settings."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Default configuration directory
CONFIG_DIR = Path.home() / ".printmaster"
CONFIG_FILE = CONFIG_DIR / "config.json"


class ConfigManager:
    """Manages persistent configuration for the printer client."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """
        Initialize the configuration manager.

        Args:
            config_path: Optional path to the config file. Defaults to ~/.printmaster/config.json
        """
        self.config_path = config_path or CONFIG_FILE
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from disk."""
        if not self.config_path.exists():
            log.info("Configuration file does not exist, using defaults")
            self._config = {}
            return

        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._config = loaded
                    log.info("Configuration loaded successfully")
                else:
                    log.warning("Invalid config format, using defaults")
                    self._config = {}
        except (OSError, json.JSONDecodeError) as error:
            log.error(f"Failed to load configuration: {error}")
            self._config = {}

    def save(self) -> bool:
        """
        Save configuration to disk with secure permissions.

        Returns:
            True if save was successful, False otherwise
        """
        try:
            # Ensure config directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            # Write config file
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, sort_keys=True)

            # Set restrictive file permissions (owner read/write only)
            # This is important for protecting the API key
            if os.name != "nt":  # Unix-like systems
                os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)

            log.info("Configuration saved successfully")
            return True
        except (OSError, json.JSONDecodeError) as error:
            log.error(f"Failed to save configuration: {error}")
            return False

    def get_api_key(self) -> Optional[str]:
        """
        Get the API key from configuration.

        Returns:
            API key if set, None otherwise
        """
        api_key = self._config.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip()
        return None

    def set_api_key(self, api_key: str) -> None:
        """
        Set the API key in configuration.

        Args:
            api_key: The API key to store
        """
        self._config["api_key"] = api_key.strip()

    def get_recipient_id(self) -> Optional[str]:
        """
        Get the recipient ID from configuration.

        Returns:
            Recipient ID if set, None otherwise
        """
        recipient_id = self._config.get("recipient_id")
        if isinstance(recipient_id, str) and recipient_id.strip():
            return recipient_id.strip()
        return None

    def set_recipient_id(self, recipient_id: str) -> None:
        """
        Set the recipient ID in configuration.

        Args:
            recipient_id: The recipient ID to store
        """
        self._config["recipient_id"] = recipient_id.strip()

    def get_backend_url(self) -> Optional[str]:
        """
        Get the backend URL from configuration.

        Returns:
            Backend URL if set, None otherwise
        """
        backend_url = self._config.get("backend_url")
        if isinstance(backend_url, str) and backend_url.strip():
            return backend_url.strip()
        return None

    def set_backend_url(self, backend_url: str) -> None:
        """
        Set the backend URL in configuration.

        Args:
            backend_url: The backend URL to store
        """
        self._config["backend_url"] = backend_url.strip()

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration value by key.

        Args:
            key: Configuration key
            value: Value to store
        """
        self._config[key] = value

    def is_configured(self) -> bool:
        """
        Check if the client is properly configured.

        Returns:
            True if API key and recipient ID are set, False otherwise
        """
        return bool(self.get_api_key() and self.get_recipient_id())

    def validate_api_key_format(self, api_key: str) -> tuple[bool, str]:
        """
        Validate API key format.

        Args:
            api_key: The API key to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not api_key or not api_key.strip():
            return False, "API key cannot be empty"

        api_key = api_key.strip()

        # Check if it starts with 'pk_'
        if not api_key.startswith("pk_"):
            return False, "API key must start with 'pk_'"

        # Check minimum length
        if len(api_key) < 10:
            return False, "API key is too short"

        return True, ""

    def get_masked_api_key(self) -> str:
        """
        Get a masked version of the API key for display.

        Returns:
            Masked API key (e.g., "pk_***xyz") or empty string if not set
        """
        api_key = self.get_api_key()
        if not api_key:
            return ""

        if len(api_key) <= 6:
            return "***"

        # Show first 3 and last 3 characters
        return f"{api_key[:3]}...{api_key[-3:]}"

    def clear(self) -> None:
        """Clear all configuration."""
        self._config = {}

    def to_dict(self) -> Dict[str, Any]:
        """
        Get a copy of the configuration dictionary.

        Returns:
            Copy of configuration
        """
        return dict(self._config)


# Global instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """
    Get the global ConfigManager instance.

    Returns:
        Global ConfigManager instance
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
