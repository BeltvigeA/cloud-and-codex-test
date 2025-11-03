"""
Image Preprocessing Module

Normalizes build plate images for consistent comparison by:
- Converting to grayscale
- Applying CLAHE for lighting normalization
- Background subtraction for uneven lighting
- Downsampling for performance optimization

Performance target: <10ms per image
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
    gaussian_kernel: Tuple[int, int] = (31, 31)
) -> np.ndarray:
    """
    Preprocess build plate image for consistent comparison.

    Applies a multi-stage preprocessing pipeline:
    1. Convert to grayscale (if needed)
    2. Resize to target dimensions
    3. Apply CLAHE for contrast enhancement
    4. Background subtraction using Gaussian blur
    5. Normalize to 0-255 range

    Args:
        image: Input image (PIL Image or numpy array, RGB or grayscale)
        target_size: Target dimensions (width, height) for downsampling
        clahe_clip_limit: Contrast limit for CLAHE algorithm
        clahe_tile_grid: Tile grid size for CLAHE (width, height)
        gaussian_kernel: Kernel size for Gaussian blur in background subtraction

    Returns:
        Preprocessed grayscale numpy array of shape (height, width)

    Raises:
        ValueError: If image is invalid or empty
        TypeError: If image type is not supported

    Example:
        >>> from PIL import Image
        >>> img = Image.open("build_plate.jpg")
        >>> processed = preprocess_image(img)
        >>> processed.shape
        (540, 960)
        >>> processed.dtype
        dtype('uint8')
    """
    try:
        # Convert PIL Image to numpy array if needed
        if isinstance(image, Image.Image):
            image = np.array(image)
        elif not isinstance(image, np.ndarray):
            raise TypeError(f"Image must be PIL Image or numpy array, got {type(image)}")

        if image.size == 0:
            raise ValueError("Input image is empty")

        # Step 1: Convert to grayscale if needed
        if len(image.shape) == 3:
            if image.shape[2] == 4:  # RGBA
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
            elif image.shape[2] == 3:  # RGB
                image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        elif len(image.shape) != 2:
            raise ValueError(f"Unexpected image shape: {image.shape}")

        # Step 2: Resize to target dimensions for performance
        if image.shape[:2][::-1] != target_size:  # OpenCV uses (width, height)
            image = cv2.resize(image, target_size, interpolation=cv2.INTER_AREA)

        # Step 3: Apply CLAHE for lighting normalization
        clahe = cv2.createCLAHE(
            clipLimit=clahe_clip_limit,
            tileGridSize=clahe_tile_grid
        )
        image = clahe.apply(image)

        # Step 4: Background subtraction using Gaussian blur
        # This helps normalize uneven lighting across the build plate
        blurred = cv2.GaussianBlur(image, gaussian_kernel, 0)

        # Subtract background and add mean to preserve brightness
        background_subtracted = cv2.subtract(image, blurred)
        mean_value = np.mean(image)
        image = cv2.add(background_subtracted, int(mean_value))

        # Step 5: Normalize to full 0-255 range
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)

        logger.debug(
            f"Preprocessed image: shape={image.shape}, "
            f"dtype={image.dtype}, range=[{image.min()}, {image.max()}]"
        )

        return image.astype(np.uint8)

    except Exception as e:
        logger.error(f"Error preprocessing image: {e}")
        raise


def load_and_preprocess(
    image_path: str,
    **preprocessing_kwargs
) -> np.ndarray:
    """
    Load image from file and preprocess it.

    Convenience function that combines image loading with preprocessing.

    Args:
        image_path: Path to image file
        **preprocessing_kwargs: Additional arguments for preprocess_image()

    Returns:
        Preprocessed grayscale numpy array

    Raises:
        FileNotFoundError: If image file doesn't exist
        IOError: If image cannot be loaded

    Example:
        >>> processed = load_and_preprocess("/path/to/plate.jpg")
        >>> processed.shape
        (540, 960)
    """
    try:
        image = Image.open(image_path)
        return preprocess_image(image, **preprocessing_kwargs)
    except FileNotFoundError:
        logger.error(f"Image file not found: {image_path}")
        raise
    except Exception as e:
        logger.error(f"Error loading image from {image_path}: {e}")
        raise IOError(f"Failed to load image: {e}")


def batch_preprocess(
    image_paths: list,
    **preprocessing_kwargs
) -> list:
    """
    Preprocess multiple images in batch.

    Args:
        image_paths: List of paths to image files
        **preprocessing_kwargs: Additional arguments for preprocess_image()

    Returns:
        List of preprocessed numpy arrays

    Example:
        >>> paths = ["plate1.jpg", "plate2.jpg", "plate3.jpg"]
        >>> processed = batch_preprocess(paths)
        >>> len(processed)
        3
    """
    results = []
    for path in image_paths:
        try:
            processed = load_and_preprocess(path, **preprocessing_kwargs)
            results.append(processed)
        except Exception as e:
            logger.warning(f"Failed to preprocess {path}: {e}")
            results.append(None)

    return results


def validate_preprocessed_image(image: np.ndarray) -> bool:
    """
    Validate that an image has been properly preprocessed.

    Args:
        image: Numpy array to validate

    Returns:
        True if image is valid preprocessed format

    Example:
        >>> img = preprocess_image(raw_image)
        >>> validate_preprocessed_image(img)
        True
    """
    if not isinstance(image, np.ndarray):
        return False

    if len(image.shape) != 2:  # Must be 2D grayscale
        return False

    if image.dtype != np.uint8:  # Must be uint8
        return False

    if image.min() < 0 or image.max() > 255:  # Must be in valid range
        return False

    return True
