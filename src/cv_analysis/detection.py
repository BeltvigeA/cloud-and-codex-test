"""
Main Detection Pipeline Module

Orchestrates the complete plate object detection workflow:
1. Hash pre-filter (fast rejection of obviously different images)
2. SSIM comparison (structural similarity analysis)
3. Region analysis (identify specific object locations)
4. Adaptive thresholding (Z-height-based decision making)

Target: <50ms total detection time per image
"""

import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional

from .preprocessing import preprocess_image, load_and_preprocess
from .perceptual_hash import (
    calculate_perceptual_hash,
    compare_hashes,
    is_hash_match
)
from .ssim_comparison import (
    compare_images_ssim,
    get_difference_statistics
)
from .region_analysis import (
    analyze_difference_regions,
    filter_regions_by_position,
    merge_overlapping_regions,
    get_region_summary
)
from .adaptive_threshold import (
    get_adaptive_threshold,
    get_confidence_score
)
from .file_manager import (
    find_calibration_reference,
    load_calibration_metadata
)

logger = logging.getLogger(__name__)


def detect_plate_objects(
    current_image_path: str,
    printer_serial: str,
    z_height: float,
    calibration_dir: str,
    hash_threshold: int = 5,
    tolerance_mm: float = 3.0,
    false_positive_rate_24h: Optional[float] = None,
    save_visualization: bool = False,
    visualization_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Detect objects on build plate by comparing against calibration reference.

    Three-stage detection pipeline:
    1. Perceptual hash pre-filter (reject exact matches quickly)
    2. SSIM comparison (measure structural similarity)
    3. Region analysis (identify specific object regions if present)

    Args:
        current_image_path: Path to current plate image
        printer_serial: Printer serial number
        z_height: Current Z-height in mm
        calibration_dir: Directory containing calibration references
        hash_threshold: Hamming distance threshold for hash matching
        tolerance_mm: Z-height tolerance for finding calibration reference
        false_positive_rate_24h: Recent FP rate for adaptive thresholding
        save_visualization: Whether to save debug visualization
        visualization_path: Path to save visualization (if enabled)

    Returns:
        Dictionary with keys:
        - 'is_clean': bool (True if plate is clean)
        - 'detection_method': str ('hash_match', 'ssim_clean', 'ssim_object')
        - 'ssim_score': float (similarity score, 0.0-1.0)
        - 'threshold_used': float (adaptive threshold applied)
        - 'regions_detected': List[Dict] (detected object regions)
        - 'confidence': float (0.0-1.0, confidence in detection)
        - 'reference_z': float (Z-height of reference used)
        - 'processing_time_ms': float (total processing time)

    Raises:
        FileNotFoundError: If image or calibration files not found
        ValueError: If invalid parameters provided

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
        ...     print(f"✗ Objects detected: {len(result['regions_detected'])} regions")
    """
    start_time = time.time()

    try:
        # Validate inputs
        if not Path(current_image_path).exists():
            raise FileNotFoundError(f"Current image not found: {current_image_path}")

        if z_height < 0 or z_height > 300:
            raise ValueError(f"Invalid Z-height: {z_height}mm")

        logger.info(
            f"Starting detection for printer {printer_serial} at Z={z_height:.1f}mm"
        )

        # Step 1: Load and preprocess current image
        logger.debug("Loading and preprocessing current image...")
        current_image = load_and_preprocess(current_image_path)

        # Step 2: Find appropriate calibration reference
        logger.debug("Finding calibration reference...")
        reference_path = find_calibration_reference(
            printer_serial=printer_serial,
            z_height=z_height,
            calibration_dir=calibration_dir,
            tolerance_mm=tolerance_mm
        )

        if reference_path is None:
            logger.error("No calibration reference found")
            return _create_error_result(
                "no_calibration_reference",
                z_height=z_height,
                processing_time_ms=(time.time() - start_time) * 1000
            )

        # Parse reference Z-height from filename
        reference_z = _parse_z_height_from_filename(reference_path)

        # Load and preprocess reference image
        logger.debug(f"Loading reference: {Path(reference_path).name}")
        reference_image = load_and_preprocess(reference_path)

        # Step 3: Calculate perceptual hashes (fast pre-filter)
        logger.debug("Calculating perceptual hashes...")
        current_hash = calculate_perceptual_hash(current_image)
        reference_hash = calculate_perceptual_hash(reference_image)

        hash_distance = compare_hashes(current_hash, reference_hash)
        logger.info(f"Hash distance: {hash_distance}")

        # If hashes match closely, images are nearly identical - plate is clean
        if is_hash_match(current_hash, reference_hash, threshold=hash_threshold):
            logger.info("Hash match - plate is clean (fast path)")
            processing_time = (time.time() - start_time) * 1000

            return {
                'is_clean': True,
                'detection_method': 'hash_match',
                'ssim_score': 1.0,  # Estimate
                'threshold_used': 0.0,  # Not used
                'regions_detected': [],
                'confidence': 0.99,  # Very high confidence for hash matches
                'reference_z': reference_z,
                'hash_distance': hash_distance,
                'processing_time_ms': processing_time
            }

        # Step 4: Calculate adaptive threshold
        logger.debug("Calculating adaptive threshold...")
        threshold = get_adaptive_threshold(
            z_height=z_height,
            printer_id=printer_serial,
            false_positive_rate_24h=false_positive_rate_24h
        )

        # Step 5: SSIM comparison (detailed structural analysis)
        logger.debug("Performing SSIM comparison...")
        ssim_score, diff_map = compare_images_ssim(
            reference=reference_image,
            current=current_image,
            return_difference_map=True
        )

        logger.info(
            f"SSIM score: {ssim_score:.4f}, threshold: {threshold:.4f}"
        )

        # Step 6: Analyze difference regions (if SSIM indicates differences)
        regions = []

        if ssim_score < threshold:
            logger.debug("Analyzing difference regions...")
            regions = analyze_difference_regions(
                difference_map=diff_map,
                min_area=100,
                max_aspect_ratio=5.0,
                difference_threshold=0.5
            )

            # Filter border regions (often artifacts)
            regions = filter_regions_by_position(
                regions=regions,
                image_shape=diff_map.shape,
                exclude_border_percent=0.05
            )

            # Merge overlapping regions
            regions = merge_overlapping_regions(
                regions=regions,
                overlap_threshold=0.3
            )

            logger.info(f"Detected {len(regions)} significant regions")

            # Log region details
            if regions:
                region_summary = get_region_summary(regions)
                logger.info(
                    f"Region summary: count={region_summary['count']}, "
                    f"total_area={region_summary['total_area']}, "
                    f"largest_area={region_summary['largest_area']}"
                )

        # Step 7: Calculate confidence
        confidence = get_confidence_score(
            ssim_score=ssim_score,
            threshold=threshold,
            regions=regions
        )

        # Step 8: Make final decision
        is_clean = ssim_score >= threshold

        if is_clean:
            detection_method = 'ssim_clean'
            logger.info("✓ Plate is clean")
        else:
            detection_method = 'ssim_object'
            logger.info("✗ Object detected")

        processing_time = (time.time() - start_time) * 1000

        # Step 9: Save visualization if requested
        if save_visualization and visualization_path:
            try:
                from .visualization import create_comparison_visualization
                create_comparison_visualization(
                    reference=reference_image,
                    current=current_image,
                    difference_map=diff_map,
                    regions=regions,
                    ssim_score=ssim_score,
                    output_path=visualization_path
                )
                logger.info(f"Saved visualization to {visualization_path}")
            except Exception as e:
                logger.warning(f"Failed to save visualization: {e}")

        # Step 10: Return comprehensive result
        result = {
            'is_clean': is_clean,
            'detection_method': detection_method,
            'ssim_score': float(ssim_score),
            'threshold_used': float(threshold),
            'regions_detected': regions,
            'confidence': float(confidence),
            'reference_z': float(reference_z),
            'hash_distance': int(hash_distance),
            'processing_time_ms': float(processing_time)
        }

        logger.info(
            f"Detection complete: is_clean={is_clean}, "
            f"ssim={ssim_score:.4f}, confidence={confidence:.2f}, "
            f"time={processing_time:.1f}ms"
        )

        return result

    except Exception as e:
        logger.error(f"Error during detection: {e}", exc_info=True)
        processing_time = (time.time() - start_time) * 1000
        return _create_error_result(
            f"detection_error: {str(e)}",
            z_height=z_height,
            processing_time_ms=processing_time
        )


def detect_from_checkpoints(
    checkpoint_images: list,
    printer_serial: str,
    calibration_dir: str,
    **detection_kwargs
) -> list:
    """
    Run detection on multiple checkpoint images.

    Args:
        checkpoint_images: List of dicts with 'path' and 'z_height' keys
        printer_serial: Printer serial number
        calibration_dir: Calibration directory
        **detection_kwargs: Additional arguments for detect_plate_objects()

    Returns:
        List of detection results

    Example:
        >>> checkpoints = [
        ...     {'path': '/path/to/checkpoint_0pct_Z0mm.png', 'z_height': 0.0},
        ...     {'path': '/path/to/checkpoint_100pct_Z138mm.png', 'z_height': 138.0}
        ... ]
        >>> results = detect_from_checkpoints(checkpoints, "00M09A3B1000685", "/data/calibration")
        >>> all(r['is_clean'] for r in results)
        True  # All checkpoints are clean
    """
    results = []

    for checkpoint in checkpoint_images:
        try:
            result = detect_plate_objects(
                current_image_path=checkpoint['path'],
                printer_serial=printer_serial,
                z_height=checkpoint['z_height'],
                calibration_dir=calibration_dir,
                **detection_kwargs
            )
            results.append(result)

        except Exception as e:
            logger.error(f"Failed to detect on checkpoint {checkpoint['path']}: {e}")
            results.append(_create_error_result(
                f"checkpoint_error: {str(e)}",
                z_height=checkpoint['z_height'],
                processing_time_ms=0
            ))

    return results


def _parse_z_height_from_filename(filename: str) -> float:
    """
    Parse Z-height from calibration filename.

    Args:
        filename: Calibration filename (e.g., "Z010mm_20250131_143100.png")

    Returns:
        Z-height in millimeters

    Example:
        >>> _parse_z_height_from_filename("Z010mm_20250131_143100.png")
        10.0
    """
    try:
        stem = Path(filename).stem
        z_str = stem.split('_')[0]  # "Z010mm"
        z_value = float(z_str[1:-2])  # Remove "Z" and "mm"
        return z_value
    except Exception as e:
        logger.warning(f"Failed to parse Z-height from {filename}: {e}")
        return 0.0


def _create_error_result(
    error_message: str,
    z_height: float,
    processing_time_ms: float
) -> Dict[str, Any]:
    """
    Create an error result dictionary.

    Safety principle: On error, assume plate is NOT clean (conservative approach).

    Args:
        error_message: Description of the error
        z_height: Z-height at time of error
        processing_time_ms: Processing time before error

    Returns:
        Error result dictionary
    """
    return {
        'is_clean': False,  # Conservative: assume not clean on error
        'detection_method': 'error',
        'ssim_score': 0.0,
        'threshold_used': 0.95,  # Conservative threshold
        'regions_detected': [],
        'confidence': 0.0,
        'reference_z': z_height,
        'hash_distance': -1,
        'processing_time_ms': processing_time_ms,
        'error': error_message
    }


def batch_detect(
    image_paths: list,
    printer_serial: str,
    z_heights: list,
    calibration_dir: str,
    **detection_kwargs
) -> list:
    """
    Perform detection on multiple images in batch.

    Args:
        image_paths: List of image paths
        printer_serial: Printer serial number
        z_heights: List of Z-heights corresponding to images
        calibration_dir: Calibration directory
        **detection_kwargs: Additional arguments for detect_plate_objects()

    Returns:
        List of detection results

    Example:
        >>> paths = ["img1.png", "img2.png", "img3.png"]
        >>> z_heights = [10.0, 50.0, 100.0]
        >>> results = batch_detect(paths, "00M09A3B1000685", z_heights, "/data/calibration")
    """
    if len(image_paths) != len(z_heights):
        raise ValueError("Number of images must match number of Z-heights")

    results = []

    for img_path, z_height in zip(image_paths, z_heights):
        result = detect_plate_objects(
            current_image_path=img_path,
            printer_serial=printer_serial,
            z_height=z_height,
            calibration_dir=calibration_dir,
            **detection_kwargs
        )
        results.append(result)

    return results
