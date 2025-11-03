# Windows Installation Guide for CV Plate Verification

## 🪟 Quick Install (Windows PowerShell)

### Option 1: Install with Updated Requirements (Recommended)

```powershell
# Pull latest changes (includes requirements.txt fix)
git pull

# Install dependencies
pip install -r requirements.txt
```

### Option 2: Install CV Dependencies Manually

If you still encounter issues, install CV packages individually:

```powershell
# Core dependencies (you already have these)
pip install Flask google-cloud-storage google-cloud-firestore bambulabs_api rich

# CV dependencies (install one by one)
pip install opencv-python
pip install scikit-image
pip install "numpy>=1.24.0,<2.0"
pip install Pillow
pip install imagehash
pip install pytest
pip install PyYAML
```

### Option 3: Use Conda (Alternative)

If pip continues to have issues, use Anaconda/Miniconda:

```powershell
# Create new conda environment
conda create -n printer_farm python=3.11
conda activate printer_farm

# Install CV packages from conda-forge
conda install -c conda-forge opencv pillow scikit-image numpy pytest pyyaml

# Install remaining packages with pip
pip install imagehash Flask google-cloud-storage google-cloud-firestore bambulabs_api rich
```

---

## 🔧 Troubleshooting Common Windows Issues

### Issue 1: OpenCV Version Error

**Error:**
```
ERROR: Could not find a version that satisfies the requirement opencv-python==4.8.1
```

**Solution:**
The updated requirements.txt now uses `opencv-python>=4.8.0` which will install the latest compatible version (e.g., `4.8.1.78`).

```powershell
# Pull latest requirements.txt
git pull

# Or manually install latest OpenCV
pip install opencv-python --upgrade
```

### Issue 2: NumPy 2.0 Compatibility

**Error:**
```
AttributeError: module 'numpy' has no attribute 'X'
```

**Solution:**
NumPy 2.0 has breaking changes. We pin to <2.0:

```powershell
pip install "numpy>=1.24.0,<2.0"
```

### Issue 3: Microsoft Visual C++ Required

**Error:**
```
error: Microsoft Visual C++ 14.0 or greater is required
```

**Solution:**
Install Microsoft C++ Build Tools:
1. Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Install "Desktop development with C++"
3. Retry: `pip install -r requirements.txt`

Or use pre-built wheels:
```powershell
pip install --only-binary :all: opencv-python scikit-image
```

### Issue 4: Python 3.13 Compatibility

Some packages may not have Python 3.13 wheels yet.

**Solution A: Use Python 3.11 or 3.12 (Recommended)**
```powershell
# Check your Python version
python --version

# If 3.13, install Python 3.11 or 3.12 from python.org
# Then create virtual environment with specific version
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Solution B: Wait for packages to compile (slower)**
```powershell
pip install -r requirements.txt --no-binary :all:
```

---

## ✅ Verify Installation

After installation, verify everything works:

```powershell
# Test imports
python -c "import cv2; import numpy; import PIL; from skimage.metrics import structural_similarity; print('✓ All CV dependencies installed successfully!')"

# Run quick test
python test_cv_realistic.py
```

Expected output:
```
✓ All CV dependencies installed successfully!
```

---

## 🐍 Recommended Python Setup for Windows

### Best Practice: Use Virtual Environment

```powershell
# Create virtual environment
python -m venv venv

# Activate (PowerShell)
.\venv\Scripts\Activate.ps1

# Or activate (CMD)
venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt

# Verify
python -c "import cv2; print('OpenCV version:', cv2.__version__)"
```

### Recommended Python Version

- ✅ **Python 3.11** - Best compatibility (recommended)
- ✅ **Python 3.12** - Good compatibility
- ⚠️ **Python 3.13** - Some packages may need compilation
- ❌ **Python 3.10 or older** - Not tested

---

## 📦 Package Versions Installed

After successful installation, you should have:

```powershell
# Check installed versions
pip list | Select-String "opencv|numpy|scikit|pillow|imagehash|pytest|yaml"
```

Expected output (versions may vary):
```
imagehash              4.3.1
numpy                  1.26.4
opencv-python          4.10.0.84
Pillow                 10.4.0
pytest                 8.3.4
PyYAML                 6.0.2
scikit-image           0.24.0
```

**Note:** Exact versions will vary, but should be >= the minimum requirements.

---

## 🚀 Quick Start After Installation

```powershell
# Run test to verify system works
python test_cv_realistic.py

# Expected output:
# ✓ Identical plate detected as clean (SSIM=1.0)
# ✓ Plate with object detected as dirty
# ✓ All core modules working!
```

---

## 💡 Windows-Specific Tips

### PowerShell Execution Policy

If you can't run scripts:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Long Path Support

Enable long paths (for deep directory structures):
```powershell
# Run as Administrator
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

### Path Separators

The code uses `pathlib` which handles Windows/Linux path separators automatically. No changes needed!

---

## 🆘 Still Having Issues?

### Check System Info

```powershell
# Python version
python --version

# Pip version
pip --version

# Installed packages
pip list

# System info
systeminfo | Select-String "OS Name|OS Version|System Type"
```

### Clean Install

If nothing works, try a clean install:

```powershell
# 1. Uninstall all CV packages
pip uninstall opencv-python scikit-image numpy Pillow imagehash pytest PyYAML -y

# 2. Clear pip cache
pip cache purge

# 3. Upgrade pip
python -m pip install --upgrade pip

# 4. Reinstall from updated requirements
pip install -r requirements.txt
```

---

## 📞 Getting Help

1. **Check Python version**: Must be 3.11, 3.12, or 3.13
2. **Use virtual environment**: Avoids conflicts with other packages
3. **Update pip**: `python -m pip install --upgrade pip`
4. **Check requirements.txt**: Should use `>=` not `==` for CV packages
5. **Try conda**: If pip fails, conda often has pre-built binaries

---

## ✅ Success Checklist

- [ ] Python 3.11 or 3.12 installed
- [ ] Virtual environment created and activated
- [ ] Latest requirements.txt pulled from git
- [ ] All packages installed without errors
- [ ] `python -c "import cv2"` works
- [ ] `python test_cv_realistic.py` passes

Once all checked, you're ready to use the CV system! 🎉

---

**Windows install should now work! If you still encounter issues after pulling the updated requirements.txt, let me know the specific error.**
