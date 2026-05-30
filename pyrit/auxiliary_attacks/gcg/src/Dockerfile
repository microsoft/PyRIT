FROM nvidia/cuda:12.1.1-devel-ubuntu22.04

# Note: we deliberately do NOT use apt's python3.11 — on Ubuntu 22.04 jammy
# the python3.11 package is frozen at 3.11.0rc1, which predates the addition
# of sys.get_int_max_str_digits in Python 3.11.0 final and breaks modern
# torch (torch._dynamo.polyfills.sys references the attribute at import).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git build-essential ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install uv-managed Python 3.11 (python-build-standalone) and create the venv
# from it, ignoring any system Python so we always get a complete 3.11.
RUN uv python install 3.11
RUN uv venv /opt/venv --python 3.11 --python-preference only-managed
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

WORKDIR /app

# Install PyRIT with GCG extras to get all dependencies
COPY pyproject.toml MANIFEST.in README.md LICENSE /app/
COPY pyrit/ /app/pyrit/
RUN uv pip install -e ".[gcg]"
