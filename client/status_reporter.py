"""Status reporter module for sending printer status updates to backend API."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

import requests


class StatusReporter:
    """
    Reports printer status to backend API.

    Handles:
    - Ping testing
    - Status data parsing
    - HTTP POST to backend
    - Offline status reporting
    - Rate limiting
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        recipient_id: str,
        *,
        report_interval: int = 10,
        ping_timeout_ms: int = 1000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Initialize status reporter.

        Args:
            base_url: Backend API base URL
            api_key: API key for authentication
            recipient_id: Recipient ID for the user
            report_interval: Seconds between status reports (default: 10)
            ping_timeout_ms: Ping timeout in milliseconds (default: 1000)
            logger: Optional logger instance
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.recipient_id = recipient_id
        self.report_interval = report_interval
        self.ping_timeout_ms = ping_timeout_ms
        self.log = logger or logging.getLogger(__name__)

        # Track last report time per printer (for rate limiting)
        self.last_report_time: Dict[str, float] = {}

        self.log.info("✅ Status reporter initialized")
        self.log.info(f"   Base URL: {self.base_url}")
        self.log.info(f"   Recipient ID: {self.recipient_id[:8]}...")
        self.log.info(f"   Report interval: {self.report_interval}s")

    def should_report(self, printer_serial: str) -> bool:
        """
        Check if enough time has passed to send another report.

        Args:
            printer_serial: Printer serial number

        Returns:
            True if should report, False otherwise
        """
        current_time = time.monotonic()
        last_time = self.last_report_time.get(printer_serial, 0)

        if current_time - last_time >= self.report_interval:
            self.last_report_time[printer_serial] = current_time
            return True

        return False

    def ping_printer(self, ip_address: str) -> Dict[str, Any]:
        """
        Ping printer to check connectivity.

        Args:
            ip_address: Printer IP address

        Returns:
            Dict with status ("success" or "failed"), success bool, and optional error
        """
        if not ip_address:
            return {"status": "failed", "success": False, "error": "No IP address"}

        ping_executable = shutil.which("ping")
        if not ping_executable:
            # If ping not available, assume online
            return {"status": "success", "success": True}

        system_name = platform.system().lower()
        timeout_seconds = max(1, int(max(self.ping_timeout_ms, 100) / 1000))

        if "windows" in system_name:
            command = [
                ping_executable,
                "-n",
                "1",
                "-w",
                str(max(self.ping_timeout_ms, 100)),
                ip_address,
            ]
        else:
            command = [
                ping_executable,
                "-c",
                "1",
                "-W",
                str(timeout_seconds),
                ip_address,
            ]

        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout_seconds + 1,
            )

            if result.returncode == 0:
                return {"status": "success", "success": True}
            else:
                return {"status": "failed", "success": False, "error": "Ping failed"}

        except subprocess.TimeoutExpired:
            return {"status": "failed", "success": False, "error": "Ping timeout"}
        except Exception as e:
            return {"status": "failed", "success": False, "error": str(e)}

    def parse_print_job_data(self, status_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse MQTT status data into structured print job data.

        Args:
            status_data: Raw MQTT status data from printer

        Returns:
            Parsed status data ready for backend
        """
        # Extract state and gcode_state
        state = status_data.get("state", "UNKNOWN")
        gcode_state = status_data.get("gcodeState", status_data.get("gcode_state"))

        # Determine overall status
        if not state or state == "UNKNOWN":
            if gcode_state:
                state = gcode_state
            else:
                state = "UNKNOWN"

        # Extract progress
        progress = status_data.get("progressPercent")
        if progress is not None:
            try:
                progress = float(progress)
            except (ValueError, TypeError):
                progress = None

        # Extract temperatures
        bed_temp = status_data.get("bedTemp")
        nozzle_temp = status_data.get("nozzleTemp")

        # Extract other fields
        fan_speed = status_data.get("fanSpeedPercent")
        print_speed = status_data.get("printSpeed")
        remaining_time = status_data.get("remainingTimeSeconds")

        # Get raw state payload for additional fields
        raw_state = status_data.get("rawStatePayload", {})

        # Try to extract layer info
        current_layer = raw_state.get("layer_num") or raw_state.get("current_layer")
        total_layers = raw_state.get("total_layer_num") or raw_state.get("total_layers")

        # Try to extract file name
        file_name = (
            raw_state.get("gcode_file") or
            raw_state.get("subtask_name") or
            raw_state.get("print_file")
        )

        # Try to extract target temperatures
        bed_target_temp = raw_state.get("bed_target_temper") or raw_state.get("target_bed_temp")
        nozzle_target_temp = raw_state.get("nozzle_target_temper") or raw_state.get("target_nozzle_temp")

        # Try to extract chamber temp
        chamber_temp = raw_state.get("chamber_temper") or raw_state.get("chamber_temp")

        # Try to extract light status
        light_on = raw_state.get("lights_report", [{}])[0].get("mode") if raw_state.get("lights_report") else None

        # Build structured status
        parsed_status: Dict[str, Any] = {
            "status": state,
            "state": state,
        }

        # Add optional fields if available
        if gcode_state:
            parsed_status["gcodeState"] = gcode_state
        if progress is not None:
            parsed_status["jobProgress"] = max(0, min(100, progress))
        if bed_temp is not None:
            parsed_status["bedTemp"] = bed_temp
        if bed_target_temp is not None:
            parsed_status["bedTargetTemp"] = bed_target_temp
        if nozzle_temp is not None:
            parsed_status["nozzleTemp"] = nozzle_temp
        if nozzle_target_temp is not None:
            parsed_status["nozzleTargetTemp"] = nozzle_target_temp
        if chamber_temp is not None:
            parsed_status["chamberTemp"] = chamber_temp
        if fan_speed is not None:
            parsed_status["fanSpeed"] = max(0, min(100, int(fan_speed)))
        if print_speed is not None:
            parsed_status["speedPercentage"] = int(print_speed)
        if light_on is not None:
            parsed_status["lightOn"] = light_on == "on"
        if file_name:
            parsed_status["fileName"] = str(file_name)
        if current_layer is not None:
            parsed_status["currentLayer"] = int(current_layer)
        if total_layers is not None:
            parsed_status["totalLayers"] = int(total_layers)
        if remaining_time is not None:
            parsed_status["timeRemaining"] = int(remaining_time)

        return parsed_status

    def report_status(
        self,
        printer_serial: str,
        printer_ip: str,
        status_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Report printer status to backend API.

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address
            status_data: Raw status data from MQTT

        Returns:
            Response data from backend, or None if failed
        """
        # Rate limiting - check if enough time has passed
        if not self.should_report(printer_serial):
            return None

        # Ping printer first
        ping_result = self.ping_printer(printer_ip)

        # If offline, report offline status instead
        if not ping_result.get("success"):
            self.log.debug(f"Printer {printer_serial} is offline (ping failed)")
            return self.report_offline(printer_serial, printer_ip)

        # Parse status data
        parsed_status = self.parse_print_job_data(status_data)

        # Add ping status to parsed data
        parsed_status["pingStatus"] = ping_result.get("status", "unknown")

        # Build payload
        payload = {
            "recipientId": self.recipient_id,
            "printerSerial": printer_serial,
            "printerIpAddress": printer_ip,
            "pingStatus": ping_result.get("status", "unknown"),
            "status": parsed_status,
        }

        # Send to backend
        try:
            url = f"{self.base_url}/api/printer-status/update"
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            }

            # VERBOSE LOGGING - Before sending
            self.log.info("─" * 80)
            self.log.info(f"📤 SENDING STATUS UPDATE to backend")
            self.log.info(f"   Printer Serial: {printer_serial}")
            self.log.info(f"   Printer IP: {printer_ip}")
            self.log.info(f"   Target URL: {url}")
            self.log.info(f"   API Key: {'✅ Present (' + str(len(self.api_key)) + ' chars)' if self.api_key else '❌ MISSING'}")
            self.log.info(f"   Recipient ID: {self.recipient_id}")
            self.log.info("   ─── PAYLOAD DATA ───")
            self.log.info(f"   Status: {parsed_status.get('status', 'UNKNOWN')}")
            self.log.info(f"   GCode State: {parsed_status.get('gcodeState', 'N/A')}")
            self.log.info(f"   Job Progress: {parsed_status.get('jobProgress', 'N/A')}%")
            self.log.info(f"   File Name: {parsed_status.get('fileName', 'N/A')}")
            self.log.info(f"   Nozzle Temp: {parsed_status.get('nozzleTemp', 'N/A')}°C (Target: {parsed_status.get('nozzleTargetTemp', 'N/A')}°C)")
            self.log.info(f"   Bed Temp: {parsed_status.get('bedTemp', 'N/A')}°C (Target: {parsed_status.get('bedTargetTemp', 'N/A')}°C)")
            self.log.info(f"   Chamber Temp: {parsed_status.get('chamberTemp', 'N/A')}°C")
            self.log.info(f"   Current Layer: {parsed_status.get('currentLayer', 'N/A')}/{parsed_status.get('totalLayers', 'N/A')}")
            self.log.info(f"   Time Remaining: {parsed_status.get('timeRemaining', 'N/A')}s")
            self.log.info(f"   Fan Speed: {parsed_status.get('fanSpeed', 'N/A')}%")
            self.log.info(f"   Speed Percentage: {parsed_status.get('speedPercentage', 'N/A')}%")
            self.log.info(f"   Light On: {parsed_status.get('lightOn', 'N/A')}")
            self.log.info(f"   Ping Status: {ping_result.get('status', 'unknown')}")
            self.log.info("   ─── FULL JSON PAYLOAD ───")
            self.log.info(f"{json.dumps(payload, indent=2)}")
            self.log.info("─" * 80)

            self.log.info(f"🌐 Making HTTP POST request...")
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=10,
            )

            self.log.info(f"📥 Got response: HTTP {response.status_code}")

            if response.status_code == 200:
                result = response.json()

                # VERBOSE LOGGING - Success response
                self.log.info("✅ STATUS UPDATE SENT SUCCESSFULLY")
                self.log.info(f"   Printer Serial: {printer_serial}")
                self.log.info(f"   Status: {parsed_status.get('status', 'UNKNOWN')}")
                progress = parsed_status.get("jobProgress")
                if progress is not None:
                    self.log.info(f"   Progress: {progress}%")
                self.log.info(f"   Response Data: {result}")
                self.log.info("─" * 80)

                return result
            else:
                self.log.warning("❌ STATUS UPDATE FAILED")
                self.log.warning(
                    f"   HTTP {response.status_code} - {response.text[:500]}"
                )
                self.log.warning("─" * 80)
                return None

        except requests.RequestException as e:
            self.log.error("❌ NETWORK ERROR reporting status")
            self.log.error(f"   Printer Serial: {printer_serial}")
            self.log.error(f"   Error: {e}")
            self.log.error("─" * 80)
            return None
        except Exception as e:
            self.log.error("❌ UNEXPECTED ERROR reporting status")
            self.log.error(f"   Printer Serial: {printer_serial}")
            self.log.error(f"   Error Type: {type(e).__name__}")
            self.log.error(f"   Error: {e}")
            import traceback
            self.log.error(f"   Traceback:\n{traceback.format_exc()}")
            self.log.error("─" * 80)
            return None

    def report_offline(
        self,
        printer_serial: str,
        printer_ip: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Report printer as offline to backend API.

        Args:
            printer_serial: Printer serial number
            printer_ip: Printer IP address

        Returns:
            Response data from backend, or None if failed
        """
        payload = {
            "recipientId": self.recipient_id,
            "printerSerial": printer_serial,
            "printerIpAddress": printer_ip,
            "pingStatus": "failed",
            "status": {
                "status": "OFFLINE",
                "state": "OFFLINE",
                "pingStatus": "failed",
            },
        }

        try:
            url = f"{self.base_url}/api/printer-status/update"
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            }

            # VERBOSE LOGGING - Before sending offline status
            self.log.info("─" * 80)
            self.log.info(f"📤 SENDING OFFLINE STATUS to backend")
            self.log.info(f"   Printer Serial: {printer_serial}")
            self.log.info(f"   Printer IP: {printer_ip}")
            self.log.info(f"   Target URL: {url}")
            self.log.info(f"   Status: OFFLINE")
            self.log.info(f"   Ping Status: failed")
            self.log.info("─" * 80)

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json()
                self.log.info("✅ OFFLINE STATUS SENT SUCCESSFULLY")
                self.log.info(f"   Printer Serial: {printer_serial}")
                self.log.info(f"   Response: {result}")
                self.log.info("─" * 80)
                return result
            else:
                self.log.warning("❌ OFFLINE STATUS UPDATE FAILED")
                self.log.warning(
                    f"   HTTP {response.status_code} - {response.text[:200]}"
                )
                self.log.warning("─" * 80)
                return None

        except requests.RequestException as e:
            self.log.error("❌ NETWORK ERROR reporting offline status")
            self.log.error(f"   Printer Serial: {printer_serial}")
            self.log.error(f"   Error: {e}")
            self.log.error("─" * 80)
            return None
        except Exception as e:
            self.log.error("❌ ERROR reporting offline status")
            self.log.error(f"   Printer Serial: {printer_serial}")
            self.log.error(f"   Error: {e}")
            self.log.error("─" * 80)
            return None
