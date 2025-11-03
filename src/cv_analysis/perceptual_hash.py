"""
Perceptual Hashing Module

Implements difference hash (dhash) for fast image comparison pre-filtering.
Dhash is robust to small changes and lighting variations while being extremely fast.

Performance target: <3ms per hash calculation
"""

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def calculate_perceptual_hash(
    image: np.ndarray,
    hash_size: int = 16
) -> str:
    """
    Calculate difference hash (dhash) for image comparison.

    Dhash works by:
    1. Resizing to (hash_size + 1) x hash_size
    2. Computing horizontal gradient (difference between adjacent pixels)
    3. Converting gradient to binary (1 if left > right, else 0)
    4. Encoding as hexadecimal string

    Args:
        image: Preprocessed grayscale numpy array
        hash_size: Hash dimensions (default 16 for 256-bit hash)

    Returns:
        Hexadecimal string representation of the hash

    Raises:
        ValueError: If image is invalid

    Example:
        >>> img = preprocess_image(raw_image)
        >>> hash_str = calculate_perceptual_hash(img)
        >>> len(hash_str)
        64  # 256 bits = 64 hex characters
        >>> hash_str
        'a3f5d8c2e1b4f7a9...'
    """
    try:
        if image is None or image.size == 0:
            raise ValueError("Image is empty")

        if len(image.shape) != 2:
            raise ValueError(f"Image must be 2D grayscale, got shape {image.shape}")

        # Resize to (hash_size + 1) x hash_size for horizontal gradient
        resized = cv2.resize(
            image,
            (hash_size + 1, hash_size),
            interpolation=cv2.INTER_AREA
        )

        # Calculate horizontal gradient (difference between adjacent pixels)
        # This captures the essential structure while being invariant to absolute brightness
        diff = resized[:, 1:] > resized[:, :-1]

        # Convert boolean array to binary string, then to hex
        # This creates a compact representation of the image structure
        hash_bytes = np.packbits(diff.flatten())
        hash_hex = ''.join(f'{byte:02x}' for byte in hash_bytes)

        logger.debug(f"Calculated dhash: {hash_hex[:16]}... (length: {len(hash_hex)})")

        return hash_hex

    except Exception as e:
        logger.error(f"Error calculating perceptual hash: {e}")
        raise


def compare_hashes(hash1: str, hash2: str) -> int:
    """
    Calculate Hamming distance between two hashes.

    Hamming distance is the number of bit positions where the hashes differ.
    Lower distance = more similar images.

    Args:
        hash1: First hash (hex string)
        hash2: Second hash (hex string)

    Returns:
        Hamming distance (0 = identical, higher = more different)

    Raises:
        ValueError: If hashes have different lengths

    Example:
        >>> hash1 = calculate_perceptual_hash(img1)
        >>> hash2 = calculate_perceptual_hash(img2)
        >>> distance = compare_hashes(hash1, hash2)
        >>> distance
        5  # Only 5 bits different out of 256
        >>> similarity_percent = 100 * (1 - distance / 256)
        >>> similarity_percent
        98.05
    """
    try:
        if len(hash1) != len(hash2):
            raise ValueError(
                f"Hash length mismatch: {len(hash1)} vs {len(hash2)}"
            )

        # Convert hex strings to integers and XOR them
        # XOR gives 1 for differing bits, 0 for matching bits
        xor_result = int(hash1, 16) ^ int(hash2, 16)

        # Count the number of 1s (differing bits)
        hamming_distance = bin(xor_result).count('1')

        logger.debug(
            f"Hamming distance: {hamming_distance} "
            f"({100 * (1 - hamming_distance / (len(hash1) * 4)):.1f}% similar)"
        )

        return hamming_distance

    except Exception as e:
        logger.error(f"Error comparing hashes: {e}")
        raise


def is_hash_match(
    hash1: str,
    hash2: str,
    threshold: int = 5
) -> bool:
    """
    Check if two hashes match within a threshold.

    Args:
        hash1: First hash
        hash2: Second hash
        threshold: Maximum Hamming distance for a match (default 5)

    Returns:
        True if hashes match (distance <= threshold)

    Example:
        >>> if is_hash_match(current_hash, calibration_hash, threshold=5):
        ...     print("Images are nearly identical - plate is clean")
    """
    distance = compare_hashes(hash1, hash2)
    return distance <= threshold


def calculate_hash_similarity(hash1: str, hash2: str) -> float:
    """
    Calculate similarity score between two hashes as a percentage.

    Args:
        hash1: First hash
        hash2: Second hash

    Returns:
        Similarity score from 0.0 (completely different) to 1.0 (identical)

    Example:
        >>> similarity = calculate_hash_similarity(hash1, hash2)
        >>> similarity
        0.98  # 98% similar
    """
    if not hash1 or not hash2:
        return 0.0

    try:
        distance = compare_hashes(hash1, hash2)
        max_distance = len(hash1) * 4  # Each hex char = 4 bits
        similarity = 1.0 - (distance / max_distance)
        return max(0.0, min(1.0, similarity))  # Clamp to [0, 1]

    except Exception as e:
        logger.error(f"Error calculating hash similarity: {e}")
        return 0.0


def batch_calculate_hashes(images: list, hash_size: int = 16) -> list:
    """
    Calculate perceptual hashes for multiple images.

    Args:
        images: List of preprocessed numpy arrays
        hash_size: Hash dimensions

    Returns:
        List of hash strings (None for failed calculations)

    Example:
        >>> images = [img1, img2, img3]
        >>> hashes = batch_calculate_hashes(images)
        >>> len(hashes)
        3
    """
    results = []
    for i, image in enumerate(images):
        try:
            if image is not None:
                hash_str = calculate_perceptual_hash(image, hash_size)
                results.append(hash_str)
            else:
                logger.warning(f"Image {i} is None, skipping hash calculation")
                results.append(None)
        except Exception as e:
            logger.warning(f"Failed to calculate hash for image {i}: {e}")
            results.append(None)

    return results


def find_best_match(
    target_hash: str,
    reference_hashes: dict,
    max_distance: int = 10
) -> Tuple[str, int]:
    """
    Find the best matching reference hash from a dictionary.

    Args:
        target_hash: Hash to match
        reference_hashes: Dict of {identifier: hash} pairs
        max_distance: Maximum acceptable Hamming distance

    Returns:
        Tuple of (best_match_identifier, distance) or (None, float('inf'))

    Example:
        >>> references = {
        ...     'Z000mm': hash_0mm,
        ...     'Z005mm': hash_5mm,
        ...     'Z010mm': hash_10mm
        ... }
        >>> best_match, distance = find_best_match(current_hash, references)
        >>> best_match
        'Z005mm'
        >>> distance
        3
    """
    best_match = None
    best_distance = float('inf')

    for identifier, ref_hash in reference_hashes.items():
        if ref_hash is None:
            continue

        try:
            distance = compare_hashes(target_hash, ref_hash)
            if distance < best_distance:
                best_distance = distance
                best_match = identifier

        except Exception as e:
            logger.warning(f"Error comparing with {identifier}: {e}")
            continue

    if best_distance > max_distance:
        logger.info(
            f"No good match found (best distance: {best_distance} > {max_distance})"
        )
        return None, float('inf')

    logger.info(f"Best match: {best_match} (distance: {best_distance})")
    return best_match, best_distance
