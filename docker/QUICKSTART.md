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

First follow [Source Build Provenance](./README.md#source-build-provenance) to
export the full source commit and exact dirty flag in the current shell, and
build the devcontainer base image as described in [Build and Start](./README.md#build-and-start).
From the `docker/` directory, use profiles to run specific modes:

```bash
# Jupyter mode
docker compose --profile jupyter up --build

# GUI mode
docker compose --profile gui up --build
```

Both profiles publish ports on `127.0.0.1` only. Open the GUI at
`http://127.0.0.1:8000` or Jupyter at `http://127.0.0.1:8888` on the Docker host.
The backend still listens on `0.0.0.0` **inside the container** so Docker can
forward requests; the host-side port mapping is what restricts publication.

**Security:** The GUI API does not require authentication when
`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_ALLOWED_GROUP_IDS` are all unset.
Target API keys do not authenticate incoming GUI requests. Do not change the
GUI mapping to `8000:8000` or expose it through a proxy without first configuring
Entra authentication, HTTPS, and appropriate network access restrictions.
Direct (non-Docker) backend launches should likewise bind to `127.0.0.1` unless
you have secured remote access.

### Updating an existing GUI installation

Existing containers keep their original port mappings. Editing the Compose file
or running `docker restart` does **not** apply the corrected publication.
After updating your checkout, recreate the GUI container from the same `docker`
directory, preserving any Compose project name or options used originally.
Refresh the [source provenance variables](./README.md#source-build-provenance)
in this shell before running Compose:

```bash
docker compose --profile gui up -d --force-recreate pyrit-gui
```

This briefly interrupts the GUI; finish any in-progress work first. If the
existing installation is exposed to an untrusted network, restrict ingress
before updating it. Check any custom Compose overrides for broader publication.

Verify the **running** container, not just the YAML:

```bash
docker inspect pyrit-gui --format '{{json .NetworkSettings.Ports}}'
```

The `8000/tcp` entry must contain exactly one binding with
`"HostIp":"127.0.0.1"` and `"HostPort":"8000"`, with no additional wildcard or
IPv6 publication.

For local no-auth acceptance, ensure the Entra settings above and
`PYRIT_ALLOW_UNAUTHENTICATED_ADMIN` are unset in the effective container
environment, including mounted environment files. Verify:

- On the Docker host, the GUI loads at `http://127.0.0.1:8000`,
  `/api/auth/config` reports `"enabled": false`, and `/api/targets` returns 200
  without credentials.
- `/api/config` still returns 403 without credentials.
- From another machine, a TCP connection to the Docker host's LAN address on
  port 8000 fails. An HTTP 401 or 403 is not a successful network-isolation test.

The Docker CI workflow checks resolved Compose bindings without starting
containers. That check does not replace this live local/remote verification.

## Troubleshooting

**Image not found**: Run `python docker/build_pyrit_docker.py --source local` first

**.env missing**: Create `.env` file at `~/.pyrit/.env` with your API keys

**Azure auth fails in container**: Local Docker Desktop does not currently support
Azure token-based authentication. Use API key-based targets instead.

**GUI frontend missing**: Build with `--source local` (PyPI builds before GUI release won't work)

For complete documentation, see [docker/README.md](./README.md)
