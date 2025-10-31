"""
Tests for image preprocessing module
"""

import pytest
import numpy as np
from PIL import Image
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from cv_analysis.preprocessing import (
    preprocess_image,
    validate_image_pair,
    normalize_image_histogram,
    calculate_image_statistics
)


class TestPreprocessing:
    """Test image preprocessing functions"""

    def test_preprocess_grayscale_image(self):
        """Test preprocessing of grayscale image"""
        # Create test grayscale image
        img = np.random.randint(0, 255, (1080, 1920), dtype=np.uint8)

        result = preprocess_image(img)

        assert result.shape == (540, 960)  # Downsampled
        assert result.dtype == np.uint8
        assert 0 <= result.min() <= result.max() <= 255

    def test_preprocess_rgb_image(self):
        """Test preprocessing of RGB image"""
        # Create test RGB image
        img = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)

        result = preprocess_image(img)

        assert result.shape == (540, 960)  # Downsampled and grayscale
        assert result.dtype == np.uint8
        assert len(result.shape) == 2  # Should be grayscale

    def test_preprocess_pil_image(self):
        """Test preprocessing of PIL Image"""
        # Create test PIL image
        img = Image.new('RGB', (1920, 1080), color='white')

        result = preprocess_image(img)

        assert result.shape == (540, 960)
        assert result.dtype == np.uint8

    def test_preprocess_rgba_image(self):
        """Test preprocessing of RGBA image"""
        # Create test RGBA image
        img = np.random.randint(0, 255, (1080, 1920, 4), dtype=np.uint8)

        result = preprocess_image(img)

        assert result.shape == (540, 960)
        assert result.dtype == np.uint8
        assert len(result.shape) == 2  # Should be grayscale

    def test_preprocess_invalid_image(self):
        """Test preprocessing with invalid input"""
        with pytest.raises(ValueError):
            preprocess_image(np.array([]))

        with pytest.raises(ValueError):
            preprocess_image(None)

    def test_validate_image_pair_compatible(self):
        """Test validation of compatible image pair"""
        img1 = np.zeros((540, 960), dtype=np.uint8)
        img2 = np.zeros((540, 960), dtype=np.uint8)

        assert validate_image_pair(img1, img2) is True

    def test_validate_image_pair_incompatible_shape(self):
        """Test validation of incompatible shapes"""
        img1 = np.zeros((540, 960), dtype=np.uint8)
        img2 = np.zeros((480, 640), dtype=np.uint8)

        assert validate_image_pair(img1, img2) is False

    def test_normalize_histogram(self):
        """Test histogram normalization"""
        img = np.random.randint(50, 150, (540, 960), dtype=np.uint8)

        result = normalize_image_histogram(img)

        assert result.shape == img.shape
        assert result.dtype == np.uint8
        # Normalized image should have better contrast
        assert result.max() - result.min() >= img.max() - img.min()

    def test_calculate_statistics(self):
        """Test image statistics calculation"""
        img = np.random.randint(0, 255, (540, 960), dtype=np.uint8)

        stats = calculate_image_statistics(img)

        assert 'mean' in stats
        assert 'std' in stats
        assert 'min' in stats
        assert 'max' in stats
        assert 'median' in stats
        assert 'histogram' in stats

        assert 0 <= stats['mean'] <= 255
        assert stats['min'] == img.min()
        assert stats['max'] == img.max()
        assert len(stats['histogram']) == 256

    def test_preprocessing_consistency(self):
        """Test that preprocessing same image twice gives same result"""
        img = np.random.randint(0, 255, (1080, 1920), dtype=np.uint8)

        result1 = preprocess_image(img.copy())
        result2 = preprocess_image(img.copy())

        np.testing.assert_array_equal(result1, result2)

    def test_custom_target_size(self):
        """Test preprocessing with custom target size"""
        img = np.random.randint(0, 255, (1080, 1920), dtype=np.uint8)

        result = preprocess_image(img, target_size=(640, 480))

        assert result.shape == (480, 640)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
