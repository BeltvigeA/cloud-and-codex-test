"""
Computer Vision Analysis Module for 3D Printer Build Plate Verification

This module provides computer vision-based detection of leftover objects on
3D printer build plates by comparing current images against calibration references.

Key Features:
- SSIM (Structural Similarity Index) comparison
- Perceptual hashing for quick pre-filtering
- Adaptive thresholding based on Z-height
- Region analysis for object detection
- Comprehensive logging and visualization

Usage:
    from cv_analysis.detection import detect_plate_objects

    result = detect_plate_objects(
        current_image_path="path/to/current/image.png",
        printer_serial="00M09A3B1000685",
        z_height=138.0,
        calibration_dir="/print_farm_data/calibration"
    )

    if result['is_clean']:
        print("Plate is clean!")
    else:
        print(f"Objects detected: {len(result['regions_detected'])}")
"""

__version__ = "1.0.0"
__author__ = "3D Printer Farm Management System"

from .detection import detect_plate_objects
from .preprocessing import preprocess_image
from .ssim_comparison import compare_images_ssim
from .perceptual_hash import calculate_perceptual_hash, compare_hashes
from .region_analysis import analyze_difference_regions
from .adaptive_threshold import get_adaptive_threshold

__all__ = [
    'detect_plate_objects',
    'preprocess_image',
    'compare_images_ssim',
    'calculate_perceptual_hash',
    'compare_hashes',
    'analyze_difference_regions',
    'get_adaptive_threshold',
]
