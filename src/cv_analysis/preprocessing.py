"""
Image Preprocessing Module for Build Plate Verification

This module provides image preprocessing utilities to normalize build plate images
for consistent comparison against calibration references.

Key Features:
- Grayscale conversion
- CLAHE (Contrast Limited Adaptive Histogram Equalization) for lighting normalization
- Background subtraction using Gaussian blur
- Pixel value normalization
- Resolution downsampling for performance optimization
"""

import logging
from typing import Union, Tuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def preprocess_image(
    image: Union[np.ndarray, Image.Image],
    target_size: Tuple[int, int] = (960, 540),
    clahe_clip_limit: float = 2.0,
    clahe_tile_grid: Tuple[int, int] = (8, 8),
    gaussian_blur_kernel: Tuple[int, int] = (31, 31)
) -> np.ndarray:
    """
    Preprocess build plate image for consistent comparison.

    This function applies a series of transformations to normalize images:
    1. Convert to grayscale (if RGB input)
    2. Resize to target dimensions
    3. Apply CLAHE for lighting normalization
    4. Apply background subtraction
    5. Normalize pixel values to 0-255 range

    Args:
        image: Input image (PIL Image or numpy array, RGB or grayscale)
        target_size: Target resolution (width, height) for downsampling
        clahe_clip_limit: CLAHE contrast limit (higher = more contrast)
        clahe_tile_grid: CLAHE tile grid size (width, height)
        gaussian_blur_kernel: Gaussian blur kernel size for background estimation

    Returns:
        Preprocessed grayscale numpy array at target resolution

    Raises:
        ValueError: If input image is invalid or empty

    Example:
        >>> from PIL import Image
        >>> img = Image.open('build_plate.png')
        >>> processed = preprocess_image(img)
        >>> print(processed.shape)
        (540, 960)
    """
    try:
        # Convert PIL Image to numpy array if needed
        if isinstance(image, Image.Image):
            image = np.array(image)

        # Validate input
        if image is None or image.size == 0:
            raise ValueError("Input image is empty or invalid")

        # Convert to grayscale if RGB
        if len(image.shape) == 3:
            if image.shape[2] == 4:  # RGBA
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
            elif image.shape[2] == 3:  # RGB
                image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        elif len(image.shape) != 2:
            raise ValueError(f"Unexpected image shape: {image.shape}")

        # Resize to target resolution
        if image.shape[:2][::-1] != target_size:  # OpenCV uses (width, height)
            image = cv2.resize(image, target_size, interpolation=cv2.INTER_AREA)

        # Apply CLAHE for lighting normalization
        clahe = cv2.createCLAHE(
            clipLimit=clahe_clip_limit,
            tileGridSize=clahe_tile_grid
        )
        image = clahe.apply(image)

        # Apply background subtraction for uneven lighting
        # Create a blurred version representing the background
        background = cv2.GaussianBlur(image, gaussian_blur_kernel, 0)

        # Subtract background and add offset to maintain visibility
        image_float = image.astype(np.float32)
        background_float = background.astype(np.float32)
        corrected = image_float - background_float + 128

        # Normalize to 0-255 range
        corrected = np.clip(corrected, 0, 255)
        image = corrected.astype(np.uint8)

        logger.debug(
            f"Preprocessed image: shape={image.shape}, "
            f"dtype={image.dtype}, range=[{image.min()}, {image.max()}]"
        )

        return image

    except Exception as e:
        logger.error(f"Error preprocessing image: {str(e)}")
        raise


def validate_image_pair(
    reference: np.ndarray,
    current: np.ndarray
) -> bool:
    """
    Validate that two images are compatible for comparison.

    Args:
        reference: Reference calibration image
        current: Current image to compare

    Returns:
        True if images are compatible, False otherwise

    Example:
        >>> ref = preprocess_image(Image.open('calibration.png'))
        >>> cur = preprocess_image(Image.open('current.png'))
        >>> if validate_image_pair(ref, cur):
        ...     # Proceed with comparison
    """
    if reference.shape != current.shape:
        logger.warning(
            f"Image shape mismatch: reference={reference.shape}, "
            f"current={current.shape}"
        )
        return False

    if reference.dtype != current.dtype:
        logger.warning(
            f"Image dtype mismatch: reference={reference.dtype}, "
            f"current={current.dtype}"
        )
        return False

    return True


def normalize_image_histogram(image: np.ndarray) -> np.ndarray:
    """
    Normalize image histogram for better comparison.

    This is an alternative normalization method that can be used
    instead of or in addition to CLAHE.

    Args:
        image: Input grayscale image

    Returns:
        Histogram-normalized image
    """
    return cv2.equalizeHist(image)


def calculate_image_statistics(image: np.ndarray) -> dict:
    """
    Calculate statistical properties of an image.

    Useful for debugging and quality assessment.

    Args:
        image: Input grayscale image

    Returns:
        Dictionary containing mean, std, min, max, and histogram

    Example:
        >>> stats = calculate_image_statistics(processed_image)
        >>> print(f"Mean brightness: {stats['mean']:.2f}")
    """
    return {
        'mean': float(np.mean(image)),
        'std': float(np.std(image)),
        'min': int(np.min(image)),
        'max': int(np.max(image)),
        'median': float(np.median(image)),
        'histogram': cv2.calcHist([image], [0], None, [256], [0, 256]).flatten().tolist()
    }
