#!/usr/bin/env python3
"""
Step 2: Test Detection on a Clean Plate

This script captures an image of a clean build plate and runs CV detection.
This verifies your calibration is working correctly.

Run this AFTER completing Step 1 (calibration).
"""

import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bambulabs_api import BambuClient
from src.cv_analysis import detect_plate_objects


def test_clean_plate(
    printer_serial: str,
    printer_ip: str,
    access_code: str,
    calibration_dir: str = "print_farm_data/calibration",
    test_z_height: float = 0.0,
    save_image: bool = True
):
    """
    Test CV detection on a clean build plate.

    Args:
        printer_serial: Printer serial number
        printer_ip: Printer IP address
        access_code: Printer access code
        calibration_dir: Directory containing calibration images
        test_z_height: Z-height to test at (default: 0mm)
        save_image: Whether to save test image
    """

    print("=" * 70)
    print(" TEST: CLEAN PLATE DETECTION")
    print("=" * 70)
    print(f"\nPrinter: {printer_serial}")
    print(f"Test Z-height: {test_z_height}mm")
    print()
    print("⚠️  IMPORTANT:")
    print("  1. Build plate must be CLEAN and EMPTY")
    print("  2. Remove any objects from the plate")
    print("  3. Wipe plate if necessary")
    print()

    input("Press Enter when plate is clean (or Ctrl+C to cancel)...")

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
        return

    # Move to test height
    if test_z_height > 0:
        print(f"\nMoving to Z={test_z_height}mm...")
        try:
            client.send_gcode(f"G0 Z{test_z_height} F3000")
            import time
            time.sleep(2)
        except Exception as e:
            print(f"⚠️  Failed to move: {e}")

    # Capture current image
    print("\nCapturing plate image...")

    temp_dir = Path("print_farm_data/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = temp_dir / f"test_clean_{printer_serial}_{timestamp}.png"

    try:
        client.get_camera_image(output_path=str(image_path))
        print(f"✓ Image saved: {image_path}")
    except Exception as e:
        print(f"✗ Failed to capture image: {e}")
        return

    # Run CV detection
    print("\nRunning CV detection...")
    print("-" * 70)

    try:
        result = detect_plate_objects(
            current_image_path=str(image_path),
            printer_serial=printer_serial,
            z_height=test_z_height,
            calibration_dir=calibration_dir,
            save_visualization=True,
            visualization_path=str(temp_dir / f"visualization_{timestamp}.png")
        )

        # Display results
        print("\n" + "=" * 70)
        print(" DETECTION RESULTS")
        print("=" * 70)

        status_icon = "✓" if result['is_clean'] else "✗"
        status_text = "CLEAN" if result['is_clean'] else "DIRTY"

        print(f"\n{status_icon} Status: {status_text}")
        print(f"  SSIM Score: {result['ssim_score']:.4f}")
        print(f"  Threshold: {result['threshold_used']:.4f}")
        print(f"  Confidence: {result['confidence']:.1%}")
        print(f"  Detection Method: {result['detection_method']}")
        print(f"  Processing Time: {result['processing_time_ms']:.1f}ms")

        if result['regions_detected']:
            print(f"  Regions Detected: {len(result['regions_detected'])}")
            for i, region in enumerate(result['regions_detected'], 1):
                print(f"    Region {i}: {region['area']} pixels at {region['centroid']}")

        print("\n" + "=" * 70)

        # Interpretation
        print("\n📊 INTERPRETATION:")
        print("-" * 70)

        if result['is_clean']:
            if result['ssim_score'] > 0.95:
                print("✓ EXCELLENT: Plate is very clean (SSIM > 0.95)")
                print("  → Calibration is working perfectly")
                print("  → Safe to start prints automatically")
            elif result['ssim_score'] > 0.90:
                print("✓ GOOD: Plate is clean (SSIM > 0.90)")
                print("  → Calibration is working well")
                print("  → Some minor variation, but acceptable")
            else:
                print("⚠️  BORDERLINE: Plate detected as clean but SSIM is low")
                print("  → May need to recalibrate")
                print("  → Check lighting conditions match calibration")
        else:
            if len(result['regions_detected']) > 0:
                print("✗ OBJECT DETECTED")
                print(f"  → Found {len(result['regions_detected'])} region(s)")
                print("  → If plate is actually clean, this is a FALSE POSITIVE")
                print("  → Consider recalibrating or adjusting threshold")
            else:
                print("✗ PLATE DIRTY (low SSIM, no clear objects)")
                print("  → Image is different from calibration")
                print("  → Could be lighting change or actual debris")

        # Save visualization info
        viz_path = temp_dir / f"visualization_{timestamp}.png"
        if viz_path.exists():
            print(f"\n📸 Visualization saved: {viz_path}")
            print("   Open this image to see the comparison")

        print()

        # Clean up if test passed
        if not save_image and result['is_clean']:
            image_path.unlink()
            print("✓ Test image deleted (plate was clean)")

        return result

    except Exception as e:
        print(f"\n✗ Error during detection: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # Example usage - update these values
    PRINTER_CONFIG = {
        "printer_serial": "00M09A3B1000685",  # ← Update this
        "printer_ip": "192.168.1.100",        # ← Update this
        "access_code": "12345678",            # ← Update this
    }

    print("\n⚠️  CONFIGURATION REQUIRED!")
    print("\nBefore running, update the PRINTER_CONFIG in this file")
    print("Then run: python examples/step2_test_clean_plate.py")
    print()

    # Uncomment to run:
    # result = test_clean_plate(**PRINTER_CONFIG)
    #
    # if result and result['is_clean']:
    #     print("🎉 SUCCESS! Your CV system is working correctly!")
    # elif result:
    #     print("⚠️  System detected dirt on clean plate - may need calibration adjustment")
    # else:
    #     print("❌ Test failed - check error messages above")
