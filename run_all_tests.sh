#!/bin/bash
# Run all CV system tests

set -e  # Exit on error

echo "======================================================================="
echo " CV PLATE VERIFICATION SYSTEM - TEST SUITE"
echo "======================================================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Quick validation
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 1: Quick Validation (Synthetic Data)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python test_cv_quick.py
echo ""

# Test 2: Realistic test
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 2: Realistic Test (Correlated Images)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python test_cv_realistic.py
echo ""

# Test 3: Unit tests (if pytest is available)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 3: Unit Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if command -v pytest &> /dev/null; then
    python -m pytest tests/cv_analysis/ -v --tb=short
else
    echo -e "${YELLOW}⚠️  pytest not installed - skipping unit tests${NC}"
    echo "Install with: pip install pytest"
fi
echo ""

# Test 4: Module imports
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 4: Module Import Test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python -c "
from src.cv_analysis import detect_plate_objects
from src.cv_analysis.preprocessing import preprocess_image
from src.cv_analysis.perceptual_hash import calculate_perceptual_hash
from src.cv_analysis.ssim_comparison import compare_images_ssim
from src.cv_analysis.region_analysis import analyze_difference_regions
from src.cv_analysis.adaptive_threshold import get_adaptive_threshold
from src.cv_analysis.file_manager import find_calibration_reference
from src.cv_analysis.visualization import create_comparison_visualization
print('✓ All modules imported successfully')
"
echo ""

# Summary
echo "======================================================================="
echo " TEST SUITE COMPLETE"
echo "======================================================================="
echo ""
echo "Next steps:"
echo "  1. Review test results above"
echo "  2. Check TESTING_GUIDE.md for production testing"
echo "  3. Capture real calibration images from your printers"
echo "  4. Test with actual Bambulabs camera images"
echo ""
echo "Documentation:"
echo "  - TESTING_GUIDE.md       - Complete testing instructions"
echo "  - src/cv_analysis/README.md - Full API documentation"
echo "  - examples/cv_detection_example.py - Usage examples"
echo ""
echo "======================================================================="
