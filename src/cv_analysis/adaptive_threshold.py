"""
Adaptive Threshold Module

Calculates SSIM thresholds based on Z-height and printer-specific history.

Lower Z-heights require higher thresholds (more conservative) because:
- Small objects near the plate are harder to detect
- Risk is higher (collision can damage printer)
- Less margin for error

Higher Z-heights can use lower thresholds because:
- Objects are more visible
- Less collision risk
- More tolerance for false positives
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# Default Z-height zones and their thresholds
DEFAULT_Z_ZONES = [
    {'z_max': 5.0, 'threshold': 0.95},    # Very conservative for low heights
    {'z_max': 20.0, 'threshold': 0.92},   # Conservative for mid-low heights
    {'z_max': 1000.0, 'threshold': 0.90}  # Standard for higher heights
]

# Safety bounds - never go outside these limits
MIN_THRESHOLD = 0.85
MAX_THRESHOLD = 0.97

# False positive rate adjustment
FP_RATE_ADJUSTMENT = 0.02


def get_adaptive_threshold(
    z_height: float,
    printer_id: str,
    false_positive_rate_24h: Optional[float] = None,
    z_zones: Optional[list] = None,
    min_threshold: float = MIN_THRESHOLD,
    max_threshold: float = MAX_THRESHOLD
) -> float:
    """
    Calculate adaptive SSIM threshold based on Z-height and printer history.

    Threshold selection strategy:
    1. Start with base threshold from Z-height zone
    2. Adjust based on recent false positive rate
    3. Clamp to safety bounds

    Args:
        z_height: Current Z-height in millimeters
        printer_id: Printer serial number (for logging/future use)
        false_positive_rate_24h: Recent FP rate (0.0-1.0), if available
        z_zones: Custom Z-height zones (defaults to DEFAULT_Z_ZONES)
        min_threshold: Minimum allowed threshold (safety bound)
        max_threshold: Maximum allowed threshold (safety bound)

    Returns:
        Adaptive SSIM threshold (0.85-0.97)

    Example:
        >>> # Low Z-height, no history
        >>> threshold = get_adaptive_threshold(z_height=3.0, printer_id="P001")
        >>> threshold
        0.95  # Very conservative

        >>> # Mid Z-height with high FP rate
        >>> threshold = get_adaptive_threshold(
        ...     z_height=15.0,
        ...     printer_id="P001",
        ...     false_positive_rate_24h=0.15
        ... )
        >>> threshold
        0.90  # Lowered due to high FP rate

        >>> # High Z-height
        >>> threshold = get_adaptive_threshold(z_height=100.0, printer_id="P001")
        >>> threshold
        0.90  # Standard threshold
    """
    try:
        # Use default zones if none provided
        if z_zones is None:
            z_zones = DEFAULT_Z_ZONES

        # Step 1: Find base threshold from Z-height zone
        base_threshold = 0.90  # Default fallback

        for zone in z_zones:
            if z_height < zone['z_max']:
                base_threshold = zone['threshold']
                break

        logger.debug(
            f"Base threshold for Z={z_height:.1f}mm: {base_threshold:.3f}"
        )

        # Step 2: Adjust based on false positive rate
        adjusted_threshold = base_threshold

        if false_positive_rate_24h is not None:
            # If FP rate is high (>10%), lower threshold to reduce FPs
            # If FP rate is very low (<2%), we can afford to be more conservative
            if false_positive_rate_24h > 0.10:
                # High FP rate - lower threshold
                adjustment = -FP_RATE_ADJUSTMENT
                adjusted_threshold = base_threshold + adjustment
                logger.info(
                    f"Printer {printer_id}: High FP rate ({false_positive_rate_24h:.1%}), "
                    f"lowering threshold by {abs(adjustment):.3f}"
                )
            elif false_positive_rate_24h < 0.02:
                # Very low FP rate - we can be more conservative
                adjustment = FP_RATE_ADJUSTMENT
                adjusted_threshold = base_threshold + adjustment
                logger.info(
                    f"Printer {printer_id}: Low FP rate ({false_positive_rate_24h:.1%}), "
                    f"raising threshold by {adjustment:.3f}"
                )

        # Step 3: Clamp to safety bounds
        final_threshold = max(min_threshold, min(max_threshold, adjusted_threshold))

        if final_threshold != adjusted_threshold:
            logger.warning(
                f"Threshold {adjusted_threshold:.3f} clamped to "
                f"[{min_threshold}, {max_threshold}] -> {final_threshold:.3f}"
            )

        logger.info(
            f"Adaptive threshold for printer {printer_id} at Z={z_height:.1f}mm: "
            f"{final_threshold:.3f}"
        )

        return float(final_threshold)

    except Exception as e:
        logger.error(f"Error calculating adaptive threshold: {e}")
        # Return conservative threshold on error
        return 0.95


def get_threshold_recommendation(
    detection_history: list,
    window_hours: int = 24
) -> Dict[str, Any]:
    """
    Analyze recent detection history and recommend threshold adjustments.

    Args:
        detection_history: List of recent detection results with keys:
            - 'timestamp': Detection time
            - 'ssim_score': SSIM score
            - 'is_clean': Detection result
            - 'actual_clean': Ground truth (if verified)
        window_hours: Time window for analysis

    Returns:
        Dictionary with:
        - 'false_positive_rate': Calculated FP rate
        - 'false_negative_rate': Calculated FN rate (if ground truth available)
        - 'recommended_adjustment': Suggested threshold change
        - 'confidence': Confidence in recommendation (based on sample size)

    Example:
        >>> history = load_detection_history("P001", hours=24)
        >>> recommendation = get_threshold_recommendation(history)
        >>> recommendation
        {
            'false_positive_rate': 0.12,
            'false_negative_rate': 0.01,
            'recommended_adjustment': -0.02,
            'confidence': 0.85
        }
    """
    if not detection_history:
        return {
            'false_positive_rate': 0.0,
            'false_negative_rate': 0.0,
            'recommended_adjustment': 0.0,
            'confidence': 0.0
        }

    # Calculate false positive rate (detected object when plate was clean)
    false_positives = 0
    true_negatives = 0
    false_negatives = 0
    true_positives = 0

    for detection in detection_history:
        is_clean = detection.get('is_clean', True)
        actual_clean = detection.get('actual_clean')

        if actual_clean is not None:
            # We have ground truth
            if actual_clean and not is_clean:
                false_positives += 1
            elif actual_clean and is_clean:
                true_negatives += 1
            elif not actual_clean and not is_clean:
                true_positives += 1
            elif not actual_clean and is_clean:
                false_negatives += 1

    total_clean = true_negatives + false_positives
    total_objects = true_positives + false_negatives

    fp_rate = false_positives / total_clean if total_clean > 0 else 0.0
    fn_rate = false_negatives / total_objects if total_objects > 0 else 0.0

    # Calculate confidence based on sample size
    total_samples = len([d for d in detection_history if d.get('actual_clean') is not None])
    confidence = min(1.0, total_samples / 50.0)  # Full confidence at 50+ samples

    # Recommend adjustment
    recommended_adjustment = 0.0

    if fp_rate > 0.10 and fn_rate < 0.02:
        # High FP, low FN -> lower threshold
        recommended_adjustment = -0.02
    elif fp_rate < 0.02 and fn_rate > 0.05:
        # Low FP, high FN -> raise threshold
        recommended_adjustment = 0.02
    elif fp_rate > 0.15:
        # Very high FP -> aggressively lower threshold
        recommended_adjustment = -0.03

    result = {
        'false_positive_rate': float(fp_rate),
        'false_negative_rate': float(fn_rate),
        'recommended_adjustment': float(recommended_adjustment),
        'confidence': float(confidence),
        'sample_size': total_samples
    }

    logger.info(
        f"Threshold recommendation: FP={fp_rate:.1%}, FN={fn_rate:.1%}, "
        f"adjustment={recommended_adjustment:+.3f}, confidence={confidence:.2f}"
    )

    return result


def calculate_dynamic_threshold(
    z_height: float,
    ssim_scores: list,
    percentile: float = 5.0
) -> float:
    """
    Calculate threshold dynamically based on historical SSIM scores at this Z-height.

    Instead of fixed thresholds, use percentile of historical scores.
    This adapts to specific printer characteristics.

    Args:
        z_height: Current Z-height
        ssim_scores: Historical SSIM scores for clean plates at similar Z-heights
        percentile: Percentile to use for threshold (lower = more conservative)

    Returns:
        Dynamic threshold based on historical data

    Example:
        >>> # Get historical clean plate scores near Z=10mm
        >>> scores = get_historical_scores(printer_id="P001", z_height=10.0, tolerance=2.0)
        >>> threshold = calculate_dynamic_threshold(10.0, scores, percentile=5.0)
        >>> threshold
        0.917  # 5th percentile of historical scores
    """
    if not ssim_scores or len(ssim_scores) < 10:
        logger.warning(
            f"Insufficient historical data ({len(ssim_scores)} scores), "
            "using default threshold"
        )
        return get_adaptive_threshold(z_height, "unknown")

    import numpy as np

    # Calculate percentile threshold
    threshold = float(np.percentile(ssim_scores, percentile))

    # Apply safety bounds
    threshold = max(MIN_THRESHOLD, min(MAX_THRESHOLD, threshold))

    logger.info(
        f"Dynamic threshold at Z={z_height:.1f}mm: {threshold:.3f} "
        f"({percentile}th percentile of {len(ssim_scores)} scores)"
    )

    return threshold


def get_confidence_score(
    ssim_score: float,
    threshold: float,
    regions: list
) -> float:
    """
    Calculate confidence score for a detection decision.

    Confidence is higher when:
    - SSIM is far from threshold (either direction)
    - Few/no regions detected (for clean) or large regions (for dirty)

    Args:
        ssim_score: SSIM comparison score
        threshold: Threshold used for decision
        regions: List of detected regions

    Returns:
        Confidence score from 0.0 to 1.0

    Example:
        >>> # Clear clean plate
        >>> confidence = get_confidence_score(ssim_score=0.96, threshold=0.92, regions=[])
        >>> confidence
        0.95  # High confidence - well above threshold

        >>> # Borderline case
        >>> confidence = get_confidence_score(ssim_score=0.921, threshold=0.92, regions=[])
        >>> confidence
        0.52  # Low confidence - very close to threshold
    """
    # Distance from threshold (normalized)
    distance = abs(ssim_score - threshold)
    distance_confidence = min(1.0, distance / 0.05)  # Full confidence at 5% distance

    # Region-based confidence
    if ssim_score >= threshold:
        # Predicted clean - confidence higher with fewer regions
        region_confidence = 1.0 if len(regions) == 0 else max(0.3, 1.0 - len(regions) * 0.2)
    else:
        # Predicted object - confidence higher with more/larger regions
        if regions:
            max_area = max(r['area'] for r in regions)
            region_confidence = min(1.0, max_area / 5000.0)  # Full confidence at 5000 px
        else:
            region_confidence = 0.5  # Moderate confidence - SSIM low but no clear regions

    # Combine confidences
    confidence = (distance_confidence * 0.6 + region_confidence * 0.4)

    logger.debug(
        f"Confidence: {confidence:.2f} (distance: {distance_confidence:.2f}, "
        f"regions: {region_confidence:.2f})"
    )

    return float(confidence)
