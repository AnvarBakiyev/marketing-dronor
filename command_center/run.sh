#!/bin/bash
set -e

cd "$(dirname "$0")"

# Check virtual env
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

echo "Starting Command Center on http://localhost:5555"
python cc_backend.py
