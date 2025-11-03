"""
Tests for image preprocessing module
"""

import pytest
import numpy as np
from PIL import Image

from src.cv_analysis.preprocessing import (
    preprocess_image,
    load_and_preprocess,
    batch_preprocess,
    validate_preprocessed_image
)


class TestPreprocessImage:
    """Tests for preprocess_image function"""

    def test_preprocess_rgb_image(self):
        """Test preprocessing an RGB image"""
        # Create a synthetic RGB image
        rgb_image = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        processed = preprocess_image(rgb_image)

        # Check output properties
        assert processed.shape == (540, 960)  # Downsampled
        assert processed.dtype == np.uint8
        assert len(processed.shape) == 2  # Grayscale
        assert processed.min() >= 0
        assert processed.max() <= 255

    def test_preprocess_grayscale_image(self):
        """Test preprocessing a grayscale image"""
        gray_image = np.random.randint(0, 255, (1080, 1920), dtype=np.uint8)

        processed = preprocess_image(gray_image)

        assert processed.shape == (540, 960)
        assert processed.dtype == np.uint8

    def test_preprocess_pil_image(self):
        """Test preprocessing a PIL Image"""
        pil_image = Image.new('RGB', (1920, 1080), color=(128, 128, 128))

        processed = preprocess_image(pil_image)

        assert processed.shape == (540, 960)
        assert processed.dtype == np.uint8

    def test_preprocess_custom_size(self):
        """Test preprocessing with custom target size"""
        image = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        processed = preprocess_image(image, target_size=(480, 270))

        assert processed.shape == (270, 480)

    def test_preprocess_empty_image(self):
        """Test that empty image raises ValueError"""
        empty_image = np.array([])

        with pytest.raises(ValueError):
            preprocess_image(empty_image)

    def test_preprocess_invalid_type(self):
        """Test that invalid image type raises TypeError"""
        with pytest.raises(TypeError):
            preprocess_image("not an image")

    def test_preprocessed_image_validation(self):
        """Test validation of preprocessed images"""
        image = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        processed = preprocess_image(image)

        assert validate_preprocessed_image(processed) is True

    def test_validation_rejects_invalid_images(self):
        """Test that validation rejects invalid images"""
        # Wrong type
        assert validate_preprocessed_image("not an array") is False

        # Wrong dimensions
        rgb_image = np.zeros((100, 100, 3), dtype=np.uint8)
        assert validate_preprocessed_image(rgb_image) is False

        # Wrong dtype
        float_image = np.zeros((100, 100), dtype=np.float32)
        assert validate_preprocessed_image(float_image) is False


class TestBatchPreprocess:
    """Tests for batch preprocessing"""

    def test_batch_preprocess_multiple_images(self):
        """Test batch preprocessing of multiple images"""
        # Create synthetic test images
        images = [
            np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
            for _ in range(3)
        ]

        # Save to temporary files
        import tempfile
        import os

        temp_dir = tempfile.mkdtemp()
        paths = []

        for i, img in enumerate(images):
            path = os.path.join(temp_dir, f"test_{i}.png")
            Image.fromarray(img).save(path)
            paths.append(path)

        # Batch preprocess
        results = batch_preprocess(paths)

        assert len(results) == 3
        assert all(r is not None for r in results)
        assert all(r.shape == (540, 960) for r in results)

        # Cleanup
        for path in paths:
            os.remove(path)
        os.rmdir(temp_dir)


class TestPreprocessingConsistency:
    """Tests for preprocessing consistency"""

    def test_same_image_produces_same_result(self):
        """Test that preprocessing is deterministic"""
        image = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        result1 = preprocess_image(image.copy())
        result2 = preprocess_image(image.copy())

        # Results should be identical (deterministic)
        np.testing.assert_array_equal(result1, result2)

    def test_preprocessing_is_reversible_in_range(self):
        """Test that preprocessing maintains reasonable value ranges"""
        # Create image with known values
        image = np.ones((1080, 1920), dtype=np.uint8) * 128

        processed = preprocess_image(image)

        # Should still be in valid range
        assert processed.min() >= 0
        assert processed.max() <= 255
