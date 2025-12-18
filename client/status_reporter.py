"""Status reporter module for sending printer status updates to backend API."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Mapping
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
        report_interval: int = 60,
        ping_timeout_ms: int = 1000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Initialize status reporter.

        Args:
            base_url: Backend API base URL
            api_key: API key for authentication
            recipient_id: Recipient ID for the user
            report_interval: Seconds between status reports (default: 60)
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

    @staticmethod
    def parse_print_job_data(status_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse MQTT status data into structured print job data.

        Extracts ALL fields from Print Job tab (except bed image) including:
        - print_type, gcode_state, file_name, gcode_file, print_error_code
        - progress, time_remaining, current_layer, total_layers
        - temperatures (nozzle, bed, chamber)
        - print_speed, light_state, skipped_objects, chamber_fan_speed

        Args:
            status_data: Raw MQTT status data from printer

        Returns:
            Parsed status data ready for backend with ALL Print Job fields
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
        if raw_state is None:
            raw_state = {}
        elif not isinstance(raw_state, Mapping):
            if hasattr(raw_state, "__dict__"):
                raw_state = vars(raw_state)
            else:
                try:
                    raw_state = dict(raw_state)  # type: ignore[arg-type]
                except Exception:
                    raw_state = {}

        # CRITICAL: Bambu printers store most MQTT data under the nested 'print' key
        # This is the primary source for print job fields - extract it as mqtt_print
        mqtt_print = {}
        if isinstance(raw_state, dict):
            mqtt_print = raw_state.get("print", {}) if isinstance(raw_state.get("print"), dict) else {}

        # Try to extract layer info (with mqtt_print fallback)
        current_layer = (
            status_data.get("currentLayer") or
            raw_state.get("layer_num") or
            raw_state.get("current_layer") or
            mqtt_print.get("layer_num")
        )
        total_layers = (
            status_data.get("totalLayers") or
            raw_state.get("total_layer_num") or
            raw_state.get("total_layers") or
            mqtt_print.get("total_layer_num")
        )

        # ========== NEW FIELDS FROM PRINT JOB TAB ==========
        
        # Extract print_type (with mqtt_print fallback)
        print_type = (
            status_data.get("printType") or
            status_data.get("print_type") or
            raw_state.get("print_type") or
            mqtt_print.get("print_type")
        )

        # Try to extract file name (enhanced with mqtt_print fallback)
        file_name = (
            status_data.get("fileName") or
            status_data.get("file_name") or
            raw_state.get("gcode_file") or
            raw_state.get("subtask_name") or
            raw_state.get("print_file") or
            mqtt_print.get("subtask_name") or
            mqtt_print.get("gcode_file")
        )

        # Extract gcode_file (with mqtt_print fallback)
        gcode_file = (
            status_data.get("gcodeFile") or
            status_data.get("gcode_file") or
            raw_state.get("gcode_file") or
            mqtt_print.get("gcode_file")
        )

        # Extract print_error_code (with mqtt_print fallback)
        print_error_code = (
            status_data.get("printErrorCode") or
            status_data.get("print_error_code") or
            raw_state.get("print_error") or
            raw_state.get("print_error_code") or
            mqtt_print.get("print_error") or
            mqtt_print.get("mc_print_error_code")
        )

        # Extract skipped_objects (with mqtt_print fallback)
        skipped_objects = (
            status_data.get("skippedObjects") or
            status_data.get("skipped_objects") or
            raw_state.get("skipped_objects") or
            mqtt_print.get("skipped_objects")
        )

        # Extract chamber_fan_speed (with mqtt_print fallback)
        chamber_fan_speed = (
            status_data.get("chamberFanSpeed") or
            status_data.get("chamber_fan_speed") or
            raw_state.get("cooling_fan_speed") or
            raw_state.get("big_fan1_speed") or
            raw_state.get("big_fan2_speed") or
            mqtt_print.get("cooling_fan_speed") or
            mqtt_print.get("big_fan1_speed")
        )

        # Try to extract target temperatures (with mqtt_print fallback)
        bed_target_temp = (
            raw_state.get("bed_target_temper") or
            raw_state.get("target_bed_temp") or
            mqtt_print.get("bed_target_temper")
        )
        nozzle_target_temp = (
            raw_state.get("nozzle_target_temper") or
            raw_state.get("target_nozzle_temp") or
            mqtt_print.get("nozzle_target_temper")
        )

        # Try to extract chamber temp (with mqtt_print fallback)
        chamber_temp = (
            status_data.get("chamberTemp") or
            raw_state.get("chamber_temper") or
            raw_state.get("chamber_temp") or
            mqtt_print.get("chamber_temper")
        )

        # Try to extract light status (with mqtt_print fallback)
        light_state = status_data.get("lightState") or status_data.get("light_state")
        if not light_state:
            lights_report = raw_state.get("lights_report") or mqtt_print.get("lights_report")
            if lights_report and isinstance(lights_report, list) and len(lights_report) > 0:
                light_state = lights_report[0].get("mode")

        # Build structured status with ALL Print Job fields
        parsed_status: Dict[str, Any] = {
            "status": state,
            "state": state,
        }

        # Add optional fields if available
        if gcode_state:
            parsed_status["gcodeState"] = gcode_state
        if progress is not None:
            parsed_status["progressPercent"] = max(0, min(100, progress))
            parsed_status["jobProgress"] = max(0, min(100, progress))  # Alias
        
        # Always include temperature fields (use 0.0 fallback if missing)
        parsed_status["bedTemp"] = bed_temp if bed_temp is not None else 0.0
        if bed_target_temp is not None:
            parsed_status["bedTargetTemp"] = bed_target_temp
        parsed_status["nozzleTemp"] = nozzle_temp if nozzle_temp is not None else 0.0
        if nozzle_target_temp is not None:
            parsed_status["nozzleTargetTemp"] = nozzle_target_temp
        parsed_status["chamberTemp"] = chamber_temp if chamber_temp is not None else 0.0
        
        if fan_speed is not None:
            parsed_status["fanSpeed"] = max(0, min(100, int(fan_speed)))
        if print_speed is not None:
            parsed_status["speedPercentage"] = int(print_speed)
            parsed_status["printSpeed"] = int(print_speed)  # Alias
        
        # Light state
        if light_state is not None:
            if isinstance(light_state, str):
                parsed_status["lightOn"] = light_state.lower() == "on"
                parsed_status["lightState"] = light_state
            else:
                parsed_status["lightOn"] = bool(light_state)
        
        if file_name:
            parsed_status["fileName"] = str(file_name)
        if current_layer is not None:
            try:
                parsed_status["currentLayer"] = int(current_layer)
            except (ValueError, TypeError):
                pass
        if total_layers is not None:
            try:
                parsed_status["totalLayers"] = int(total_layers)
            except (ValueError, TypeError):
                pass
        if remaining_time is not None:
            try:
                parsed_status["remainingTimeSeconds"] = int(remaining_time)
                parsed_status["timeRemaining"] = int(remaining_time)  # Alias
            except (ValueError, TypeError):
                pass

        # ========== ADD NEW PRINT JOB FIELDS ==========
        if print_type:
            parsed_status["printType"] = str(print_type)
        
        if gcode_file:
            parsed_status["gcodeFile"] = str(gcode_file)
        
        if print_error_code:
            try:
                parsed_status["printErrorCode"] = int(print_error_code)
            except (ValueError, TypeError):
                parsed_status["printErrorCode"] = str(print_error_code)
        
        if skipped_objects:
            if isinstance(skipped_objects, list):
                parsed_status["skippedObjects"] = skipped_objects
            else:
                parsed_status["skippedObjects"] = str(skipped_objects)
        
        if chamber_fan_speed is not None:
            try:
                parsed_status["chamberFanSpeed"] = int(chamber_fan_speed)
            except (ValueError, TypeError):
                pass

        # ========== ADD JOB ID AND PRODUCT INFO ==========
        # These are critical for tracking which job is running on each printer
        current_job_id = (
            status_data.get("currentJobId") or
            status_data.get("job_id") or
            raw_state.get("job_id") or
            mqtt_print.get("job_id") or
            mqtt_print.get("task_id")
        )
        if current_job_id:
            parsed_status["currentJobId"] = str(current_job_id)
        
        product_name = (
            status_data.get("productName") or
            status_data.get("product_name")
        )
        if product_name:
            parsed_status["productName"] = str(product_name)
        
        product_id = (
            status_data.get("productId") or
            status_data.get("product_id")
        )
        if product_id:
            parsed_status["productId"] = str(product_id)

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
        # DEBUG: Log that method was called
        self.log.debug(f"🔍 report_status() called for printer {printer_serial}")

        # Rate limiting - check if enough time has passed
        if not self.should_report(printer_serial):
            self.log.debug(f"⏸️  Skipping report for {printer_serial} (rate limited)")
            return None

        self.log.debug(f"✅ Rate limit passed for {printer_serial}, proceeding with report")

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

        # Build payload - flatten parsed_status to top level (API expects flat structure)
        payload = {
            "recipientId": self.recipient_id,
            "printerSerial": printer_serial,
            "printerIpAddress": printer_ip,
            "pingStatus": ping_result.get("status", "unknown"),
        }
        
        # Merge parsed status fields into top-level payload (not nested)
        # The API expects: nozzleTemp, bedTemp, etc. at the root level
        payload.update(parsed_status)

        # Send to backend
        try:
            url = f"{self.base_url}/api/printer-status/update"
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            }

            # Simplified logging - only IP, status, and progress
            status_str = parsed_status.get('status', 'UNKNOWN')
            progress = parsed_status.get('progressPercent')
            
            if progress is not None:
                self.log.info(f"Status update: {printer_ip} | {status_str} | {progress}%")
            else:
                self.log.info(f"Status update: {printer_ip} | {status_str}")

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json()

                return result
            else:
                self.log.warning(f"Status update failed: {printer_ip} | HTTP {response.status_code}")
                return None

        except requests.exceptions.ConnectionError as e:
            self.log.error(f"Connection error: {printer_ip} | {e}")
            return None
        except requests.RequestException as e:
            self.log.error(f"Network error: {printer_ip} | {type(e).__name__}: {e}")
            return None
        except Exception as e:
            self.log.error(f"Unexpected error: {printer_ip} | {type(e).__name__}: {e}")
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

            self.log.info(f"Status update: {printer_ip} | OFFLINE")

            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json()
                return result
            else:
                self.log.warning(f"Offline status failed: {printer_ip} | HTTP {response.status_code}")
                return None

        except requests.RequestException as e:
            self.log.error(f"Network error (offline): {printer_ip} | {e}")
            return None
        except Exception as e:
            self.log.error(f"Error (offline): {printer_ip} | {e}")
            return None
