#!/usr/bin/env bash
# ==============================================================================
# MVentX Development Environment Setup Script
# ==============================================================================

set -euo pipefail

echo "==========================================="
echo " Setting up MVentX Development Environment "
echo "==========================================="

# Check Python 3 version
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Detected Python version: $PYTHON_VERSION"

# Create virtual environment if not present
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists in .venv."
fi

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip and packaging tools..."
pip install --upgrade pip setuptools wheel

# Install MVentX in editable mode with development dependencies
echo "Installing MVentX in editable mode with dev dependencies..."
pip install -e ".[dev]"

echo ""
echo "Development environment initialized successfully!"
echo "To activate the environment, run:"
echo "    source .venv/bin/activate"
echo "To run tests:"
echo "    pytest"

