"""
Region Analysis Module

Analyzes SSIM difference maps to identify and characterize specific regions
where objects may be present on the build plate.

Filters out noise and edge artifacts while identifying significant object regions.

Performance target: <5ms per analysis
"""

import logging
from typing import List, Dict, Any, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def analyze_difference_regions(
    difference_map: np.ndarray,
    min_area: int = 100,
    max_aspect_ratio: float = 5.0,
    difference_threshold: float = 0.5
) -> List[Dict[str, Any]]:
    """
    Extract and analyze significant difference regions from SSIM difference map.

    Workflow:
    1. Binarize difference map at threshold
    2. Find contours of different regions
    3. Filter by minimum area (remove noise)
    4. Filter by aspect ratio (remove edge artifacts)
    5. Calculate properties for each region

    Args:
        difference_map: Normalized difference map (0=same, 1=different)
        min_area: Minimum region area in pixels to consider
        max_aspect_ratio: Maximum aspect ratio to filter out edge artifacts
        difference_threshold: Threshold for binarizing difference map

    Returns:
        List of region dictionaries with keys:
        - 'bbox': Bounding box as (x, y, width, height)
        - 'area': Region area in pixels
        - 'aspect_ratio': Width / height ratio
        - 'centroid': Center point as (x, y)
        - 'mean_difference': Mean difference value in region
        - 'max_difference': Maximum difference value in region

    Example:
        >>> score, diff_map = compare_images_ssim(reference, current)
        >>> regions = analyze_difference_regions(diff_map)
        >>> len(regions)
        2  # Found 2 significant regions
        >>> regions[0]
        {
            'bbox': (120, 340, 80, 95),
            'area': 6420,
            'aspect_ratio': 0.84,
            'centroid': (160, 387),
            'mean_difference': 0.73,
            'max_difference': 0.92
        }
    """
    try:
        if difference_map is None or difference_map.size == 0:
            logger.warning("Empty difference map provided")
            return []

        # Step 1: Binarize the difference map
        # Convert to uint8 for OpenCV operations
        diff_uint8 = (difference_map * 255).astype(np.uint8)
        _, binary = cv2.threshold(
            diff_uint8,
            int(difference_threshold * 255),
            255,
            cv2.THRESH_BINARY
        )

        # Step 2: Find contours
        contours, _ = cv2.findContours(
            binary,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        logger.debug(f"Found {len(contours)} contours in difference map")

        # Step 3-5: Filter and analyze regions
        regions = []

        for contour in contours:
            # Calculate bounding box and area
            x, y, w, h = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)

            # Filter by minimum area
            if area < min_area:
                continue

            # Calculate aspect ratio
            aspect_ratio = w / h if h > 0 else 0

            # Filter by aspect ratio (remove long thin edge artifacts)
            if aspect_ratio > max_aspect_ratio or aspect_ratio < 1.0 / max_aspect_ratio:
                logger.debug(
                    f"Filtered region with aspect ratio {aspect_ratio:.2f}"
                )
                continue

            # Calculate centroid
            M = cv2.moments(contour)
            if M['m00'] != 0:
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
            else:
                cx, cy = x + w // 2, y + h // 2

            # Extract region from difference map for statistics
            region_diff = difference_map[y:y+h, x:x+w]
            mean_diff = float(np.mean(region_diff))
            max_diff = float(np.max(region_diff))

            region = {
                'bbox': (int(x), int(y), int(w), int(h)),
                'area': int(area),
                'aspect_ratio': float(aspect_ratio),
                'centroid': (int(cx), int(cy)),
                'mean_difference': mean_diff,
                'max_difference': max_diff
            }

            regions.append(region)
            logger.debug(
                f"Region: bbox={region['bbox']}, area={area:.0f}, "
                f"aspect_ratio={aspect_ratio:.2f}, mean_diff={mean_diff:.3f}"
            )

        logger.info(f"Identified {len(regions)} significant regions")

        # Sort by area (largest first)
        regions.sort(key=lambda r: r['area'], reverse=True)

        return regions

    except Exception as e:
        logger.error(f"Error analyzing difference regions: {e}")
        raise


def filter_regions_by_position(
    regions: List[Dict[str, Any]],
    image_shape: Tuple[int, int],
    exclude_border_percent: float = 0.05
) -> List[Dict[str, Any]]:
    """
    Filter out regions that are too close to image borders.

    Border regions are often artifacts from lighting or mechanical edges.

    Args:
        regions: List of region dictionaries
        image_shape: Shape of image as (height, width)
        exclude_border_percent: Percent of image dimensions to exclude at borders

    Returns:
        Filtered list of regions

    Example:
        >>> regions = analyze_difference_regions(diff_map)
        >>> filtered = filter_regions_by_position(regions, diff_map.shape)
        >>> len(filtered)
        1  # Removed border artifacts
    """
    height, width = image_shape
    border_x = int(width * exclude_border_percent)
    border_y = int(height * exclude_border_percent)

    filtered = []

    for region in regions:
        x, y, w, h = region['bbox']
        cx, cy = region['centroid']

        # Check if centroid is within valid region
        if (border_x < cx < width - border_x and
            border_y < cy < height - border_y):
            filtered.append(region)
        else:
            logger.debug(
                f"Filtered border region at centroid ({cx}, {cy})"
            )

    logger.info(
        f"Filtered {len(regions) - len(filtered)} border regions, "
        f"{len(filtered)} remaining"
    )

    return filtered


