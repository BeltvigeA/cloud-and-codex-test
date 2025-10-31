"""
SSIM Comparison Module for Build Plate Object Detection

This module implements Structural Similarity Index (SSIM) comparison
for detecting leftover objects on 3D printer build plates.

SSIM is superior to simple pixel-wise comparison because it captures:
- Luminance changes
- Contrast variations
- Structural differences

Key Features:
- SSIM score calculation (0.0 = completely different, 1.0 = identical)
- Difference map generation for region analysis
- Multi-scale SSIM for different object sizes
- Configurable window sizes and weights
"""

import logging
from typing import Tuple, Optional

import numpy as np
from skimage.metrics import structural_similarity
import cv2

logger = logging.getLogger(__name__)


def compare_images_ssim(
    reference: np.ndarray,
    current: np.ndarray,
    return_difference_map: bool = True,
    window_size: int = 7,
    gaussian_weights: bool = True
) -> Tuple[float, Optional[np.ndarray]]:
    """
    Compare two preprocessed images using SSIM.

    This is the core detection algorithm. It compares the current build plate
    image against a calibration reference to detect structural differences
    that indicate leftover objects.

    Args:
        reference: Calibration reference image (preprocessed grayscale)
        current: Current plate image (preprocessed grayscale)
        return_difference_map: If True, return the pixel-wise difference map
        window_size: Size of sliding window (must be odd, >= 3)
        gaussian_weights: If True, use Gaussian weighting in window

    Returns:
        Tuple of (ssim_score, difference_map)
        - ssim_score: Float in range [0.0, 1.0] where 1.0 = identical
        - difference_map: Array same size as input showing pixel-wise differences
                         (None if return_difference_map=False)

    Raises:
        ValueError: If images are incompatible or window_size is invalid

    Example:
        >>> from cv_analysis.preprocessing import preprocess_image
        >>> ref = preprocess_image(reference_image)
        >>> cur = preprocess_image(current_image)
        >>> ssim_score, diff_map = compare_images_ssim(ref, cur)
        >>> if ssim_score > 0.95:
        ...     print("Plate appears clean")
        >>> else:
        ...     print(f"Detected differences (SSIM: {ssim_score:.3f})")
    """
    try:
        # Validate inputs
        if reference.shape != current.shape:
            raise ValueError(
                f"Image shape mismatch: reference={reference.shape}, "
                f"current={current.shape}"
            )

        if window_size < 3 or window_size % 2 == 0:
            raise ValueError(f"window_size must be odd and >= 3, got {window_size}")

        # Ensure images are proper format
        if reference.dtype != np.uint8:
            reference = reference.astype(np.uint8)
        if current.dtype != np.uint8:
            current = current.astype(np.uint8)

        # Calculate SSIM
        if return_difference_map:
            ssim_score, diff_map = structural_similarity(
                reference,
                current,
                full=True,
                win_size=window_size,
                gaussian_weights=gaussian_weights,
                data_range=255
            )
        else:
            ssim_score = structural_similarity(
                reference,
                current,
                full=False,
                win_size=window_size,
                gaussian_weights=gaussian_weights,
                data_range=255
            )
            diff_map = None

        logger.debug(
            f"SSIM comparison: score={ssim_score:.4f}, "
            f"window_size={window_size}"
        )

        return float(ssim_score), diff_map

    except Exception as e:
        logger.error(f"Error in SSIM comparison: {str(e)}")
        raise


