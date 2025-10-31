"""
Adaptive Threshold Module for Z-Height-Based Detection

This module implements adaptive SSIM thresholding that adjusts based on:
1. Z-height (lower heights require stricter thresholds)
2. Printer-specific false positive rate history
3. Environmental factors (optional future enhancement)

The threshold determines what SSIM score is considered "clean enough".

Key Features:
- Z-height-based threshold zones
- Historical false positive rate adjustment
- Per-printer calibration
- Safety bounds to prevent over/under-sensitivity
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
import json

logger = logging.getLogger(__name__)


# Default threshold configuration
DEFAULT_CONFIG = {
    'base_threshold': 0.90,
    'z_height_zones': [
        {'z_max': 5.0, 'threshold': 0.95},   # Very conservative for low heights
        {'z_max': 20.0, 'threshold': 0.92},  # Conservative for medium heights
        {'z_max': 1000.0, 'threshold': 0.90} # Standard for high heights
    ],
    'fp_rate_adjustment': 0.02,
    'min_threshold': 0.85,
    'max_threshold': 0.97
}


def get_adaptive_threshold(
    z_height: float,
    printer_id: str,
    false_positive_rate_24h: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None
) -> float:
    """
    Calculate adaptive SSIM threshold based on Z-height and printer history.

    The threshold represents the minimum SSIM score required to consider
    the plate "clean". Higher thresholds are more conservative (more likely
    to flag potential objects).

    Threshold Logic:
    - Z < 5mm: Very strict (0.95) - critical first layer area
    - Z = 5-20mm: Strict (0.92) - common print height range
    - Z > 20mm: Standard (0.90) - taller prints
    - Adjust based on printer's false positive rate

    Args:
        z_height: Current Z-height in millimeters
        printer_id: Unique printer identifier (serial number)
        false_positive_rate_24h: False positive rate in last 24h (0.0-1.0)
        config: Optional configuration dict (uses DEFAULT_CONFIG if None)

    Returns:
        SSIM threshold value (0.85-0.97)

    Example:
        >>> # First layer (Z=0mm) - very strict
        >>> threshold = get_adaptive_threshold(0.0, "PRINTER123")
        >>> print(threshold)  # 0.95
        >>> # High print (Z=100mm) - standard
        >>> threshold = get_adaptive_threshold(100.0, "PRINTER123")
        >>> print(threshold)  # 0.90
        >>> # Adjust for high false positive rate
        >>> threshold = get_adaptive_threshold(50.0, "PRINTER123", fp_rate=0.15)
        >>> print(threshold)  # Lower than base (less strict)
    """
    try:
        # Use provided config or default
        cfg = config or DEFAULT_CONFIG

        # Step 1: Get base threshold for Z-height
        base_threshold = _get_z_height_threshold(z_height, cfg['z_height_zones'])

        # Step 2: Adjust for printer-specific false positive rate
        threshold = base_threshold
        if false_positive_rate_24h is not None:
            adjustment = _calculate_fp_adjustment(
                false_positive_rate_24h,
                cfg['fp_rate_adjustment']
            )
            threshold = base_threshold + adjustment

        # Step 3: Apply safety bounds
        threshold = max(cfg['min_threshold'], min(cfg['max_threshold'], threshold))

        logger.debug(
            f"Adaptive threshold: z_height={z_height:.1f}mm, "
            f"printer={printer_id}, base={base_threshold:.3f}, "
            f"fp_rate={false_positive_rate_24h}, final={threshold:.3f}"
        )

        return float(threshold)

    except Exception as e:
        logger.error(f"Error calculating adaptive threshold: {str(e)}")
        # Return conservative default on error
        return 0.92


def _get_z_height_threshold(
    z_height: float,
    zones: List[Dict[str, float]]
) -> float:
    """
    Get base threshold for a given Z-height.

    Args:
        z_height: Z-height in millimeters
        zones: List of zone configurations

    Returns:
        Base threshold for this Z-height
    """
    for zone in zones:
        if z_height < zone['z_max']:
            return zone['threshold']

    # If beyond all zones, use last zone's threshold
    return zones[-1]['threshold']


def _calculate_fp_adjustment(
    fp_rate: float,
    max_adjustment: float
) -> float:
    """
    Calculate threshold adjustment based on false positive rate.

    If FP rate is high, we should lower the threshold (be less strict)
    to reduce false positives. If FP rate is low, we can increase
    threshold (be more strict) to catch more potential objects.

    Target FP rate: ~5% (0.05)
    - If FP rate > 5%: reduce threshold
    - If FP rate < 5%: increase threshold

    Args:
        fp_rate: False positive rate (0.0-1.0)
        max_adjustment: Maximum adjustment magnitude

    Returns:
        Adjustment value to add to base threshold (-max to +max)
    """
    target_fp_rate = 0.05  # 5% target

    # Calculate deviation from target
    deviation = target_fp_rate - fp_rate

    # Scale to adjustment range
    # If deviation is positive (FP rate < target), increase threshold
    # If deviation is negative (FP rate > target), decrease threshold
    adjustment = deviation * max_adjustment * 2

    # Clamp to max adjustment
    adjustment = max(-max_adjustment, min(max_adjustment, adjustment))

    return adjustment


def load_printer_fp_history(
    printer_id: str,
    history_dir: str = "/print_farm_data/cv_analysis/fp_history"
) -> Optional[float]:
    """
    Load recent false positive rate for a printer.

    Args:
        printer_id: Printer serial number
        history_dir: Directory containing FP history files

    Returns:
        24-hour false positive rate (0.0-1.0) or None if no history

    Example:
        >>> fp_rate = load_printer_fp_history("00M09A3B1000685")
        >>> threshold = get_adaptive_threshold(50.0, "00M09A3B1000685", fp_rate)
    """
    try:
        history_file = Path(history_dir) / f"{printer_id}_fp_history.json"

        if not history_file.exists():
            logger.debug(f"No FP history found for printer {printer_id}")
            return None

        with open(history_file, 'r') as f:
            data = json.load(f)

        fp_rate_24h = data.get('fp_rate_24h')

        if fp_rate_24h is not None:
            logger.debug(f"Loaded FP rate for {printer_id}: {fp_rate_24h:.3f}")
            return float(fp_rate_24h)

        return None

    except Exception as e:
        logger.warning(f"Error loading FP history for {printer_id}: {str(e)}")
        return None


def save_printer_fp_history(
    printer_id: str,
    fp_rate_24h: float,
    history_dir: str = "/print_farm_data/cv_analysis/fp_history"
) -> None:
    """
    Save updated false positive rate for a printer.

    Args:
        printer_id: Printer serial number
        fp_rate_24h: 24-hour false positive rate (0.0-1.0)
        history_dir: Directory for FP history files

    Example:
        >>> # After analyzing detection results
        >>> save_printer_fp_history("00M09A3B1000685", 0.08)
    """
    try:
        history_path = Path(history_dir)
        history_path.mkdir(parents=True, exist_ok=True)

        history_file = history_path / f"{printer_id}_fp_history.json"

        data = {
            'printer_id': printer_id,
            'fp_rate_24h': float(fp_rate_24h),
            'last_updated': None  # Could add timestamp
        }

        with open(history_file, 'w') as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Saved FP history for {printer_id}: {fp_rate_24h:.3f}")

    except Exception as e:
        logger.error(f"Error saving FP history for {printer_id}: {str(e)}")


def get_threshold_with_history(
    z_height: float,
    printer_id: str,
    history_dir: str = "/print_farm_data/cv_analysis/fp_history",
    config: Optional[Dict[str, Any]] = None
) -> Tuple[float, Optional[float]]:
    """
    Get adaptive threshold including historical FP rate lookup.

    This is a convenience function that combines FP history loading
    with threshold calculation.

    Args:
        z_height: Z-height in millimeters
        printer_id: Printer serial number
        history_dir: Directory containing FP history
        config: Optional threshold configuration

    Returns:
        Tuple of (threshold, fp_rate_used)

    Example:
        >>> threshold, fp_rate = get_threshold_with_history(50.0, "PRINTER123")
        >>> print(f"Using threshold {threshold:.3f} (FP rate: {fp_rate})")
    """
    fp_rate = load_printer_fp_history(printer_id, history_dir)
    threshold = get_adaptive_threshold(z_height, printer_id, fp_rate, config)

    return threshold, fp_rate


def calculate_recommended_threshold(
    detection_history: List[Dict[str, Any]],
    target_fp_rate: float = 0.05
) -> float:
    """
    Calculate recommended threshold based on detection history.

    Analyzes past detections to find optimal threshold that achieves
    target false positive rate.

    Args:
        detection_history: List of past detections with keys:
            - 'ssim_score': SSIM score
            - 'was_false_positive': Boolean (True if FP)
        target_fp_rate: Desired false positive rate (default 0.05 = 5%)

    Returns:
        Recommended threshold value

    Example:
        >>> history = [
        ...     {'ssim_score': 0.88, 'was_false_positive': True},
        ...     {'ssim_score': 0.75, 'was_false_positive': False},
        ...     {'ssim_score': 0.95, 'was_false_positive': True},
        ... ]
        >>> recommended = calculate_recommended_threshold(history)
    """
    try:
        if not detection_history:
            return DEFAULT_CONFIG['base_threshold']

        # Extract false positives and their SSIM scores
        fp_scores = [
            d['ssim_score'] for d in detection_history
            if d.get('was_false_positive', False)
        ]

        if not fp_scores:
            # No false positives - can use strict threshold
            return DEFAULT_CONFIG['max_threshold']

        # Sort false positive scores
        fp_scores_sorted = sorted(fp_scores)

        # Find threshold that would exclude target % of false positives
        # We want to be above (target_fp_rate * 100)% of FP scores
        target_index = int(len(fp_scores_sorted) * target_fp_rate)
        if target_index >= len(fp_scores_sorted):
            target_index = len(fp_scores_sorted) - 1

        recommended = fp_scores_sorted[target_index]

        # Apply safety bounds
        recommended = max(
            DEFAULT_CONFIG['min_threshold'],
            min(DEFAULT_CONFIG['max_threshold'], recommended)
        )

        logger.info(
            f"Recommended threshold: {recommended:.3f} "
            f"(from {len(detection_history)} detections, "
            f"{len(fp_scores)} false positives)"
        )

        return float(recommended)

    except Exception as e:
        logger.error(f"Error calculating recommended threshold: {str(e)}")
        return DEFAULT_CONFIG['base_threshold']


def get_threshold_explanation(
    z_height: float,
    threshold: float,
    fp_rate: Optional[float] = None
) -> str:
    """
    Generate human-readable explanation of threshold selection.

    Useful for logging and debugging.

    Args:
        z_height: Z-height used
        threshold: Calculated threshold
        fp_rate: False positive rate (if used)

    Returns:
        Explanation string

    Example:
        >>> explanation = get_threshold_explanation(5.5, 0.92, 0.08)
        >>> print(explanation)
        Threshold 0.92 selected for Z=5.5mm (medium height zone).
        Adjusted for FP rate of 8.0%.
    """
    zone = "high height"
    if z_height < 5.0:
        zone = "low height (critical first layer)"
    elif z_height < 20.0:
        zone = "medium height"

    explanation = f"Threshold {threshold:.3f} selected for Z={z_height:.1f}mm ({zone})."

    if fp_rate is not None:
        explanation += f"\nAdjusted for FP rate of {fp_rate * 100:.1f}%."

    if threshold >= 0.95:
        explanation += "\nUsing very strict threshold (high sensitivity)."
    elif threshold <= 0.87:
        explanation += "\nUsing relaxed threshold (lower sensitivity)."

    return explanation
