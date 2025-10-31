"""
Perceptual Hashing Module for Quick Image Pre-filtering

This module implements difference hashing (dhash) for rapid comparison of
build plate images. Perceptual hashing creates a compact fingerprint of an
image that is robust to minor lighting changes but sensitive to structural
differences.

Key Features:
- Difference hash (dhash) calculation
- Hamming distance comparison
- Fast pre-filtering before expensive SSIM computation
- 256-bit hash for high accuracy
"""

import logging
from typing import Optional

import cv2
import numpy as np
import imagehash
from PIL import Image

logger = logging.getLogger(__name__)


def calculate_perceptual_hash(
    image: np.ndarray,
    hash_size: int = 16
) -> str:
    """
    Calculate dhash (difference hash) for image comparison.

    The difference hash algorithm:
    1. Resize image to (hash_size+1, hash_size)
    2. Compare each pixel to its horizontal neighbor
    3. Create binary hash from comparisons
    4. Convert to hexadecimal string

    This creates a 256-bit hash (for hash_size=16) that captures
    the structural gradient information of the image.

    Args:
        image: Preprocessed grayscale numpy array
        hash_size: Hash dimension (default 16 for 256-bit hash)

    Returns:
        Hexadecimal hash string (64 characters for 256-bit hash)

    Raises:
        ValueError: If image is invalid or hash_size is too small

    Example:
        >>> from cv_analysis.preprocessing import preprocess_image
        >>> img = preprocess_image(Image.open('plate.png'))
        >>> hash_str = calculate_perceptual_hash(img)
        >>> print(len(hash_str))  # 64 characters
        64
    """
    try:
        if image is None or image.size == 0:
            raise ValueError("Input image is empty or invalid")

        if hash_size < 2:
            raise ValueError("hash_size must be at least 2")

        # Convert numpy array to PIL Image for imagehash library
        pil_image = Image.fromarray(image)

        # Calculate difference hash
        hash_obj = imagehash.dhash(pil_image, hash_size=hash_size)

        # Convert to string
        hash_str = str(hash_obj)

        logger.debug(f"Calculated perceptual hash: {hash_str[:16]}... (truncated)")

        return hash_str

    except Exception as e:
        logger.error(f"Error calculating perceptual hash: {str(e)}")
        raise


def compare_hashes(hash1: str, hash2: str) -> int:
    """
    Calculate Hamming distance between two perceptual hashes.

    The Hamming distance is the number of bit positions where the
    two hashes differ. A distance of 0 means identical hashes,
    while larger distances indicate greater differences.

    Typical interpretation:
    - 0-5: Essentially identical (same plate state)
    - 6-15: Very similar (minor lighting/positioning changes)
    - 16-30: Similar structure, some differences
    - >30: Significant differences (likely different plate state)

    Args:
        hash1: First hash string
        hash2: Second hash string

    Returns:
        Hamming distance (number of differing bits)

    Raises:
        ValueError: If hash strings have different lengths

    Example:
        >>> hash1 = calculate_perceptual_hash(clean_plate_img)
        >>> hash2 = calculate_perceptual_hash(current_img)
        >>> distance = compare_hashes(hash1, hash2)
        >>> if distance <= 5:
        ...     print("Images are essentially identical")
    """
    try:
        if len(hash1) != len(hash2):
            raise ValueError(
                f"Hash length mismatch: {len(hash1)} vs {len(hash2)}"
            )

        # Convert hex strings to imagehash objects
        hash_obj1 = imagehash.hex_to_hash(hash1)
        hash_obj2 = imagehash.hex_to_hash(hash2)

        # Calculate Hamming distance
        distance = hash_obj1 - hash_obj2  # imagehash overloads - operator

        logger.debug(f"Hash comparison: distance={distance}")

        return int(distance)

    except Exception as e:
        logger.error(f"Error comparing hashes: {str(e)}")
        raise


def is_hash_match(
    hash1: str,
    hash2: str,
    threshold: int = 5
) -> bool:
    """
    Determine if two hashes represent the same plate state.

    This is a convenience function that combines hash comparison
    with threshold evaluation.

    Args:
        hash1: First hash string
        hash2: Second hash string
        threshold: Maximum Hamming distance for a match (default 5)

    Returns:
        True if hashes match within threshold, False otherwise

    Example:
        >>> if is_hash_match(ref_hash, current_hash):
        ...     print("Quick match - plate appears clean")
        ...     # Skip expensive SSIM computation
    """
    distance = compare_hashes(hash1, hash2)
    is_match = distance <= threshold

    logger.debug(
        f"Hash match check: distance={distance}, "
        f"threshold={threshold}, match={is_match}"
    )

    return is_match


