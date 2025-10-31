#!/usr/bin/env python3
"""
Example Usage of CV Plate Verification System

This script demonstrates how to use the computer vision analysis
module to detect leftover objects on 3D printer build plates.

Usage:
    python cv_detection_example.py

Requirements:
    - Calibration images must be captured beforehand
    - Checkpoint images from print job
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cv_analysis.detection import (
    detect_plate_objects,
    batch_detect,
    is_breaking_successful,
    get_detection_summary
)
from cv_analysis.file_manager import (
    ensure_directory_structure,
    save_detection_result,
    save_analysis_summary
)
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def example_single_detection():
    """
    Example 1: Single image detection

    Detect objects on a plate after a print completes.
    """
    print("\n" + "="*70)
    print("EXAMPLE 1: Single Image Detection")
    print("="*70 + "\n")

    # Configuration
    current_image_path = "/print_farm_data/checkpoints/job_abc123/checkpoint_100pct_Z138mm.png"
    printer_serial = "00M09A3B1000685"
    z_height = 138.0
    calibration_dir = "/print_farm_data/calibration"

    print(f"Analyzing image: {current_image_path}")
    print(f"Printer: {printer_serial}")
    print(f"Z-height: {z_height}mm\n")

    try:
        # Perform detection
        result = detect_plate_objects(
            current_image_path=current_image_path,
            printer_serial=printer_serial,
            z_height=z_height,
            calibration_dir=calibration_dir,
            save_visualization=True,
            visualization_dir="/print_farm_data/cv_analysis/job_abc123/attempt_1"
        )

        # Print results
        print(get_detection_summary(result))
        print(f"\nDetailed results:")
        print(f"  Detection method: {result['detection_method']}")
        print(f"  SSIM score: {result['ssim_score']:.4f}")
        print(f"  Threshold used: {result['threshold_used']:.4f}")
        print(f"  Confidence: {result['confidence']:.3f}")
        print(f"  Processing time: {result['processing_time_ms']:.1f}ms")

        if not result['is_clean']:
            print(f"\n  Detected regions:")
            for i, region in enumerate(result['regions_detected'], 1):
                print(f"    Region {i}:")
                print(f"      Area: {region['area']} pixels")
                print(f"      Position: {region['bbox']}")
                print(f"      Type: {region.get('type', 'unknown')}")

        # Save result
        save_detection_result(
            result=result,
            job_id="job_abc123",
            attempt_number=1,
            z_height=z_height,
            output_dir="/print_farm_data/cv_analysis"
        )

        return result

    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Make sure calibration images exist for this printer!")
        return None


def example_batch_detection():
    """
    Example 2: Batch detection on multiple checkpoints

    Analyze all checkpoints from a print job to verify plate is clean
    at all stages.
    """
    print("\n" + "="*70)
    print("EXAMPLE 2: Batch Detection on Print Job")
    print("="*70 + "\n")

    # Configuration
    job_id = "job_abc123"
    printer_serial = "00M09A3B1000685"
    calibration_dir = "/print_farm_data/calibration"

    # Define checkpoints
    checkpoints = [
        {"path": f"/print_farm_data/checkpoints/{job_id}/checkpoint_0pct_Z0mm.png", "z": 0.0},
        {"path": f"/print_farm_data/checkpoints/{job_id}/checkpoint_33pct_Z45mm.png", "z": 45.0},
        {"path": f"/print_farm_data/checkpoints/{job_id}/checkpoint_66pct_Z91mm.png", "z": 91.0},
        {"path": f"/print_farm_data/checkpoints/{job_id}/checkpoint_100pct_Z138mm.png", "z": 138.0},
    ]

    image_paths = [cp['path'] for cp in checkpoints]
    z_heights = [cp['z'] for cp in checkpoints]

    print(f"Analyzing {len(checkpoints)} checkpoints for job {job_id}\n")

    try:
        # Perform batch detection
        results = batch_detect(
            image_paths=image_paths,
            printer_serial=printer_serial,
            z_heights=z_heights,
            calibration_dir=calibration_dir
        )

        # Print results for each checkpoint
        for checkpoint, result in zip(checkpoints, results):
            print(f"Checkpoint at Z={checkpoint['z']}mm:")
            print(f"  {get_detection_summary(result)}")
            print()

        # Check if breaking was successful
        breaking_successful = is_breaking_successful(results)

        print(f"\n{'='*70}")
        if breaking_successful:
            print("✓ PLATE IS CLEAN - Ready for next print!")
        else:
            print("✗ OBJECTS DETECTED - Retry plate breaking")
        print(f"{'='*70}\n")

        # Save analysis summary
        save_analysis_summary(
            job_id=job_id,
            attempt_number=1,
            detections=results,
            output_dir="/print_farm_data/cv_analysis"
        )

        return results

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return None


def example_with_visualization():
    """
    Example 3: Detection with visualization for debugging

    Useful for analyzing false positives/negatives.
    """
    print("\n" + "="*70)
    print("EXAMPLE 3: Detection with Visualization")
    print("="*70 + "\n")

    current_image_path = "/print_farm_data/checkpoints/job_xyz789/checkpoint_100pct_Z50mm.png"
    printer_serial = "00M09A3B1000685"
    z_height = 50.0

    try:
        result = detect_plate_objects(
            current_image_path=current_image_path,
            printer_serial=printer_serial,
            z_height=z_height,
            calibration_dir="/print_farm_data/calibration",
            save_visualization=True,
            visualization_dir="/print_farm_data/cv_analysis/job_xyz789/debug"
        )

        print("Detection complete with visualization saved:")
        print(f"  Visualization: /print_farm_data/cv_analysis/job_xyz789/debug/detection_Z050mm.png")
        print(f"\n{get_detection_summary(result)}")

        return result

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return None


def example_calibration_workflow():
    """
    Example 4: Calibration workflow

    This shows how to perform initial calibration for a new printer.
    Note: This example assumes you have a way to capture images at
    different Z-heights (typically done via the printer's API).
    """
    print("\n" + "="*70)
    print("EXAMPLE 4: Calibration Workflow (Conceptual)")
    print("="*70 + "\n")

    from cv_analysis.file_manager import save_calibration_image

    printer_serial = "00M09A3B1000685"
    calibration_dir = "/print_farm_data/calibration"

    print("Calibration process:")
    print("1. Ensure build plate is completely clean")
    print("2. Move print head to different Z-heights")
    print("3. Capture image at each height")
    print("4. Save images to calibration directory\n")

    # Example Z-heights (0mm to 235mm in 5mm increments)
    z_heights = list(range(0, 240, 5))

    print(f"Required calibration points: {len(z_heights)}")
    print(f"Z-heights: {z_heights}\n")

    # Simulated calibration (in practice, you'd capture real images)
    print("In a real workflow, you would:")
    for z in z_heights[:3]:  # Just show first 3 as example
        print(f"  1. Move to Z={z}mm")
        print(f"  2. Capture image")
        print(f"  3. Save: save_calibration_image(image_path, '{printer_serial}', {z}, '{calibration_dir}')")
        print()

    print("After calibration, verify:")
    from cv_analysis.file_manager import list_calibration_images

    images = list_calibration_images(printer_serial, calibration_dir)
    print(f"  Total calibration images: {len(images)}")


def example_custom_configuration():
    """
    Example 5: Detection with custom configuration

    Override default detection parameters for specific use cases.
    """
    print("\n" + "="*70)
    print("EXAMPLE 5: Custom Configuration")
    print("="*70 + "\n")

    # Custom configuration
    custom_config = {
        'hash_match_threshold': 3,  # Stricter hash matching
        'ssim_window_size': 9,       # Larger window for SSIM
        'min_region_area': 150,      # Ignore smaller regions
        'max_aspect_ratio': 4.0,     # Filter elongated regions
        'edge_margin': 30,           # Larger edge margin
    }

    print("Using custom configuration:")
    for key, value in custom_config.items():
        print(f"  {key}: {value}")
    print()

    try:
        result = detect_plate_objects(
            current_image_path="/print_farm_data/checkpoints/job_custom/checkpoint_100pct_Z100mm.png",
            printer_serial="00M09A3B1000685",
            z_height=100.0,
            calibration_dir="/print_farm_data/calibration",
            config=custom_config,
            save_visualization=False
        )

        print(get_detection_summary(result))

        return result

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return None


def main():
    """
    Main function - run all examples
    """
    print("\n" + "="*70)
    print("CV PLATE VERIFICATION SYSTEM - USAGE EXAMPLES")
    print("="*70)

    # Ensure directory structure exists
    print("\nSetting up directory structure...")
    try:
        ensure_directory_structure("/print_farm_data")
        print("✓ Directory structure ready\n")
    except Exception as e:
        print(f"✗ Error creating directories: {e}\n")

    # Run examples
    # Note: These will fail if you don't have actual images
    # Uncomment to run when you have real data

    # example_single_detection()
    # example_batch_detection()
    # example_with_visualization()
    example_calibration_workflow()
    # example_custom_configuration()

    print("\n" + "="*70)
    print("Examples complete!")
    print("="*70 + "\n")


if __name__ == '__main__':
    main()
