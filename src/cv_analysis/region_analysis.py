"""
Region Analysis Module for Object Detection

This module analyzes SSIM difference maps to identify and characterize
specific regions where objects may be present on the build plate.

Key Features:
- Binarization of difference maps
- Contour detection and analysis
- Region filtering (area, aspect ratio, position)
- Object characterization (size, shape, location)
"""

import logging
from typing import List, Dict, Any, Tuple, Optional

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
    Extract and analyze significant difference regions from SSIM map.

    This function identifies discrete regions where the current image differs
    from the reference, which likely indicates leftover objects on the plate.

    Args:
        difference_map: SSIM difference map (values in [0, 1])
        min_area: Minimum region area in pixels to consider (filters noise)
        max_aspect_ratio: Maximum width/height ratio (filters edge artifacts)
        difference_threshold: Threshold for binarization (default 0.5)

    Returns:
        List of region dictionaries, each containing:
        - 'bbox': Bounding box (x, y, width, height)
        - 'area': Region area in pixels
        - 'aspect_ratio': Width/height ratio
        - 'centroid': (x, y) center point
        - 'perimeter': Region perimeter length
        - 'circularity': Shape metric (1.0 = perfect circle)

    Example:
        >>> ssim_score, diff_map = compare_images_ssim(ref, cur)
        >>> regions = analyze_difference_regions(diff_map, min_area=150)
        >>> if len(regions) > 0:
        ...     print(f"Detected {len(regions)} objects")
        ...     largest = max(regions, key=lambda r: r['area'])
        ...     print(f"Largest object: {largest['area']} pixels")
    """
    try:
        # Normalize difference map to 0-255 range
        if difference_map.max() <= 1.0:
            diff_normalized = (difference_map * 255).astype(np.uint8)
        else:
            diff_normalized = difference_map.astype(np.uint8)

        # Binarize: areas with low SSIM (< threshold) are potential objects
        # Invert so that differences are white (255)
        threshold_val = int(difference_threshold * 255)
        _, binary = cv2.threshold(
            diff_normalized,
            threshold_val,
            255,
            cv2.THRESH_BINARY_INV
        )

        # Apply morphological operations to clean up noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # Find contours
        contours, _ = cv2.findContours(
            binary,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        regions = []

        for contour in contours:
            # Calculate region properties
            area = cv2.contourArea(contour)

            # Filter by minimum area
            if area < min_area:
                continue

            # Get bounding box
            x, y, w, h = cv2.boundingRect(contour)

            # Calculate aspect ratio
            aspect_ratio = max(w, h) / max(min(w, h), 1)

            # Filter by aspect ratio (removes thin edge artifacts)
            if aspect_ratio > max_aspect_ratio:
                continue

            # Calculate centroid
            moments = cv2.moments(contour)
            if moments['m00'] != 0:
                cx = int(moments['m10'] / moments['m00'])
                cy = int(moments['m01'] / moments['m00'])
            else:
                cx, cy = x + w // 2, y + h // 2

            # Calculate perimeter
            perimeter = cv2.arcLength(contour, True)

            # Calculate circularity (4π × area / perimeter²)
            # 1.0 = perfect circle, lower = more irregular
            if perimeter > 0:
                circularity = (4 * np.pi * area) / (perimeter ** 2)
            else:
                circularity = 0.0

            region = {
                'bbox': (int(x), int(y), int(w), int(h)),
                'area': int(area),
                'aspect_ratio': float(aspect_ratio),
                'centroid': (int(cx), int(cy)),
                'perimeter': float(perimeter),
                'circularity': float(circularity),
                'contour': contour  # Keep for visualization
            }

            regions.append(region)

        # Sort by area (largest first)
        regions.sort(key=lambda r: r['area'], reverse=True)

        logger.debug(
            f"Region analysis: found {len(regions)} regions, "
            f"total_area={sum(r['area'] for r in regions)}"
        )

        return regions

    except Exception as e:
        logger.error(f"Error analyzing difference regions: {str(e)}")
        raise


def classify_region_type(region: Dict[str, Any]) -> str:
    """
    Classify the type of detected region.

    Args:
        region: Region dictionary from analyze_difference_regions

    Returns:
        Classification string: 'object', 'residue', 'artifact', 'edge'

    Example:
        >>> for region in regions:
        ...     region_type = classify_region_type(region)
        ...     if region_type == 'object':
        ...         print("Significant object detected!")
    """
    area = region['area']
    aspect_ratio = region['aspect_ratio']
    circularity = region['circularity']

    # Large, compact regions are likely objects
    if area > 1000 and circularity > 0.3:
        return 'object'

    # Small, irregular regions might be residue
    if area < 500 and circularity < 0.3:
        return 'residue'

    # Long, thin regions are likely edge artifacts
    if aspect_ratio > 3.0:
        return 'edge'

    # Default
    return 'artifact'


def calculate_region_statistics(regions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate aggregate statistics for all detected regions.

    Args:
        regions: List of region dictionaries

    Returns:
        Dictionary with statistics:
        - 'count': Number of regions
        - 'total_area': Sum of all region areas
        - 'average_area': Mean region area
        - 'largest_area': Area of largest region
        - 'coverage_percentage': Percentage of image covered by regions

    Example:
        >>> stats = calculate_region_statistics(regions)
        >>> if stats['coverage_percentage'] > 2.0:
        ...     print("More than 2% of plate has objects!")
    """
    if not regions:
        return {
            'count': 0,
            'total_area': 0,
            'average_area': 0.0,
            'largest_area': 0,
            'coverage_percentage': 0.0
        }

    total_area = sum(r['area'] for r in regions)
    average_area = total_area / len(regions)
    largest_area = max(r['area'] for r in regions)

    # Assume standard preprocessed image size
    image_area = 960 * 540  # Default from preprocessing
    coverage_percentage = (total_area / image_area) * 100

    return {
        'count': len(regions),
        'total_area': int(total_area),
        'average_area': float(average_area),
        'largest_area': int(largest_area),
        'coverage_percentage': float(coverage_percentage)
    }


