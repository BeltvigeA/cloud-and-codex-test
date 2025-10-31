"""
File Management Module for CV Analysis

This module handles all file operations for the computer vision system:
- Loading calibration reference images
- Saving detection results
- Managing checkpoint images
- Organizing analysis output

Directory structure:
/print_farm_data/
├── calibration/{printer_serial}/
│   ├── Z000mm_20250131_143022.png
│   ├── Z005mm_20250131_143045.png
│   ├── ...
│   └── metadata.json
├── checkpoints/{job_uuid}/
│   ├── checkpoint_0pct_Z0mm.png
│   ├── ...
│   └── checkpoint_100pct_Z138mm.png
└── cv_analysis/{job_uuid}/
    └── breaking_attempt_{n}/
        ├── Z0mm_comparison.png
        ├── detection_results.json
        └── ...
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import shutil

logger = logging.getLogger(__name__)


def find_calibration_reference(
    printer_serial: str,
    z_height: float,
    calibration_dir: str,
    tolerance_mm: float = 3.0
) -> Optional[str]:
    """
    Find calibration reference image closest to target Z-height.

    Calibration images are named: Z{height}mm_{timestamp}.png
    where height is in 5mm increments (000, 005, 010, ..., 235)

    Args:
        printer_serial: Printer serial number
        z_height: Target Z-height in millimeters
        calibration_dir: Root calibration directory
        tolerance_mm: Maximum allowed distance from target (default 3mm)

    Returns:
        Path to calibration image or None if not found

    Raises:
        FileNotFoundError: If calibration directory doesn't exist

    Example:
        >>> ref_path = find_calibration_reference("00M09A3B1000685", 47.3, "/data/calibration")
        >>> # Returns: "/data/calibration/00M09A3B1000685/Z045mm_20250131_143022.png"
    """
    try:
        cal_path = Path(calibration_dir) / printer_serial

        if not cal_path.exists():
            raise FileNotFoundError(
                f"Calibration directory not found: {cal_path}"
            )

        # Find all calibration images
        cal_images = list(cal_path.glob("Z*mm_*.png"))

        if not cal_images:
            logger.error(f"No calibration images found in {cal_path}")
            return None

        # Parse Z-heights from filenames
        best_match = None
        best_distance = float('inf')

        for img_path in cal_images:
            try:
                # Extract Z-height from filename (e.g., "Z045mm_...")
                z_str = img_path.stem.split('_')[0][1:-2]  # Remove 'Z' and 'mm'
                img_z_height = float(z_str)

                distance = abs(img_z_height - z_height)

                if distance < best_distance:
                    best_distance = distance
                    best_match = img_path

            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse Z-height from {img_path.name}: {e}")
                continue

        # Check if match is within tolerance
        if best_match and best_distance <= tolerance_mm:
            logger.debug(
                f"Found calibration reference: {best_match.name} "
                f"(distance={best_distance:.1f}mm)"
            )
            return str(best_match)
        elif best_match:
            logger.warning(
                f"Best calibration match {best_match.name} is {best_distance:.1f}mm "
                f"away (tolerance={tolerance_mm}mm)"
            )
            return str(best_match)  # Return anyway but log warning
        else:
            logger.error(f"No calibration reference found for Z={z_height}mm")
            return None

    except Exception as e:
        logger.error(f"Error finding calibration reference: {str(e)}")
        return None


def load_calibration_metadata(
    printer_serial: str,
    calibration_dir: str
) -> Dict[str, Any]:
    """
    Load calibration session metadata.

    Metadata file contains information about when calibration was performed,
    environmental conditions, etc.

    Args:
        printer_serial: Printer serial number
        calibration_dir: Root calibration directory

    Returns:
        Dictionary with metadata or empty dict if not found

    Example:
        >>> metadata = load_calibration_metadata("00M09A3B1000685", "/data/cal")
        >>> print(f"Calibrated on: {metadata.get('calibration_date')}")
    """
    try:
        metadata_file = Path(calibration_dir) / printer_serial / "metadata.json"

        if not metadata_file.exists():
            logger.debug(f"No metadata file found: {metadata_file}")
            return {}

        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        logger.debug(f"Loaded calibration metadata for {printer_serial}")
        return metadata

    except Exception as e:
        logger.warning(f"Error loading calibration metadata: {str(e)}")
        return {}


def save_detection_result(
    result: Dict[str, Any],
    job_id: str,
    attempt_number: int,
    z_height: float,
    output_dir: str,
    include_timestamp: bool = True
) -> str:
    """
    Save detection result with metadata JSON.

    Args:
        result: Detection result dictionary
        job_id: Print job UUID
        attempt_number: Breaking attempt number (1, 2, 3, ...)
        z_height: Z-height of detection
        output_dir: Root analysis output directory
        include_timestamp: Add timestamp to filename

    Returns:
        Path to saved JSON file

    Example:
        >>> path = save_detection_result(
        ...     result={'is_clean': False, 'ssim_score': 0.82},
        ...     job_id='job-uuid-123',
        ...     attempt_number=1,
        ...     z_height=138.0,
        ...     output_dir='/data/cv_analysis'
        ... )
    """
    try:
        # Create output directory structure
        attempt_dir = Path(output_dir) / job_id / f"breaking_attempt_{attempt_number}"
        attempt_dir.mkdir(parents=True, exist_ok=True)

        # Create filename
        timestamp_str = f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}" if include_timestamp else ""
        filename = f"Z{int(z_height):03d}mm_detection{timestamp_str}.json"
        output_file = attempt_dir / filename

        # Add metadata
        output_data = {
            'detection_result': result,
            'job_id': job_id,
            'attempt_number': attempt_number,
            'z_height': z_height,
            'timestamp': datetime.now().isoformat(),
            'version': '1.0'
        }

        # Save JSON
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)

        logger.info(f"Saved detection result: {output_file}")
        return str(output_file)

    except Exception as e:
        logger.error(f"Error saving detection result: {str(e)}")
        raise


def load_detection_result(result_path: str) -> Dict[str, Any]:
    """
    Load a previously saved detection result.

    Args:
        result_path: Path to detection result JSON file

    Returns:
        Detection result dictionary

    Example:
        >>> result = load_detection_result('/data/cv_analysis/job123/Z050mm_detection.json')
        >>> print(result['detection_result']['is_clean'])
    """
    try:
        with open(result_path, 'r') as f:
            data = json.load(f)

        return data

    except Exception as e:
        logger.error(f"Error loading detection result from {result_path}: {str(e)}")
        raise


def get_checkpoint_image_path(
    job_id: str,
    checkpoint_percentage: int,
    z_height: float,
    checkpoints_dir: str
) -> str:
    """
    Construct path to checkpoint image.

    Args:
        job_id: Print job UUID
        checkpoint_percentage: Progress percentage (0, 33, 66, 100)
        z_height: Z-height at checkpoint
        checkpoints_dir: Root checkpoints directory

    Returns:
        Path to checkpoint image

    Example:
        >>> path = get_checkpoint_image_path(
        ...     'job-123', 100, 138.0, '/data/checkpoints'
        ... )
        >>> # Returns: '/data/checkpoints/job-123/checkpoint_100pct_Z138mm.png'
    """
    filename = f"checkpoint_{checkpoint_percentage}pct_Z{int(z_height)}mm.png"
    return str(Path(checkpoints_dir) / job_id / filename)


def save_calibration_image(
    image_path: str,
    printer_serial: str,
    z_height: float,
    calibration_dir: str,
    copy_file: bool = True
) -> str:
    """
    Save/copy calibration image to standard location.

    Args:
        image_path: Source image path
        printer_serial: Printer serial number
        z_height: Z-height of calibration image
        calibration_dir: Root calibration directory
        copy_file: If True, copy file; if False, move file

    Returns:
        Path to saved calibration image

    Example:
        >>> saved_path = save_calibration_image(
        ...     '/tmp/plate_clean.png',
        ...     '00M09A3B1000685',
        ...     45.0,
        ...     '/data/calibration'
        ... )
    """
    try:
        # Create printer calibration directory
        printer_dir = Path(calibration_dir) / printer_serial
        printer_dir.mkdir(parents=True, exist_ok=True)

        # Create standardized filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"Z{int(z_height):03d}mm_{timestamp}.png"
        dest_path = printer_dir / filename

        # Copy or move file
        source = Path(image_path)
        if copy_file:
            shutil.copy2(source, dest_path)
        else:
            shutil.move(str(source), dest_path)

        logger.info(f"Saved calibration image: {dest_path}")
        return str(dest_path)

    except Exception as e:
        logger.error(f"Error saving calibration image: {str(e)}")
        raise


def list_calibration_images(
    printer_serial: str,
    calibration_dir: str
) -> List[Tuple[float, str]]:
    """
    List all calibration images for a printer.

    Args:
        printer_serial: Printer serial number
        calibration_dir: Root calibration directory

    Returns:
        List of (z_height, image_path) tuples, sorted by Z-height

    Example:
        >>> images = list_calibration_images('00M09A3B1000685', '/data/cal')
        >>> for z, path in images:
        ...     print(f"Z={z}mm: {path}")
    """
    try:
        printer_dir = Path(calibration_dir) / printer_serial

        if not printer_dir.exists():
            return []

        images = []
        for img_path in printer_dir.glob("Z*mm_*.png"):
            try:
                # Parse Z-height from filename
                z_str = img_path.stem.split('_')[0][1:-2]
                z_height = float(z_str)
                images.append((z_height, str(img_path)))
            except (ValueError, IndexError):
                continue

        # Sort by Z-height
        images.sort(key=lambda x: x[0])

        return images

    except Exception as e:
        logger.error(f"Error listing calibration images: {str(e)}")
        return []


def get_analysis_summary_path(
    job_id: str,
    attempt_number: int,
    output_dir: str
) -> str:
    """
    Get path to analysis summary file for a breaking attempt.

    Args:
        job_id: Print job UUID
        attempt_number: Breaking attempt number
        output_dir: Root analysis directory

    Returns:
        Path to summary JSON file

    Example:
        >>> summary_path = get_analysis_summary_path('job-123', 1, '/data/cv_analysis')
    """
    attempt_dir = Path(output_dir) / job_id / f"breaking_attempt_{attempt_number}"
    return str(attempt_dir / "analysis_summary.json")


def save_analysis_summary(
    job_id: str,
    attempt_number: int,
    detections: List[Dict[str, Any]],
    output_dir: str
) -> str:
    """
    Save summary of all detections for a breaking attempt.

    Args:
        job_id: Print job UUID
        attempt_number: Breaking attempt number
        detections: List of detection result dictionaries
        output_dir: Root analysis directory

    Returns:
        Path to saved summary file

    Example:
        >>> summary_path = save_analysis_summary(
        ...     'job-123', 1,
        ...     [{'z_height': 0, 'is_clean': False}, ...],
        ...     '/data/cv_analysis'
        ... )
    """
    try:
        summary_path = get_analysis_summary_path(job_id, attempt_number, output_dir)
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)

        summary = {
            'job_id': job_id,
            'attempt_number': attempt_number,
            'total_detections': len(detections),
            'detections': detections,
            'timestamp': datetime.now().isoformat(),
            'overall_result': 'clean' if all(d.get('is_clean', False) for d in detections) else 'objects_detected'
        }

        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Saved analysis summary: {summary_path}")
        return summary_path

    except Exception as e:
        logger.error(f"Error saving analysis summary: {str(e)}")
        raise


def ensure_directory_structure(base_dir: str = "/print_farm_data") -> None:
    """
    Ensure all required directories exist.

    Creates the complete directory structure needed for CV analysis.

    Args:
        base_dir: Base data directory

    Example:
        >>> ensure_directory_structure('/print_farm_data')
    """
    try:
        base = Path(base_dir)

        # Create main directories
        directories = [
            base / "calibration",
            base / "checkpoints",
            base / "cv_analysis",
            base / "cv_analysis" / "fp_history"
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {directory}")

    except Exception as e:
        logger.error(f"Error creating directory structure: {str(e)}")
        raise


def cleanup_old_results(
    output_dir: str,
    days_to_keep: int = 30
) -> int:
    """
    Clean up old detection results to save disk space.

    Args:
        output_dir: Root analysis directory
        days_to_keep: Number of days to keep results

    Returns:
        Number of files deleted

    Example:
        >>> deleted = cleanup_old_results('/data/cv_analysis', days_to_keep=30)
        >>> print(f"Cleaned up {deleted} old files")
    """
    try:
        from datetime import timedelta
        import time

        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
        deleted_count = 0

        analysis_dir = Path(output_dir)

        if not analysis_dir.exists():
            return 0

        for result_file in analysis_dir.rglob("*.json"):
            if result_file.stat().st_mtime < cutoff_time:
                result_file.unlink()
                deleted_count += 1

        logger.info(f"Cleaned up {deleted_count} old result files")
        return deleted_count

    except Exception as e:
        logger.error(f"Error cleaning up old results: {str(e)}")
        return 0
