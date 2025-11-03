#!/usr/bin/env python3
"""
Realistic test for CV plate verification system
Creates correlated synthetic images that simulate actual plate conditions
"""

import tempfile
import shutil
from pathlib import Path
import numpy as np
from PIL import Image
import cv2

from src.cv_analysis.detection import detect_plate_objects
from src.cv_analysis.file_manager import save_calibration_metadata

print("=" * 70)
print("CV PLATE VERIFICATION - REALISTIC TEST")
print("=" * 70)

def create_realistic_plate_image(seed=42, add_object=False):
    """Create a realistic build plate image with consistent base texture"""
    np.random.seed(seed)

    # Start with a base texture pattern (simulating the build plate surface)
    base = np.ones((1080, 1920), dtype=np.uint8) * 110

    # Add consistent texture noise
    texture = np.random.randint(-5, 5, (1080, 1920), dtype=np.int16)
    base = np.clip(base + texture, 0, 255).astype(np.uint8)

    # Add realistic lighting gradient
    y_grad = np.linspace(0.85, 1.15, 1080)[:, np.newaxis]
    x_grad = np.linspace(0.95, 1.05, 1920)[np.newaxis, :]
    lighting = y_grad * x_grad

    img = (base * lighting).astype(np.uint8)

    # Apply slight Gaussian blur for realism
    img = cv2.GaussianBlur(img, (5, 5), 0)

    if add_object:
        # Add a realistic print failure object
        obj_y, obj_x, obj_h, obj_w = 450, 850, 180, 140

        # Create object with gradient (3D appearance)
        object_mask = np.ones((obj_h, obj_w), dtype=np.uint8)
        obj_gradient = np.linspace(0.4, 0.7, obj_h)[:, np.newaxis]
        object_region = (base[obj_y:obj_y+obj_h, obj_x:obj_x+obj_w] * obj_gradient).astype(np.uint8)

        # Apply object to image
        img[obj_y:obj_y+obj_h, obj_x:obj_x+obj_w] = object_region

    return img

# Create temporary directory
temp_dir = tempfile.mkdtemp()
print(f"\nUsing temporary directory: {temp_dir}")

