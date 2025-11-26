"""Unit tests for the HeartbeatWorker."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from client.heartbeat import HeartbeatWorker


class TestHeartbeatWorker(unittest.TestCase):
    """Test cases for HeartbeatWorker."""

    def test_init(self) -> None:
        """Test HeartbeatWorker initialization."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
            interval_seconds=20.0,
            client_version="1.0.0",
        )

        self.assertEqual(worker.base_url, "https://example.com")
        self.assertEqual(worker.recipient_id, "test123")
        self.assertEqual(worker.auth_token, "test_token_123")
        self.assertEqual(worker.interval_seconds, 20.0)
        self.assertEqual(worker.client_version, "1.0.0")
        self.assertFalse(worker.is_running())
        self.assertIsNone(worker._thread)
        self.assertIsNone(worker._last_success)

    def test_init_strips_trailing_slash(self) -> None:
        """Test that base URL trailing slash is removed."""
        worker = HeartbeatWorker(
            base_url="https://example.com/",
            recipient_id="test123",
            auth_token="test_token_123",
        )
        self.assertEqual(worker.base_url, "https://example.com")

    def test_init_minimum_interval(self) -> None:
        """Test that interval has a minimum value of 10 seconds."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
            interval_seconds=5.0,  # Below minimum
        )
        self.assertEqual(worker.interval_seconds, 10.0)  # Should be clamped to minimum

    def test_start_stop(self) -> None:
        """Test starting and stopping the worker."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
            interval_seconds=10.0,
        )

        # Mock the _send_heartbeat method to avoid network calls
        with patch.object(worker, "_send_heartbeat"):
            worker.start()
            self.assertTrue(worker.is_running())
            self.assertIsNotNone(worker._thread)

            # Wait a bit to ensure thread started
            time.sleep(0.1)
            self.assertTrue(worker.is_running())

            worker.stop()
            self.assertFalse(worker.is_running())

    def test_start_already_running(self) -> None:
        """Test that starting an already running worker is safe."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
            interval_seconds=10.0,
        )

        with patch.object(worker, "_send_heartbeat"):
            worker.start()
            first_thread = worker._thread

            # Try to start again
            worker.start()
            second_thread = worker._thread

            # Thread should be the same
            self.assertIs(first_thread, second_thread)

            worker.stop()

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_success(self, mock_post: Mock) -> None:
        """Test successful heartbeat sending."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"lastHeartbeat": "2025-11-25T12:00:00Z"}
        mock_post.return_value = mock_response

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
        )

        worker._send_heartbeat()

        # Should succeed
        self.assertIsNotNone(worker._last_success)
        self.assertEqual(worker._consecutive_failures, 0)

        # Verify the request
        mock_post.assert_called_once_with(
            "https://example.com/api/heartbeat",
            json={
                "recipientId": "test123",
                "clientVersion": "1.0.0",
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_token_123",
            },
            timeout=10,
        )

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_failure(self, mock_post: Mock) -> None:
        """Test heartbeat sending with non-200 status."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = Exception("HTTP 500")
        mock_post.return_value = mock_response

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
        )

        worker._send_heartbeat()

        # Should fail and increment failure counter
        self.assertEqual(worker._consecutive_failures, 1)

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_network_error(self, mock_post: Mock) -> None:
        """Test heartbeat sending with network error."""
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError("Network error")

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
        )

        worker._send_heartbeat()

        # Should fail and increment failure counter
        self.assertEqual(worker._consecutive_failures, 1)

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_timeout(self, mock_post: Mock) -> None:
        """Test heartbeat sending with timeout."""
        import requests

        mock_post.side_effect = requests.exceptions.Timeout("Request timeout")

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
        )

        worker._send_heartbeat()

        # Should fail and increment failure counter
        self.assertEqual(worker._consecutive_failures, 1)

    def test_exponential_backoff(self) -> None:
        """Test that exponential backoff is applied on failures."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
            interval_seconds=20.0,
        )

        # Initial interval should be the configured interval
        self.assertEqual(worker._get_current_interval(), 20.0)

        # Simulate failures
        worker._consecutive_failures = 1
        self.assertEqual(worker._get_current_interval(), 40.0)  # 20 * 2^1

        worker._consecutive_failures = 2
        self.assertEqual(worker._get_current_interval(), 60.0)  # 20 * 2^2 = 80, clamped to 60

        worker._consecutive_failures = 10
        self.assertEqual(worker._get_current_interval(), 60.0)  # Should cap at 60

    @patch("client.heartbeat.requests.post")
    def test_reset_backoff_on_success(self, mock_post: Mock) -> None:
        """Test that backoff resets on successful request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"lastHeartbeat": "2025-11-25T12:00:00Z"}
        mock_post.return_value = mock_response

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="test_token_123",
        )

        # Set some failures
        worker._consecutive_failures = 3

        # Send successful heartbeat
        worker._send_heartbeat()

        # Failures should be reset
        self.assertEqual(worker._consecutive_failures, 0)

    def test_mask_token(self) -> None:
        """Test auth token masking for logging."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            auth_token="this_is_a_very_long_auth_token_12345",
        )

        # Test normal token masking
        masked = worker._mask_token("this_is_a_very_long_auth_token_12345")
        self.assertEqual(masked, "this_...12345")

        # Test short token
        masked_short = worker._mask_token("short")
        self.assertEqual(masked_short, "***")

        # Test empty token
        masked_empty = worker._mask_token("")
        self.assertEqual(masked_empty, "***")


if __name__ == "__main__":
    unittest.main()
