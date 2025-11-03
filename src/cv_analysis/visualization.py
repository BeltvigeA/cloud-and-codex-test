"""
Visualization Module

Creates debug visualizations for false positive analysis and system tuning.

Generates annotated comparison images showing:
- Reference and current images side-by-side
- Difference map with color coding
- Detected regions highlighted with bounding boxes
- SSIM score and detection metadata
"""

import logging
from typing import List, Dict, Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def create_comparison_visualization(
    reference: np.ndarray,
    current: np.ndarray,
    difference_map: np.ndarray,
    regions: List[Dict[str, Any]],
    ssim_score: float,
    output_path: str,
    threshold: Optional[float] = None,
    show_metadata: bool = True
) -> None:
    """
    Create annotated comparison image for debugging.

    Generates a visualization with three panels:
    1. Reference calibration image
    2. Current plate image with detected regions
    3. Difference map (heatmap)

    Args:
        reference: Reference calibration image
        current: Current plate image
        difference_map: SSIM difference map
        regions: List of detected regions
        ssim_score: SSIM similarity score
        output_path: Path to save visualization
        threshold: Detection threshold (optional, for display)
        show_metadata: Whether to overlay metadata text

    Example:
        >>> create_comparison_visualization(
        ...     reference=ref_img,
        ...     current=current_img,
        ...     difference_map=diff_map,
        ...     regions=detected_regions,
        ...     ssim_score=0.923,
        ...     output_path="/output/comparison.png",
        ...     threshold=0.92
        ... )
    """
    try:
        # Convert grayscale images to BGR for color annotations
        ref_bgr = cv2.cvtColor(reference, cv2.COLOR_GRAY2BGR)
        current_bgr = cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)

        # Draw bounding boxes on current image
        current_annotated = draw_regions(current_bgr.copy(), regions)

        # Create difference heatmap
        diff_heatmap = create_heatmap(difference_map)

        # Stack images horizontally
        visualization = np.hstack([ref_bgr, current_annotated, diff_heatmap])

        # Add metadata overlay if requested
        if show_metadata:
            visualization = add_metadata_overlay(
                image=visualization,
                ssim_score=ssim_score,
                threshold=threshold,
                num_regions=len(regions)
            )

        # Save visualization
        cv2.imwrite(output_path, visualization)
        logger.info(f"Saved comparison visualization to {output_path}")

    except Exception as e:
        logger.error(f"Error creating visualization: {e}")
        raise


