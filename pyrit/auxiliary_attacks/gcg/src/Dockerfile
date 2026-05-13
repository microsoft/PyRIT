FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    curl git build-essential && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Create venv
RUN uv venv /opt/venv --python 3.11
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

WORKDIR /app

# Install PyRIT with GCG extras to get all dependencies
COPY pyproject.toml MANIFEST.in README.md LICENSE /app/
COPY pyrit/ /app/pyrit/
RUN uv pip install -e ".[gcg]" && \
    uv pip install "fschat @ git+https://github.com/lm-sys/FastChat.git@2c68a13bfe10b86f40e3eefc3fcfacb32c00b02a"
