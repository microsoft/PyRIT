# syntax=docker/dockerfile:1.4
# ============================================================================
# PyRIT Production Dockerfile
#
# This Dockerfile builds on top of the devcontainer base image to avoid
# duplication. The devcontainer is built first and used as the base.
#
# Build with:
#   python docker/build_pyrit_docker.py --source local
#   python docker/build_pyrit_docker.py --source pypi --version 0.10.0
# ============================================================================

# No default — callers must pass --build-arg BASE_IMAGE=... (see infra/README.md).
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS production

LABEL description="Docker container for PyRIT with Jupyter Notebook and GUI support"

# Build arguments for version tracking
ARG PYRIT_SOURCE=pypi
ARG PYRIT_VERSION=""
ARG GIT_COMMIT=""
ARG GIT_MODIFIED=false

# Production environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV JUPYTER_ENABLE_LAB=yes
ENV CUDA_VISIBLE_DEVICES=-1
ENV ENABLE_GPU=false
ENV HOME=/home/vscode

USER root

# Ensure we use the venv from the devcontainer base
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

# Set up working directory
WORKDIR /app

# For local: copy source, install editable, and build frontend
COPY --chown=vscode:vscode pyproject.toml MANIFEST.in README.md LICENSE /app/
COPY --chown=vscode:vscode pyrit/ /app/pyrit/
COPY --chown=vscode:vscode frontend/ /app/frontend/
COPY --chown=vscode:vscode build_scripts/ /app/build_scripts/
COPY --chown=vscode:vscode doc/ /app/doc/

# Install PyRIT and create build info (combined to ensure dependencies are available)
# Note: We use 'uv pip' because the devcontainer creates venv with uv (no pip by default)
RUN if [ "$PYRIT_SOURCE" = "pypi" ]; then \
        echo "Installing PyRIT from PyPI version: $PYRIT_VERSION"; \
        uv pip install --python /opt/venv/bin/python pyrit[dev,speech,opencv,fairness_bias,fastapi,playwright]==$PYRIT_VERSION; \
    elif [ "$PYRIT_SOURCE" = "local" ]; then \
        echo "Installing PyRIT from local source"; \
        uv pip install --python /opt/venv/bin/python -e .[dev,speech,opencv,fairness_bias,fastapi,playwright]; \
        echo "Building frontend..."; \
        /opt/venv/bin/python build_scripts/prepare_package.py; \
    fi && \
    echo "Creating build info..." && \
    /opt/venv/bin/python -c "import json; import pyrit; \
info = { \
    'source': '$PYRIT_SOURCE', \
    'version': pyrit.__version__, \
    'commit': '$GIT_COMMIT' if '$GIT_COMMIT' else None, \
    'modified': '$GIT_MODIFIED' == 'true', \
    'display': '$PYRIT_VERSION' if '$PYRIT_SOURCE' == 'pypi' else ('$GIT_COMMIT' + (' + local changes' if '$GIT_MODIFIED' == 'true' else '') if '$GIT_COMMIT' else pyrit.__version__) \
}; \
f = open('/app/build_info.json', 'w'); json.dump(info, f); f.close(); \
print(f'PyRIT version: {pyrit.__version__}')"

# Create directories for notebooks and data
RUN mkdir -p /app/notebooks /app/data /app/assets && \
    chmod -R 777 /app/notebooks /app/data /app/assets

# Create PyRIT config directory for env files (will be mounted at runtime)
RUN mkdir -p /home/vscode/.pyrit && \
    chown -R vscode:vscode /home/vscode/.pyrit

# Register the Jupyter kernel for the venv
RUN /opt/venv/bin/python -m ipykernel install --user --name pyrit --display-name "PyRIT"

# Copy doc to notebooks for Jupyter mode
RUN if [ -d "/app/doc" ]; then \
        cp -r /app/doc/* /app/notebooks/ || true; \
    fi

RUN chown -R vscode:vscode /app

# Create and set permissions for the startup script
COPY docker/start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Expose ports for JupyterLab (8888) and GUI (8000)
EXPOSE 8888 8000

# Set the entrypoint to the startup script (mode determined by PYRIT_MODE env var)
ENTRYPOINT ["/app/start.sh"]