def filter_edge_regions(
    regions: List[Dict[str, Any]],
    image_shape: Tuple[int, int],
    edge_margin: int = 20
) -> List[Dict[str, Any]]:
    """
    Filter out regions that touch the image edges.

    Edge regions are often artifacts from calibration misalignment
    rather than actual objects.

    Args:
        regions: List of region dictionaries
        image_shape: (height, width) of the image
        edge_margin: Distance from edge to consider (pixels)

    Returns:
        Filtered list of regions

    Example:
        >>> # Remove edge artifacts
        >>> filtered = filter_edge_regions(regions, (540, 960))
    """
    h, w = image_shape
    filtered = []

    for region in regions:
        x, y, rw, rh = region['bbox']

        # Check if region touches edges
        touches_left = x < edge_margin
        touches_right = (x + rw) > (w - edge_margin)
        touches_top = y < edge_margin
        touches_bottom = (y + rh) > (h - edge_margin)

        if not (touches_left or touches_right or touches_top or touches_bottom):
            filtered.append(region)
        else:
            logger.debug(f"Filtered edge region at {(x, y)}")

    return filtered


def merge_nearby_regions(
    regions: List[Dict[str, Any]],
    distance_threshold: int = 50
) -> List[Dict[str, Any]]:
    """
    Merge regions that are close to each other.

    Sometimes a single object appears as multiple disconnected regions
    due to internal texture. This function merges nearby regions.

    Args:
        regions: List of region dictionaries
        distance_threshold: Maximum distance between centroids to merge

    Returns:
        List of merged regions

    Example:
        >>> # Merge regions within 40 pixels of each other
        >>> merged = merge_nearby_regions(regions, distance_threshold=40)
    """
    if len(regions) <= 1:
        return regions

    merged = []
    used = set()

    for i, region1 in enumerate(regions):
        if i in used:
            continue

        group = [region1]
        cx1, cy1 = region1['centroid']

        for j, region2 in enumerate(regions[i + 1:], start=i + 1):
            if j in used:
                continue

            cx2, cy2 = region2['centroid']
            distance = np.sqrt((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2)

            if distance < distance_threshold:
                group.append(region2)
                used.add(j)

        # Create merged region from group
        if len(group) == 1:
            merged.append(group[0])
        else:
            # Compute bounding box that contains all regions
            all_x = [r['bbox'][0] for r in group]
            all_y = [r['bbox'][1] for r in group]
            all_x2 = [r['bbox'][0] + r['bbox'][2] for r in group]
            all_y2 = [r['bbox'][1] + r['bbox'][3] for r in group]

            x = min(all_x)
            y = min(all_y)
            w = max(all_x2) - x
            h = max(all_y2) - y

            total_area = sum(r['area'] for r in group)

            merged_region = {
                'bbox': (x, y, w, h),
                'area': int(total_area),
                'aspect_ratio': float(max(w, h) / max(min(w, h), 1)),
                'centroid': (int(sum(r['centroid'][0] for r in group) / len(group)),
                           int(sum(r['centroid'][1] for r in group) / len(group))),
                'perimeter': sum(r['perimeter'] for r in group),
                'circularity': np.mean([r['circularity'] for r in group]),
                'merged_from': len(group)
            }

            merged.append(merged_region)

        used.add(i)

    logger.debug(f"Merged {len(regions)} regions into {len(merged)}")

    return merged


def find_largest_region(regions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Find the largest detected region.

    Args:
        regions: List of region dictionaries

    Returns:
        Largest region or None if list is empty

    Example:
        >>> largest = find_largest_region(regions)
        >>> if largest and largest['area'] > 2000:
        ...     print("Large object detected!")
    """
    if not regions:
        return None

    return max(regions, key=lambda r: r['area'])


def create_region_mask(
    regions: List[Dict[str, Any]],
    image_shape: Tuple[int, int]
) -> np.ndarray:
    """
    Create a binary mask showing all detected regions.

    Args:
        regions: List of region dictionaries
        image_shape: (height, width) for output mask

    Returns:
        Binary mask (uint8) with regions marked as 255

    Example:
        >>> mask = create_region_mask(regions, (540, 960))
        >>> # Use mask for visualization or further analysis
    """
    mask = np.zeros(image_shape, dtype=np.uint8)

    for region in regions:
        if 'contour' in region:
            cv2.drawContours(mask, [region['contour']], -1, 255, -1)
        else:
            # Use bounding box if contour not available
            x, y, w, h = region['bbox']
            cv2.rectangle(mask, (x, y), (x + w, y + h), 255, -1)

    return mask


def analyze_region_distribution(
    regions: List[Dict[str, Any]],
    image_shape: Tuple[int, int],
    grid_size: Tuple[int, int] = (3, 3)
) -> np.ndarray:
    """
    Analyze the spatial distribution of detected regions.

    Divides image into grid and counts regions in each cell.

    Args:
        regions: List of region dictionaries
        image_shape: (height, width) of image
        grid_size: (rows, cols) for distribution grid

    Returns:
        2D array showing region count per grid cell

    Example:
        >>> distribution = analyze_region_distribution(regions, (540, 960))
        >>> # Check if objects clustered in one area
        >>> if distribution.max() > 3:
        ...     print("Multiple objects in one area!")
    """
    h, w = image_shape
    rows, cols = grid_size

    distribution = np.zeros(grid_size, dtype=np.int32)

    cell_h = h / rows
    cell_w = w / cols

    for region in regions:
        cx, cy = region['centroid']

        # Determine which cell the centroid falls in
        cell_row = min(int(cy / cell_h), rows - 1)
        cell_col = min(int(cx / cell_w), cols - 1)

        distribution[cell_row, cell_col] += 1

    return distribution
