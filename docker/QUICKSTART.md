# PyRIT Docker - Quick Start Guide

Docker container for PyRIT with support for both **Jupyter Notebook** and **GUI** modes.

## Prerequisites
- Docker installed and running
- `~/.pyrit/.env` with your API keys
- `~/.pyrit/.pyrit_conf` with your configuration (operator, operation, initializers)
- Optionally, `~/.pyrit/.env.local` for additional environment overrides

## Azure Authentication in Docker

When deployed to **Azure infrastructure** (AKS, ACI, Azure VM), managed identity
works out of the box — no configuration needed. Assign the managed identity the
**Cognitive Services OpenAI User** role on your Azure OpenAI resources.

> **Note:** Azure authentication for local Docker Desktop is not yet supported.
> Local Docker is currently limited to targets that use API keys configured in
> your `.env` file.

## Quick Start

### 1. Build the Image

Build from local source (includes frontend):
```bash
python docker/build_pyrit_docker.py --source local
```

Build from PyPI version:
```bash
python docker/build_pyrit_docker.py --source pypi --version 0.10.0
```

Rebuild base image (when devcontainer changes):
```bash
python docker/build_pyrit_docker.py --source local --rebuild-base
```

> **Note:** The build script automatically builds the devcontainer base image if needed.
> The base image is cached and reused for faster subsequent builds.

### 2. Run PyRIT

Jupyter mode (port 8888):
```bash
python docker/run_pyrit_docker.py jupyter
```

GUI mode (port 8000):
```bash
python docker/run_pyrit_docker.py gui
```

The run script automatically mounts these files from `~/.pyrit/`:
- `.env` — API keys (required)
- `.env.local` — Additional environment overrides (optional)
- `.pyrit_conf` — PyRIT configuration: operator, operation, initializers (optional)

## Image Tags

Images are tagged with version information:
- PyPI: `pyrit:0.10.0`, `pyrit:latest`
- Local (clean): `pyrit:<full-commit-hash>`, `pyrit:latest`
- Local (modified): `pyrit:<full-commit-hash>-modified`, `pyrit:latest`

Run specific tag:
```bash
python docker/run_pyrit_docker.py gui --tag abc1234def5678
```

## Version Display

The GUI shows PyRIT version in a tooltip on the logo:
- PyPI builds: `0.10.0`
- Local builds: `abc1234def5678` or `abc1234def5678 + local changes`

## Docker Compose

Use profiles to run specific modes:

```bash
# Jupyter mode
docker-compose --profile jupyter up

# GUI mode
docker-compose --profile gui up
```

## Troubleshooting

**Image not found**: Run `python docker/build_pyrit_docker.py --source local` first

**.env missing**: Create `.env` file at `~/.pyrit/.env` with your API keys

**Azure auth fails in container**: Local Docker Desktop does not currently support
Azure token-based authentication. Use API key-based targets instead.

**GUI frontend missing**: Build with `--source local` (PyPI builds before GUI release won't work)

For complete documentation, see [docker/README.md](./README.md)
