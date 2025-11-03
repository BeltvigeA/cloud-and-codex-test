"""
SSIM (Structural Similarity Index) Comparison Module

Implements detailed image comparison using SSIM to detect structural differences
that indicate leftover objects on the build plate.

SSIM is more perceptually meaningful than pixel-wise comparison and is robust
to small lighting variations.

Performance target: <20ms per comparison
"""

import logging
from typing import Tuple, Optional

import numpy as np
from skimage.metrics import structural_similarity

logger = logging.getLogger(__name__)


def compare_images_ssim(
    reference: np.ndarray,
    current: np.ndarray,
    return_difference_map: bool = True,
    win_size: int = 7,
    gaussian_weights: bool = True
) -> Tuple[float, Optional[np.ndarray]]:
    """
    Compare two preprocessed images using SSIM.

    SSIM measures structural similarity by comparing:
    - Luminance (brightness)
    - Contrast
    - Structure (patterns)

    Args:
        reference: Calibration reference image (preprocessed)
        current: Current plate image (preprocessed)
        return_difference_map: Whether to return the difference map
        win_size: Window size for SSIM calculation (must be odd, >=3)
        gaussian_weights: Use Gaussian weighting for local windows

    Returns:
        Tuple of (ssim_score, difference_map)
        - ssim_score: Float from -1 to 1 (1 = identical, <0.9 = likely different)
        - difference_map: 2D array showing per-pixel differences (if requested)

    Raises:
        ValueError: If images have different shapes or invalid parameters

    Example:
        >>> reference = preprocess_image(calibration_img)
        >>> current = preprocess_image(current_img)
        >>> score, diff_map = compare_images_ssim(reference, current)
        >>> score
        0.923  # 92.3% similar
        >>> diff_map.shape
        (540, 960)
    """
    try:
        # Validate inputs
        if reference.shape != current.shape:
            raise ValueError(
                f"Image shape mismatch: reference {reference.shape} "
                f"vs current {current.shape}"
            )

        if len(reference.shape) != 2:
            raise ValueError(
                f"Images must be 2D grayscale, got shape {reference.shape}"
            )

        if win_size < 3 or win_size % 2 == 0:
            raise ValueError(f"win_size must be odd and >= 3, got {win_size}")

        # Calculate SSIM with difference map
        ssim_score, diff_map = structural_similarity(
            reference,
            current,
            full=True,
            win_size=win_size,
            gaussian_weights=gaussian_weights,
            data_range=255  # For uint8 images
        )

        logger.debug(
            f"SSIM score: {ssim_score:.4f}, "
            f"diff_map range: [{diff_map.min():.3f}, {diff_map.max():.3f}]"
        )

        if return_difference_map:
            # Convert difference map to 0-1 range for easier thresholding
            # SSIM diff map is already in [-1, 1], we want [0, 1] where:
            # 0 = completely different, 1 = identical
            # Invert so that differences are bright: 0 = same, 1 = different
            diff_map_normalized = 1.0 - diff_map
            return float(ssim_score), diff_map_normalized
        else:
            return float(ssim_score), None

    except Exception as e:
        logger.error(f"Error comparing images with SSIM: {e}")
        raise


def compare_with_threshold(
    reference: np.ndarray,
    current: np.ndarray,
    threshold: float = 0.90,
    **ssim_kwargs
) -> Tuple[bool, float, Optional[np.ndarray]]:
    """
    Compare images and determine if they match based on threshold.

    Args:
        reference: Calibration reference image
        current: Current plate image
        threshold: Minimum SSIM score for images to be considered matching
        **ssim_kwargs: Additional arguments for compare_images_ssim()

    Returns:
        Tuple of (is_clean, ssim_score, difference_map)
        - is_clean: True if SSIM >= threshold (plate is clean)
        - ssim_score: SSIM similarity score
        - difference_map: Per-pixel difference map

    Example:
        >>> is_clean, score, diff = compare_with_threshold(ref, current, 0.92)
        >>> if is_clean:
        ...     print(f"Plate is clean (SSIM: {score:.3f})")
        ... else:
        ...     print(f"Object detected (SSIM: {score:.3f})")
    """
    ssim_score, diff_map = compare_images_ssim(
        reference, current, return_difference_map=True, **ssim_kwargs
    )

    is_clean = ssim_score >= threshold

    logger.info(
        f"SSIM comparison: score={ssim_score:.4f}, "
        f"threshold={threshold:.4f}, is_clean={is_clean}"
    )

    return is_clean, ssim_score, diff_map