def calculate_region_overlap(
    region1: Dict[str, Any],
    region2: Dict[str, Any]
) -> float:
    """
    Calculate overlap percentage between two regions.

    Args:
        region1: First region dictionary
        region2: Second region dictionary

    Returns:
        Overlap as fraction of smaller region area (0.0 to 1.0)

    Example:
        >>> overlap = calculate_region_overlap(regions[0], regions[1])
        >>> overlap
        0.15  # 15% overlap
    """
    x1, y1, w1, h1 = region1['bbox']
    x2, y2, w2, h2 = region2['bbox']

    # Calculate intersection
    x_left = max(x1, x2)
    y_top = max(y1, y2)
    x_right = min(x1 + w1, x2 + w2)
    y_bottom = min(y1 + h1, y2 + h2)

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    intersection_area = (x_right - x_left) * (y_bottom - y_top)
    smaller_area = min(region1['area'], region2['area'])

    overlap = intersection_area / smaller_area if smaller_area > 0 else 0.0

    return float(overlap)


def merge_overlapping_regions(
    regions: List[Dict[str, Any]],
    overlap_threshold: float = 0.3
) -> List[Dict[str, Any]]:
    """
    Merge regions that significantly overlap.

    Args:
        regions: List of region dictionaries
        overlap_threshold: Minimum overlap fraction to trigger merge

    Returns:
        List of merged regions

    Example:
        >>> regions = analyze_difference_regions(diff_map)
        >>> merged = merge_overlapping_regions(regions, overlap_threshold=0.3)
        >>> len(merged) < len(regions)
        True  # Some regions were merged
    """
    if len(regions) <= 1:
        return regions

    merged = []
    used = set()

    for i, region1 in enumerate(regions):
        if i in used:
            continue

        # Start with current region
        merged_bbox = list(region1['bbox'])
        merged_area = region1['area']
        merged_indices = [i]

        # Check for overlaps with remaining regions
        for j, region2 in enumerate(regions[i+1:], start=i+1):
            if j in used:
                continue

            overlap = calculate_region_overlap(region1, region2)

            if overlap >= overlap_threshold:
                # Merge bounding boxes
                x1, y1, w1, h1 = merged_bbox
                x2, y2, w2, h2 = region2['bbox']

                new_x = min(x1, x2)
                new_y = min(y1, y2)
                new_w = max(x1 + w1, x2 + w2) - new_x
                new_h = max(y1 + h1, y2 + h2) - new_y

                merged_bbox = [new_x, new_y, new_w, new_h]
                merged_area += region2['area']
                merged_indices.append(j)
                used.add(j)

        # Create merged region
        x, y, w, h = merged_bbox
        merged_region = {
            'bbox': tuple(merged_bbox),
            'area': merged_area,
            'aspect_ratio': w / h if h > 0 else 0,
            'centroid': (x + w // 2, y + h // 2),
            'mean_difference': np.mean([regions[idx]['mean_difference']
                                       for idx in merged_indices]),
            'max_difference': max([regions[idx]['max_difference']
                                  for idx in merged_indices])
        }

        merged.append(merged_region)
        used.add(i)

    logger.info(
        f"Merged {len(regions)} regions into {len(merged)} regions "
        f"(threshold: {overlap_threshold})"
    )

    return merged


def get_largest_region(regions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Get the largest region by area.

    Args:
        regions: List of region dictionaries

    Returns:
        Largest region dictionary or None if list is empty

    Example:
        >>> regions = analyze_difference_regions(diff_map)
        >>> largest = get_largest_region(regions)
        >>> largest['area']
        6420
    """
    if not regions:
        return None

    return max(regions, key=lambda r: r['area'])


def calculate_total_difference_area(regions: List[Dict[str, Any]]) -> int:
    """
    Calculate total area covered by all difference regions.

    Args:
        regions: List of region dictionaries

    Returns:
        Total area in pixels

    Example:
        >>> regions = analyze_difference_regions(diff_map)
        >>> total_area = calculate_total_difference_area(regions)
        >>> total_area
        8750  # pixels
    """
    return sum(r['area'] for r in regions)


def get_region_summary(regions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate a summary of all detected regions.

    Args:
        regions: List of region dictionaries

    Returns:
        Summary dictionary with statistics

    Example:
        >>> summary = get_region_summary(regions)
        >>> summary
        {
            'count': 2,
            'total_area': 8750,
            'mean_area': 4375,
            'largest_area': 6420,
            'mean_difference': 0.68
        }
    """
    if not regions:
        return {
            'count': 0,
            'total_area': 0,
            'mean_area': 0,
            'largest_area': 0,
            'mean_difference': 0.0
        }

    areas = [r['area'] for r in regions]
    mean_diffs = [r['mean_difference'] for r in regions]

    summary = {
        'count': len(regions),
        'total_area': sum(areas),
        'mean_area': int(np.mean(areas)),
        'largest_area': max(areas),
        'mean_difference': float(np.mean(mean_diffs))
    }

    return summary