def calculate_regional_ssim(
    reference: np.ndarray,
    current: np.ndarray,
    num_regions: Tuple[int, int] = (4, 4),
    window_size: int = 7
) -> Tuple[float, np.ndarray]:
    """
    Calculate SSIM for different regions of the image.

    This can help identify which part of the build plate has objects.
    Useful for localization before detailed region analysis.

    Args:
        reference: Reference image
        current: Current image
        num_regions: Grid size (rows, cols) to divide image
        window_size: SSIM window size

    Returns:
        Tuple of (overall_ssim, regional_ssim_grid)
        - overall_ssim: Average SSIM across all regions
        - regional_ssim_grid: 2D array of SSIM scores per region

    Example:
        >>> overall, regional = calculate_regional_ssim(ref, cur, (3, 3))
        >>> # Check if any region has low SSIM
        >>> min_regional = regional.min()
        >>> if min_regional < 0.85:
        ...     print(f"Problem area detected: SSIM={min_regional:.3f}")
    """
    try:
        h, w = reference.shape
        rows, cols = num_regions

        region_h = h // rows
        region_w = w // cols

        regional_scores = np.zeros(num_regions)

        for i in range(rows):
            for j in range(cols):
                # Extract region
                r_start = i * region_h
                r_end = (i + 1) * region_h if i < rows - 1 else h
                c_start = j * region_w
                c_end = (j + 1) * region_w if j < cols - 1 else w

                ref_region = reference[r_start:r_end, c_start:c_end]
                cur_region = current[r_start:r_end, c_start:c_end]

                # Calculate SSIM for this region
                score, _ = compare_images_ssim(
                    ref_region,
                    cur_region,
                    return_difference_map=False,
                    window_size=window_size
                )

                regional_scores[i, j] = score

        overall_ssim = float(np.mean(regional_scores))

        logger.debug(
            f"Regional SSIM: overall={overall_ssim:.4f}, "
            f"min={regional_scores.min():.4f}, max={regional_scores.max():.4f}"
        )

        return overall_ssim, regional_scores

    except Exception as e:
        logger.error(f"Error in regional SSIM calculation: {str(e)}")
        raise


def multi_scale_ssim(
    reference: np.ndarray,
    current: np.ndarray,
    scales: list[float] = [1.0, 0.5, 0.25]
) -> Tuple[float, list[float]]:
    """
    Calculate SSIM at multiple scales for multi-resolution comparison.

    This helps detect both large objects and fine details. The final score
    is a weighted average of all scales.

    Args:
        reference: Reference image
        current: Current image
        scales: List of scale factors (1.0 = original size)

    Returns:
        Tuple of (weighted_ssim, scale_scores)
        - weighted_ssim: Weighted average of all scales
        - scale_scores: List of SSIM scores at each scale

    Example:
        >>> ms_ssim, scales = multi_scale_ssim(ref, cur)
        >>> print(f"Multi-scale SSIM: {ms_ssim:.3f}")
        >>> for i, score in enumerate(scales):
        ...     print(f"  Scale {i}: {score:.3f}")
    """
    try:
        scale_scores = []
        weights = [0.5, 0.3, 0.2][:len(scales)]  # Default weights

        # Normalize weights
        weight_sum = sum(weights)
        weights = [w / weight_sum for w in weights]

        for scale in scales:
            if scale == 1.0:
                ref_scaled = reference
                cur_scaled = current
            else:
                new_size = (
                    int(reference.shape[1] * scale),
                    int(reference.shape[0] * scale)
                )
                ref_scaled = cv2.resize(reference, new_size, interpolation=cv2.INTER_AREA)
                cur_scaled = cv2.resize(current, new_size, interpolation=cv2.INTER_AREA)

            score, _ = compare_images_ssim(
                ref_scaled,
                cur_scaled,
                return_difference_map=False,
                window_size=7
            )

            scale_scores.append(score)

        # Calculate weighted average
        weighted_ssim = sum(s * w for s, w in zip(scale_scores, weights))

        logger.debug(
            f"Multi-scale SSIM: weighted={weighted_ssim:.4f}, "
            f"scales={[f'{s:.4f}' for s in scale_scores]}"
        )

        return float(weighted_ssim), scale_scores

    except Exception as e:
        logger.error(f"Error in multi-scale SSIM: {str(e)}")
        raise


