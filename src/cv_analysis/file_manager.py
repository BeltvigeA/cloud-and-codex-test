"""
File Structure Management Module

Handles calibration and checkpoint image storage, organization, and retrieval.

Directory structure:
    /print_farm_data/
    ├── calibration/
    │   └── {printer_serial}/
    │       ├── Z000mm_20250131_143022.png
    │       ├── Z005mm_20250131_143045.png
    │       ├── ...
    │       └── metadata.json
    │
    ├── checkpoints/
    │   └── {job_uuid}/
    │       ├── checkpoint_0pct_Z0mm.png
    │       ├── checkpoint_33pct_Z45mm.png
    │       ├── checkpoint_66pct_Z91mm.png
    │       └── checkpoint_100pct_Z138mm.png
    │
    └── cv_analysis/
        └── {job_uuid}/
            └── breaking_attempt_{n}/
                ├── Z0mm_comparison.png
                ├── detection_results.json
                └── ...
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def find_calibration_reference(
    printer_serial: str,
    z_height: float,
    calibration_dir: str,
    tolerance_mm: float = 3.0
) -> Optional[str]:
    """
    Find calibration reference image closest to target Z-height.

    Calibration images are captured at 5mm increments (0, 5, 10, ..., 235mm).
    This function finds the nearest reference within tolerance.

    Args:
        printer_serial: Printer serial number
        z_height: Target Z-height in millimeters
        calibration_dir: Root calibration directory
        tolerance_mm: Maximum acceptable distance from target (default 3mm)

    Returns:
        Path to calibration reference image, or None if not found

    Raises:
        FileNotFoundError: If calibration directory doesn't exist

    Example:
        >>> ref_path = find_calibration_reference(
        ...     printer_serial="00M09A3B1000685",
        ...     z_height=12.5,
        ...     calibration_dir="/print_farm_data/calibration"
        ... )
        >>> ref_path
        '/print_farm_data/calibration/00M09A3B1000685/Z010mm_20250131_143100.png'
    """
    try:
        calibration_path = Path(calibration_dir) / printer_serial

        if not calibration_path.exists():
            raise FileNotFoundError(
                f"Calibration directory not found: {calibration_path}"
            )

        # Find all calibration images
        calibration_images = list(calibration_path.glob("Z*mm_*.png"))

        if not calibration_images:
            logger.error(f"No calibration images found in {calibration_path}")
            return None

        # Parse Z-heights from filenames and find closest
        best_match = None
        best_distance = float('inf')

        for img_path in calibration_images:
            # Extract Z-height from filename (e.g., "Z010mm_20250131_143100.png")
            try:
                z_str = img_path.stem.split('_')[0]  # "Z010mm"
                z_value = float(z_str[1:-2])  # Remove "Z" and "mm", convert to float

                distance = abs(z_value - z_height)

                if distance < best_distance:
                    best_distance = distance
                    best_match = img_path

            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse Z-height from {img_path.name}: {e}")
                continue

        if best_match and best_distance <= tolerance_mm:
            logger.info(
                f"Found calibration reference: {best_match.name} "
                f"(target: {z_height:.1f}mm, actual: {z_height-best_distance:.1f}mm, "
                f"distance: {best_distance:.1f}mm)"
            )
            return str(best_match)
        else:
            logger.warning(
                f"No calibration reference within {tolerance_mm}mm of Z={z_height:.1f}mm "
                f"(closest: {best_distance:.1f}mm)"
            )
            return None

    except Exception as e:
        logger.error(f"Error finding calibration reference: {e}")
        raise


def load_calibration_metadata(
    printer_serial: str,
    calibration_dir: str
) -> Dict[str, Any]:
    """
    Load calibration session metadata.

    Args:
        printer_serial: Printer serial number
        calibration_dir: Root calibration directory

    Returns:
        Metadata dictionary with keys:
        - 'calibration_date': ISO timestamp
        - 'z_heights': List of calibrated Z-heights
        - 'image_count': Number of calibration images
        - 'notes': Optional calibration notes

    Example:
        >>> metadata = load_calibration_metadata("00M09A3B1000685", "/print_farm_data/calibration")
        >>> metadata['image_count']
        47
        >>> metadata['calibration_date']
        '2025-01-31T14:30:22'
    """
    try:
        metadata_path = Path(calibration_dir) / printer_serial / "metadata.json"

        if not metadata_path.exists():
            logger.warning(f"Calibration metadata not found: {metadata_path}")
            return {
                'calibration_date': None,
                'z_heights': [],
                'image_count': 0,
                'notes': 'Metadata file not found'
            }

        with open(metadata_path, 'r') as f:
            metadata = json.load(f)

        logger.debug(f"Loaded calibration metadata for {printer_serial}")
        return metadata

    except Exception as e:
        logger.error(f"Error loading calibration metadata: {e}")
        return {
            'calibration_date': None,
            'z_heights': [],
            'image_count': 0,
            'notes': f'Error loading metadata: {e}'
        }


def save_calibration_metadata(
    printer_serial: str,
    calibration_dir: str,
    z_heights: List[float],
    notes: str = ""
) -> None:
    """
    Save calibration session metadata.

    Args:
        printer_serial: Printer serial number
        calibration_dir: Root calibration directory
        z_heights: List of calibrated Z-heights
        notes: Optional calibration notes

    Example:
        >>> z_heights = list(range(0, 240, 5))  # 0, 5, 10, ..., 235
        >>> save_calibration_metadata(
        ...     "00M09A3B1000685",
        ...     "/print_farm_data/calibration",
        ...     z_heights,
        ...     notes="Initial calibration after nozzle replacement"
        ... )
    """
    try:
        calibration_path = Path(calibration_dir) / printer_serial
        calibration_path.mkdir(parents=True, exist_ok=True)

        metadata = {
            'calibration_date': datetime.now().isoformat(),
            'z_heights': z_heights,
            'image_count': len(z_heights),
            'printer_serial': printer_serial,
            'notes': notes
        }

        metadata_path = calibration_path / "metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Saved calibration metadata to {metadata_path}")

    except Exception as e:
        logger.error(f"Error saving calibration metadata: {e}")
        raise


def save_detection_result(
    result: Dict[str, Any],
    job_id: str,
    attempt_number: int,
    z_height: float,
    output_dir: str
) -> str:
    """
    Save detection result with metadata JSON.

    Args:
        result: Detection result dictionary from detect_plate_objects()
        job_id: Job UUID
        attempt_number: Breaking attempt number (1, 2, 3, ...)
        z_height: Z-height at detection
        output_dir: Root output directory for CV analysis

    Returns:
        Path to saved JSON file

    Example:
        >>> result = detect_plate_objects(...)
        >>> json_path = save_detection_result(
        ...     result=result,
        ...     job_id="job-123-456",
        ...     attempt_number=1,
        ...     z_height=138.0,
        ...     output_dir="/print_farm_data/cv_analysis"
        ... )
        >>> json_path
        '/print_farm_data/cv_analysis/job-123-456/breaking_attempt_1/Z138mm_detection.json'
    """
    try:
        # Create output directory structure
        attempt_dir = Path(output_dir) / job_id / f"breaking_attempt_{attempt_number}"
        attempt_dir.mkdir(parents=True, exist_ok=True)

        # Add metadata to result
        result_with_metadata = {
            'timestamp': datetime.now().isoformat(),
            'job_id': job_id,
            'attempt_number': attempt_number,
            'z_height': z_height,
            **result
        }

        # Save to JSON file
        json_filename = f"Z{int(z_height):03d}mm_detection.json"
        json_path = attempt_dir / json_filename

        with open(json_path, 'w') as f:
            json.dump(result_with_metadata, f, indent=2)

        logger.info(f"Saved detection result to {json_path}")
        return str(json_path)

    except Exception as e:
        logger.error(f"Error saving detection result: {e}")
        raise


def load_detection_result(json_path: str) -> Dict[str, Any]:
    """
    Load a previously saved detection result.

    Args:
        json_path: Path to detection result JSON file

    Returns:
        Detection result dictionary

    Example:
        >>> result = load_detection_result(
        ...     "/print_farm_data/cv_analysis/job-123-456/breaking_attempt_1/Z138mm_detection.json"
        ... )
        >>> result['is_clean']
        False
    """
    try:
        with open(json_path, 'r') as f:
            result = json.load(f)

        logger.debug(f"Loaded detection result from {json_path}")
        return result

    except Exception as e:
        logger.error(f"Error loading detection result: {e}")
        raise


def get_checkpoint_images(
    job_id: str,
    checkpoints_dir: str
) -> List[Dict[str, Any]]:
    """
    Get all checkpoint images for a job.

    Args:
        job_id: Job UUID
        checkpoints_dir: Root checkpoints directory

    Returns:
        List of checkpoint dictionaries with keys:
        - 'path': Path to checkpoint image
        - 'percent': Print completion percentage
        - 'z_height': Z-height at checkpoint

    Example:
        >>> checkpoints = get_checkpoint_images("job-123-456", "/print_farm_data/checkpoints")
        >>> len(checkpoints)
        4
        >>> checkpoints[0]
        {
            'path': '/print_farm_data/checkpoints/job-123-456/checkpoint_0pct_Z0mm.png',
            'percent': 0,
            'z_height': 0.0
        }
    """
    try:
        checkpoints_path = Path(checkpoints_dir) / job_id

        if not checkpoints_path.exists():
            logger.warning(f"Checkpoints directory not found: {checkpoints_path}")
            return []

        checkpoint_files = list(checkpoints_path.glob("checkpoint_*pct_Z*mm.png"))
        checkpoints = []

        for checkpoint_file in checkpoint_files:
            try:
                # Parse filename: checkpoint_33pct_Z45mm.png
                parts = checkpoint_file.stem.split('_')
                percent = int(parts[1].replace('pct', ''))
                z_height = float(parts[2].replace('Z', '').replace('mm', ''))

                checkpoints.append({
                    'path': str(checkpoint_file),
                    'percent': percent,
                    'z_height': z_height
                })

            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse checkpoint filename {checkpoint_file.name}: {e}")
                continue

        # Sort by percentage
        checkpoints.sort(key=lambda c: c['percent'])

        logger.info(f"Found {len(checkpoints)} checkpoint images for job {job_id}")
        return checkpoints

    except Exception as e:
        logger.error(f"Error getting checkpoint images: {e}")
        return []


def ensure_directory_exists(directory: str) -> Path:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        directory: Directory path

    Returns:
        Path object for the directory

    Example:
        >>> path = ensure_directory_exists("/print_farm_data/cv_analysis/new_job")
        >>> path.exists()
        True
    """
    try:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {path}")
        return path

    except Exception as e:
        logger.error(f"Error creating directory {directory}: {e}")
        raise


