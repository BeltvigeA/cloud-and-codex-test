"""
Main Detection Module - Orchestrates Complete CV Pipeline

This is the primary entry point for build plate object detection.
It coordinates all other modules to perform a complete detection workflow.

Pipeline:
1. Load and preprocess images
2. Quick perceptual hash pre-filter
3. SSIM comparison with adaptive threshold
4. Region analysis for detected differences
5. Generate result with confidence score
6. Optional visualization for debugging
"""

import logging
import time
from typing import Dict, Any, Optional
from pathlib import Path

import numpy as np
from PIL import Image

from .preprocessing import preprocess_image, validate_image_pair
from .perceptual_hash import calculate_perceptual_hash, compare_hashes, is_hash_match
from .ssim_comparison import compare_images_ssim, compare_with_confidence
from .region_analysis import (
    analyze_difference_regions,
    calculate_region_statistics,
    filter_edge_regions,
    merge_nearby_regions,
    classify_region_type
)
from .adaptive_threshold import get_adaptive_threshold, load_printer_fp_history
from .file_manager import find_calibration_reference, load_calibration_metadata
from .visualization import create_comparison_visualization, save_debug_images

logger = logging.getLogger(__name__)


def detect_plate_objects(
    current_image_path: str,
    printer_serial: str,
    z_height: float,
    calibration_dir: str,
    config: Optional[Dict[str, Any]] = None,
    save_visualization: bool = True,
    visualization_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Detect objects on build plate by comparing against calibration reference.

    This is the main entry point for the CV detection system. It performs
    a three-stage pipeline:
    1. Hash pre-filter (fast rejection of obviously different images)
    2. SSIM comparison with adaptive threshold
    3. Region analysis for detailed object characterization

    Args:
        current_image_path: Path to current plate image
        printer_serial: Printer serial number
        z_height: Current Z-height in mm
        calibration_dir: Directory containing calibration references
        config: Optional configuration dict (uses defaults if None)
        save_visualization: Whether to save debug visualization
        visualization_dir: Where to save visualizations (if enabled)

    Returns:
        Dictionary with keys:
        - 'is_clean': bool (True if plate is clean)
        - 'detection_method': str ('hash_match', 'ssim_clean', 'ssim_object')
        - 'ssim_score': float (similarity score)
        - 'threshold_used': float (adaptive threshold applied)
        - 'regions_detected': List[Dict] (detected object regions)
        - 'confidence': float (0.0-1.0, confidence in detection)
        - 'reference_z': float (Z-height of reference used)
        - 'reference_path': str (path to reference image used)
        - 'processing_time_ms': float (total processing time)
        - 'hash_distance': int (perceptual hash distance)

    Raises:
        FileNotFoundError: If current image or calibration reference not found
        ValueError: If images are incompatible

    Example:
        >>> result = detect_plate_objects(
        ...     current_image_path="/data/checkpoints/job123/checkpoint_100pct_Z138mm.png",
        ...     printer_serial="00M09A3B1000685",
        ...     z_height=138.0,
        ...     calibration_dir="/data/calibration"
        ... )
        >>> if result['is_clean']:
        ...     print(f"✓ Plate is clean (SSIM: {result['ssim_score']:.3f})")
        ... else:
        ...     print(f"✗ Objects detected (SSIM: {result['ssim_score']:.3f})")
        ...     print(f"  Regions: {len(result['regions_detected'])}")
    """
    start_time = time.time()

    try:
        logger.info(
            f"Starting detection: printer={printer_serial}, "
            f"z_height={z_height:.1f}mm, image={current_image_path}"
        )

        # Step 1: Load configuration (use defaults if not provided)
        cfg = config or {}

        # Step 2: Find calibration reference
        reference_path = find_calibration_reference(
            printer_serial,
            z_height,
            calibration_dir
        )

        if reference_path is None:
            logger.error(f"No calibration reference found for printer {printer_serial}")
            return _create_error_result(
                "no_calibration_reference",
                "No calibration reference available"
            )

        # Extract reference Z-height
        reference_z = _extract_z_from_filename(reference_path)

        # Step 3: Load and preprocess images
        logger.debug("Loading and preprocessing images...")
        current_raw = Image.open(current_image_path)
        reference_raw = Image.open(reference_path)

        current_preprocessed = preprocess_image(current_raw)
        reference_preprocessed = preprocess_image(reference_raw)

        # Validate compatibility
        if not validate_image_pair(reference_preprocessed, current_preprocessed):
            raise ValueError("Reference and current images are incompatible")

        # Step 4: Perceptual hash pre-filter
        logger.debug("Calculating perceptual hashes...")
        hash_current = calculate_perceptual_hash(current_preprocessed)
        hash_reference = calculate_perceptual_hash(reference_preprocessed)
        hash_distance = compare_hashes(hash_current, hash_reference)

        logger.debug(f"Hash distance: {hash_distance}")

        # If hash distance is very small, images are essentially identical
        hash_threshold = cfg.get('hash_match_threshold', 5)
        if hash_distance <= hash_threshold:
            logger.info(f"Hash match (distance={hash_distance}) - plate is clean")
            processing_time = (time.time() - start_time) * 1000

            return {
                'is_clean': True,
                'detection_method': 'hash_match',
                'ssim_score': 1.0,  # Assume perfect match
                'threshold_used': 0.0,  # Not used
                'regions_detected': [],
                'confidence': 1.0,
                'reference_z': reference_z,
                'reference_path': reference_path,
                'processing_time_ms': processing_time,
                'hash_distance': hash_distance
            }

        # Step 5: Calculate adaptive threshold
        logger.debug("Calculating adaptive threshold...")
        fp_rate = load_printer_fp_history(
            printer_serial,
            cfg.get('fp_history_dir', '/print_farm_data/cv_analysis/fp_history')
        )
        threshold = get_adaptive_threshold(z_height, printer_serial, fp_rate)

        logger.debug(f"Using threshold: {threshold:.3f} (FP rate: {fp_rate})")

        # Step 6: SSIM comparison
        logger.debug("Computing SSIM...")
        ssim_score, confidence, difference_map = compare_with_confidence(
            reference_preprocessed,
            current_preprocessed,
            window_size=cfg.get('ssim_window_size', 7)
        )

        logger.info(f"SSIM score: {ssim_score:.4f}, confidence: {confidence:.3f}")

        # Step 7: Analyze regions if SSIM indicates differences
        regions = []
        if ssim_score < threshold:
            logger.debug("SSIM below threshold - analyzing regions...")
            regions = analyze_difference_regions(
                difference_map,
                min_area=cfg.get('min_region_area', 100),
                max_aspect_ratio=cfg.get('max_aspect_ratio', 5.0),
                difference_threshold=cfg.get('difference_threshold', 0.5)
            )

            # Filter edge regions (likely artifacts)
            regions = filter_edge_regions(
                regions,
                current_preprocessed.shape,
                edge_margin=cfg.get('edge_margin', 20)
            )

            # Merge nearby regions
            regions = merge_nearby_regions(
                regions,
                distance_threshold=cfg.get('merge_distance', 50)
            )

            # Classify regions
            for region in regions:
                region['type'] = classify_region_type(region)

            logger.info(f"Detected {len(regions)} regions after filtering")

        # Step 8: Calculate statistics
        region_stats = calculate_region_statistics(regions)

        # Step 9: Make final decision
        is_clean = ssim_score >= threshold

        # Even if SSIM is below threshold, if no significant regions found, consider clean
        if not is_clean and len(regions) == 0:
            logger.info("SSIM below threshold but no regions found - considering clean")
            is_clean = True

        # If only small residue regions, might still be clean
        if not is_clean and all(r.get('type') == 'residue' for r in regions):
            if region_stats['total_area'] < 500:  # Small total area
                logger.info("Only small residue detected - considering clean")
                is_clean = True

        detection_method = 'ssim_clean' if is_clean else 'ssim_object'

        # Step 10: Save visualization if requested
        if save_visualization and visualization_dir:
            try:
                vis_path = Path(visualization_dir) / f"detection_Z{int(z_height):03d}mm.png"
                vis_path.parent.mkdir(parents=True, exist_ok=True)

                create_comparison_visualization(
                    reference_preprocessed,
                    current_preprocessed,
                    difference_map,
                    regions,
                    ssim_score,
                    str(vis_path),
                    threshold=threshold,
                    z_height=z_height
                )

                logger.debug(f"Saved visualization: {vis_path}")
            except Exception as e:
                logger.warning(f"Failed to save visualization: {str(e)}")

        # Step 11: Calculate processing time
        processing_time = (time.time() - start_time) * 1000

        # Step 12: Compile result
        result = {
            'is_clean': is_clean,
            'detection_method': detection_method,
            'ssim_score': float(ssim_score),
            'threshold_used': float(threshold),
            'regions_detected': regions,
            'confidence': float(confidence),
            'reference_z': float(reference_z),
            'reference_path': reference_path,
            'processing_time_ms': float(processing_time),
            'hash_distance': int(hash_distance),
            'region_statistics': region_stats,
            'z_height': float(z_height),
            'printer_serial': printer_serial
        }

        logger.info(
            f"Detection complete: is_clean={is_clean}, "
            f"ssim={ssim_score:.3f}, regions={len(regions)}, "
            f"time={processing_time:.1f}ms"
        )

        return result

    except Exception as e:
        logger.error(f"Error in detection pipeline: {str(e)}", exc_info=True)
        return _create_error_result('detection_error', str(e))


def _extract_z_from_filename(filepath: str) -> float:
    """
    Extract Z-height from calibration filename.

    Args:
        filepath: Path to calibration image

    Returns:
        Z-height in millimeters
    """
    try:
        filename = Path(filepath).stem
        # Format: Z045mm_timestamp
        z_str = filename.split('_')[0][1:-2]  # Remove 'Z' and 'mm'
        return float(z_str)
    except Exception:
        logger.warning(f"Could not extract Z-height from {filepath}")
        return 0.0


def _create_error_result(error_type: str, error_message: str) -> Dict[str, Any]:
    """
    Create error result dictionary.

    Safety first: On error, return is_clean=False to avoid starting
    a new print when we're uncertain.

    Args:
        error_type: Type of error
        error_message: Error description

    Returns:
        Error result dictionary
    """
    return {
        'is_clean': False,  # Safe default
        'detection_method': 'error',
        'ssim_score': 0.0,
        'threshold_used': 0.0,
        'regions_detected': [],
        'confidence': 0.0,
        'reference_z': 0.0,
        'reference_path': None,
        'processing_time_ms': 0.0,
        'hash_distance': 999,
        'error_type': error_type,
        'error_message': error_message
    }


def batch_detect(
    image_paths: list[str],
    printer_serial: str,
    z_heights: list[float],
    calibration_dir: str,
    config: Optional[Dict[str, Any]] = None
) -> list[Dict[str, Any]]:
    """
    Perform batch detection on multiple images.

    Useful for analyzing a complete print job (all checkpoints).

    Args:
        image_paths: List of image paths
        printer_serial: Printer serial number
        z_heights: List of Z-heights (corresponding to image_paths)
        calibration_dir: Calibration directory
        config: Optional configuration

    Returns:
        List of detection results

    Example:
        >>> checkpoints = [
        ...     '/data/checkpoints/job123/checkpoint_0pct_Z0mm.png',
        ...     '/data/checkpoints/job123/checkpoint_33pct_Z45mm.png',
        ...     '/data/checkpoints/job123/checkpoint_66pct_Z91mm.png',
        ...     '/data/checkpoints/job123/checkpoint_100pct_Z138mm.png',
        ... ]
        >>> z_heights = [0, 45, 91, 138]
        >>> results = batch_detect(checkpoints, "PRINTER123", z_heights, "/data/cal")
        >>> all_clean = all(r['is_clean'] for r in results)
    """
    results = []

    for img_path, z_height in zip(image_paths, z_heights):
        try:
            result = detect_plate_objects(
                img_path,
                printer_serial,
                z_height,
                calibration_dir,
                config=config,
                save_visualization=False
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Error detecting {img_path}: {str(e)}")
            results.append(_create_error_result('batch_detection_error', str(e)))

    return results


def is_breaking_successful(
    detection_results: list[Dict[str, Any]],
    require_all_clean: bool = True
) -> bool:
    """
    Determine if plate breaking was successful based on detection results.

    Args:
        detection_results: List of detection results (from batch_detect)
        require_all_clean: If True, all checkpoints must be clean

    Returns:
        True if breaking was successful (plate is clean)

    Example:
        >>> results = batch_detect([...], "PRINTER123", [...], "/data/cal")
        >>> if is_breaking_successful(results):
        ...     print("Plate is clean - ready for next print!")
        ... else:
        ...     print("Objects remain - retry breaking")
    """
    if not detection_results:
        return False

    if require_all_clean:
        return all(r.get('is_clean', False) for r in detection_results)
    else:
        # At least the final checkpoint must be clean
        return detection_results[-1].get('is_clean', False)


def get_detection_summary(result: Dict[str, Any]) -> str:
    """
    Generate human-readable summary of detection result.

    Args:
        result: Detection result dictionary

    Returns:
        Summary string

    Example:
        >>> result = detect_plate_objects(...)
        >>> print(get_detection_summary(result))
        ✓ Plate is clean (SSIM: 0.962, confidence: 0.89, 2.3ms)
    """
    if result.get('error_type'):
        return f"✗ Error: {result.get('error_message', 'Unknown error')}"

    symbol = "✓" if result['is_clean'] else "✗"
    status = "clean" if result['is_clean'] else "has objects"

    summary = (
        f"{symbol} Plate is {status} "
        f"(SSIM: {result['ssim_score']:.3f}, "
        f"threshold: {result['threshold_used']:.3f}, "
        f"confidence: {result['confidence']:.2f}, "
        f"{result['processing_time_ms']:.1f}ms)"
    )

    if not result['is_clean']:
        num_regions = len(result['regions_detected'])
        total_area = result.get('region_statistics', {}).get('total_area', 0)
        summary += f"\n  Detected {num_regions} region(s), total area: {total_area}px"

    return summary
