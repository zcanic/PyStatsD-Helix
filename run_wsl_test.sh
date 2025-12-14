#!/bin/bash
set -e
echo "Installing dependencies..."
pip install -e .[dev] || pip install -e .

echo "Checking environment..."
python3 -c "import sys, os; print(f'Platform: {sys.platform}, OS: {os.name}')"
python3 -c "import uvloop; print('uvloop available')" || echo "uvloop NOT available"

echo "Running smoke test..."
python3 smoke_test.py