def calculate_mean_ssim(
    reference: np.ndarray,
    current_images: list,
    **ssim_kwargs
) -> float:
    """
    Calculate mean SSIM across multiple current images.

    Useful for comparing against multiple checkpoints or averaging
    across multiple views.

    Args:
        reference: Single reference image
        current_images: List of current images to compare
        **ssim_kwargs: Additional arguments for compare_images_ssim()

    Returns:
        Mean SSIM score across all comparisons

    Example:
        >>> checkpoints = [checkpoint_0, checkpoint_33, checkpoint_66, checkpoint_100]
        >>> mean_score = calculate_mean_ssim(calibration, checkpoints)
        >>> mean_score
        0.915
    """
    scores = []

    for img in current_images:
        if img is None:
            logger.warning("Skipping None image in batch")
            continue

        try:
            score, _ = compare_images_ssim(
                reference, img, return_difference_map=False, **ssim_kwargs
            )
            scores.append(score)
        except Exception as e:
            logger.warning(f"Failed to calculate SSIM: {e}")
            continue

    if not scores:
        logger.error("No valid SSIM scores calculated")
        return 0.0

    mean_score = np.mean(scores)
    logger.info(
        f"Mean SSIM across {len(scores)} images: {mean_score:.4f} "
        f"(range: {min(scores):.4f} - {max(scores):.4f})"
    )

    return float(mean_score)


def get_difference_statistics(
    difference_map: np.ndarray
) -> dict:
    """
    Calculate statistics about the difference map.

    Args:
        difference_map: Normalized difference map (0=same, 1=different)

    Returns:
        Dictionary with statistics:
        - mean_difference: Average difference across image
        - max_difference: Maximum difference value
        - std_difference: Standard deviation of differences
        - high_difference_percent: Percent of pixels with high difference (>0.5)

    Example:
        >>> score, diff_map = compare_images_ssim(ref, current)
        >>> stats = get_difference_statistics(diff_map)
        >>> stats['high_difference_percent']
        2.3  # 2.3% of pixels are significantly different
    """
    if difference_map is None or difference_map.size == 0:
        return {
            'mean_difference': 0.0,
            'max_difference': 0.0,
            'std_difference': 0.0,
            'high_difference_percent': 0.0
        }

    mean_diff = float(np.mean(difference_map))
    max_diff = float(np.max(difference_map))
    std_diff = float(np.std(difference_map))

    # Calculate percentage of pixels with significant difference
    high_diff_mask = difference_map > 0.5
    high_diff_percent = 100.0 * np.sum(high_diff_mask) / difference_map.size

    stats = {
        'mean_difference': mean_diff,
        'max_difference': max_diff,
        'std_difference': std_diff,
        'high_difference_percent': high_diff_percent
    }

    logger.debug(
        f"Difference stats: mean={mean_diff:.3f}, max={max_diff:.3f}, "
        f"std={std_diff:.3f}, high_diff={high_diff_percent:.2f}%"
    )

    return stats


def compare_multiple_references(
    current: np.ndarray,
    references: dict,
    threshold: float = 0.90
) -> dict:
    """
    Compare current image against multiple reference images.

    Useful when you have calibration references at different Z heights
    and want to find the best match.

    Args:
        current: Current plate image
        references: Dict of {identifier: reference_image} pairs
        threshold: Minimum SSIM for a match

    Returns:
        Dictionary with:
        - best_match: Identifier of best matching reference
        - best_score: SSIM score of best match
        - is_clean: Whether best match exceeds threshold
        - all_scores: Dict of all {identifier: score} pairs

    Example:
        >>> references = {
        ...     'Z000mm': ref_0mm,
        ...     'Z005mm': ref_5mm,
        ...     'Z010mm': ref_10mm
        ... }
        >>> result = compare_multiple_references(current, references)
        >>> result['best_match']
        'Z005mm'
        >>> result['best_score']
        0.945
    """
    all_scores = {}
    best_match = None
    best_score = -1.0

    for identifier, reference in references.items():
        if reference is None:
            logger.warning(f"Reference {identifier} is None, skipping")
            continue

        try:
            score, _ = compare_images_ssim(
                reference, current, return_difference_map=False
            )
            all_scores[identifier] = score

            if score > best_score:
                best_score = score
                best_match = identifier

        except Exception as e:
            logger.warning(f"Failed to compare with {identifier}: {e}")
            continue

    is_clean = best_score >= threshold if best_match else False

    result = {
        'best_match': best_match,
        'best_score': float(best_score),
        'is_clean': is_clean,
        'all_scores': all_scores
    }

    logger.info(
        f"Best match: {best_match} (score: {best_score:.4f}, "
        f"is_clean: {is_clean})"
    )

    return result