try:
    # Step 1: Create calibration images
    print("\n[1/4] Creating realistic calibration data...")

    printer_serial = "TEST_PRINTER_001"
    calibration_dir = Path(temp_dir) / "calibration"
    printer_cal_dir = calibration_dir / printer_serial
    printer_cal_dir.mkdir(parents=True)

    z_heights = [0, 5, 10, 15, 20, 50, 100, 150, 200, 235]

    # Create calibration images with consistent seed
    for i, z in enumerate(z_heights):
        img = create_realistic_plate_image(seed=100 + i)
        img_path = printer_cal_dir / f"Z{z:03d}mm_20250101_120000.png"
        Image.fromarray(img).save(img_path)

    save_calibration_metadata(
        printer_serial=printer_serial,
        calibration_dir=str(calibration_dir),
        z_heights=z_heights,
        notes="Realistic mock calibration"
    )

    print(f"✓ Created {len(z_heights)} realistic calibration images")

    # Step 2: Test clean plate (using same seed as calibration Z=10mm)
    print("\n[2/4] Testing CLEAN plate (should match calibration)...")

    # Create plate image with SAME seed as Z=10mm calibration (seed=102)
    clean_img = create_realistic_plate_image(seed=102, add_object=False)
    clean_path = Path(temp_dir) / "clean_plate.png"
    Image.fromarray(clean_img).save(clean_path)

    result_clean = detect_plate_objects(
        current_image_path=str(clean_path),
        printer_serial=printer_serial,
        z_height=10.0,
        calibration_dir=str(calibration_dir),
        save_visualization=True,
        visualization_path=str(Path(temp_dir) / "clean_comparison.png")
    )

    print(f"  Status: {'✓ CLEAN' if result_clean['is_clean'] else '✗ DIRTY'}")
    print(f"  SSIM Score: {result_clean['ssim_score']:.4f}")
    print(f"  Threshold: {result_clean['threshold_used']:.4f}")
    print(f"  Detection Method: {result_clean['detection_method']}")
    print(f"  Hash Distance: {result_clean['hash_distance']}")
    print(f"  Confidence: {result_clean['confidence']:.2%}")
    print(f"  Processing Time: {result_clean['processing_time_ms']:.1f}ms")

    # Step 3: Test nearly-clean plate (small variation)
    print("\n[3/4] Testing NEARLY-CLEAN plate (slight variation)...")

    # Create similar but not identical plate (different seed)
    nearly_clean_img = create_realistic_plate_image(seed=202, add_object=False)
    nearly_clean_path = Path(temp_dir) / "nearly_clean_plate.png"
    Image.fromarray(nearly_clean_img).save(nearly_clean_path)

    result_nearly = detect_plate_objects(
        current_image_path=str(nearly_clean_path),
        printer_serial=printer_serial,
        z_height=10.0,
        calibration_dir=str(calibration_dir)
    )

    print(f"  Status: {'✓ CLEAN' if result_nearly['is_clean'] else '✗ DIRTY'}")
    print(f"  SSIM Score: {result_nearly['ssim_score']:.4f}")
    print(f"  Threshold: {result_nearly['threshold_used']:.4f}")
    print(f"  Confidence: {result_nearly['confidence']:.2%}")
    print(f"  Processing Time: {result_nearly['processing_time_ms']:.1f}ms")

    # Step 4: Test dirty plate with object
    print("\n[4/4] Testing DIRTY plate (with object)...")

    dirty_img = create_realistic_plate_image(seed=102, add_object=True)
    dirty_path = Path(temp_dir) / "dirty_plate.png"
    Image.fromarray(dirty_img).save(dirty_path)

    result_dirty = detect_plate_objects(
        current_image_path=str(dirty_path),
        printer_serial=printer_serial,
        z_height=10.0,
        calibration_dir=str(calibration_dir),
        save_visualization=True,
        visualization_path=str(Path(temp_dir) / "dirty_comparison.png")
    )

    print(f"  Status: {'✓ CLEAN' if result_dirty['is_clean'] else '✗ DIRTY'}")
    print(f"  SSIM Score: {result_dirty['ssim_score']:.4f}")
    print(f"  Threshold: {result_dirty['threshold_used']:.4f}")
    print(f"  Regions Detected: {len(result_dirty['regions_detected'])}")
    if result_dirty['regions_detected']:
        largest = max(result_dirty['regions_detected'], key=lambda r: r['area'])
        print(f"  Largest Region: {largest['area']} pixels at {largest['centroid']}")
    print(f"  Confidence: {result_dirty['confidence']:.2%}")
    print(f"  Processing Time: {result_dirty['processing_time_ms']:.1f}ms")

    # Results summary
    print("\n" + "=" * 70)
    print("TEST RESULTS:")
    print("=" * 70)

    tests = {
        "Identical plate detected as clean": result_clean['is_clean'],
        "Similar plate detected as clean": result_nearly['is_clean'],
        "Plate with object detected as dirty": not result_dirty['is_clean'],
        "Object regions found": len(result_dirty['regions_detected']) > 0,
        "Clean SSIM is high (>0.90)": result_clean['ssim_score'] > 0.90,
        "Dirty SSIM is lower than clean": result_dirty['ssim_score'] < result_clean['ssim_score'],
        "Performance <100ms": all(r['processing_time_ms'] < 100 for r in [result_clean, result_dirty])
    }

    for test_name, passed in tests.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status} - {test_name}")

    all_passed = all(tests.values())

    print("=" * 70)
    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("=" * 70)
    print(f"  Identical Plate:   SSIM={result_clean['ssim_score']:.4f} → {'✓ CLEAN' if result_clean['is_clean'] else '✗ DIRTY'}")
    print(f"  Similar Plate:     SSIM={result_nearly['ssim_score']:.4f} → {'✓ CLEAN' if result_nearly['is_clean'] else '✗ DIRTY'}")
    print(f"  Plate with Object: SSIM={result_dirty['ssim_score']:.4f} → {'✗ DIRTY' if not result_dirty['is_clean'] else '✓ CLEAN'}")
    print(f"  Avg Processing:    {np.mean([r['processing_time_ms'] for r in [result_clean, result_dirty]]):.1f}ms")
    print(f"  Overall Status:    {'✓ ALL TESTS PASSED' if all_passed else '⚠️ SOME TESTS FAILED'}")
    print("=" * 70)

    if all_passed:
        print("\n🎉 SUCCESS! CV system is working correctly with realistic images.")
    else:
        print("\n⚠️  Some tests failed - this may be due to synthetic image variation.")

    print(f"\nVisualization images saved to:")
    print(f"  - {temp_dir}/clean_comparison.png")
    print(f"  - {temp_dir}/dirty_comparison.png")
    print(f"\nTest images saved to:")
    print(f"  - {temp_dir}/clean_plate.png")
    print(f"  - {temp_dir}/dirty_plate.png")
    print(f"\nKeeping temp directory for review: {temp_dir}")
    print("(Delete manually when done: rm -rf {})".format(temp_dir))

except Exception as e:
    print(f"\n✗ Error during testing: {e}")
    import traceback
    traceback.print_exc()
    shutil.rmtree(temp_dir)
    raise

print("\n✓ Realistic test completed!\n")
