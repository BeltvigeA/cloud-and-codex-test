"""
HMS Error Handler - Processes HMS error codes from Bambu printers
"""

import base64
import io
import logging
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)

def parse_hms_error(hms_code: str) -> Dict[str, Any]:
    """
    Parse HMS error code and return structured data

    HMS code format: XXXX_YYYY_ZZZZ_WWWW
    Example: 0300_0300_0002_0003

    Args:
        hms_code: HMS error code string

    Returns:
        Dict with hmsCode, description, severity, module
    """
    if not hms_code or not isinstance(hms_code, str):
        return {
            "hmsCode": str(hms_code),
            "description": "Invalid HMS error code",
            "severity": "unknown",
            "module": "unknown"
        }

    parts = hms_code.split('_')

    if len(parts) != 4:
        return {
            "hmsCode": hms_code,
            "description": f"Malformed HMS error: {hms_code}",
            "severity": "unknown",
            "module": "unknown"
        }

    # Module mapping (first 4 digits)
    module_map = {
        "0300": "hotbed",
        "0500": "extruder",
        "0700": "motion",
        "0C00": "ams",
        "0D00": "filament",
        "1200": "chamber",
    }

    module_code = parts[0]
    module = module_map.get(module_code, "unknown")

    # Severity determination
    # This is a simplified version - expand based on actual HMS codes
    severity = "warning"
    error_type_code = parts[2]

    if error_type_code in ["0002", "0003", "0004"]:
        severity = "critical"
    elif error_type_code in ["0001"]:
        severity = "error"

    # Look up known HMS errors
    description = HMS_ERROR_DESCRIPTIONS.get(
        hms_code,
        f"HMS Error {hms_code} (module: {module})"
    )

    return {
        "hmsCode": hms_code,
        "description": description,
        "severity": severity,
        "module": module,
        "raw": {
            "module_code": parts[0],
            "error_category": parts[1],
            "error_type": parts[2],
            "error_detail": parts[3]
        }
    }

# HMS Error Database (starter set - expand as needed)
HMS_ERROR_DESCRIPTIONS = {
    "0300_0300_0002_0003": "Hotbed heating abnormal; please check whether the hotbed wiring is damaged or the connector is loose, then retry. If the problem persists, contact customer service.",
    "0500_0200_0001_0001": "Nozzle temperature abnormal; the temperature sensor may be damaged. Please contact customer service.",
    "0500_0300_0002_0001": "Nozzle temperature control error; heating failed. Please check the nozzle heater.",
    "0700_0300_0001_0002": "Homing failed; mechanical components may be stuck. Please check for obstructions.",
    "0C00_0100_0001_0001": "AMS communication error; please check AMS connection.",
    "0D00_0200_0001_0001": "Filament runout detected; please load new filament.",
    "0700_0200_0001_0001": "Motion system error; mechanical components may be stuck or damaged.",
    "0500_0100_0001_0001": "Nozzle temperature sensor error; please check sensor connection.",
    "0300_0200_0001_0001": "Hotbed temperature sensor error; please check sensor connection.",
    "1200_0100_0001_0001": "Chamber temperature abnormal; please check ventilation.",
    # Add more HMS codes as you encounter them
}

def capture_error_snapshot(printer_ip: str) -> Optional[bytes]:
    """
    Capture camera snapshot from printer when error occurs

    Args:
        printer_ip: Printer IP address

    Returns:
        Image bytes (JPEG) if successful, None otherwise
    """
    try:
        # Try to import and use existing camera capture logic
        from . import bambuPrinter

        # Check if bambulabs_api is available
        if bambuPrinter.bambulabsApi is None:
            log.warning("bambulabs_api not available for camera capture")
            return None

        # Get printer credentials from environment or config
        # For HMS errors, we may not have all credentials readily available
        # This is a best-effort attempt
        try:
            # Try to create printer instance for camera capture
            # This requires serial and access code which we may not have in context
            # For now, we'll skip the full implementation and return None
            # The caller should handle None gracefully
            log.debug(f"Camera snapshot capture for {printer_ip} requires printer credentials")
            return None
        except Exception as e:
            log.debug(f"Could not capture snapshot for {printer_ip}: {e}")
            return None
    except ImportError:
        log.warning("Camera capture not available - bambuPrinter not found")
        return None
    except Exception as error:
        log.error(f"Failed to capture error snapshot from {printer_ip}: {error}")
        return None

def capture_camera_frame_from_printer(printer: Any) -> Optional[bytes]:
    """
    Capture camera frame from an already-connected printer instance

    Args:
        printer: Connected printer instance from bambulabs_api

    Returns:
        Image bytes (JPEG) if successful, None otherwise
    """
    try:
        # Try get_camera_image first (returns PIL Image)
        cameraImageMethod = getattr(printer, "get_camera_image", None)
        if callable(cameraImageMethod):
            try:
                pillowImage = cameraImageMethod()
                byteStream = io.BytesIO()
                pillowImage.save(byteStream, format="JPEG")
                return byteStream.getvalue()
            except Exception as e:
                log.debug(f"get_camera_image failed: {e}")

        # Try get_camera_frame (returns base64 string)
        cameraFrameMethod = getattr(printer, "get_camera_frame", None)
        if callable(cameraFrameMethod):
            try:
                frameData = cameraFrameMethod()
                if isinstance(frameData, str):
                    rawBytes = base64.b64decode(frameData, validate=False)
                    return rawBytes
            except Exception as e:
                log.debug(f"get_camera_frame failed: {e}")

        # Try get_camera_snapshot (returns bytes)
        cameraSnapshotMethod = getattr(printer, "get_camera_snapshot", None)
        if callable(cameraSnapshotMethod):
            try:
                snapshotData = cameraSnapshotMethod()
                if isinstance(snapshotData, (bytes, bytearray)):
                    return bytes(snapshotData)
            except Exception as e:
                log.debug(f"get_camera_snapshot failed: {e}")

        log.debug("No camera capture method available on printer")
        return None

    except Exception as error:
        log.error(f"Failed to capture camera frame from printer: {error}")
        return None
