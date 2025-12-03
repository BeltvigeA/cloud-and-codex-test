"""
Event Reporter - Sends events to backend
Mirrors command_controller pattern for consistency
"""

import json
import logging
import requests
from typing import Dict, Optional, Any

log = logging.getLogger(__name__)

class EventReporter:
    """
    Handles event reporting to backend.
    Sends job lifecycle events, HMS errors, and status updates.
    """

    def __init__(self, base_url: str, api_key: str, recipient_id: str):
        """
        Initialize event reporter

        Args:
            base_url: Backend API URL (e.g. https://printpro3d-api-931368217793.europe-west1.run.app)
            api_key: API key for authentication
            recipient_id: Unique recipient ID for this client
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.recipient_id = recipient_id
        self.report_url = f"{self.base_url}/api/printer-events/report"
        self.upload_url_template = f"{self.base_url}/api/printer-events/{{event_id}}/upload-image"

        # VERBOSE LOGGING
        log.info("=" * 80)
        log.info("🚀 EventReporter INITIALIZED")
        log.info(f"   Base URL: {self.base_url}")
        log.info(f"   Report URL: {self.report_url}")
        log.info(f"   API Key: {'✅ Present (' + str(len(api_key)) + ' chars)' if api_key else '❌ MISSING'}")
        log.info(f"   Recipient ID: {'✅ ' + recipient_id if recipient_id else '❌ MISSING'}")
        log.info("=" * 80)

    def report_event(
        self,
        event_type: str,
        printer_serial: str,
        printer_ip: str,
        event_status: str = "info",
        print_job_id: Optional[str] = None,
        error_data: Optional[Dict[str, Any]] = None,
        status_data: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
    ) -> Optional[str]:
        """
        Report an event to backend

        Args:
            event_type: Type of event (job_started, job_completed, hms_error, etc.)
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            event_status: Status (success, failed, warning, info)
            print_job_id: Optional print job UUID
            error_data: Optional error details (for HMS errors)
            status_data: Optional status snapshot
            message: Optional human-readable message

        Returns:
            Event ID if successful, None otherwise
        """
        payload = {
            "recipientId": self.recipient_id,
            "printerSerial": printer_serial,
            "printerIpAddress": printer_ip,
            "eventType": event_type,
            "eventStatus": event_status,
        }

        if print_job_id:
            payload["printJobId"] = print_job_id
        if error_data:
            payload["errorData"] = error_data
        if status_data:
            payload["statusData"] = status_data
        if message:
            payload["message"] = message

        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }

        # VERBOSE LOGGING - Before sending
        log.info("─" * 80)
        log.info(f"📤 SENDING EVENT to backend")
        log.info(f"   Event Type: {event_type}")
        log.info(f"   Event Status: {event_status}")
        log.info(f"   Printer Serial: {printer_serial}")
        log.info(f"   Printer IP: {printer_ip}")
        log.info(f"   Print Job ID: {print_job_id or 'None'}")
        log.info(f"   Message: {message or 'None'}")
        log.info(f"   Has Error Data: {'✅ Yes' if error_data else '❌ No'}")
        log.info(f"   Has Status Data: {'✅ Yes' if status_data else '❌ No'}")
        log.info(f"   Target URL: {self.report_url}")
        log.info(f"   API Key: {'✅ Present' if self.api_key else '❌ MISSING'}")
        log.info(f"   Recipient ID: {self.recipient_id}")
        log.info("   ─── FULL JSON PAYLOAD ───")
        log.info(f"{json.dumps(payload, indent=2)}")
        log.info("─" * 80)

        try:
            log.info(f"🌐 Making HTTP POST request...")
            response = requests.post(
                self.report_url,
                json=payload,
                headers=headers,
                timeout=15
            )

            log.info(f"📥 Got response: HTTP {response.status_code}")

            if response.status_code >= 400:
                error_text = response.text[:500]  # First 500 chars
                log.error(f"❌ Backend returned error {response.status_code}")
                log.error(f"   Response: {error_text}")

            response.raise_for_status()
            data = response.json()
            event_id = data.get("eventId")

            log.info("✅ EVENT SENT SUCCESSFULLY")
            log.info(f"   Event ID: {event_id}")
            log.info(f"   Event Type: {event_type}")
            log.info(f"   Created At: {data.get('created_at', 'N/A')}")
            log.info("─" * 80)

            return event_id

        except requests.exceptions.Timeout as error:
            log.error("❌ REQUEST TIMEOUT")
            log.error(f"   Event Type: {event_type}")
            log.error(f"   Target: {self.report_url}")
            log.error(f"   Error: {error}")
            return None

        except requests.exceptions.ConnectionError as error:
            log.error("❌ CONNECTION ERROR")
            log.error(f"   Event Type: {event_type}")
            log.error(f"   Target: {self.report_url}")
            log.error(f"   Error: {error}")
            log.error(f"   ⚠️  Check that backend is running and accessible")
            return None

        except requests.exceptions.HTTPError as error:
            log.error("❌ HTTP ERROR")
            log.error(f"   Event Type: {event_type}")
            log.error(f"   Status Code: {response.status_code}")
            log.error(f"   Response: {response.text[:500]}")
            log.error(f"   Error: {error}")
            return None

        except Exception as error:
            log.error("❌ UNEXPECTED ERROR")
            log.error(f"   Event Type: {event_type}")
            log.error(f"   Error Type: {type(error).__name__}")
            log.error(f"   Error: {error}")
            import traceback
            log.error(f"   Traceback:\n{traceback.format_exc()}")
            return None

    def upload_event_image(
        self,
        event_id: str,
        image_data: bytes,
        filename: str = "error_snapshot.jpg"
    ) -> bool:
        """
        Upload image for an event (e.g., camera snapshot at error time)

        Args:
            event_id: Event UUID to attach image to
            image_data: Raw image bytes (JPEG format)
            filename: Filename for the image

        Returns:
            True if successful, False otherwise
        """
        url = self.upload_url_template.format(event_id=event_id)

        files = {"image": (filename, image_data, "image/jpeg")}
        headers = {"X-API-Key": self.api_key}

        # VERBOSE LOGGING - Before uploading
        log.info("─" * 80)
        log.info(f"📤 UPLOADING EVENT IMAGE to backend")
        log.info(f"   Event ID: {event_id}")
        log.info(f"   Filename: {filename}")
        log.info(f"   Image Size: {len(image_data)} bytes ({len(image_data)/1024:.2f} KB)")
        log.info(f"   Target URL: {url}")
        log.info(f"   API Key: {'✅ Present' if self.api_key else '❌ MISSING'}")
        log.info("─" * 80)

        try:
            log.info(f"🌐 Making HTTP POST request (multipart/form-data)...")
            response = requests.post(url, files=files, headers=headers, timeout=30)

            log.info(f"📥 Got response: HTTP {response.status_code}")

            response.raise_for_status()

            log.info("✅ IMAGE UPLOADED SUCCESSFULLY")
            log.info(f"   Event ID: {event_id}")
            log.info(f"   Filename: {filename}")
            log.info(f"   Size: {len(image_data)/1024:.2f} KB")
            log.info("─" * 80)
            return True

        except requests.exceptions.Timeout as error:
            log.error("❌ IMAGE UPLOAD TIMEOUT")
            log.error(f"   Event ID: {event_id}")
            log.error(f"   Target: {url}")
            log.error(f"   Error: {error}")
            log.error("─" * 80)
            return False

        except requests.exceptions.HTTPError as error:
            log.error("❌ IMAGE UPLOAD HTTP ERROR")
            log.error(f"   Event ID: {event_id}")
            log.error(f"   Status Code: {response.status_code}")
            log.error(f"   Response: {response.text[:500]}")
            log.error(f"   Error: {error}")
            log.error("─" * 80)
            return False

        except requests.exceptions.RequestException as error:
            log.error("❌ IMAGE UPLOAD REQUEST ERROR")
            log.error(f"   Event ID: {event_id}")
            log.error(f"   Error: {error}")
            log.error("─" * 80)
            return False

        except Exception as error:
            log.error("❌ UNEXPECTED ERROR UPLOADING IMAGE")
            log.error(f"   Event ID: {event_id}")
            log.error(f"   Error Type: {type(error).__name__}")
            log.error(f"   Error: {error}", exc_info=True)
            log.error("─" * 80)
            return False

    def report_job_started(
        self,
        printer_serial: str,
        printer_ip: str,
        print_job_id: str,
        file_name: str,
        estimated_time: Optional[int] = None,
        plates_requested: int = 1
    ) -> Optional[str]:
        """Convenience method for job started event"""
        return self.report_event(
            event_type="job_started",
            printer_serial=printer_serial,
            printer_ip=printer_ip,
            event_status="info",
            print_job_id=print_job_id,
            status_data={
                "fileName": file_name,
                "estimatedTime": estimated_time,
                "plates": plates_requested
            },
            message=f"Print job started: {file_name}"
        )

    def report_job_completed(
        self,
        printer_serial: str,
        printer_ip: str,
        print_job_id: str,
        file_name: str
    ) -> Optional[str]:
        """Convenience method for job completed event"""
        return self.report_event(
            event_type="job_completed",
            printer_serial=printer_serial,
            printer_ip=printer_ip,
            event_status="success",
            print_job_id=print_job_id,
            message=f"Print job completed: {file_name}"
        )

    def report_job_failed(
        self,
        printer_serial: str,
        printer_ip: str,
        print_job_id: str,
        file_name: str,
        error_message: str
    ) -> Optional[str]:
        """Convenience method for job failed event"""
        return self.report_event(
            event_type="job_failed",
            printer_serial=printer_serial,
            printer_ip=printer_ip,
            event_status="failed",
            print_job_id=print_job_id,
            message=f"Print job failed: {file_name} - {error_message}"
        )

    def report_hms_error(
        self,
        printer_serial: str,
        printer_ip: str,
        hms_code: str,
        error_data: Dict[str, Any],
        image_data: Optional[bytes] = None
    ) -> Optional[str]:
        """
        Convenience method for HMS error event
        Optionally uploads camera snapshot
        """
        event_id = self.report_event(
            event_type="hms_error",
            printer_serial=printer_serial,
            printer_ip=printer_ip,
            event_status="failed",
            error_data=error_data,
            message=f"HMS Error {hms_code}: {error_data.get('description', 'Unknown error')}"
        )

        # Upload image if provided
        if event_id and image_data:
            self.upload_event_image(event_id, image_data)

        return event_id
