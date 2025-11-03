#!/usr/bin/env python3
"""
Example usage script for CV plate verification system

Demonstrates:
1. Basic detection on a single image
2. Batch detection on multiple checkpoints
3. Saving detection results
4. Visualization generation
5. False positive rate tracking
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cv_analysis.detection import detect_plate_objects, detect_from_checkpoints
from src.cv_analysis.file_manager import (
    save_detection_result,
    get_checkpoint_images,
    load_detection_result
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def example_1_basic_detection():
    """
    Example 1: Basic plate detection on a single image
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 1: Basic Plate Detection")
    print("=" * 70)

    # Configuration
    printer_serial = "00M09A3B1000685"
    z_height = 138.0  # Final Z-height of print
    current_image_path = "/print_farm_data/checkpoints/job-123-456/checkpoint_100pct_Z138mm.png"
    calibration_dir = "/print_farm_data/calibration"

    # Run detection
    print(f"\nRunning detection for printer {printer_serial} at Z={z_height}mm...")

    result = detect_plate_objects(
        current_image_path=current_image_path,
        printer_serial=printer_serial,
        z_height=z_height,
        calibration_dir=calibration_dir,
        save_visualization=True,
        visualization_path="/tmp/detection_visualization.png"
    )

    # Display results
    print(f"\n{'─' * 70}")
    print(f"Detection Result:")
    print(f"{'─' * 70}")
    print(f"  Status: {'✓ CLEAN' if result['is_clean'] else '✗ OBJECT DETECTED'}")
    print(f"  SSIM Score: {result['ssim_score']:.4f}")
    print(f"  Threshold Used: {result['threshold_used']:.4f}")
    print(f"  Confidence: {result['confidence']:.2%}")
    print(f"  Detection Method: {result['detection_method']}")
    print(f"  Processing Time: {result['processing_time_ms']:.1f}ms")

    if not result['is_clean']:
        print(f"  Regions Detected: {len(result['regions_detected'])}")
        for i, region in enumerate(result['regions_detected'], 1):
            print(f"    Region {i}:")
            print(f"      - Bounding Box: {region['bbox']}")
            print(f"      - Area: {region['area']} pixels")
            print(f"      - Mean Difference: {region['mean_difference']:.3f}")

    print(f"{'─' * 70}\n")

    if result['is_clean']:
        print("✓ Plate is clean - safe to start next print!")
    else:
        print("✗ Objects detected - manual plate clearing required!")

    return result


def example_2_checkpoint_detection():
    """
    Example 2: Detect on multiple checkpoints from a print job
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 2: Checkpoint Detection")
    print("=" * 70)

    job_id = "job-123-456"
    printer_serial = "00M09A3B1000685"
    checkpoints_dir = "/print_farm_data/checkpoints"
    calibration_dir = "/print_farm_data/calibration"

    # Get all checkpoint images for this job
    print(f"\nGetting checkpoint images for job {job_id}...")
    checkpoints = get_checkpoint_images(job_id, checkpoints_dir)

    print(f"Found {len(checkpoints)} checkpoints:")
    for cp in checkpoints:
        print(f"  - {cp['percent']}% @ Z={cp['z_height']}mm")

    # Run detection on all checkpoints
    print("\nRunning detection on all checkpoints...")
    results = detect_from_checkpoints(
        checkpoint_images=checkpoints,
        printer_serial=printer_serial,
        calibration_dir=calibration_dir
    )

    # Display summary
    print(f"\n{'─' * 70}")
    print("Detection Summary:")
    print(f"{'─' * 70}")

    all_clean = all(r['is_clean'] for r in results)

    for cp, result in zip(checkpoints, results):
        status = "✓" if result['is_clean'] else "✗"
        print(
            f"  {status} {cp['percent']:3d}% | "
            f"Z={cp['z_height']:6.1f}mm | "
            f"SSIM={result['ssim_score']:.4f} | "
            f"Regions={len(result['regions_detected'])}"
        )

    print(f"{'─' * 70}\n")

    if all_clean:
        print("✓ All checkpoints are clean!")
    else:
        print("✗ Some checkpoints show objects - review required!")

    return results


def example_3_save_results():
    """
    Example 3: Run detection and save results with metadata
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Save Detection Results")
    print("=" * 70)

    job_id = "job-123-456"
    attempt_number = 1  # First breaking attempt
    printer_serial = "00M09A3B1000685"
    z_height = 138.0

    current_image_path = "/print_farm_data/checkpoints/job-123-456/checkpoint_100pct_Z138mm.png"
    calibration_dir = "/print_farm_data/calibration"
    output_dir = "/print_farm_data/cv_analysis"

    # Run detection
    print(f"\nRunning detection (attempt {attempt_number})...")

    result = detect_plate_objects(
        current_image_path=current_image_path,
        printer_serial=printer_serial,
        z_height=z_height,
        calibration_dir=calibration_dir
    )

    # Save result with metadata
    print("Saving detection result...")

    json_path = save_detection_result(
        result=result,
        job_id=job_id,
        attempt_number=attempt_number,
        z_height=z_height,
        output_dir=output_dir
    )

    print(f"✓ Saved to: {json_path}")

    # Load and verify
    print("\nVerifying saved result...")
    loaded_result = load_detection_result(json_path)

    print(f"✓ Loaded result: is_clean={loaded_result['is_clean']}")
    print(f"  Timestamp: {loaded_result['timestamp']}")
    print(f"  Job ID: {loaded_result['job_id']}")

    return json_path


