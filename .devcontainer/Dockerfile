# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

# Makes installation faster
ENV UV_COMPILE_BYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-c"]

USER root

# Remove the Yarn repository (has expired GPG key and we don't use Yarn)
RUN rm -f /etc/apt/sources.list.d/yarn.list 2>/dev/null || true

# Install required system packages + ODBC prerequisites
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
      sudo \
      unixodbc \
      unixodbc-dev \
      libgl1 \
      git \
      curl \
      xdg-utils \
      build-essential

# Install Microsoft ODBC Driver 18 & SQL tools
# Note: Debian Trixie's sqv rejects SHA1 signatures, so we use gpg directly to import the Microsoft key
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      gnupg \
 && curl -sL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor \
      > /usr/share/keyrings/microsoft-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/microsoft.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
      msodbcsql18 \
      mssql-tools18 \
 && echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> /etc/profile.d/sqltools.sh

# audio back-ends needed by Azure Speech SDK
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
      libasound2 \
      libpulse0

# Install uv system-wide and create pyrit-dev venv
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/
RUN uv venv /opt/venv --python 3.11 --prompt pyrit-dev \
 && chown -R vscode:vscode /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Node.js 24.x LTS for frontend development
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get install -y --no-install-recommends nodejs

# vscode user already exists in the base image, just ensure sudo access
RUN echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Pre-create common user caches and fix permissions
RUN mkdir -p /home/vscode/.cache/pre-commit \
 && mkdir -p /home/vscode/.vscode-server \
 && mkdir -p /home/vscode/.cache/pip \
 && mkdir -p /home/vscode/.cache/uv \
 && mkdir -p /home/vscode/.cache/venv \
 && mkdir -p /home/vscode/.cache/pylance \
 && chown -R vscode:vscode /home/vscode/.cache /home/vscode/.vscode-server \
 && chmod -R 755 /home/vscode/.cache/pip /home/vscode/.cache/pylance /home/vscode/.cache/venv /home/vscode/.cache/uv \
 && chmod -R 755 /home/vscode/.vscode-server

USER vscode
# Create bash configuration files and activate the venv in bash sessions
RUN touch /home/vscode/.bashrc /home/vscode/.bash_profile \
 && echo "[ -f /opt/venv/bin/activate ] && source /opt/venv/bin/activate" >> /home/vscode/.bashrc \
 && echo "[ -f /opt/venv/bin/activate ] && source /opt/venv/bin/activate" >> /home/vscode/.bash_profile

# Configure Git for better performance with bind mounts
RUN git config --global core.preloadindex true \
 && git config --global core.fscache true \
 && git config --global gc.auto 256 \
 && git config --global status.showUntrackedFiles all \
 && git config --global core.fsmonitor true

# Set cache directories so they can be mounted
ENV PIP_CACHE_DIR="/home/vscode/.cache/pip"
ENV UV_CACHE_DIR="/home/vscode/.cache/uv"
