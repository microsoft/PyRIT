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

# Write .env when deploy_instance.py supplies inline content. Otherwise the
# generated PyRIT config uses the Key Vault URL from PYRIT_ENV_AKV_REF so the
# backend can read and update that environment source through managed identity.
if [ -n "$PYRIT_ENV_CONTENTS" ]; then
    mkdir -p ~/.pyrit
    echo "$PYRIT_ENV_CONTENTS" > ~/.pyrit/.env
    echo "Wrote .env file from PYRIT_ENV_CONTENTS ($(wc -l < ~/.pyrit/.env) lines)"
else
    echo "No inline PYRIT_ENV_CONTENTS set — using configured environment sources"
fi

write_deployment_config() {
    local target_file="$1"
    mkdir -p "$(dirname "$target_file")"
    {
        if [ -n "$AZURE_SQL_SERVER" ]; then
            echo "Using Azure SQL database (server: $AZURE_SQL_SERVER)" >&2
            echo "memory_db_type: AzureSQL"
        else
            echo "Using SQLite database (AZURE_SQL_SERVER not set)" >&2
            echo "memory_db_type: SQLite"
        fi
        if [ -n "$PYRIT_INITIALIZER" ]; then
            echo "Using initializer: $PYRIT_INITIALIZER" >&2
            echo "initializers:"
            # Split comma-separated initializer names into a YAML list.
            IFS=',' read -ra INIT_NAMES <<<"$PYRIT_INITIALIZER"
            for name in "${INIT_NAMES[@]}"; do
                echo "  - $(echo "$name" | xargs)"
            done
        fi
        if [ -n "$PYRIT_ENV_AKV_REF" ]; then
            echo "Using Azure Key Vault environment reference" >&2
            echo "env_akv_ref:"
            echo "  - $PYRIT_ENV_AKV_REF"
        fi
    } >"$target_file"
}

# Start the appropriate service based on PYRIT_MODE
if [ "$PYRIT_MODE" = "jupyter" ]; then
    echo "Starting JupyterLab on port 8888..."
    echo "Note: Notebooks are from the local source at build time"
    echo "JupyterLab will generate an access token. Check the logs for the URL with token."
    exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --notebook-dir=/app/notebooks
elif [ "$PYRIT_MODE" = "gui" ]; then
    echo "Starting PyRIT GUI on port 8000..."
    if [ -n "${PYRIT_CONFIG_FILE:-}" ]; then
        CONFIG_FILE="$PYRIT_CONFIG_FILE"
        DEPLOYMENT_BASE_CONFIG="$HOME/.pyrit/.pyrit_conf"
        if [ "$CONFIG_FILE" = "$DEPLOYMENT_BASE_CONFIG" ]; then
            echo "ERROR: PYRIT_CONFIG_FILE cannot point to $DEPLOYMENT_BASE_CONFIG in this container" >&2
            exit 1
        fi
        # ConfigurationLoader overlays the explicit source on its default file.
        # Materialize deployment-derived values there so omitted external keys do
        # not silently switch Azure SQL to local SQLite or drop the AKV source.
        write_deployment_config "$DEPLOYMENT_BASE_CONFIG"
        echo "Using external PyRIT configuration over deployment defaults"
    else
        # Translate deployment settings into a runtime config file so the FastAPI
        # lifespan (ConfigurationLoader) picks them up on startup.
        RUNTIME_CONFIG=/tmp/pyrit_runtime.yaml
        write_deployment_config "$RUNTIME_CONFIG"
        CONFIG_FILE="$RUNTIME_CONFIG"
    fi

    # Pick the launcher module. PR #1753 moved the launcher from
    # ``pyrit.cli.pyrit_backend`` to ``pyrit.backend.pyrit_backend``. The PyPI
    # docker_build CI job pins to whatever's currently published (0.13.0 at
    # time of writing), which still uses the old path, so fall back to it when
    # the new module isn't present. Once a release containing the new layout
    # ships, this fallback is dead code and can be removed.
    if python -c "import pyrit.backend.pyrit_backend" >/dev/null 2>&1; then
        BACKEND_MODULE="pyrit.backend.pyrit_backend"
    elif python -c "import pyrit.cli.pyrit_backend" >/dev/null 2>&1; then
        echo "Using legacy pyrit.cli.pyrit_backend launcher (PyRIT <= 0.13.0)"
        BACKEND_MODULE="pyrit.cli.pyrit_backend"
    else
        echo "ERROR: cannot find pyrit backend launcher module" >&2
        exit 1
    fi

    exec python -m "$BACKEND_MODULE" \
        --host 0.0.0.0 \
        --port 8000 \
        --config-file "$CONFIG_FILE"
else
    echo "ERROR: Invalid PYRIT_MODE '$PYRIT_MODE'. Must be 'jupyter' or 'gui'"
    exit 1
fi
