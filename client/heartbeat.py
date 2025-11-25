"""Heartbeat worker for sending periodic signals to the backend server."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)


class HeartbeatWorker:
    """Worker that sends periodic heartbeat signals to the backend server."""

    def __init__(
        self,
        base_url: str,
        recipient_id: str,
        jwt_token: Optional[str] = None,
        interval: int = 20,
        on_status_change: Optional[Callable[[bool, Optional[str]], None]] = None,
    ):
        """
        Initialize the HeartbeatWorker.

        Args:
            base_url: The base URL of the backend server
            recipient_id: The recipient ID to send in heartbeat requests
            jwt_token: JWT token for authentication (optional, can be read from env)
            interval: Interval in seconds between heartbeat requests (default: 20)
            on_status_change: Optional callback function(is_active, last_timestamp)
        """
        self.base_url = base_url.rstrip("/")
        self.recipient_id = recipient_id
        self.jwt_token = jwt_token or os.getenv("PRINTRELAY_JWT_TOKEN", "").strip()
        self.interval = interval
        self.on_status_change = on_status_change

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.last_success_time: Optional[str] = None

        # Exponential backoff settings
        self.min_backoff = 1
        self.max_backoff = 60
        self.current_backoff = self.min_backoff

    def start(self) -> None:
        """Start the heartbeat worker in a background thread."""
        if self.running:
            log.warning("HeartbeatWorker is already running")
            return

        if not self.base_url or not self.recipient_id:
            log.error("Cannot start HeartbeatWorker: missing base_url or recipient_id")
            return

        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        log.info(f"HeartbeatWorker started (interval: {self.interval}s)")

        # Notify status change
        if self.on_status_change:
            try:
                self.on_status_change(True, self.last_success_time)
            except Exception as error:
                log.error(f"Error in status change callback: {error}")

    def stop(self) -> None:
        """Stop the heartbeat worker gracefully."""
        if not self.running:
            return

        log.info("Stopping HeartbeatWorker...")
        self.running = False
        self.stop_event.set()

        # Wait for thread to finish (with timeout)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                log.warning("HeartbeatWorker thread did not stop within timeout")

        self.thread = None
        log.info("HeartbeatWorker stopped")

        # Notify status change
        if self.on_status_change:
            try:
                self.on_status_change(False, self.last_success_time)
            except Exception as error:
                log.error(f"Error in status change callback: {error}")

    def _worker(self) -> None:
        """Main worker loop that sends periodic heartbeat requests."""
        log.debug("HeartbeatWorker thread started")

        while self.running and not self.stop_event.is_set():
            try:
                success = self._send_heartbeat()

                if success:
                    # Reset backoff on success
                    self.current_backoff = self.min_backoff
                    # Wait for the normal interval
                    wait_time = self.interval
                else:
                    # Use exponential backoff on failure
                    wait_time = self.current_backoff
                    self.current_backoff = min(self.current_backoff * 2, self.max_backoff)
                    log.debug(f"Next retry in {wait_time}s (backoff)")

                # Wait for the specified interval or until stop is requested
                self.stop_event.wait(timeout=wait_time)

            except Exception as error:
                log.error(f"Unexpected error in HeartbeatWorker loop: {error}")
                # Wait a bit before retrying to avoid tight error loops
                self.stop_event.wait(timeout=5.0)

        log.debug("HeartbeatWorker thread finished")

    def _send_heartbeat(self) -> bool:
        """
        Send a heartbeat POST request to the backend.

        Returns:
            True if the request was successful, False otherwise
        """
        url = f"{self.base_url}/api/heartbeat"
        headers = {
            "Content-Type": "application/json",
        }

        # Add Authorization header if JWT token is available
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"

        payload = {
            "recipientId": self.recipient_id,
        }

        try:
            log.debug(f"Sending heartbeat to {url}")
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=10.0,
            )

            if response.status_code == 200:
                log.info(f"Heartbeat sent successfully to {url}")
                self.last_success_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Notify status change
                if self.on_status_change:
                    try:
                        self.on_status_change(True, self.last_success_time)
                    except Exception as error:
                        log.error(f"Error in status change callback: {error}")

                return True
            else:
                log.warning(
                    f"Heartbeat request failed with status {response.status_code}: {response.text}"
                )
                return False

        except requests.exceptions.Timeout:
            log.warning(f"Heartbeat request timed out: {url}")
            return False
        except requests.exceptions.ConnectionError as error:
            log.warning(f"Heartbeat connection error: {error}")
            return False
        except requests.exceptions.RequestException as error:
            log.error(f"Heartbeat request error: {error}")
            return False
        except Exception as error:
            log.error(f"Unexpected error sending heartbeat: {error}")
            return False

    def is_running(self) -> bool:
        """
        Check if the heartbeat worker is currently running.

        Returns:
            True if running, False otherwise
        """
        return self.running and self.thread is not None and self.thread.is_alive()

    def get_last_success_time(self) -> Optional[str]:
        """
        Get the timestamp of the last successful heartbeat.

        Returns:
            Timestamp string or None if no successful heartbeat yet
        """
        return self.last_success_time
