import unittest
from unittest.mock import Mock, patch
from client.event_reporter import EventReporter


class TestEventReporter(unittest.TestCase):
    def setUp(self):
        self.reporter = EventReporter(
            base_url="https://test-api.com",
            api_key="test-key",
            recipient_id="test-recipient"
        )

    def test_init(self):
        """Test EventReporter initialization"""
        self.assertEqual(self.reporter.base_url, "https://test-api.com")
        self.assertEqual(self.reporter.api_key, "test-key")
        self.assertEqual(self.reporter.recipient_id, "test-recipient")
        self.assertEqual(self.reporter.report_url, "https://test-api.com/api/printer-events/report")

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is removed from base_url"""
        reporter = EventReporter(
            base_url="https://test-api.com/",
            api_key="test-key",
            recipient_id="test-recipient"
        )
        self.assertEqual(reporter.base_url, "https://test-api.com")

    @patch('requests.post')
    def test_report_event_success(self, mock_post):
        """Test successful event reporting"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        event_id = self.reporter.report_event(
            event_type="job_completed",
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            event_status="success"
        )

        self.assertEqual(event_id, "test-event-id")
        mock_post.assert_called_once()

        # Check the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['recipientId'], 'test-recipient')
        self.assertEqual(payload['printerSerial'], '01P00A123')
        self.assertEqual(payload['printerIpAddress'], '192.168.1.100')
        self.assertEqual(payload['eventType'], 'job_completed')
        self.assertEqual(payload['eventStatus'], 'success')

    @patch('requests.post')
    def test_report_event_with_all_fields(self, mock_post):
        """Test event reporting with all optional fields"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        event_id = self.reporter.report_event(
            event_type="hms_error",
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            event_status="failed",
            print_job_id="job-123",
            error_data={"hmsCode": "0300_0300_0002_0003"},
            status_data={"progress": 50},
            message="Test error message"
        )

        self.assertEqual(event_id, "test-event-id")

        # Check the payload includes all fields
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['printJobId'], 'job-123')
        self.assertEqual(payload['errorData'], {"hmsCode": "0300_0300_0002_0003"})
        self.assertEqual(payload['statusData'], {"progress": 50})
        self.assertEqual(payload['message'], 'Test error message')

    @patch('requests.post')
    def test_report_event_failure(self, mock_post):
        """Test event reporting failure handling"""
        mock_post.side_effect = Exception("Network error")

        event_id = self.reporter.report_event(
            event_type="job_failed",
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            event_status="failed"
        )

        self.assertIsNone(event_id)

    @patch('requests.post')
    def test_upload_event_image_success(self, mock_post):
        """Test successful image upload"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        image_data = b"fake image data"
        result = self.reporter.upload_event_image("event-123", image_data)

        self.assertTrue(result)
        mock_post.assert_called_once()

        # Check that files were included
        call_args = mock_post.call_args
        self.assertIn('files', call_args[1])

    @patch('requests.post')
    def test_upload_event_image_failure(self, mock_post):
        """Test image upload failure handling"""
        mock_post.side_effect = Exception("Upload error")

        image_data = b"fake image data"
        result = self.reporter.upload_event_image("event-123", image_data)

        self.assertFalse(result)

    @patch('requests.post')
    def test_report_job_started(self, mock_post):
        """Test job_started convenience method"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        event_id = self.reporter.report_job_started(
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            print_job_id="job-123",
            file_name="test.3mf",
            estimated_time=3600,
            plates_requested=2
        )

        self.assertEqual(event_id, "test-event-id")

        # Check the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['eventType'], 'job_started')
        self.assertEqual(payload['eventStatus'], 'info')
        self.assertEqual(payload['printJobId'], 'job-123')
        self.assertIn('fileName', payload['statusData'])
        self.assertEqual(payload['statusData']['fileName'], 'test.3mf')

    @patch('requests.post')
    def test_report_job_completed(self, mock_post):
        """Test job_completed convenience method"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        event_id = self.reporter.report_job_completed(
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            print_job_id="job-123",
            file_name="test.3mf"
        )

        self.assertEqual(event_id, "test-event-id")

        # Check the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['eventType'], 'job_completed')
        self.assertEqual(payload['eventStatus'], 'success')

    @patch('requests.post')
    def test_report_job_failed(self, mock_post):
        """Test job_failed convenience method"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        event_id = self.reporter.report_job_failed(
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            print_job_id="job-123",
            file_name="test.3mf",
            error_message="Print failed"
        )

        self.assertEqual(event_id, "test-event-id")

        # Check the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['eventType'], 'job_failed')
        self.assertEqual(payload['eventStatus'], 'failed')
        self.assertIn('Print failed', payload['message'])

    @patch('requests.post')
    def test_report_hms_error(self, mock_post):
        """Test HMS error reporting convenience method"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        error_data = {
            "hmsCode": "0300_0300_0002_0003",
            "description": "Hotbed heating abnormal",
            "severity": "critical",
            "module": "hotbed"
        }

        event_id = self.reporter.report_hms_error(
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            hms_code="0300_0300_0002_0003",
            error_data=error_data
        )

        self.assertEqual(event_id, "test-event-id")

        # Check the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['eventType'], 'hms_error')
        self.assertEqual(payload['eventStatus'], 'failed')
        self.assertEqual(payload['errorData'], error_data)

    @patch('requests.post')
    def test_report_hms_error_with_image(self, mock_post):
        """Test HMS error reporting with image upload"""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"eventId": "test-event-id"}
        mock_post.return_value = mock_response

        error_data = {
            "hmsCode": "0300_0300_0002_0003",
            "description": "Hotbed heating abnormal",
            "severity": "critical",
            "module": "hotbed"
        }
        image_data = b"fake image data"

        event_id = self.reporter.report_hms_error(
            printer_serial="01P00A123",
            printer_ip="192.168.1.100",
            hms_code="0300_0300_0002_0003",
            error_data=error_data,
            image_data=image_data
        )

        self.assertEqual(event_id, "test-event-id")

        # Should have been called twice: once for event, once for image
        self.assertEqual(mock_post.call_count, 2)


if __name__ == '__main__':
    unittest.main()
