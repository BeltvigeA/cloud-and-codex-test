"""
Setup configuration for 3D Printer Farm Management System
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_path = Path(__file__).parent / "README.md"
long_description = readme_path.read_text() if readme_path.exists() else ""

setup(
    name="printer-farm-cv",
    version="1.0.0",
    description="Computer Vision plate verification for 3D printer farms",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="3D Printer Farm Team",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.8",
    install_requires=[
        "Flask>=2.3.3,<3.0",
        "google-cloud-storage>=3.4.0",
        "google-cloud-firestore>=2.11.1,<3.0",
        "google-cloud-kms>=2.12.0,<3.0",
        "google-cloud-secret-manager>=2.20.0,<3.0",
        "google-api-core>=2.11.1,<3.0",
        "requests>=2.31.0,<3.0",
        "google-auth>=2.41.1",
        "bambulabs_api>=2.6.5",
        "rich>=13.7.0",
        # CV dependencies
        "opencv-python>=4.8.1,<5.0",
        "scikit-image>=0.22.0,<1.0",
        "numpy>=1.24.3,<2.0",
        "Pillow>=10.1.0,<11.0",
        "imagehash>=4.3.1,<5.0",
        "PyYAML>=6.0,<7.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.3,<8.0",
            "pytest-cov>=4.1.0,<5.0",
            "pytest-benchmark>=4.0.0,<5.0",
        ]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
