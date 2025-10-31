"""
Visualization Module for CV Analysis Debugging

This module creates visual outputs for debugging and false positive analysis:
- Side-by-side comparison images
- Annotated difference maps
- Region overlay visualizations
- SSIM heatmaps

All visualizations are designed to help diagnose why detections succeeded/failed.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def create_comparison_visualization(
    reference: np.ndarray,
    current: np.ndarray,
    difference_map: np.ndarray,
    regions: List[Dict[str, Any]],
    ssim_score: float,
    output_path: str,
    threshold: float = 0.90,
    z_height: Optional[float] = None
) -> None:
    """
    Create annotated comparison image for debugging.

    Generates a 2x2 grid showing:
    - Top left: Reference image
    - Top right: Current image with detected regions
    - Bottom left: Difference map heatmap
    - Bottom right: Difference map with region overlays

    Args:
        reference: Reference calibration image
        current: Current plate image
        difference_map: SSIM difference map
        regions: List of detected regions
        ssim_score: Overall SSIM score
        output_path: Path to save visualization
        threshold: Detection threshold used
        z_height: Optional Z-height for annotation

    Example:
        >>> create_comparison_visualization(
        ...     ref, cur, diff_map, regions, 0.87,
        ...     '/data/cv_analysis/job123/comparison.png',
        ...     threshold=0.90, z_height=45.0
        ... )
    """
    try:
        # Ensure images are the right format
        if reference.dtype != np.uint8:
            reference = (reference * 255).astype(np.uint8) if reference.max() <= 1.0 else reference.astype(np.uint8)
        if current.dtype != np.uint8:
            current = (current * 255).astype(np.uint8) if current.max() <= 1.0 else current.astype(np.uint8)

        # Convert grayscale to BGR for colored annotations
        ref_bgr = cv2.cvtColor(reference, cv2.COLOR_GRAY2BGR)
        cur_bgr = cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)

        # Create difference map heatmap
        diff_heatmap = _create_difference_heatmap(difference_map)

        # Create annotated current image with regions
        cur_annotated = cur_bgr.copy()
        for region in regions:
            x, y, w, h = region['bbox']
            # Draw bounding box
            cv2.rectangle(cur_annotated, (x, y), (x + w, y + h), (0, 0, 255), 2)
            # Draw centroid
            cx, cy = region['centroid']
            cv2.circle(cur_annotated, (cx, cy), 5, (255, 0, 0), -1)
            # Add area label
            label = f"{region['area']}px"
            cv2.putText(cur_annotated, label, (x, y - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        # Create difference map with region overlays
        diff_annotated = diff_heatmap.copy()
        for region in regions:
            x, y, w, h = region['bbox']
            cv2.rectangle(diff_annotated, (x, y), (x + w, y + h), (255, 255, 255), 2)

        # Combine into 2x2 grid
        top_row = np.hstack([ref_bgr, cur_annotated])
        bottom_row = np.hstack([diff_heatmap, diff_annotated])
        combined = np.vstack([top_row, bottom_row])

        # Add text annotations
        combined = _add_text_overlay(
            combined,
            ssim_score=ssim_score,
            threshold=threshold,
            num_regions=len(regions),
            z_height=z_height
        )

        # Save image
        cv2.imwrite(output_path, combined)

        logger.info(f"Saved comparison visualization: {output_path}")

    except Exception as e:
        logger.error(f"Error creating comparison visualization: {str(e)}")
        raise


def _create_difference_heatmap(difference_map: np.ndarray) -> np.ndarray:
    """
    Convert SSIM difference map to color heatmap.

    Args:
        difference_map: SSIM difference map (0-1 range)

    Returns:
        BGR heatmap image
    """
    # Normalize to 0-255
    if difference_map.max() <= 1.0:
        diff_normalized = (difference_map * 255).astype(np.uint8)
    else:
        diff_normalized = difference_map.astype(np.uint8)

    # Invert so differences are hot colors
    diff_inverted = 255 - diff_normalized

    # Apply colormap (COLORMAP_JET: blue=similar, red=different)
    heatmap = cv2.applyColorMap(diff_inverted, cv2.COLORMAP_JET)

    return heatmap


def _add_text_overlay(
    image: np.ndarray,
    ssim_score: float,
    threshold: float,
    num_regions: int,
    z_height: Optional[float] = None
) -> np.ndarray:
    """
    Add text overlay with detection information.

    Args:
        image: Image to annotate
        ssim_score: SSIM score
        threshold: Threshold used
        num_regions: Number of regions detected
        z_height: Z-height (optional)

    Returns:
        Annotated image
    """
    result_text = "CLEAN" if ssim_score >= threshold else "OBJECT DETECTED"
    result_color = (0, 255, 0) if ssim_score >= threshold else (0, 0, 255)

    # Add semi-transparent background for text
    overlay = image.copy()
    cv2.rectangle(overlay, (10, 10), (500, 100), (0, 0, 0), -1)
    image = cv2.addWeighted(overlay, 0.3, image, 0.7, 0)

    # Add text
    y_offset = 30
    cv2.putText(image, f"Result: {result_text}", (20, y_offset),
               cv2.FONT_HERSHEY_SIMPLEX, 0.7, result_color, 2)

    y_offset += 25
    cv2.putText(image, f"SSIM: {ssim_score:.4f} (threshold: {threshold:.4f})", (20, y_offset),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    y_offset += 25
    cv2.putText(image, f"Regions detected: {num_regions}", (20, y_offset),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    if z_height is not None:
        y_offset += 25
        cv2.putText(image, f"Z-height: {z_height:.1f}mm", (20, y_offset),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    return image


def create_region_overlay(
    image: np.ndarray,
    regions: List[Dict[str, Any]],
    output_path: str,
    show_labels: bool = True
) -> None:
    """
    Create image with region overlays only.

    Simpler visualization showing just detected regions on the image.

    Args:
        image: Base image
        regions: List of detected regions
        output_path: Path to save visualization
        show_labels: Show region info labels

    Example:
        >>> create_region_overlay(current_image, regions, '/path/to/output.png')
    """
    try:
        # Convert to BGR if grayscale
        if len(image.shape) == 2:
            output = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            output = image.copy()

        # Draw each region
        for i, region in enumerate(regions):
            x, y, w, h = region['bbox']

            # Draw bounding box
            cv2.rectangle(output, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # Draw centroid
            cx, cy = region['centroid']
            cv2.circle(output, (cx, cy), 3, (255, 0, 0), -1)

            if show_labels:
                # Add label
                label = f"#{i+1}: {region['area']}px"
                cv2.putText(output, label, (x, y - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        # Save
        cv2.imwrite(output_path, output)

        logger.info(f"Saved region overlay: {output_path}")

    except Exception as e:
        logger.error(f"Error creating region overlay: {str(e)}")
        raise


def create_ssim_heatmap(
    difference_map: np.ndarray,
    output_path: str,
    colormap: int = cv2.COLORMAP_JET
) -> None:
    """
    Create standalone SSIM difference heatmap.

    Args:
        difference_map: SSIM difference map
        output_path: Path to save heatmap
        colormap: OpenCV colormap constant

    Example:
        >>> create_ssim_heatmap(diff_map, '/path/to/heatmap.png')
    """
    try:
        heatmap = _create_difference_heatmap(difference_map)
        cv2.imwrite(output_path, heatmap)

        logger.info(f"Saved SSIM heatmap: {output_path}")

    except Exception as e:
        logger.error(f"Error creating SSIM heatmap: {str(e)}")
        raise


def create_detection_montage(
    detections: List[Dict[str, Any]],
    output_path: str,
    max_images: int = 10
) -> None:
    """
    Create montage of multiple detection results.

    Useful for reviewing a batch of detections at once.

    Args:
        detections: List of detection dictionaries with 'current_image' and metadata
        output_path: Path to save montage
        max_images: Maximum number of images to include

    Example:
        >>> detections = [
        ...     {'current_image': img1, 'ssim_score': 0.85, 'is_clean': False},
        ...     {'current_image': img2, 'ssim_score': 0.96, 'is_clean': True},
        ... ]
        >>> create_detection_montage(detections, '/path/to/montage.png')
    """
    try:
        num_images = min(len(detections), max_images)
        if num_images == 0:
            logger.warning("No detections to create montage")
            return

        # Calculate grid dimensions (try to make it square-ish)
        cols = int(np.ceil(np.sqrt(num_images)))
        rows = int(np.ceil(num_images / cols))

        # Assume all images are same size (from preprocessing)
        img_h, img_w = 540, 960  # Standard preprocessed size

        # Create blank canvas
        montage = np.zeros((rows * img_h, cols * img_w, 3), dtype=np.uint8)

        for i, detection in enumerate(detections[:max_images]):
            row = i // cols
            col = i % cols

            # Get image
            img = detection.get('current_image')
            if img is None:
                continue

            # Convert to BGR if needed
            if len(img.shape) == 2:
                img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            else:
                img_bgr = img.copy()

            # Add label
            ssim = detection.get('ssim_score', 0.0)
            is_clean = detection.get('is_clean', False)
            label = f"SSIM: {ssim:.3f} - {'CLEAN' if is_clean else 'OBJECT'}"
            color = (0, 255, 0) if is_clean else (0, 0, 255)

            cv2.putText(img_bgr, label, (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            # Place in montage
            y_start = row * img_h
            x_start = col * img_w
            montage[y_start:y_start+img_h, x_start:x_start+img_w] = img_bgr

        # Save
        cv2.imwrite(output_path, montage)

        logger.info(f"Saved detection montage: {output_path}")

    except Exception as e:
        logger.error(f"Error creating detection montage: {str(e)}")
        raise


def draw_detection_timeline(
    z_heights: List[float],
    ssim_scores: List[float],
    threshold: float,
    output_path: str,
    image_size: Tuple[int, int] = (800, 400)
) -> None:
    """
    Create a plot showing SSIM scores vs Z-height.

    Args:
        z_heights: List of Z-heights
        ssim_scores: List of corresponding SSIM scores
        threshold: Detection threshold
        output_path: Path to save plot
        image_size: Plot size (width, height)

    Example:
        >>> draw_detection_timeline(
        ...     [0, 45, 91, 138],
        ...     [0.98, 0.96, 0.85, 0.92],
        ...     0.90,
        ...     '/path/to/timeline.png'
        ... )
    """
    try:
        width, height = image_size

        # Create blank white canvas
        canvas = np.ones((height, width, 3), dtype=np.uint8) * 255

        if not z_heights or not ssim_scores:
            cv2.imwrite(output_path, canvas)
            return

        # Calculate scaling
        z_min, z_max = 0, max(z_heights)
        z_range = z_max - z_min if z_max > z_min else 1

        margin = 50
        plot_width = width - 2 * margin
        plot_height = height - 2 * margin

        # Draw axes
        cv2.line(canvas, (margin, height - margin),
                (width - margin, height - margin), (0, 0, 0), 2)  # X-axis
        cv2.line(canvas, (margin, margin),
                (margin, height - margin), (0, 0, 0), 2)  # Y-axis

        # Draw threshold line
        threshold_y = int(height - margin - (threshold * plot_height))
        cv2.line(canvas, (margin, threshold_y),
                (width - margin, threshold_y), (0, 0, 255), 1)
        cv2.putText(canvas, f"Threshold: {threshold:.2f}",
                   (width - margin - 150, threshold_y - 5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

        # Plot points and lines
        points = []
        for z, ssim in zip(z_heights, ssim_scores):
            x = int(margin + (z / z_range) * plot_width)
            y = int(height - margin - (ssim * plot_height))
            points.append((x, y))

        # Draw lines between points
        for i in range(len(points) - 1):
            cv2.line(canvas, points[i], points[i + 1], (0, 128, 255), 2)

        # Draw points
        for (x, y), ssim in zip(points, ssim_scores):
            color = (0, 255, 0) if ssim >= threshold else (255, 0, 0)
            cv2.circle(canvas, (x, y), 5, color, -1)

        # Add labels
        cv2.putText(canvas, "Z-height (mm)", (width // 2 - 50, height - 10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        cv2.putText(canvas, "SSIM", (10, margin - 10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Save
        cv2.imwrite(output_path, canvas)

        logger.info(f"Saved detection timeline: {output_path}")

    except Exception as e:
        logger.error(f"Error creating detection timeline: {str(e)}")
        raise


def save_debug_images(
    reference: np.ndarray,
    current: np.ndarray,
    difference_map: np.ndarray,
    regions: List[Dict[str, Any]],
    output_dir: str,
    prefix: str = ""
) -> Dict[str, str]:
    """
    Save individual debug images for detailed analysis.

    Args:
        reference: Reference image
        current: Current image
        difference_map: Difference map
        regions: Detected regions
        output_dir: Output directory
        prefix: Filename prefix

    Returns:
        Dictionary mapping image type to saved path

    Example:
        >>> paths = save_debug_images(ref, cur, diff, regions, '/data/debug')
        >>> print(paths['heatmap'])
    """
    try:
        from pathlib import Path
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        saved_paths = {}

        # Save reference
        ref_path = output_path / f"{prefix}reference.png"
        cv2.imwrite(str(ref_path), reference)
        saved_paths['reference'] = str(ref_path)

        # Save current
        cur_path = output_path / f"{prefix}current.png"
        cv2.imwrite(str(cur_path), current)
        saved_paths['current'] = str(cur_path)

        # Save heatmap
        heatmap_path = output_path / f"{prefix}heatmap.png"
        create_ssim_heatmap(difference_map, str(heatmap_path))
        saved_paths['heatmap'] = str(heatmap_path)

        # Save regions overlay
        if regions:
            overlay_path = output_path / f"{prefix}regions.png"
            create_region_overlay(current, regions, str(overlay_path))
            saved_paths['regions'] = str(overlay_path)

        logger.info(f"Saved {len(saved_paths)} debug images to {output_dir}")

        return saved_paths

    except Exception as e:
        logger.error(f"Error saving debug images: {str(e)}")
        return {}
