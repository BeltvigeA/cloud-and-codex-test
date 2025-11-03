"""
Computer Vision Analysis Module for Build Plate Verification

This module provides automated detection of leftover objects on 3D printer build plates
using Structural Similarity Index (SSIM) and perceptual hashing techniques.

Main components:
- preprocessing: Image normalization and preparation
- perceptual_hash: Fast image hashing for pre-filtering
- ssim_comparison: Detailed structural similarity analysis
- region_analysis: Object region detection and characterization
- adaptive_threshold: Z-height-based threshold calculation
- detection: Main detection pipeline orchestration
- file_manager: Calibration and checkpoint file management
- visualization: Debug and analysis visualization tools

Example usage:
    from cv_analysis.detection import detect_plate_objects

    result = detect_plate_objects(
        current_image_path="/path/to/current_plate.png",
        printer_serial="00M09A3B1000685",
        z_height=138.0,
        calibration_dir="/print_farm_data/calibration"
    )

    if result['is_clean']:
        print(f"Plate is clean (SSIM: {result['ssim_score']:.3f})")
    else:
        print(f"Objects detected: {len(result['regions_detected'])} regions")
"""

__version__ = "0.1.0"
__author__ = "Print Farm Management System"

# Expose main detection function for convenience
from .detection import detect_plate_objects

__all__ = ['detect_plate_objects']
