#!/usr/bin/env python3
"""
Step 1: Capture Calibration Images from Bambulabs Printer

This script moves the print head to different Z-heights and captures
images for calibration reference.

Run this ONCE per printer to create the calibration dataset.
"""

import time
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bambulabs_api import BambuClient
from src.cv_analysis.file_manager import save_calibration_metadata


def capture_calibration(
    printer_serial: str,
    printer_ip: str,
    access_code: str,
    calibration_dir: str = "print_farm_data/calibration",
    z_start: int = 0,
    z_end: int = 235,
    z_increment: int = 5
):
    """
    Capture calibration images at multiple Z-heights.

    Args:
        printer_serial: Printer serial number (e.g., "00M09A3B1000685")
        printer_ip: Printer IP address
        access_code: Printer access code
        calibration_dir: Directory to save calibration images
        z_start: Starting Z-height in mm (default: 0)
        z_end: Ending Z-height in mm (default: 235)
        z_increment: Z-height increment in mm (default: 5)
    """

    print("=" * 70)
    print(" BAMBULABS PRINTER CALIBRATION")
    print("=" * 70)
    print(f"\nPrinter: {printer_serial}")
    print(f"IP: {printer_ip}")
    print(f"Z-range: {z_start}mm to {z_end}mm (every {z_increment}mm)")

    # Calculate number of images
    z_heights = list(range(z_start, z_end + 1, z_increment))
    print(f"Images to capture: {len(z_heights)}")
    print(f"\nEstimated time: ~{len(z_heights) * 10 / 60:.1f} minutes")
    print("\n⚠️  IMPORTANT:")
    print("  1. Make sure build plate is CLEAN and EMPTY")
    print("  2. Printer should be idle (no active print)")
    print("  3. This will move the print head - do not interrupt")
    print()

    input("Press Enter to start calibration (or Ctrl+C to cancel)...")

    # Create calibration directory
    cal_path = Path(calibration_dir) / printer_serial
    cal_path.mkdir(parents=True, exist_ok=True)

    print(f"\nSaving calibration images to: {cal_path}")

    # Connect to printer
    print("\nConnecting to printer...")
    try:
        client = BambuClient(
            device_type="your_device_type",  # Update this
            serial=printer_serial,
            host=printer_ip,
            access_code=access_code
        )
        print("✓ Connected!")
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        print("\nTroubleshooting:")
        print("  - Check printer IP address")
        print("  - Verify access code")
        print("  - Ensure printer is on same network")
        return

    # Home the printer first
    print("\n[0/{}] Homing printer...".format(len(z_heights)))
    try:
        client.send_gcode("G28")  # Home all axes
        time.sleep(5)  # Wait for homing
        print("✓ Homing complete")
    except Exception as e:
        print(f"⚠️  Homing failed: {e}")
        print("Continuing anyway...")

    # Capture images at each Z-height
    captured_count = 0

    for i, z in enumerate(z_heights, 1):
        try:
            print(f"\n[{i}/{len(z_heights)}] Z = {z}mm")

            # Move to Z-height
            print(f"  Moving to Z={z}mm...")
            client.send_gcode(f"G0 Z{z} F3000")  # Move at 3000mm/min
            time.sleep(3)  # Wait for movement + camera stabilization

            # Capture image
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_filename = f"Z{z:03d}mm_{timestamp}.png"
            image_path = cal_path / image_filename

            print(f"  Capturing image...")
            client.get_camera_image(output_path=str(image_path))

            if image_path.exists():
                print(f"  ✓ Saved: {image_filename}")
                captured_count += 1
            else:
                print(f"  ✗ Failed to save image")

        except KeyboardInterrupt:
            print("\n\n⚠️  Calibration interrupted by user!")
            break
        except Exception as e:
            print(f"  ✗ Error at Z={z}mm: {e}")
            print("  Continuing to next height...")
            continue

    # Move back to home
    print(f"\n[{len(z_heights) + 1}/{len(z_heights) + 1}] Returning to home...")
    try:
        client.send_gcode("G28")
        print("✓ Returned to home position")
    except Exception as e:
        print(f"⚠️  Failed to return home: {e}")

    # Save calibration metadata
    print("\nSaving calibration metadata...")
    try:
        save_calibration_metadata(
            printer_serial=printer_serial,
            calibration_dir=calibration_dir,
            z_heights=[z for z in z_heights if (cal_path / f"Z{z:03d}mm_{timestamp}.png").exists()],
            notes=f"Calibration captured on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print("✓ Metadata saved")
    except Exception as e:
        print(f"⚠️  Failed to save metadata: {e}")

    # Summary
    print("\n" + "=" * 70)
    print(" CALIBRATION COMPLETE")
    print("=" * 70)
    print(f"Images captured: {captured_count}/{len(z_heights)}")
    print(f"Success rate: {100 * captured_count / len(z_heights):.1f}%")
    print(f"Calibration directory: {cal_path}")
    print()

    if captured_count >= len(z_heights) * 0.9:  # 90% success
        print("✓ Calibration successful! You can now use this printer for CV detection.")
    else:
        print("⚠️  Some images failed to capture. Consider re-running calibration.")
        print("   Missing images may cause detection errors at those Z-heights.")

    print()


if __name__ == "__main__":
    # Example usage - update these values for your printer
    PRINTER_CONFIG = {
        "printer_serial": "00M09A3B1000685",  # ← Update this
        "printer_ip": "192.168.1.100",        # ← Update this
        "access_code": "12345678",            # ← Update this
    }

    print("\n⚠️  CONFIGURATION REQUIRED!")
    print("\nBefore running, update the PRINTER_CONFIG in this file with:")
    print("  - Your printer's serial number")
    print("  - Your printer's IP address")
    print("  - Your printer's access code")
    print("\nThen run: python examples/step1_capture_calibration.py")
    print()

    # Uncomment to run with your settings:
    # capture_calibration(**PRINTER_CONFIG)
