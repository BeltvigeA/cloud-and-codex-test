#!/usr/bin/env python3
"""
Step 3: Test Detection with Object on Plate

This script tests CV detection with a deliberate object on the build plate.
This verifies the system can detect leftover prints/objects.

Run this AFTER Step 2 passes (clean plate detection works).
"""

import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bambulabs_api import BambuClient
from src.cv_analysis import detect_plate_objects


def test_with_object(
    printer_serial: str,
    printer_ip: str,
    access_code: str,
    calibration_dir: str = "print_farm_data/calibration",
    test_z_height: float = 0.0
):
    """
    Test CV detection with an object on the build plate.

    Args:
        printer_serial: Printer serial number
        printer_ip: Printer IP address
        access_code: Printer access code
        calibration_dir: Directory containing calibration images
        test_z_height: Z-height to test at (default: 0mm)
    """

    print("=" * 70)
    print(" TEST: OBJECT DETECTION")
    print("=" * 70)
    print(f"\nPrinter: {printer_serial}")
    print(f"Test Z-height: {test_z_height}mm")
    print()
    print("🎯 TEST PROCEDURE:")
    print("  1. Place a small object on the build plate")
    print("     (e.g., calibration cube, benchy, or any print)")
    print("  2. Object should be clearly visible from camera")
    print("  3. Press Enter to capture and test")
    print()

    input("Press Enter when object is on plate (or Ctrl+C to cancel)...")

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
    image_path = temp_dir / f"test_object_{printer_serial}_{timestamp}.png"

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
            visualization_path=str(temp_dir / f"object_visualization_{timestamp}.png")
        )

        # Display results
        print("\n" + "=" * 70)
        print(" DETECTION RESULTS")
        print("=" * 70)

        status_icon = "✗" if not result['is_clean'] else "✓"
        status_text = "DIRTY (Object Detected)" if not result['is_clean'] else "CLEAN (No Object)"

        print(f"\n{status_icon} Status: {status_text}")
        print(f"  SSIM Score: {result['ssim_score']:.4f}")
        print(f"  Threshold: {result['threshold_used']:.4f}")
        print(f"  Confidence: {result['confidence']:.1%}")
        print(f"  Detection Method: {result['detection_method']}")
        print(f"  Processing Time: {result['processing_time_ms']:.1f}ms")

        if result['regions_detected']:
            print(f"\n  📍 Regions Detected: {len(result['regions_detected'])}")
            for i, region in enumerate(result['regions_detected'], 1):
                print(f"    Region {i}:")
                print(f"      - Location: {region['centroid']}")
                print(f"      - Size: {region['area']} pixels")
                print(f"      - Bounding Box: {region['bbox']}")
                print(f"      - Aspect Ratio: {region['aspect_ratio']:.2f}")
                print(f"      - Mean Difference: {region['mean_difference']:.3f}")

        print("\n" + "=" * 70)

        # Interpretation
        print("\n📊 TEST RESULTS:")
        print("-" * 70)

        if not result['is_clean']:
            print("✓ PASS: Object correctly detected!")
            print(f"  → SSIM score: {result['ssim_score']:.4f} (below threshold {result['threshold_used']:.4f})")
            if result['regions_detected']:
                print(f"  → Found {len(result['regions_detected'])} region(s)")
                print("  → System is working correctly!")
            else:
                print("  → No distinct regions found (low SSIM only)")
                print("  → Object may be small or camera quality issue")
        else:
            print("✗ FAIL: Object NOT detected (False Negative)")
            print(f"  → SSIM score: {result['ssim_score']:.4f} (above threshold {result['threshold_used']:.4f})")
            print("\n  ⚠️  TROUBLESHOOTING:")
            print("  1. Object may be too small - try larger object")
            print("  2. Object color may blend with plate - try darker object")
            print("  3. Calibration may need adjustment")
            print("  4. Threshold may be too low - consider increasing")

        # Save visualization info
        viz_path = temp_dir / f"object_visualization_{timestamp}.png"
        if viz_path.exists():
            print(f"\n📸 Visualization saved: {viz_path}")
            print("   Open this image to see:")
            print("   - Reference calibration image")
            print("   - Current plate with detected regions highlighted")
            print("   - Difference heatmap")

        print()

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
    print("Then run: python examples/step3_test_with_object.py")
    print()

    # Uncomment to run:
    # result = test_with_object(**PRINTER_CONFIG)
    #
    # if result and not result['is_clean']:
    #     print("\n🎉 SUCCESS! Your CV system correctly detects objects!")
    #     print("   Your printer farm is ready for automated operation!")
    # elif result:
    #     print("\n⚠️  FAILED: System did not detect the object")
    #     print("   Review troubleshooting suggestions above")
    # else:
    #     print("\n❌ Test failed - check error messages above")