def example_4_adaptive_thresholding():
    """
    Example 4: Adaptive thresholding based on false positive rate
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 4: Adaptive Thresholding")
    print("=" * 70)

    printer_serial = "00M09A3B1000685"
    current_image_path = "/print_farm_data/checkpoints/job-123-456/checkpoint_100pct_Z138mm.png"
    calibration_dir = "/print_farm_data/calibration"

    # Simulate different false positive rates
    fp_rates = [0.01, 0.05, 0.10, 0.15]  # 1%, 5%, 10%, 15%

    print("\nTesting threshold adaptation with different FP rates:")
    print(f"{'─' * 70}")
    print(f"{'FP Rate':>10} | {'Threshold':>10} | {'SSIM':>10} | {'Is Clean':>10}")
    print(f"{'─' * 70}")

    for fp_rate in fp_rates:
        result = detect_plate_objects(
            current_image_path=current_image_path,
            printer_serial=printer_serial,
            z_height=50.0,  # Mid-height
            calibration_dir=calibration_dir,
            false_positive_rate_24h=fp_rate
        )

        print(
            f"{fp_rate:>9.1%} | "
            f"{result['threshold_used']:>10.4f} | "
            f"{result['ssim_score']:>10.4f} | "
            f"{'Yes' if result['is_clean'] else 'No':>10}"
        )

    print(f"{'─' * 70}\n")
    print("Note: Higher FP rate → Lower threshold → Fewer false positives")


def example_5_z_height_zones():
    """
    Example 5: Show how thresholds change with Z-height
    """
    print("\n" + "=" * 70)
    print("EXAMPLE 5: Z-Height Zone Thresholds")
    print("=" * 70)

    from src.cv_analysis.adaptive_threshold import get_adaptive_threshold

    printer_serial = "00M09A3B1000685"
    z_heights = [0, 2, 5, 10, 15, 20, 50, 100, 150, 200, 235]

    print("\nThreshold changes across Z-heights:")
    print(f"{'─' * 70}")
    print(f"{'Z-Height (mm)':>15} | {'Threshold':>15} | {'Zone Description':>30}")
    print(f"{'─' * 70}")

    for z in z_heights:
        threshold = get_adaptive_threshold(
            z_height=z,
            printer_id=printer_serial
        )

        if z < 5:
            zone = "Very conservative (low Z)"
        elif z < 20:
            zone = "Conservative (mid-low Z)"
        else:
            zone = "Standard (high Z)"

        print(f"{z:>15.1f} | {threshold:>15.4f} | {zone:>30}")

    print(f"{'─' * 70}\n")
    print("Note: Lower Z-heights use higher thresholds (more conservative)")


def main():
    """
    Run all examples
    """
    print("\n")
    print("=" * 70)
    print(" CV PLATE VERIFICATION SYSTEM - EXAMPLE USAGE")
    print("=" * 70)

    try:
        # Run examples
        example_1_basic_detection()
        example_2_checkpoint_detection()
        example_3_save_results()
        example_4_adaptive_thresholding()
        example_5_z_height_zones()

        print("\n" + "=" * 70)
        print(" ALL EXAMPLES COMPLETED SUCCESSFULLY")
        print("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"Error running examples: {e}", exc_info=True)
        print(f"\n✗ Error: {e}\n")
        print("Note: Some examples may fail if test data is not available.")
        print("To run successfully, you need:")
        print("  1. Calibration images in /print_farm_data/calibration/")
        print("  2. Checkpoint images in /print_farm_data/checkpoints/")


if __name__ == "__main__":
    main()
