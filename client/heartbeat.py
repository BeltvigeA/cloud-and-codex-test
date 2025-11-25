"""Background worker that sends periodic heartbeat signals to the backend."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)


class HeartbeatWorker:
    """Background worker that sends periodic heartbeat signals to the backend."""

    def __init__(
        self,
        base_url: str,
        recipient_id: str,
        jwt_token: str,
        interval_seconds: float = 20.0,
        client_version: str = "1.0.0",
    ) -> None:
        """
        Initialize the HeartbeatWorker.

        Args:
            base_url: The base URL of the backend server
            recipient_id: The recipient ID to send in heartbeat requests
            jwt_token: JWT token for authentication
            interval_seconds: Interval in seconds between heartbeat requests (default: 20.0, min: 10.0)
            client_version: Version string of the client (default: "1.0.0")
        """
        self.base_url = base_url.rstrip("/")
        self.recipient_id = recipient_id.strip()
        self.jwt_token = jwt_token.strip()
        self.interval_seconds = max(10.0, float(interval_seconds))
        self.client_version = client_version

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_success: Optional[float] = None
        self._consecutive_failures = 0

    def start(self) -> None:
        """Start the heartbeat worker thread."""
        if self._thread and self._thread.is_alive():
            log.debug("Heartbeat worker already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop, name="HeartbeatWorker", daemon=True
        )
        self._thread.start()
        log.info("Heartbeat worker started (interval: %.1fs)", self.interval_seconds)

    def stop(self) -> None:
        """Stop the heartbeat worker thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        log.info("Heartbeat worker stopped")

    def is_running(self) -> bool:
        """Check if the heartbeat worker is currently running."""
        return self._thread is not None and self._thread.is_alive()

    def _worker_loop(self) -> None:
        """Main worker loop that sends heartbeats periodically."""
        while not self._stop_event.is_set():
            try:
                self._send_heartbeat()
            except Exception as error:  # noqa: BLE001 - prevent thread crash
                log.error("Unexpected error in heartbeat worker: %s", error)

            # Sleep in small intervals to allow quick shutdown
            sleep_slices = max(1, int(self._get_current_interval() / 0.5))
            for _ in range(sleep_slices):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)

    def _get_current_interval(self) -> float:
        """Get current interval with exponential backoff on failures."""
        if self._consecutive_failures == 0:
            return self.interval_seconds

        # Exponential backoff: 20s, 40s, 60s (max)
        backoff = min(self.interval_seconds * (2**self._consecutive_failures), 60.0)
        return backoff

    def _send_heartbeat(self) -> None:
        """Send a single heartbeat request to the backend."""
        endpoint = f"{self.base_url}/api/heartbeat"

        headers = {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
        }

        payload = {"recipientId": self.recipient_id, "clientVersion": self.client_version}

        # Log detailed request information
        masked_token = self._mask_jwt_token(self.jwt_token)
        log.info(
            "Sending heartbeat request:\n"
            "  URL: %s\n"
            "  Method: POST\n"
            "  Headers: {Authorization: Bearer %s, Content-Type: application/json}\n"
            "  Payload: %s",
            endpoint,
            masked_token,
            payload,
        )

        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
            response.raise_for_status()

            # Success
            self._last_success = time.time()
            self._consecutive_failures = 0

            data = response.json()
            log.info(
                "Heartbeat sent successfully (status: %d, recipient: %s, last: %s)",
                response.status_code,
                self.recipient_id,
                data.get("lastHeartbeat", "unknown"),
            )

        except requests.Timeout:
            self._consecutive_failures += 1
            log.warning(
                "Heartbeat request timed out (URL: %s, failures: %d)",
                endpoint,
                self._consecutive_failures,
            )

        except requests.RequestException as error:
            self._consecutive_failures += 1
            log.warning(
                "Heartbeat request failed (URL: %s): %s (failures: %d)",
                endpoint,
                error,
                self._consecutive_failures,
            )

        except Exception as error:  # noqa: BLE001 - catch all to prevent thread crash
            self._consecutive_failures += 1
            log.error(
                "Unexpected error sending heartbeat (URL: %s): %s (failures: %d)",
                endpoint,
                error,
                self._consecutive_failures,
            )

    def _mask_jwt_token(self, token: str) -> str:
        """Mask JWT token for logging, showing only first and last few characters."""
        if not token or len(token) <= 10:
            return "***"
        return f"{token[:5]}...{token[-5:]}"
