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
        self.dialog.geometry("500x550")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (550 // 2)
        self.dialog.geometry(f"500x550+{x}+{y}")

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

        # Recipient ID section (auto-generated, read-only with show/hide/rotate)
        recipient_frame = ttk.LabelFrame(main_frame, text="Recipient ID (Auto-generated)", padding="10")
        recipient_frame.pack(fill=tk.X, pady=(0, 10))

        recipient_input_frame = ttk.Frame(recipient_frame)
        recipient_input_frame.pack(fill=tk.X)

        ttk.Label(recipient_input_frame, text="Recipient ID:").pack(side=tk.LEFT, padx=(0, 5))

        self.recipient_id_var = tk.StringVar()
        self.recipient_id_entry = ttk.Entry(
            recipient_input_frame,
            textvariable=self.recipient_id_var,
            width=30,
            state='readonly',
            show="*"
        )
        self.recipient_id_entry.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)

        # Recipient ID visibility state (hidden by default)
        self.recipient_id_visible = False

        # Show/Hide button for Recipient ID
        self.recipient_show_hide_btn = ttk.Button(
            recipient_input_frame,
            text="👁",
            width=3,
            command=self._toggle_recipient_id_visibility
        )
        self.recipient_show_hide_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Copy to clipboard button for Recipient ID
        self.recipient_copy_btn = ttk.Button(
            recipient_input_frame,
            text="📋",
            width=3,
            command=self._copy_recipient_id_to_clipboard
        )
        self.recipient_copy_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Rotate button for Recipient ID
        self.recipient_rotate_btn = ttk.Button(
            recipient_input_frame,
            text="🔄",
            width=3,
            command=self._rotate_recipient_id
        )
        self.recipient_rotate_btn.pack(side=tk.LEFT)

        # Help text for Recipient ID
        help_text = ttk.Label(
            recipient_frame,
            text="Auto-generated unique ID. Use 📋 to copy, 🔄 to generate new.",
            font=("", 8),
            foreground="gray"
        )
        help_text.pack(fill=tk.X, pady=(5, 0))

        # Organization ID section (optional)
        org_id_frame = ttk.LabelFrame(main_frame, text="Organization ID (Optional)", padding="10")
        org_id_frame.pack(fill=tk.X, pady=(0, 10))

        org_id_input_frame = ttk.Frame(org_id_frame)
        org_id_input_frame.pack(fill=tk.X)

        ttk.Label(org_id_input_frame, text="Organization ID:").pack(side=tk.LEFT, padx=(0, 5))

        self.organization_id_var = tk.StringVar()
        self.organization_id_entry = ttk.Entry(
            org_id_input_frame,
            textvariable=self.organization_id_var,
            width=30
        )
        self.organization_id_entry.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)

        # Help text for Organization ID
        org_help_text = ttk.Label(
            org_id_frame,
            text="Optional organization identifier for heartbeat tracking.",
            font=("", 8),
            foreground="gray"
        )
        org_help_text.pack(fill=tk.X, pady=(5, 0))

        # Printer Info Update Interval section
        interval_frame = ttk.LabelFrame(main_frame, text="Printer Info Update Interval", padding="10")
        interval_frame.pack(fill=tk.X, pady=(0, 10))

        interval_input_frame = ttk.Frame(interval_frame)
        interval_input_frame.pack(fill=tk.X)

        ttk.Label(interval_input_frame, text="Update Interval:").pack(side=tk.LEFT, padx=(0, 10))

        # Slider for update interval (minimum 3 minutes, maximum 60 minutes)
        self.update_interval_var = tk.IntVar()
        interval_slider = ttk.Scale(
            interval_input_frame,
            from_=3,
            to=60,
            orient=tk.HORIZONTAL,
            length=200,
            variable=self.update_interval_var,
            command=self._on_interval_slider_changed
        )
        interval_slider.pack(side=tk.LEFT, padx=(0, 10))

        self.interval_label = ttk.Label(interval_input_frame, text="5 min")
        self.interval_label.pack(side=tk.LEFT)

        # Help text for Update Interval
        interval_help_text = ttk.Label(
            interval_frame,
            text="How often to automatically fetch detailed printer information (minimum 3 minutes).",
            font=("", 8),
            foreground="gray"
        )
        interval_help_text.pack(fill=tk.X, pady=(5, 0))

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        # Test Connection button
        self.test_btn = ttk.Button(button_frame, text="Test Connection", command=self._test_connection)
        self.test_btn.pack(side=tk.LEFT, padx=(0, 5))

        # Note about test connection
        note_label = ttk.Label(
            button_frame,
            text="(Optional - you can save without testing)",
            font=("", 8),
            foreground="gray"
        )
        note_label.pack(side=tk.LEFT, padx=(5, 0))

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

    def _on_interval_slider_changed(self, value: str) -> None:
        """Handle slider value change for update interval."""
        try:
            minutes = int(float(value))
            self.interval_label.config(text=f"{minutes} min")
        except (ValueError, TypeError):
            pass

    def _load_current_settings(self) -> None:
        """Load current settings from config manager."""
        api_key = self.config_manager.get_api_key()
        if api_key:
            self.api_key_var.set(api_key)

        recipient_id = self.config_manager.get_recipient_id()
        if recipient_id:
            self.recipient_id_var.set(recipient_id)
        else:
            # Generate a new recipient ID if none exists
            self._generate_new_recipient_id()

        organization_id = self.config_manager.get_organization_id()
        if organization_id:
            self.organization_id_var.set(organization_id)

        # Load printer info update interval
        interval_minutes = self.config_manager.get_printer_info_update_interval_minutes()
        self.update_interval_var.set(interval_minutes)
        self.interval_label.config(text=f"{interval_minutes} min")

    def _toggle_api_key_visibility(self) -> None:
        """Toggle API key visibility."""
        self.api_key_visible = not self.api_key_visible
        if self.api_key_visible:
            self.api_key_entry.config(show="")
            self.show_hide_btn.config(text="🙈")
        else:
            self.api_key_entry.config(show="*")
            self.show_hide_btn.config(text="👁")

    def _toggle_recipient_id_visibility(self) -> None:
        """Toggle Recipient ID visibility."""
        self.recipient_id_visible = not self.recipient_id_visible
        current_value = self.recipient_id_var.get()

        # Temporarily change state to normal to modify the entry
        self.recipient_id_entry.config(state='normal')

        if self.recipient_id_visible:
            # Show the full recipient ID
            self.recipient_id_entry.config(show="")
            self.recipient_show_hide_btn.config(text="🙈")
        else:
            # Mask the recipient ID
            self.recipient_id_entry.config(show="*")
            self.recipient_show_hide_btn.config(text="👁")

        # Set it back to readonly
        self.recipient_id_entry.config(state='readonly')

    def _generate_new_recipient_id(self) -> None:
        """Generate a new recipient ID."""
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        new_id = "".join(secrets.choice(alphabet) for _ in range(32))
        self.recipient_id_var.set(new_id)

    def _copy_recipient_id_to_clipboard(self) -> None:
        """Copy Recipient ID to clipboard."""
        recipient_id = self.recipient_id_var.get()
        if recipient_id:
            try:
                self.dialog.clipboard_clear()
                self.dialog.clipboard_append(recipient_id)
                self.dialog.update()  # Required for clipboard to work
                self.status_label.config(text="✓ Recipient ID copied to clipboard", foreground="green")
                messagebox.showinfo(
                    "Copied",
                    "Recipient ID copied to clipboard!\n\n"
                    "You can now paste it in your web dashboard.",
                    parent=self.dialog
                )
            except Exception as error:
                log.error(f"Failed to copy to clipboard: {error}")
                messagebox.showerror(
                    "Copy Failed",
                    f"Failed to copy to clipboard.\n\n"
                    f"Recipient ID: {recipient_id}\n\n"
                    "Please copy it manually.",
                    parent=self.dialog
                )
        else:
            messagebox.showwarning(
                "No Recipient ID",
                "No Recipient ID to copy",
                parent=self.dialog
            )

    def _rotate_recipient_id(self) -> None:
        """Generate a new recipient ID (rotate)."""
        result = messagebox.askyesno(
            "Rotate Recipient ID",
            "Are you sure you want to generate a new Recipient ID?\n\n"
            "This will change your printer's identity and may require "
            "updating the ID in your web dashboard.\n\n"
            "Continue?",
            parent=self.dialog
        )

        if result:
            self._generate_new_recipient_id()
            messagebox.showinfo(
                "New ID Generated",
                "A new Recipient ID has been generated.\n\n"
                "Remember to save and update your web dashboard with the new ID!",
                parent=self.dialog
            )

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

        if not api_key:
            messagebox.showerror("Error", "Please enter an API key")
            return

        if not recipient_id:
            messagebox.showerror("Error", "Recipient ID is missing")
            return

        # Validate API key format
        is_valid, error_message = self.config_manager.validate_api_key_format(api_key)
        if not is_valid:
            messagebox.showerror("Invalid API Key", error_message)
            return

        # Use hardcoded backend URL
        test_url = "https://printpro3d-api-931368217793.europe-west1.run.app"

        # Build status endpoint
        status_endpoint = f"{test_url}/printer-status"

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
                "printerSerial": "TEST_CONNECTION",
                "printerIpAddress": "0.0.0.0",
                "status": "idle"
            }

            log.info(f"Testing connection to {status_endpoint}")
            log.debug(f"Request payload: recipientId={recipient_id}, printerSerial=TEST_PRINTER")

            response = requests.post(
                status_endpoint,
                json=test_payload,
                headers=headers,
                timeout=10
            )

            log.info(f"Response status: {response.status_code}")

            if response.status_code == 200:
                self.status_label.config(text="✓ Connection successful!", foreground="green")
                messagebox.showinfo("Success", "Connection test successful!")
            elif response.status_code == 401 or response.status_code == 403:
                self.status_label.config(text="✗ Authentication failed", foreground="red")
                messagebox.showerror("Authentication Failed", "Invalid API key or insufficient permissions")
            elif response.status_code == 400:
                self.status_label.config(text="✗ Recipient ID not registered", foreground="orange")
                messagebox.showwarning(
                    "Recipient ID Not Registered",
                    "The Recipient ID is not yet registered in the system.\n\n"
                    "To register your Recipient ID:\n"
                    "1. Copy your Recipient ID (click 👁 to show it)\n"
                    "2. Go to your web dashboard\n"
                    "3. Add/register this Recipient ID in your organization settings\n"
                    "4. Then try testing the connection again\n\n"
                    "Note: You can still save these settings and they will work once "
                    "the Recipient ID is registered in the dashboard."
                )
            elif response.status_code == 500:
                self.status_label.config(text="✗ Server error", foreground="red")
                error_details = ""
                try:
                    error_json = response.json()
                    error_details = error_json.get('error', str(error_json))
                except Exception:
                    error_details = response.text[:200]

                log.error(f"HTTP 500 error during connection test. Response: {response.text}")
                messagebox.showerror(
                    "Server Error",
                    "The server encountered an error while processing your request.\n\n"
                    "This might mean:\n"
                    "• The Recipient ID needs to be properly registered in the web dashboard\n"
                    "• There's a temporary server issue\n\n"
                    "You can still save your settings. They should work once the "
                    "server issue is resolved or the Recipient ID is properly registered.\n\n"
                    f"Technical details: {error_details}"
                )
            else:
                self.status_label.config(
                    text=f"✗ Connection failed (HTTP {response.status_code})",
                    foreground="red"
                )
                log.error(f"HTTP {response.status_code} error during connection test. Response: {response.text}")
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
        organization_id = self.organization_id_var.get().strip()

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
        self.config_manager.set_organization_id(organization_id)
        
        # Save printer info update interval
        interval_minutes = self.update_interval_var.get()
        self.config_manager.set_printer_info_update_interval_minutes(interval_minutes)

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
