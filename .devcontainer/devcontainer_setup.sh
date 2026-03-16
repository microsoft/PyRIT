#!/bin/bash
set -e

MYPY_CACHE="/workspace/.mypy_cache"
VIRTUAL_ENV="/opt/venv"
# Create the mypy cache directory if it doesn't exist
if [ ! -d "$MYPY_CACHE" ]; then
    echo "Creating mypy cache directory..."
    sudo mkdir -p $MYPY_CACHE
    sudo chown vscode:vscode $MYPY_CACHE
    sudo chmod 777 $MYPY_CACHE
else
    # Check ownership
    OWNER=$(stat -c '%U:%G' $MYPY_CACHE)

    if [ "$OWNER" != "vscode:vscode" ]; then
        echo "Fixing mypy cache directory ownership..."
        sudo chown -R vscode:vscode $MYPY_CACHE
    fi

    # Check permissions
    PERMS=$(stat -c '%a' $MYPY_CACHE)

    if [ "$PERMS" != "777" ]; then
        echo "Fixing mypy cache directory permissions..."
        sudo chmod -R 777 $MYPY_CACHE
    fi
fi

# cleanup old extensions
sudo rm -rf /vscode/vscode-server/extensionsCache/github.copilot-*
rm -rf /home/vscode/.vscode-server/extensions/{*,.[!.]*,..?*}

# Activate the uv venv created in the Dockerfile
source /opt/venv/bin/activate

# Store hash inside venv so it's tied to the venv lifecycle
HASH_FILE="/opt/venv/pyproject_hash"

# Compute current hash
CURRENT_HASH=$(sha256sum /workspace/pyproject.toml | awk '{print $1}')

# Check if hash file exists and if the hash has changed
if [ ! -f "$HASH_FILE" ] || [ "$(cat $HASH_FILE)" != "$CURRENT_HASH" ]; then
    echo "📦 pyproject.toml has changed, installing environment..."

    # Install dependencies
    uv pip install ipykernel
    uv pip install -e ".[dev,all]"
    # Register the kernel with Jupyter
    python -m ipykernel install --user --name=pyrit-dev --display-name="Python (pyrit-dev)"

    # Save the new hash
    echo "$CURRENT_HASH" > "$HASH_FILE"
else
    echo "✅ pyproject.toml has not changed, skipping installation."
fi

# Install frontend dependencies
echo "📦 Installing frontend dependencies..."

# Fix node_modules permissions (volume is owned by root)
if [ -d "/workspace/frontend/node_modules" ]; then
    echo "Fixing node_modules permissions..."
    sudo chown -R vscode:vscode /workspace/frontend/node_modules
fi

cd /workspace/frontend
if [ -f "package.json" ]; then
    npm install

    # Install Playwright browsers and system dependencies for E2E testing
    # This may fail if apt repos have signature issues - don't block setup
    echo "📦 Installing Playwright browsers..."

    # Remove third-party repos with SHA1 signature issues (rejected since 2026-02-01)
    # Playwright deps come from Debian main repos, these aren't needed
    sudo rm -f /etc/apt/sources.list.d/yarn.list \
               /etc/apt/sources.list.d/nodesource.list \
               /etc/apt/sources.list.d/microsoft.list 2>/dev/null || true

    if npx playwright install --with-deps chromium; then
        echo "✅ Playwright browsers installed."
    else
        echo "⚠️  Playwright installation failed (apt signature issues). Run 'npx playwright install chromium' manually if needed for E2E tests."
    fi

    echo "✅ Frontend dependencies installed."
fi
cd /workspace

echo "🚀 Dev container setup complete!"
