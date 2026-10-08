# PyRIT Docker Container

This Docker container provides a pre-configured environment for running PyRIT (Python Risk Identification Tool for generative AI) with JupyterLab integration. It comes with pre-installed PyRIT, all necessary dependencies, and supports both CPU and optional GPU modes.

📚 **For complete installation instructions and troubleshooting, see the [Docker Installation Guide](./../doc/getting_started/install_docker.md) on our documentation site.**

This README contains technical details for working with the Docker setup locally.

## Docker CI

The `docker_build` workflow builds the devcontainer base, builds the local-source
production image, and runs import, GUI, and Jupyter smoke checks on one runner.
Images stay in that runner's Docker daemon instead of being compressed, uploaded,
downloaded, and loaded between jobs. The PyPI checks temporarily run on manual
dispatches only, using a separate runner with the same co-located build/test
sequence. The two sequences share the existing GHA cache only for their identical
devcontainer build inputs. Production explicitly selects the daemon's `default`
builder (Docker driver) so it can consume the locally loaded base image rather
than looking for it in the cached builder's separate image store. Neither
sequence publishes images.

Local builds record the checked-out commit and require a clean source tree before
building, so Python and frontend compatibility stamps describe the same source.
PyPI checks resolve the latest stable, non-yanked release from PyPI at execution
time and pass that exact version to the production build. The optional
`pypiVersion` dispatch input selects an explicit published version, including a
prerelease.