def get_difference_mask(
    difference_map: np.ndarray,
    threshold: float = 0.5,
    invert: bool = True
) -> np.ndarray:
    """
    Convert SSIM difference map to binary mask.

    The SSIM difference map has values in [0, 1] where:
    - 1.0 = pixels are identical
    - 0.0 = pixels are completely different

    Args:
        difference_map: SSIM difference map from compare_images_ssim
        threshold: Similarity threshold (default 0.5)
        invert: If True, 1 = different, 0 = similar (more intuitive)

    Returns:
        Binary mask (uint8) where 255 = different, 0 = similar

    Example:
        >>> ssim_score, diff_map = compare_images_ssim(ref, cur)
        >>> mask = get_difference_mask(diff_map, threshold=0.7)
        >>> # Now mask has 255 where pixels differ significantly
    """
    try:
        # Normalize to 0-1 range if needed
        if difference_map.max() > 1.0:
            diff_normalized = difference_map / 255.0
        else:
            diff_normalized = difference_map

        # Create binary mask
        if invert:
            # Values below threshold are considered different
            mask = (diff_normalized < threshold).astype(np.uint8) * 255
        else:
            # Values above threshold are considered similar
            mask = (diff_normalized > threshold).astype(np.uint8) * 255

        logger.debug(
            f"Difference mask: threshold={threshold}, "
            f"different_pixels={np.sum(mask > 0)}"
        )

        return mask

    except Exception as e:
        logger.error(f"Error creating difference mask: {str(e)}")
        raise


def calculate_ssim_statistics(
    reference: np.ndarray,
    current: np.ndarray,
    window_size: int = 7
) -> dict:
    """
    Calculate comprehensive SSIM statistics for analysis.

    Args:
        reference: Reference image
        current: Current image
        window_size: SSIM window size

    Returns:
        Dictionary with keys:
        - 'ssim_mean': Overall SSIM score
        - 'ssim_std': Standard deviation of difference map
        - 'ssim_min': Minimum SSIM value in difference map
        - 'ssim_max': Maximum SSIM value in difference map
        - 'low_ssim_percentage': Percentage of pixels with SSIM < 0.5

    Example:
        >>> stats = calculate_ssim_statistics(ref, cur)
        >>> if stats['low_ssim_percentage'] > 5.0:
        ...     print("More than 5% of pixels differ significantly")
    """
    try:
        ssim_score, diff_map = compare_images_ssim(
            reference,
            current,
            return_difference_map=True,
            window_size=window_size
        )

        low_ssim_count = np.sum(diff_map < 0.5)
        total_pixels = diff_map.size
        low_ssim_percentage = (low_ssim_count / total_pixels) * 100

        stats = {
            'ssim_mean': float(ssim_score),
            'ssim_std': float(np.std(diff_map)),
            'ssim_min': float(np.min(diff_map)),
            'ssim_max': float(np.max(diff_map)),
            'low_ssim_percentage': float(low_ssim_percentage),
            'median_ssim': float(np.median(diff_map))
        }

        logger.debug(f"SSIM statistics: {stats}")

        return stats

    except Exception as e:
        logger.error(f"Error calculating SSIM statistics: {str(e)}")
        raise


def compare_with_confidence(
    reference: np.ndarray,
    current: np.ndarray,
    window_size: int = 7
) -> Tuple[float, float, Optional[np.ndarray]]:
    """
    Compare images and calculate confidence score.

    The confidence score indicates how reliable the SSIM comparison is,
    based on the distribution of the difference map.

    Args:
        reference: Reference image
        current: Current image
        window_size: SSIM window size

    Returns:
        Tuple of (ssim_score, confidence, difference_map)
        - confidence: 0.0-1.0, higher = more confident in result

    Example:
        >>> ssim, confidence, diff = compare_with_confidence(ref, cur)
        >>> if confidence > 0.8 and ssim > 0.95:
        ...     print("High confidence: plate is clean")
    """
    try:
        ssim_score, diff_map = compare_images_ssim(
            reference,
            current,
            return_difference_map=True,
            window_size=window_size
        )

        # Calculate confidence based on difference map distribution
        # High standard deviation = uncertain/mixed results
        # Low standard deviation = consistent results = high confidence
        std = np.std(diff_map)

        # Confidence decreases with higher std
        # Normalize std to 0-1 range (assume max meaningful std is 0.3)
        normalized_std = min(std / 0.3, 1.0)
        confidence = 1.0 - normalized_std

        logger.debug(
            f"SSIM with confidence: score={ssim_score:.4f}, "
            f"confidence={confidence:.4f}, std={std:.4f}"
        )

        return float(ssim_score), float(confidence), diff_map

    except Exception as e:
        logger.error(f"Error in SSIM comparison with confidence: {str(e)}")
        raise
