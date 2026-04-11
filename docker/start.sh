#!/bin/bash
set -e

# Activate the Python virtual environment
source /opt/venv/bin/activate

# Set HOME to vscode user's home so PyRIT finds env files at ~/.pyrit/
export HOME=/home/vscode

echo "=== PyRIT Container Startup ==="
echo "PYRIT_MODE: ${PYRIT_MODE:-not set}"
echo "Python version: $(python --version)"
echo "================================"

# Check if PYRIT_MODE is set
if [ -z "$PYRIT_MODE" ]; then
    echo "ERROR: PYRIT_MODE environment variable is not set!"
    echo "Please set PYRIT_MODE to either 'jupyter' or 'gui'"
    exit 1
fi

echo "PYRIT_MODE is set to: $PYRIT_MODE"

# Default to CPU mode
export CUDA_VISIBLE_DEVICES="-1"

# Only try to use GPU if explicitly enabled
if [ "$ENABLE_GPU" = "true" ] && command -v nvidia-smi &> /dev/null; then
    echo "GPU detected and explicitly enabled, running with GPU support"
    export CUDA_VISIBLE_DEVICES="0"
else
    echo "Running in CPU-only mode"
    export CUDA_VISIBLE_DEVICES="-1"
fi

# Print PyRIT version
echo "Checking PyRIT installation..."
python -c "import pyrit; print(f'Running PyRIT version: {pyrit.__version__}')"

# Start the appropriate service based on PYRIT_MODE
if [ "$PYRIT_MODE" = "jupyter" ]; then
    echo "Starting JupyterLab on port 8888..."
    echo "Note: Notebooks are from the local source at build time"
    echo "JupyterLab will generate an access token. Check the logs for the URL with token."
    exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --notebook-dir=/app/notebooks
elif [ "$PYRIT_MODE" = "gui" ]; then
    echo "Starting PyRIT GUI on port 8000..."
    exec python -m uvicorn pyrit.backend.main:app --host 0.0.0.0 --port 8000
else
    echo "ERROR: Invalid PYRIT_MODE '$PYRIT_MODE'. Must be 'jupyter' or 'gui'"
    exit 1
fi