def get_detection_history(
    job_id: str,
    output_dir: str
) -> List[Dict[str, Any]]:
    """
    Get all detection results for a job across all attempts.

    Args:
        job_id: Job UUID
        output_dir: Root output directory for CV analysis

    Returns:
        List of detection result dictionaries, sorted by attempt and Z-height

    Example:
        >>> history = get_detection_history("job-123-456", "/print_farm_data/cv_analysis")
        >>> len(history)
        3  # 3 detection attempts
        >>> history[0]['attempt_number']
        1
    """
    try:
        job_dir = Path(output_dir) / job_id

        if not job_dir.exists():
            logger.warning(f"Job analysis directory not found: {job_dir}")
            return []

        # Find all detection JSON files
        json_files = list(job_dir.glob("breaking_attempt_*/Z*mm_detection.json"))
        history = []

        for json_file in json_files:
            try:
                result = load_detection_result(str(json_file))
                history.append(result)
            except Exception as e:
                logger.warning(f"Failed to load {json_file}: {e}")
                continue

        # Sort by attempt number, then Z-height
        history.sort(key=lambda r: (r.get('attempt_number', 0), r.get('z_height', 0)))

        logger.info(f"Loaded {len(history)} detection results for job {job_id}")
        return history

    except Exception as e:
        logger.error(f"Error getting detection history: {e}")
        return []


def cleanup_old_analysis(
    output_dir: str,
    days_to_keep: int = 30
) -> int:
    """
    Clean up old CV analysis files to save disk space.

    Args:
        output_dir: Root output directory for CV analysis
        days_to_keep: Number of days of analysis to retain

    Returns:
        Number of directories removed

    Example:
        >>> removed = cleanup_old_analysis("/print_farm_data/cv_analysis", days_to_keep=30)
        >>> removed
        15  # Removed 15 old job analysis directories
    """
    try:
        output_path = Path(output_dir)
        if not output_path.exists():
            logger.warning(f"Output directory not found: {output_path}")
            return 0

        cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 3600)
        removed_count = 0

        for job_dir in output_path.iterdir():
            if not job_dir.is_dir():
                continue

            # Check modification time
            if job_dir.stat().st_mtime < cutoff_time:
                logger.info(f"Removing old analysis directory: {job_dir}")
                import shutil
                shutil.rmtree(job_dir)
                removed_count += 1

        logger.info(f"Cleaned up {removed_count} old analysis directories")
        return removed_count

    except Exception as e:
        logger.error(f"Error cleaning up old analysis: {e}")
        return 0