Automatic PyPI checks on `main` are paused until a coordinated `1.2.0` release has
been published and validated. Restoring those checks is tracked in
[#3007](https://github.com/microsoft/PyRIT/issues/3007).

Selection uses PyPI's release ordering, without installing dependencies or sorting
version strings. The HTTP lookup has a 30-second socket timeout, and the selection
step has a five-minute limit; neither limit caps the Docker builds. Lookup failures,
invalid metadata, yanked releases, and missing published distributions fail without
an older-version fallback. The image removes
local Python and frontend sources and uses the selected distribution's packaged
assets. Compatibility validation remains mandatory: if the latest release predates
the required stamps, the build identifies that version and fails until a
coordinated release is published. Selecting a release does not establish that its
build and smoke checks pass.

The existing `Build Devcontainer`, `Build Production (local)`, `Test Import (local)`,
`Test GUI (local)`, and `Test Jupyter (local)` check names are retained as result
gates, along with `Build Production (PyPI)`, `Test Import (PyPI)`, `Test GUI (PyPI)`,
and `Test Jupyter (PyPI)`. Each enabled gate requires both its
execution job and its corresponding stage to succeed. A failed or cancelled
execution job fails all its enabled gates, even if an earlier stage succeeded;
missing or skipped stage results also fail. The two sources are independent, and
PyPI gates use literal job names so all four remain visible as intentionally
skipped checks on pushes, PRs, and merge-queue runs, without starting gate runners. Look at
`Build and test (local)` or `Build and test (PyPI)` for the actual build/test logs
and step timings.

GUI and Jupyter checks poll for HTTP 200 for up to 120 seconds, stop early if the
container exits, and bound each HTTP request. GUI checks use the compatibility-neutral
`/api/health` endpoint and also require frontend HTML; business API compatibility
enforcement remains enabled. Each service gets an ephemeral localhost port and its own container, which
is removed on success, failure, or a handled cancellation signal. Failures print
container state and recent logs. Application errors, including migration failures,
remain failures rather than being retried or hidden.

To run the same checks against an already built image:

```bash
bash docker/smoke_test.sh pyrit:local-test import
bash docker/smoke_test.sh pyrit:local-test gui
bash docker/smoke_test.sh pyrit:local-test jupyter
```

An optional third argument sets the readiness timeout in seconds. The helper and
workflow result gates have offline regression coverage in
`tests/unit/infra/test_docker_ci.py`.

## Features

- Pre-installed PyRIT with all dependencies
- JupyterLab integration for interactive usage
- CPU mode enabled by default for broad compatibility
- Option to enable GPU support (requires NVIDIA drivers and container toolkit)
- Automatic documentation cloning from the PyRIT repository when `CLONE_DOCS=true`
- Based on Microsoft Azure ML Python 3.12 inference image

## Directory Structure

```
.
├── Dockerfile                       # Container build configuration
├── README.md                        # This documentation file
├── requirements.txt                 # Python packages
├── docker-compose.yaml              # Docker Compose configuration
├── .env_container_settings_example  # Example env file (copy to .env.container.settings)
└── start.sh                         # Container startup script
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- Git and a PyRIT source checkout

## Quick Start

Create the mounted files described in [Environment Variables](#environment-variables)
first. Run these commands from the repository's `docker/` directory using Bash
(Git Bash on Windows).

### Source Build Provenance

Compose builds from the local checkout, not the latest PyPI release. Export the
actual full source commit and an exact `true`/`false` dirty flag before running it:

```bash
set -e
PYRIT_SOURCE_COMMIT=$(git rev-parse --verify HEAD)
source_status=$(git status --porcelain)
PYRIT_SOURCE_DIRTY=false
if [ -n "$source_status" ]; then
    PYRIT_SOURCE_DIRTY=true
fi
export PYRIT_SOURCE_COMMIT PYRIT_SOURCE_DIRTY
```

Repeat this setup in each new shell before any Compose command, and after source
changes before rebuilding. These are host-side build inputs, not API secrets or
static values to copy into `.env.container.settings`. Dirty local builds warn but
keep the same compatibility identity; published builds must be clean.

### Build and Start

```bash
docker build -f ../.devcontainer/Dockerfile -t pyrit-devcontainer ../.devcontainer
docker compose --profile jupyter up --build -d

# View logs
docker compose --profile jupyter logs -f

# Stop the container
docker compose --profile jupyter down
```

**Access JupyterLab**: Open the localhost URL with its access token from the logs.
For GUI mode, replace `--profile jupyter` with `--profile gui` and open port 8000.

> 💡 **New to Docker setup?** Check out the [step-by-step installation guide](./../doc/getting_started/install_docker.md) with detailed explanations and troubleshooting tips.

## Configuration

### Environment Variables

- **CLONE_DOCS**: When set to `true` (default), the container automatically clones the PyRIT repository and copies the documentation files to the notebooks directory. To disable this behavior, set `CLONE_DOCS=false` in your environment or in the `.env.container.settings` file.
- **ENABLE_GPU**: Set to `true` to enable GPU support (requires NVIDIA drivers and container toolkit). The container defaults to CPU-only mode.

The container expects environment files to provide configuration. Create them by copying the provided examples:

```bash
mkdir -p ~/.pyrit
cp ../.env_example ~/.pyrit/.env
cp ../.env_local_example ~/.pyrit/.env.local
# Note: Example file has underscores, but copy it to a file with dots
cp .env_container_settings_example .env.container.settings
```

- **`.env`** and **`.env.local`**: API keys and secrets (in `~/.pyrit/`, mounted read-only)
- **`.env.container.settings`**: Container-specific settings like GPU and docs cloning

The source-build inputs `PYRIT_SOURCE_COMMIT` and `PYRIT_SOURCE_DIRTY` come from
[Source Build Provenance](#source-build-provenance), not the example settings file.


### Adding Your Own Notebooks and Data

- **Notebooks**: Place your Jupyter notebooks in the `notebooks/` directory. They will be available automatically in JupyterLab.
- **Data**: Place your datasets or other files in the `data/` directory. Access them from your notebooks at `/app/data/`.

### Important Permission Configuration

Ensure your `notebooks/` , `data/` and `../assets/` directories have the correct permissions to allow container access:

```bash
chmod -R 777 notebooks/ data/ ../assets
```

## Docker Compose Configuration

Use the checked-in [docker-compose.yaml](./docker-compose.yaml). It supplies the
base image and required source build arguments for both the `jupyter` and `gui`
profiles. Keep these arguments when customizing volume mounts or other settings.

## Modifying the Configuration

Edit the `docker-compose.yaml` file to change port mappings, environment variables, or volume mounts as needed.

## Using PyRIT in JupyterLab

Start a new notebook in JupyterLab and try the following:

```python
import pyrit

print(pyrit.__version__)

# Example PyRIT usage:
# [Insert your PyRIT usage examples here]
```

## GPU Support (Optional)

To enable GPU support:

1. Edit `.env.container.settings` and add/modify the following:

   ```bash
    ENABLE_GPU=true  # Enable GPU support
   ```

2. Restart the container:

   ```bash
   docker compose --profile jupyter down
   docker compose --profile jupyter up -d
   ```

## Troubleshooting

For detailed troubleshooting steps, see the [Docker Installation Guide - Troubleshooting Section](./../doc/getting_started/install_docker.md#troubleshooting).

**Quick fixes:**

- **JupyterLab not accessible**: Check logs with `docker compose --profile jupyter logs pyrit-jupyter`
- **Missing source build variables**: Repeat [Source Build Provenance](#source-build-provenance) in the same shell
- **Permission issues**: Run `chmod -R 777 notebooks/ data/ ../assets/`
- **Environment file errors**: Ensure `.env`, `.env.local`, and `.env.container.settings` files exist

## Version Information

- **Base Image**: `mcr.microsoft.com/azureml/minimal-py312-inference:latest`
- **Python**: 3.12
- **PyTorch**: Latest version with CUDA support
- **PyRIT**: Built from the source checkout, with matching Python and frontend compatibility stamps

## Customization

You can further customize the container by:

1. Modifying the `Dockerfile` to add additional system or Python dependencies.
2. Adding your own notebooks to the `/app/notebooks` directory.
3. Changing startup options in the `start.sh` script.

## Security Note

Docker Compose and the run script publish JupyterLab and GUI ports on
`127.0.0.1` only by default. JupyterLab generates an access token shown in the
container logs. Non-admin GUI APIs allow unauthenticated access when the required
Entra settings are all unset or empty at backend startup; partial configuration
is rejected. Administrator routes remain restricted by default.
Do not expose either service remotely without authentication, HTTPS, and
appropriate network access restrictions.
See the [GUI Compose security guidance](./QUICKSTART.md#docker-compose).

## Documentation & Support

- 📖 **[Docker Installation Guide](./../doc/getting_started/install_docker.md)** - Complete user-friendly installation instructions
- 🚀 **[PyRIT Documentation](https://microsoft.github.io/PyRIT/)** - Full documentation site
- 🔧 **[Contributing Guide](https://microsoft.github.io/PyRIT/contributing/readme/)** - For developers and contributors
- 🐛 **[Issues](https://github.com/microsoft/PyRIT/issues)** - Report bugs or request features