def calculate_average_hash(
    image: np.ndarray,
    hash_size: int = 16
) -> str:
    """
    Calculate average hash (ahash) as an alternative to dhash.

    Average hash is faster but less precise than difference hash.
    Use this for very quick comparisons where precision is less critical.

    Args:
        image: Preprocessed grayscale numpy array
        hash_size: Hash dimension

    Returns:
        Hexadecimal hash string

    Note:
        This is provided as an alternative, but dhash (calculate_perceptual_hash)
        is recommended for plate verification due to better gradient sensitivity.
    """
    try:
        if image is None or image.size == 0:
            raise ValueError("Input image is empty or invalid")

        pil_image = Image.fromarray(image)
        hash_obj = imagehash.average_hash(pil_image, hash_size=hash_size)

        return str(hash_obj)

    except Exception as e:
        logger.error(f"Error calculating average hash: {str(e)}")
        raise


def hash_from_file(
    image_path: str,
    hash_size: int = 16,
    use_dhash: bool = True
) -> str:
    """
    Calculate perceptual hash directly from image file.

    This is a convenience function that combines image loading
    and hash calculation. Note that it does NOT apply preprocessing,
    so results may vary with lighting conditions.

    Args:
        image_path: Path to image file
        hash_size: Hash dimension
        use_dhash: If True, use dhash; if False, use average hash

    Returns:
        Hexadecimal hash string

    Example:
        >>> hash_str = hash_from_file('/path/to/image.png')
    """
    try:
        pil_image = Image.open(image_path).convert('L')  # Convert to grayscale

        if use_dhash:
            hash_obj = imagehash.dhash(pil_image, hash_size=hash_size)
        else:
            hash_obj = imagehash.average_hash(pil_image, hash_size=hash_size)

        return str(hash_obj)

    except Exception as e:
        logger.error(f"Error calculating hash from file {image_path}: {str(e)}")
        raise


def batch_calculate_hashes(
    images: list[np.ndarray],
    hash_size: int = 16
) -> list[str]:
    """
    Calculate perceptual hashes for multiple images.

    Args:
        images: List of preprocessed grayscale numpy arrays
        hash_size: Hash dimension

    Returns:
        List of hexadecimal hash strings

    Example:
        >>> calibration_images = [preprocess_image(img) for img in raw_images]
        >>> hashes = batch_calculate_hashes(calibration_images)
    """
    hashes = []
    for i, image in enumerate(images):
        try:
            hash_str = calculate_perceptual_hash(image, hash_size)
            hashes.append(hash_str)
        except Exception as e:
            logger.warning(f"Failed to hash image {i}: {str(e)}")
            hashes.append(None)

    return hashes


def find_most_similar_hash(
    target_hash: str,
    reference_hashes: dict[str, str],
    max_distance: Optional[int] = None
) -> Optional[tuple[str, int]]:
    """
    Find the most similar hash from a set of references.

    Args:
        target_hash: Hash to match
        reference_hashes: Dictionary of {identifier: hash_string}
        max_distance: Maximum allowed Hamming distance (None = no limit)

    Returns:
        Tuple of (best_match_id, distance) or None if no match within threshold

    Example:
        >>> calibration_hashes = {
        ...     'Z000mm': hash1,
        ...     'Z005mm': hash2,
        ...     'Z010mm': hash3
        ... }
        >>> match = find_most_similar_hash(current_hash, calibration_hashes)
        >>> if match:
        ...     print(f"Best match: {match[0]} with distance {match[1]}")
    """
    best_match = None
    best_distance = float('inf')

    for ref_id, ref_hash in reference_hashes.items():
        try:
            distance = compare_hashes(target_hash, ref_hash)

            if distance < best_distance:
                if max_distance is None or distance <= max_distance:
                    best_distance = distance
                    best_match = ref_id

        except Exception as e:
            logger.warning(f"Failed to compare with {ref_id}: {str(e)}")
            continue

    if best_match is not None:
        return (best_match, int(best_distance))

    return None
