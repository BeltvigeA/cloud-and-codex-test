#!/usr/bin/env python3
"""
Quick validation test for CV plate verification system
Creates synthetic test data and runs detection
"""

import tempfile
import shutil
import json
from pathlib import Path
import numpy as np
from PIL import Image

from src.cv_analysis.detection import detect_plate_objects
from src.cv_analysis.file_manager import save_calibration_metadata

print("=" * 70)
print("CV PLATE VERIFICATION SYSTEM - QUICK TEST")
print("=" * 70)

# Create temporary directory
temp_dir = tempfile.mkdtemp()
print(f"\nUsing temporary directory: {temp_dir}")

try:
    # Step 1: Create mock calibration directory
    print("\n[1/5] Creating mock calibration data...")
    printer_serial = "TEST_PRINTER_001"
    calibration_dir = Path(temp_dir) / "calibration"
    printer_cal_dir = calibration_dir / printer_serial
    printer_cal_dir.mkdir(parents=True)

    # Create calibration images at various Z-heights
    z_heights = [0, 5, 10, 15, 20, 50, 100, 150, 200, 235]

    for z in z_heights:
        # Create a clean plate image (uniform gray with slight noise)
        img = np.random.randint(100, 120, (1080, 1920), dtype=np.uint8)

        # Add gradient to simulate lighting
        y_gradient = np.linspace(0.9, 1.1, 1080)[:, np.newaxis]
        img = (img * y_gradient).astype(np.uint8)

        # Save calibration image
        img_path = printer_cal_dir / f"Z{z:03d}mm_20250101_120000.png"
        Image.fromarray(img).save(img_path)

    # Save calibration metadata
    save_calibration_metadata(
        printer_serial=printer_serial,
        calibration_dir=str(calibration_dir),
        z_heights=z_heights,
        notes="Mock calibration for testing"
    )

    print(f"✓ Created {len(z_heights)} calibration images")

    # Step 2: Test with clean plate
    print("\n[2/5] Testing detection on CLEAN plate...")

    # Create a clean plate image (similar to calibration)
    clean_img = np.random.randint(100, 120, (1080, 1920), dtype=np.uint8)
    y_gradient = np.linspace(0.9, 1.1, 1080)[:, np.newaxis]
    clean_img = (clean_img * y_gradient).astype(np.uint8)

    clean_path = Path(temp_dir) / "clean_plate.png"
    Image.fromarray(clean_img).save(clean_path)

    # Run detection
    result_clean = detect_plate_objects(
        current_image_path=str(clean_path),
        printer_serial=printer_serial,
        z_height=10.0,
        calibration_dir=str(calibration_dir)
    )

    print(f"  Status: {'✓ CLEAN' if result_clean['is_clean'] else '✗ DIRTY'}")
    print(f"  SSIM Score: {result_clean['ssim_score']:.4f}")
    print(f"  Threshold: {result_clean['threshold_used']:.4f}")
    print(f"  Confidence: {result_clean['confidence']:.2%}")
    print(f"  Processing Time: {result_clean['processing_time_ms']:.1f}ms")

    # Step 3: Test with object on plate
    print("\n[3/5] Testing detection on plate WITH OBJECT...")

    # Create a plate with an object
    dirty_img = np.random.randint(100, 120, (1080, 1920), dtype=np.uint8)
    y_gradient = np.linspace(0.9, 1.1, 1080)[:, np.newaxis]
    dirty_img = (dirty_img * y_gradient).astype(np.uint8)

    # Add a dark rectangular object (simulated print failure)
    obj_y, obj_x, obj_h, obj_w = 400, 800, 200, 150
    dirty_img[obj_y:obj_y+obj_h, obj_x:obj_x+obj_w] = 30

    dirty_path = Path(temp_dir) / "dirty_plate.png"
    Image.fromarray(dirty_img).save(dirty_path)

    # Run detection
    result_dirty = detect_plate_objects(
        current_image_path=str(dirty_path),
        printer_serial=printer_serial,
        z_height=10.0,
        calibration_dir=str(calibration_dir)
    )

    print(f"  Status: {'✓ CLEAN' if result_dirty['is_clean'] else '✗ DIRTY'}")
    print(f"  SSIM Score: {result_dirty['ssim_score']:.4f}")
    print(f"  Threshold: {result_dirty['threshold_used']:.4f}")
    print(f"  Regions Detected: {len(result_dirty['regions_detected'])}")
    print(f"  Confidence: {result_dirty['confidence']:.2%}")
    print(f"  Processing Time: {result_dirty['processing_time_ms']:.1f}ms")

    # Step 4: Test adaptive thresholding
    print("\n[4/5] Testing adaptive thresholding at different Z-heights...")

    from src.cv_analysis.adaptive_threshold import get_adaptive_threshold

    test_z_heights = [2.0, 10.0, 50.0, 200.0]
    print(f"  {'Z-Height':>10} | {'Threshold':>10}")
    print(f"  {'-'*10}-+-{'-'*10}")

    for z in test_z_heights:
        threshold = get_adaptive_threshold(z_height=z, printer_id=printer_serial)
        print(f"  {z:>10.1f} | {threshold:>10.4f}")

    # Step 5: Verify results
    print("\n[5/5] Validating results...")

    test_results = {
        "clean_detected_as_clean": result_clean['is_clean'],
        "dirty_detected_as_dirty": not result_dirty['is_clean'],
        "clean_ssim_high": result_clean['ssim_score'] > 0.85,
        "dirty_ssim_lower": result_dirty['ssim_score'] < result_clean['ssim_score'],
        "objects_found_on_dirty": len(result_dirty['regions_detected']) > 0,
        "performance_ok": result_clean['processing_time_ms'] < 100
    }

    all_passed = all(test_results.values())

    print("\n" + "=" * 70)
    print("TEST RESULTS:")
    print("=" * 70)

    for test_name, passed in test_results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status} - {test_name.replace('_', ' ').title()}")

    print("=" * 70)

    if all_passed:
        print("\n🎉 ALL TESTS PASSED! CV system is working correctly.")
    else:
        print("\n⚠️  SOME TESTS FAILED - Review results above")

    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("=" * 70)
    print(f"  Clean Plate Detection:  {'✓ Correct' if result_clean['is_clean'] else '✗ Incorrect'}")
    print(f"  Dirty Plate Detection:  {'✓ Correct' if not result_dirty['is_clean'] else '✗ Incorrect'}")
    print(f"  Avg Processing Time:    {(result_clean['processing_time_ms'] + result_dirty['processing_time_ms'])/2:.1f}ms")
    print(f"  System Status:          {'✓ READY FOR PRODUCTION' if all_passed else '⚠️ NEEDS ATTENTION'}")
    print("=" * 70)

finally:
    # Cleanup
    print(f"\nCleaning up temporary directory: {temp_dir}")
    shutil.rmtree(temp_dir)
    print("✓ Cleanup complete")

print("\n✓ Quick test completed!\n")
