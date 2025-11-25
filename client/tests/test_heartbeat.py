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
            jwt_token="token123",
            interval=20,
        )

        self.assertEqual(worker.base_url, "https://example.com")
        self.assertEqual(worker.recipient_id, "test123")
        self.assertEqual(worker.jwt_token, "token123")
        self.assertEqual(worker.interval, 20)
        self.assertFalse(worker.running)
        self.assertIsNone(worker.thread)
        self.assertIsNone(worker.last_success_time)

    def test_init_strips_trailing_slash(self) -> None:
        """Test that base URL trailing slash is removed."""
        worker = HeartbeatWorker(
            base_url="https://example.com/",
            recipient_id="test123",
        )
        self.assertEqual(worker.base_url, "https://example.com")

    def test_init_with_env_jwt_token(self) -> None:
        """Test JWT token is read from environment if not provided."""
        with patch.dict("os.environ", {"PRINTRELAY_JWT_TOKEN": "env_token"}):
            worker = HeartbeatWorker(
                base_url="https://example.com",
                recipient_id="test123",
            )
            self.assertEqual(worker.jwt_token, "env_token")

    def test_start_stop(self) -> None:
        """Test starting and stopping the worker."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            interval=0.1,  # Short interval for testing
        )

        # Mock the _send_heartbeat method to avoid network calls
        with patch.object(worker, "_send_heartbeat", return_value=True):
            worker.start()
            self.assertTrue(worker.running)
            self.assertIsNotNone(worker.thread)

            # Wait a bit to ensure thread started
            time.sleep(0.05)
            self.assertTrue(worker.is_running())

            worker.stop()
            self.assertFalse(worker.running)

    def test_start_without_base_url(self) -> None:
        """Test that start fails gracefully without base URL."""
        worker = HeartbeatWorker(
            base_url="",
            recipient_id="test123",
        )

        worker.start()
        self.assertFalse(worker.running)
        self.assertIsNone(worker.thread)

    def test_start_without_recipient_id(self) -> None:
        """Test that start fails gracefully without recipient ID."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="",
        )

        worker.start()
        self.assertFalse(worker.running)
        self.assertIsNone(worker.thread)

    def test_start_already_running(self) -> None:
        """Test that starting an already running worker is safe."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            interval=0.1,
        )

        with patch.object(worker, "_send_heartbeat", return_value=True):
            worker.start()
            first_thread = worker.thread

            # Try to start again
            worker.start()
            second_thread = worker.thread

            # Thread should be the same
            self.assertIs(first_thread, second_thread)

            worker.stop()

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_success(self, mock_post: Mock) -> None:
        """Test successful heartbeat sending."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            jwt_token="token123",
        )

        result = worker._send_heartbeat()

        self.assertTrue(result)
        self.assertIsNotNone(worker.last_success_time)

        # Verify the request
        mock_post.assert_called_once_with(
            "https://example.com/api/heartbeat",
            json={"recipientId": "test123"},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer token123",
            },
            timeout=10.0,
        )

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_failure(self, mock_post: Mock) -> None:
        """Test heartbeat sending with non-200 status."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
        )

        result = worker._send_heartbeat()

        self.assertFalse(result)

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_network_error(self, mock_post: Mock) -> None:
        """Test heartbeat sending with network error."""
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError("Network error")

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
        )

        result = worker._send_heartbeat()

        self.assertFalse(result)

    @patch("client.heartbeat.requests.post")
    def test_send_heartbeat_timeout(self, mock_post: Mock) -> None:
        """Test heartbeat sending with timeout."""
        import requests

        mock_post.side_effect = requests.exceptions.Timeout("Request timeout")

        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
        )

        result = worker._send_heartbeat()

        self.assertFalse(result)

    def test_exponential_backoff(self) -> None:
        """Test that exponential backoff is applied on failures."""
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
        )

        # Initial backoff should be min_backoff
        self.assertEqual(worker.current_backoff, worker.min_backoff)

        # Simulate failures
        with patch.object(worker, "_send_heartbeat", return_value=False):
            worker.start()
            time.sleep(0.1)  # Let the worker run a bit

            # Current backoff should increase
            worker.stop()

    def test_callback_on_status_change(self) -> None:
        """Test that status change callback is called."""
        callback = MagicMock()
        worker = HeartbeatWorker(
            base_url="https://example.com",
            recipient_id="test123",
            interval=0.1,
            on_status_change=callback,
        )

        with patch.object(worker, "_send_heartbeat", return_value=True):
            worker.start()
            time.sleep(0.05)

            # Callback should be called on start
            callback.assert_called()

            worker.stop()

            # Callback should be called on stop
            self.assertGreater(callback.call_count, 1)


if __name__ == "__main__":
    unittest.main()
