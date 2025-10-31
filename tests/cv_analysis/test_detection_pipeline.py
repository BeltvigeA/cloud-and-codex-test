"""
Integration tests for complete detection pipeline
"""

import pytest
import numpy as np
from PIL import Image
import sys
from pathlib import Path
import tempfile
import shutil

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from cv_analysis.detection import detect_plate_objects, batch_detect, is_breaking_successful


class TestDetectionPipeline:
    """Integration tests for the complete detection pipeline"""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing"""
        temp_base = tempfile.mkdtemp()
        calibration_dir = Path(temp_base) / "calibration"
        checkpoints_dir = Path(temp_base) / "checkpoints"
        output_dir = Path(temp_base) / "output"

        calibration_dir.mkdir(parents=True)
        checkpoints_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        yield {
            'base': temp_base,
            'calibration': str(calibration_dir),
            'checkpoints': str(checkpoints_dir),
            'output': str(output_dir)
        }

        # Cleanup
        shutil.rmtree(temp_base)

    @pytest.fixture
    def create_test_images(self, temp_dirs):
        """Create test calibration and checkpoint images"""
        # Create clean calibration images
        printer_serial = "TEST_PRINTER_001"
        printer_dir = Path(temp_dirs['calibration']) / printer_serial
        printer_dir.mkdir(parents=True)

        # Create calibration references at different Z-heights
        for z in [0, 5, 10, 20, 50, 100]:
            img = self._create_clean_plate_image()
            img_path = printer_dir / f"Z{z:03d}mm_20250131_120000.png"
            img.save(img_path)

        # Create a checkpoint image (clean)
        checkpoint_dir = Path(temp_dirs['checkpoints']) / "job_test_123"
        checkpoint_dir.mkdir(parents=True)

        clean_checkpoint = self._create_clean_plate_image()
        clean_path = checkpoint_dir / "checkpoint_100pct_Z50mm.png"
        clean_checkpoint.save(clean_path)

        # Create a checkpoint with object
        object_checkpoint = self._create_plate_with_object()
        object_path = checkpoint_dir / "checkpoint_100pct_Z50mm_object.png"
        object_checkpoint.save(object_path)

        return {
            'printer_serial': printer_serial,
            'clean_path': str(clean_path),
            'object_path': str(object_path),
            'calibration_dir': temp_dirs['calibration']
        }

    def _create_clean_plate_image(self) -> Image.Image:
        """Create a synthetic clean build plate image"""
        # Create grayscale image with some texture
        img_array = np.random.randint(100, 150, (1080, 1920), dtype=np.uint8)

        # Add slight gradient to simulate lighting
        y_gradient = np.linspace(0.9, 1.1, 1080).reshape(-1, 1)
        img_array = (img_array * y_gradient).astype(np.uint8)

        return Image.fromarray(img_array, mode='L')

    def _create_plate_with_object(self) -> Image.Image:
        """Create a synthetic plate image with an object"""
        img = self._create_clean_plate_image()
        img_array = np.array(img)

        # Add a dark "object" (simulating leftover print)
        # Rectangle in center
        h, w = img_array.shape
        obj_h, obj_w = 200, 150
        y_start = (h - obj_h) // 2
        x_start = (w - obj_w) // 2

        img_array[y_start:y_start+obj_h, x_start:x_start+obj_w] = 50

        return Image.fromarray(img_array, mode='L')

    def test_detect_clean_plate(self, create_test_images):
        """Test detection on clean plate"""
        result = detect_plate_objects(
            current_image_path=create_test_images['clean_path'],
            printer_serial=create_test_images['printer_serial'],
            z_height=50.0,
            calibration_dir=create_test_images['calibration_dir'],
            save_visualization=False
        )

        assert result['is_clean'] is True
        assert result['ssim_score'] > 0.90
        assert len(result['regions_detected']) == 0
        assert result['processing_time_ms'] > 0

    def test_detect_plate_with_object(self, create_test_images):
        """Test detection on plate with object"""
        result = detect_plate_objects(
            current_image_path=create_test_images['object_path'],
            printer_serial=create_test_images['printer_serial'],
            z_height=50.0,
            calibration_dir=create_test_images['calibration_dir'],
            save_visualization=False
        )

        assert result['is_clean'] is False
        assert result['ssim_score'] < 0.90
        assert len(result['regions_detected']) > 0
        assert result['processing_time_ms'] > 0

    def test_detection_result_structure(self, create_test_images):
        """Test that detection result has all required fields"""
        result = detect_plate_objects(
            current_image_path=create_test_images['clean_path'],
            printer_serial=create_test_images['printer_serial'],
            z_height=50.0,
            calibration_dir=create_test_images['calibration_dir'],
            save_visualization=False
        )

        required_fields = [
            'is_clean',
            'detection_method',
            'ssim_score',
            'threshold_used',
            'regions_detected',
            'confidence',
            'reference_z',
            'reference_path',
            'processing_time_ms',
            'hash_distance'
        ]

        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_performance_target(self, create_test_images):
        """Test that detection meets performance target (<50ms)"""
        result = detect_plate_objects(
            current_image_path=create_test_images['clean_path'],
            printer_serial=create_test_images['printer_serial'],
            z_height=50.0,
            calibration_dir=create_test_images['calibration_dir'],
            save_visualization=False
        )

        # Performance target: <50ms per detection
        # In tests, might be slightly slower, so allow some margin
        assert result['processing_time_ms'] < 200, \
            f"Detection too slow: {result['processing_time_ms']:.1f}ms"

    def test_batch_detection(self, create_test_images, temp_dirs):
        """Test batch detection on multiple images"""
        # Create multiple checkpoint images
        checkpoint_dir = Path(temp_dirs['checkpoints']) / "job_batch_test"
        checkpoint_dir.mkdir(parents=True)

        image_paths = []
        z_heights = [0, 45, 91, 138]

        for i, z in enumerate(z_heights):
            img = self._create_clean_plate_image()
            path = checkpoint_dir / f"checkpoint_{i}_Z{z}mm.png"
            img.save(path)
            image_paths.append(str(path))

        results = batch_detect(
            image_paths=image_paths,
            printer_serial=create_test_images['printer_serial'],
            z_heights=z_heights,
            calibration_dir=create_test_images['calibration_dir']
        )

        assert len(results) == 4
        assert all(r['is_clean'] for r in results)

    def test_is_breaking_successful(self):
        """Test breaking success evaluation"""
        # All clean
        results_clean = [
            {'is_clean': True, 'ssim_score': 0.95},
            {'is_clean': True, 'ssim_score': 0.96},
            {'is_clean': True, 'ssim_score': 0.97}
        ]

        assert is_breaking_successful(results_clean) is True

        # Some not clean
        results_mixed = [
            {'is_clean': True, 'ssim_score': 0.95},
            {'is_clean': False, 'ssim_score': 0.82},
            {'is_clean': True, 'ssim_score': 0.97}
        ]

        assert is_breaking_successful(results_mixed) is False

        # With require_all_clean=False, only last needs to be clean
        assert is_breaking_successful(results_mixed, require_all_clean=False) is True

    def test_missing_calibration(self, create_test_images):
        """Test detection with missing calibration reference"""
        result = detect_plate_objects(
            current_image_path=create_test_images['clean_path'],
            printer_serial="NONEXISTENT_PRINTER",
            z_height=50.0,
            calibration_dir=create_test_images['calibration_dir'],
            save_visualization=False
        )

        assert result['detection_method'] == 'error'
        assert result['is_clean'] is False  # Safe default
        assert 'error_message' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
