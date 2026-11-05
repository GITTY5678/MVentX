#!/usr/bin/env bash
# ==============================================================================
# MVentX Build and Verification Script
# ==============================================================================

set -euo pipefail

echo "==========================================="
echo " Building MVentX Distribution Package     "
echo "==========================================="

# Activate virtual environment if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run code format and lint checks
echo "Running code linters (ruff)..."
python3 -m ruff check .

echo "Checking formatting (black)..."
python3 -m black --check .

echo "Running unit tests..."
pytest tests/unit/

echo "Building Python package wheels..."
python3 -m pip install --upgrade build
python3 -m build

echo "Build completed successfully. Artifacts available in dist/"

