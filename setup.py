"""Compatibility shim for editable installs with older pip/setuptools.

Modern build frontends use ``pyproject.toml``.  macOS can still ship pip 21,
which requires a setup.py for ``pip install -e .``.
"""

from setuptools import find_packages, setup


setup(
    name="mp72-control",
    version="0.1.0",
    description="Safe Multicomp Pro MP720115 control over VXI-11/SCPI",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    install_requires=[
        "pyvisa>=1.14",
        "pyvisa-py>=0.7",
        "psutil>=5.9",
        "zeroconf>=0.131",
    ],
    extras_require={
        "plot": ["numpy>=1.26", "matplotlib>=3.8"],
        "dev": ["numpy>=1.26", "matplotlib>=3.8"],
    },
    entry_points={"console_scripts": ["mp72-control=mp72_control.cli:main"]},
)
