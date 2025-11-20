"""Settings window GUI for configuring the printer client."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

import requests

from .config_manager import ConfigManager

log = logging.getLogger(__name__)


class SettingsWindow:
    """Settings dialog for configuring API keys and client settings."""

    def __init__(self, parent: tk.Tk, config_manager: ConfigManager, on_save_callback: Optional[callable] = None) -> None:
        """
        Initialize the settings window.

        Args:
            parent: Parent tkinter window
            config_manager: ConfigManager instance to use
            on_save_callback: Optional callback to call after successful save
        """
        self.parent = parent
        self.config_manager = config_manager
        self.on_save_callback = on_save_callback

        # Create dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Settings")
        self.dialog.geometry("500x400")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (400 // 2)
        self.dialog.geometry(f"500x400+{x}+{y}")

        # API key visibility state
        self.api_key_visible = False

        # Build UI
        self._build_ui()
        self._load_current_settings()

    def _build_ui(self) -> None:
        """Build the settings UI."""
        # Main container with padding
        main_frame = ttk.Frame(self.dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title_label = ttk.Label(main_frame, text="Printer Client Settings", font=("", 14, "bold"))
        title_label.pack(pady=(0, 20))

        # API Key section
        api_key_frame = ttk.LabelFrame(main_frame, text="API Key", padding="10")
        api_key_frame.pack(fill=tk.X, pady=(0, 10))

        # API Key input with show/hide button
        api_key_input_frame = ttk.Frame(api_key_frame)
        api_key_input_frame.pack(fill=tk.X)

        ttk.Label(api_key_input_frame, text="API Key:").pack(side=tk.LEFT, padx=(0, 5))

        self.api_key_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(api_key_input_frame, textvariable=self.api_key_var, show="*", width=35)
        self.api_key_entry.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)

        # Show/Hide button
        self.show_hide_btn = ttk.Button(
            api_key_input_frame,
            text="👁",
            width=3,
            command=self._toggle_api_key_visibility
        )
        self.show_hide_btn.pack(side=tk.LEFT)

        # API key validation label
        self.api_key_validation_label = ttk.Label(api_key_frame, text="", foreground="red")
        self.api_key_validation_label.pack(fill=tk.X, pady=(5, 0))

        # Bind validation to key release
        self.api_key_var.trace_add("write", self._validate_api_key)

        # Recipient ID section
        recipient_frame = ttk.LabelFrame(main_frame, text="Recipient ID", padding="10")
        recipient_frame.pack(fill=tk.X, pady=(0, 10))

        recipient_input_frame = ttk.Frame(recipient_frame)
        recipient_input_frame.pack(fill=tk.X)

        ttk.Label(recipient_input_frame, text="Recipient ID:").pack(side=tk.LEFT, padx=(0, 5))

        self.recipient_id_var = tk.StringVar()
        self.recipient_id_entry = ttk.Entry(recipient_input_frame, textvariable=self.recipient_id_var, width=35)
        self.recipient_id_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Backend URL section (optional)
        backend_frame = ttk.LabelFrame(main_frame, text="Backend URL (Optional)", padding="10")
        backend_frame.pack(fill=tk.X, pady=(0, 10))

        backend_input_frame = ttk.Frame(backend_frame)
        backend_input_frame.pack(fill=tk.X)

        ttk.Label(backend_input_frame, text="Backend URL:").pack(side=tk.LEFT, padx=(0, 5))

        self.backend_url_var = tk.StringVar()
        self.backend_url_entry = ttk.Entry(backend_input_frame, textvariable=self.backend_url_var, width=35)
        self.backend_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Help text
        help_text = ttk.Label(
            backend_frame,
            text="Leave empty to use default backend",
            font=("", 8),
            foreground="gray"
        )
        help_text.pack(fill=tk.X, pady=(5, 0))

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        # Test Connection button
        self.test_btn = ttk.Button(button_frame, text="Test Connection", command=self._test_connection)
        self.test_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Spacer
        ttk.Frame(button_frame).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Cancel button
        cancel_btn = ttk.Button(button_frame, text="Cancel", command=self._cancel)
        cancel_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Save button
        self.save_btn = ttk.Button(button_frame, text="Save", command=self._save)
        self.save_btn.pack(side=tk.LEFT)

        # Status label at bottom
        self.status_label = ttk.Label(main_frame, text="", foreground="blue")
        self.status_label.pack(fill=tk.X, pady=(10, 0))

    def _load_current_settings(self) -> None:
        """Load current settings from config manager."""
        api_key = self.config_manager.get_api_key()
        if api_key:
            self.api_key_var.set(api_key)

        recipient_id = self.config_manager.get_recipient_id()
        if recipient_id:
            self.recipient_id_var.set(recipient_id)

        backend_url = self.config_manager.get_backend_url()
        if backend_url:
            self.backend_url_var.set(backend_url)

    def _toggle_api_key_visibility(self) -> None:
        """Toggle API key visibility."""
        self.api_key_visible = not self.api_key_visible
        if self.api_key_visible:
            self.api_key_entry.config(show="")
            self.show_hide_btn.config(text="🙈")
        else:
            self.api_key_entry.config(show="*")
            self.show_hide_btn.config(text="👁")

    def _validate_api_key(self, *args) -> None:
        """Validate API key format as user types."""
        api_key = self.api_key_var.get()

        if not api_key:
            self.api_key_validation_label.config(text="")
            return

        is_valid, error_message = self.config_manager.validate_api_key_format(api_key)

        if is_valid:
            self.api_key_validation_label.config(text="✓ Valid format", foreground="green")
        else:
            self.api_key_validation_label.config(text=f"✗ {error_message}", foreground="red")

    def _test_connection(self) -> None:
        """Test connection to the backend with current settings."""
        api_key = self.api_key_var.get().strip()
        recipient_id = self.recipient_id_var.get().strip()
        backend_url = self.backend_url_var.get().strip()

        if not api_key:
            messagebox.showerror("Error", "Please enter an API key")
            return

        if not recipient_id:
            messagebox.showerror("Error", "Please enter a Recipient ID")
            return

        # Validate API key format
        is_valid, error_message = self.config_manager.validate_api_key_format(api_key)
        if not is_valid:
            messagebox.showerror("Invalid API Key", error_message)
            return

        # Determine backend URL
        if backend_url:
            test_url = backend_url.rstrip("/")
        else:
            # Use default backend
            test_url = "https://printpro3d-api-931368217793.europe-west1.run.app"

        # Build status endpoint
        status_endpoint = f"{test_url}/api/recipients/{recipient_id}/status/update"

        self.status_label.config(text="Testing connection...", foreground="blue")
        self.test_btn.config(state=tk.DISABLED)
        self.dialog.update()

        try:
            # Try a simple test request
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": api_key
            }

            # Send a minimal test payload
            test_payload = {
                "recipientId": recipient_id,
                "printerSerial": "TEST_PRINTER",
                "printerIpAddress": "0.0.0.0",
                "status": {
                    "status": "Testing",
                    "online": False,
                    "mqttReady": False
                }
            }

            response = requests.post(
                status_endpoint,
                json=test_payload,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                self.status_label.config(text="✓ Connection successful!", foreground="green")
                messagebox.showinfo("Success", "Connection test successful!")
            elif response.status_code == 401 or response.status_code == 403:
                self.status_label.config(text="✗ Authentication failed", foreground="red")
                messagebox.showerror("Authentication Failed", "Invalid API key or insufficient permissions")
            else:
                self.status_label.config(
                    text=f"✗ Connection failed (HTTP {response.status_code})",
                    foreground="red"
                )
                messagebox.showerror(
                    "Connection Failed",
                    f"Server returned HTTP {response.status_code}\n\n{response.text[:200]}"
                )

        except requests.Timeout:
            self.status_label.config(text="✗ Connection timeout", foreground="red")
            messagebox.showerror("Timeout", "Connection to server timed out")
        except requests.RequestException as error:
            self.status_label.config(text="✗ Connection failed", foreground="red")
            messagebox.showerror("Connection Error", f"Failed to connect to server:\n{str(error)}")
        finally:
            self.test_btn.config(state=tk.NORMAL)

    def _save(self) -> None:
        """Save settings to config."""
        api_key = self.api_key_var.get().strip()
        recipient_id = self.recipient_id_var.get().strip()
        backend_url = self.backend_url_var.get().strip()

        # Validate required fields
        if not api_key:
            messagebox.showerror("Error", "API key is required")
            return

        if not recipient_id:
            messagebox.showerror("Error", "Recipient ID is required")
            return

        # Validate API key format
        is_valid, error_message = self.config_manager.validate_api_key_format(api_key)
        if not is_valid:
            messagebox.showerror("Invalid API Key", error_message)
            return

        # Save to config
        self.config_manager.set_api_key(api_key)
        self.config_manager.set_recipient_id(recipient_id)

        if backend_url:
            self.config_manager.set_backend_url(backend_url)

        # Save to disk
        if self.config_manager.save():
            messagebox.showinfo("Success", "Settings saved successfully!")

            # Call callback if provided
            if self.on_save_callback:
                try:
                    self.on_save_callback()
                except Exception as error:
                    log.error(f"Save callback failed: {error}")

            self.dialog.destroy()
        else:
            messagebox.showerror("Error", "Failed to save settings to disk")

    def _cancel(self) -> None:
        """Cancel and close the dialog."""
        self.dialog.destroy()


def show_settings_dialog(parent: tk.Tk, config_manager: ConfigManager, on_save_callback: Optional[callable] = None) -> None:
    """
    Show the settings dialog.

    Args:
        parent: Parent tkinter window
        config_manager: ConfigManager instance
        on_save_callback: Optional callback to call after successful save
    """
    SettingsWindow(parent, config_manager, on_save_callback)


def show_first_time_setup(parent: tk.Tk, config_manager: ConfigManager) -> bool:
    """
    Show a first-time setup dialog.

    Args:
        parent: Parent tkinter window
        config_manager: ConfigManager instance

    Returns:
        True if setup was completed, False if cancelled
    """
    # Create a simple message dialog first
    result = messagebox.askyesno(
        "Initial Setup Required",
        "Welcome to Cloud Printer Listener!\n\n"
        "Before you can start using the printer client, you need to configure:\n"
        "- API Key (from the web interface)\n"
        "- Recipient ID\n\n"
        "Would you like to configure these settings now?",
        parent=parent
    )

    if result:
        # Show settings dialog
        setup_completed = False

        def on_save():
            nonlocal setup_completed
            setup_completed = True

        SettingsWindow(parent, config_manager, on_save)
        parent.wait_window()  # Wait for dialog to close

        return setup_completed
    else:
        return False
