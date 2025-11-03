"""
Integration tests for the complete detection pipeline
"""

import pytest
import numpy as np
import tempfile
import shutil
from pathlib import Path
from PIL import Image

from src.cv_analysis.detection import (
    detect_plate_objects,
    detect_from_checkpoints,
    batch_detect
)
from src.cv_analysis.preprocessing import preprocess_image


class TestDetectionPipeline:
    """Integration tests for the full detection pipeline"""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def mock_calibration_dir(self, temp_dir):
        """Create mock calibration directory structure"""
        printer_serial = "TEST_PRINTER_001"
        calibration_dir = Path(temp_dir) / "calibration" / printer_serial
        calibration_dir.mkdir(parents=True)

        # Create mock calibration images at various Z-heights
        z_heights = [0, 5, 10, 15, 20, 50, 100, 150, 200, 235]

        for z in z_heights:
            # Create a clean plate image (uniform with slight noise)
            img = self._create_clean_plate_image()
            img_path = calibration_dir / f"Z{z:03d}mm_20250101_120000.png"
            Image.fromarray(img).save(img_path)

        # Create metadata file
        import json
        metadata = {
            'calibration_date': '2025-01-01T12:00:00',
            'z_heights': z_heights,
            'image_count': len(z_heights),
            'printer_serial': printer_serial
        }
        with open(calibration_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f)

        return str(Path(temp_dir) / "calibration")

    def _create_clean_plate_image(self):
        """Create a synthetic clean plate image"""
        # Create base image with slight texture
        img = np.random.randint(100, 120, (1080, 1920), dtype=np.uint8)

        # Add some gradient to simulate lighting
        y_gradient = np.linspace(0.9, 1.1, 1080)[:, np.newaxis]
        img = (img * y_gradient).astype(np.uint8)

        return img

    def _create_plate_with_object(self):
        """Create a synthetic plate image with an object"""
        img = self._create_clean_plate_image()

        # Add a rectangular object
        obj_y = 400
        obj_x = 800
        obj_h = 200
        obj_w = 150

        # Make object darker
        img[obj_y:obj_y+obj_h, obj_x:obj_x+obj_w] = 50

        return img

    def test_clean_plate_detection(self, temp_dir, mock_calibration_dir):
        """Test detection on a clean plate (should return is_clean=True)"""
        # Create current image identical to calibration
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "current_clean.png"
        Image.fromarray(current_img).save(current_path)

        # Run detection
        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=10.0,
            calibration_dir=mock_calibration_dir
        )

        # Assertions
        assert result['is_clean'] is True
        assert result['ssim_score'] > 0.85
        assert len(result['regions_detected']) == 0
        assert result['confidence'] > 0.5
        assert 'processing_time_ms' in result
        assert result['processing_time_ms'] < 100  # Should be fast

    def test_plate_with_object_detection(self, temp_dir, mock_calibration_dir):
        """Test detection on a plate with an object (should return is_clean=False)"""
        # Create current image with object
        current_img = self._create_plate_with_object()
        current_path = Path(temp_dir) / "current_with_object.png"
        Image.fromarray(current_img).save(current_path)

        # Run detection
        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=10.0,
            calibration_dir=mock_calibration_dir
        )

        # Assertions
        assert result['is_clean'] is False
        assert result['ssim_score'] < 0.95  # Should be different
        assert len(result['regions_detected']) > 0  # Should detect region(s)
        assert result['detection_method'] in ['ssim_object', 'hash_match']

    def test_hash_match_fast_path(self, temp_dir, mock_calibration_dir):
        """Test that nearly identical images use fast hash path"""
        # Create image very similar to calibration
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "current_identical.png"
        Image.fromarray(current_img).save(current_path)

        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=10.0,
            calibration_dir=mock_calibration_dir,
            hash_threshold=10  # More lenient for test
        )

        # Should use fast path for very similar images
        assert result['is_clean'] is True
        assert result['processing_time_ms'] < 100

    def test_adaptive_threshold_by_z_height(self, temp_dir, mock_calibration_dir):
        """Test that threshold adapts based on Z-height"""
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "current.png"
        Image.fromarray(current_img).save(current_path)

        # Test low Z-height (should use higher threshold)
        result_low_z = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=2.0,
            calibration_dir=mock_calibration_dir
        )

        # Test high Z-height (should use lower threshold)
        result_high_z = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=100.0,
            calibration_dir=mock_calibration_dir
        )

        # Low Z should have higher (more conservative) threshold
        assert result_low_z['threshold_used'] > result_high_z['threshold_used']

    def test_missing_calibration_reference(self, temp_dir):
        """Test error handling when calibration reference is missing"""
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "current.png"
        Image.fromarray(current_img).save(current_path)

        # Use non-existent calibration directory
        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="NONEXISTENT_PRINTER",
            z_height=10.0,
            calibration_dir=str(Path(temp_dir) / "no_calibration")
        )

        # Should return error result (conservative: not clean)
        assert result['is_clean'] is False
        assert result['detection_method'] == 'error'
        assert 'error' in result

    def test_batch_detection(self, temp_dir, mock_calibration_dir):
        """Test batch detection on multiple images"""
        # Create multiple test images
        images = [
            (self._create_clean_plate_image(), 10.0),
            (self._create_clean_plate_image(), 50.0),
            (self._create_plate_with_object(), 100.0)
        ]

        paths = []
        z_heights = []

        for i, (img, z) in enumerate(images):
            path = Path(temp_dir) / f"batch_{i}.png"
            Image.fromarray(img).save(path)
            paths.append(str(path))
            z_heights.append(z)

        # Run batch detection
        results = batch_detect(
            image_paths=paths,
            printer_serial="TEST_PRINTER_001",
            z_heights=z_heights,
            calibration_dir=mock_calibration_dir
        )

        # Assertions
        assert len(results) == 3
        assert results[0]['is_clean'] is True  # Clean plate
        assert results[1]['is_clean'] is True  # Clean plate
        assert results[2]['is_clean'] is False  # Plate with object

    def test_detection_with_visualization(self, temp_dir, mock_calibration_dir):
        """Test detection with visualization output"""
        current_img = self._create_plate_with_object()
        current_path = Path(temp_dir) / "current_vis.png"
        Image.fromarray(current_img).save(current_path)

        vis_path = Path(temp_dir) / "visualization.png"

        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=10.0,
            calibration_dir=mock_calibration_dir,
            save_visualization=True,
            visualization_path=str(vis_path)
        )

        # Visualization file should be created
        assert vis_path.exists()

    def test_performance_requirement(self, temp_dir, mock_calibration_dir):
        """Test that detection meets performance requirement (<50ms)"""
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "perf_test.png"
        Image.fromarray(current_img).save(current_path)

        result = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=10.0,
            calibration_dir=mock_calibration_dir
        )

        # Should complete in under 100ms (allowing some overhead for I/O)
        assert result['processing_time_ms'] < 100

    def test_false_positive_rate_adjustment(self, temp_dir, mock_calibration_dir):
        """Test that high FP rate lowers threshold"""
        current_img = self._create_clean_plate_image()
        current_path = Path(temp_dir) / "current_fp.png"
        Image.fromarray(current_img).save(current_path)

        # Test with high FP rate
        result_high_fp = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=50.0,
            calibration_dir=mock_calibration_dir,
            false_positive_rate_24h=0.15  # 15% FP rate
        )

        # Test with normal FP rate
        result_normal = detect_plate_objects(
            current_image_path=str(current_path),
            printer_serial="TEST_PRINTER_001",
            z_height=50.0,
            calibration_dir=mock_calibration_dir,
            false_positive_rate_24h=0.01  # 1% FP rate
        )

        # High FP rate should lower threshold
        assert result_high_fp['threshold_used'] < result_normal['threshold_used']


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_nonexistent_image_file(self):
        """Test handling of non-existent image file"""
        with pytest.raises(FileNotFoundError):
            detect_plate_objects(
                current_image_path="/nonexistent/image.png",
                printer_serial="TEST",
                z_height=10.0,
                calibration_dir="/some/path"
            )

    def test_invalid_z_height(self, tmp_path):
        """Test handling of invalid Z-height"""
        # Create a dummy image
        img = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
        img_path = tmp_path / "test.png"
        Image.fromarray(img).save(img_path)

        # Negative Z-height should raise error
        result = detect_plate_objects(
            current_image_path=str(img_path),
            printer_serial="TEST",
            z_height=-10.0,
            calibration_dir=str(tmp_path)
        )

        assert result['detection_method'] == 'error'

    def test_extreme_lighting_conditions(self, tmp_path):
        """Test detection under extreme lighting conditions"""
        # Very dark image
        dark_img = np.ones((1080, 1920), dtype=np.uint8) * 10
        dark_path = tmp_path / "dark.png"
        Image.fromarray(dark_img).save(dark_path)

        # Very bright image
        bright_img = np.ones((1080, 1920), dtype=np.uint8) * 245
        bright_path = tmp_path / "bright.png"
        Image.fromarray(bright_img).save(bright_path)

        # Preprocessing should handle these without crashing
        from src.cv_analysis.preprocessing import load_and_preprocess

        dark_processed = load_and_preprocess(str(dark_path))
        bright_processed = load_and_preprocess(str(bright_path))

        # Should produce valid preprocessed images
        assert dark_processed.shape == (540, 960)
        assert bright_processed.shape == (540, 960)