def draw_regions(
    image: np.ndarray,
    regions: List[Dict[str, Any]],
    color: tuple = (0, 255, 0),
    thickness: int = 2
) -> np.ndarray:
    """
    Draw bounding boxes around detected regions.

    Args:
        image: BGR image to annotate
        regions: List of region dictionaries with 'bbox' key
        color: Box color in BGR (default: green)
        thickness: Line thickness in pixels

    Returns:
        Annotated image

    Example:
        >>> annotated = draw_regions(image, regions, color=(0, 0, 255), thickness=3)
    """
    annotated = image.copy()

    for i, region in enumerate(regions):
        x, y, w, h = region['bbox']

        # Draw bounding box
        cv2.rectangle(
            annotated,
            (x, y),
            (x + w, y + h),
            color,
            thickness
        )

        # Draw region number
        label = f"#{i+1}"
        label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        label_y = max(y - 5, label_size[1] + 5)

        cv2.putText(
            annotated,
            label,
            (x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1
        )

        # Draw centroid
        cx, cy = region['centroid']
        cv2.circle(annotated, (cx, cy), 3, color, -1)

    return annotated


def create_heatmap(
    difference_map: np.ndarray,
    colormap: int = cv2.COLORMAP_JET
) -> np.ndarray:
    """
    Convert difference map to color heatmap.

    Args:
        difference_map: Normalized difference map (0-1 range)
        colormap: OpenCV colormap ID (default: COLORMAP_JET)

    Returns:
        BGR heatmap image

    Example:
        >>> heatmap = create_heatmap(diff_map, colormap=cv2.COLORMAP_HOT)
    """
    # Convert to uint8
    diff_uint8 = (difference_map * 255).astype(np.uint8)

    # Apply colormap
    heatmap = cv2.applyColorMap(diff_uint8, colormap)

    return heatmap


def add_metadata_overlay(
    image: np.ndarray,
    ssim_score: float,
    threshold: Optional[float] = None,
    num_regions: int = 0
) -> np.ndarray:
    """
    Add metadata text overlay to visualization.

    Args:
        image: BGR image to annotate
        ssim_score: SSIM similarity score
        threshold: Detection threshold (optional)
        num_regions: Number of detected regions

    Returns:
        Annotated image

    Example:
        >>> annotated = add_metadata_overlay(img, ssim_score=0.923, threshold=0.92, num_regions=2)
    """
    annotated = image.copy()
    height = annotated.shape[0]

    # Create semi-transparent background for text
    overlay = annotated.copy()
    cv2.rectangle(overlay, (0, 0), (annotated.shape[1], 80), (0, 0, 0), -1)
    annotated = cv2.addWeighted(annotated, 0.7, overlay, 0.3, 0)

    # Prepare text lines
    lines = [
        f"SSIM: {ssim_score:.4f}",
        f"Threshold: {threshold:.4f}" if threshold else None,
        f"Regions: {num_regions}"
    ]
    lines = [line for line in lines if line is not None]

    # Determine status color
    if threshold:
        is_clean = ssim_score >= threshold
        status_color = (0, 255, 0) if is_clean else (0, 0, 255)  # Green if clean, red if not
        status_text = "CLEAN" if is_clean else "OBJECT DETECTED"
    else:
        status_color = (255, 255, 255)
        status_text = ""

    # Draw title
    if status_text:
        cv2.putText(
            annotated,
            status_text,
            (10, 30),
            cv2.FONT_HERSHEY_BOLD,
            0.8,
            status_color,
            2
        )

    # Draw metadata lines
    y_offset = 55
    for line in lines:
        cv2.putText(
            annotated,
            line,
            (10, y_offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1
        )
        y_offset += 20

    # Add panel labels at bottom
    panel_width = annotated.shape[1] // 3
    labels = ["REFERENCE", "CURRENT", "DIFFERENCE"]
    for i, label in enumerate(labels):
        x_pos = i * panel_width + panel_width // 2 - 50
        cv2.putText(
            annotated,
            label,
            (x_pos, height - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1
        )

    return annotated


def create_region_detail_visualization(
    image: np.ndarray,
    region: Dict[str, Any],
    output_path: str,
    padding: int = 20
) -> None:
    """
    Create detailed visualization of a single region.

    Extracts and saves a zoomed-in view of a detected region.

    Args:
        image: Source image (BGR)
        region: Region dictionary with 'bbox' key
        output_path: Path to save region image
        padding: Pixels to include around region

    Example:
        >>> for i, region in enumerate(regions):
        ...     create_region_detail_visualization(
        ...         current_img,
        ...         region,
        ...         f"/output/region_{i}.png"
        ...     )
    """
    try:
        x, y, w, h = region['bbox']

        # Add padding
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(image.shape[1], x + w + padding)
        y2 = min(image.shape[0], y + h + padding)

        # Extract region
        region_img = image[y1:y2, x1:x2].copy()

        # Draw bounding box (adjusted for cropped coordinates)
        box_x1 = x - x1
        box_y1 = y - y1
        box_x2 = box_x1 + w
        box_y2 = box_y1 + h

        cv2.rectangle(
            region_img,
            (box_x1, box_y1),
            (box_x2, box_y2),
            (0, 255, 0),
            2
        )

        # Add region metadata
        metadata_text = f"Area: {region['area']} | AR: {region['aspect_ratio']:.2f}"
        cv2.putText(
            region_img,
            metadata_text,
            (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1
        )

        # Save
        cv2.imwrite(output_path, region_img)
        logger.debug(f"Saved region detail to {output_path}")

    except Exception as e:
        logger.error(f"Error creating region detail visualization: {e}")


def create_grid_visualization(
    images: List[np.ndarray],
    labels: List[str],
    output_path: str,
    cols: int = 3
) -> None:
    """
    Create a grid visualization of multiple images.

    Args:
        images: List of images (all same size, BGR or grayscale)
        labels: List of labels for each image
        output_path: Path to save grid
        cols: Number of columns in grid

    Example:
        >>> checkpoints = [checkpoint_0, checkpoint_33, checkpoint_66, checkpoint_100]
        >>> labels = ["0%", "33%", "66%", "100%"]
        >>> create_grid_visualization(checkpoints, labels, "/output/grid.png", cols=2)
    """
    try:
        if len(images) != len(labels):
            raise ValueError("Number of images must match number of labels")

        if not images:
            raise ValueError("No images provided")

        # Ensure all images are BGR
        bgr_images = []
        for img in images:
            if len(img.shape) == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            bgr_images.append(img)

        # Calculate grid dimensions
        rows = (len(bgr_images) + cols - 1) // cols
        img_h, img_w = bgr_images[0].shape[:2]

        # Create blank canvas
        canvas = np.zeros((rows * img_h, cols * img_w, 3), dtype=np.uint8)

        # Place images in grid
        for i, (img, label) in enumerate(zip(bgr_images, labels)):
            row = i // cols
            col = i % cols

            y1 = row * img_h
            x1 = col * img_w
            y2 = y1 + img_h
            x2 = x1 + img_w

            canvas[y1:y2, x1:x2] = img

            # Add label
            cv2.putText(
                canvas,
                label,
                (x1 + 10, y1 + 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2
            )

        # Save
        cv2.imwrite(output_path, canvas)
        logger.info(f"Saved grid visualization to {output_path}")

    except Exception as e:
        logger.error(f"Error creating grid visualization: {e}")
        raise


def create_detection_summary_image(
    results: List[Dict[str, Any]],
    output_path: str,
    width: int = 800,
    height: int = 600
) -> None:
    """
    Create a summary visualization of multiple detection results.

    Shows a bar chart of SSIM scores across multiple detections.

    Args:
        results: List of detection result dictionaries
        output_path: Path to save summary image
        width: Image width in pixels
        height: Image height in pixels

    Example:
        >>> results = batch_detect(images, printer_id, z_heights, calibration_dir)
        >>> create_detection_summary_image(results, "/output/summary.png")
    """
    try:
        # Create blank canvas
        canvas = np.ones((height, width, 3), dtype=np.uint8) * 255

        if not results:
            logger.warning("No results to visualize")
            cv2.imwrite(output_path, canvas)
            return

        # Extract SSIM scores and thresholds
        ssim_scores = [r['ssim_score'] for r in results]
        thresholds = [r.get('threshold_used', 0.9) for r in results]

        # Calculate bar dimensions
        margin = 50
        bar_width = (width - 2 * margin) // len(results)
        max_bar_height = height - 2 * margin

        # Draw bars
        for i, (score, threshold) in enumerate(zip(ssim_scores, thresholds)):
            x = margin + i * bar_width
            bar_height = int(score * max_bar_height)
            y = height - margin - bar_height

            # Bar color (green if clean, red if not)
            is_clean = score >= threshold
            color = (0, 255, 0) if is_clean else (0, 0, 255)

            # Draw bar
            cv2.rectangle(
                canvas,
                (x + 5, y),
                (x + bar_width - 5, height - margin),
                color,
                -1
            )

            # Draw threshold line
            threshold_y = height - margin - int(threshold * max_bar_height)
            cv2.line(
                canvas,
                (x, threshold_y),
                (x + bar_width, threshold_y),
                (0, 0, 0),
                2
            )

            # Draw score text
            cv2.putText(
                canvas,
                f"{score:.3f}",
                (x + 5, y - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.3,
                (0, 0, 0),
                1
            )

        # Add title
        cv2.putText(
            canvas,
            "Detection Summary - SSIM Scores",
            (width // 2 - 150, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 0),
            2
        )

        # Save
        cv2.imwrite(output_path, canvas)
        logger.info(f"Saved detection summary to {output_path}")

    except Exception as e:
        logger.error(f"Error creating detection summary: {e}")
