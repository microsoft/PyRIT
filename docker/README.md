# PyRIT Docker Container

This Docker container provides a pre-configured environment for running PyRIT (Python Risk Identification Tool for generative AI) with JupyterLab integration. It comes with pre-installed PyRIT, all necessary dependencies, and supports both CPU and optional GPU modes.

📚 **For complete installation instructions and troubleshooting, see the [Docker Installation Guide](./../doc/getting_started/install_docker.md) on our documentation site.**

This README contains technical details for working with the Docker setup locally.

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

The JupyterLab instance is configured to run without authentication by default for ease of use. For production deployments, consider adding authentication or running behind a secured proxy.

## Documentation & Support

- 📖 **[Docker Installation Guide](./../doc/getting_started/install_docker.md)** - Complete user-friendly installation instructions
- 🚀 **[PyRIT Documentation](https://microsoft.github.io/PyRIT/)** - Full documentation site
- 🔧 **[Contributing Guide](https://microsoft.github.io/PyRIT/contributing/readme/)** - For developers and contributors
- 🐛 **[Issues](https://github.com/microsoft/PyRIT/issues)** - Report bugs or request features
