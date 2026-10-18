# User Docker Installation

Docker provides the fastest way to get started with PyRIT — a pre-configured container with JupyterLab, no local Python environment setup needed.

```{important}
**Version Compatibility:** This Compose setup builds Python and the frontend from
the same source checkout. Its compatibility identity includes the package version
and full Git commit. Any separately installed CLI must carry the same identity.
```

## Prerequisites

Before starting, install:

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- Git

```{note}
On Windows, we recommend Docker Desktop and Git Bash for the commands below.
On Linux, you can install Docker Engine directly.
```

## Quick Start

### 1. Clone the PyRIT Repository

```bash
git clone https://github.com/microsoft/PyRIT
cd PyRIT/docker
```

### 2. Set Up Environment Files

Create the required environment configuration files:

```bash
# Create the PyRIT config directory on your host
mkdir -p ~/.pyrit

# Create main environment files
cp ../.env_example ~/.pyrit/.env
cp ../.env_local_example ~/.pyrit/.env.local

# Create container-specific settings
# Note: The example file uses underscores, but you copy it to a file with dots
cp .env_container_settings_example .env.container.settings
```

```{important}
Edit the `.env` and `.env.local` files to add your API keys and configuration values. See [populating secrets](./populating_secrets.md) for details.
```

### 3. Set Source Build Provenance

From `PyRIT/docker`, derive the required build inputs from the checkout:

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

Run this in each new shell before any Compose command, and after changing source
before rebuilding. Do not hardcode a short commit or a clean flag, or save stale
values in `.env.container.settings`. Dirty local builds warn without changing the
identity; artifacts for publication must be built from clean source.

### 4. Build and Start the Container

```bash
docker build -f ../.devcontainer/Dockerfile -t pyrit-devcontainer ../.devcontainer
docker compose --profile jupyter up --build -d

# View logs to confirm it's running
docker compose --profile jupyter logs -f
```

The base-image build is required on first use and after devcontainer changes.
Use `--profile gui` instead for GUI mode on port 8000.

### 5. Access JupyterLab

Once the container is running, retrieve the access URL (including the authentication token) from the logs:

```bash
docker compose --profile jupyter logs pyrit-jupyter
```

Look for a line like:

```
http://127.0.0.1:8888/lab?token=<your-token>
```

Open that full URL in your browser. The token is required for access.

```{note}
JupyterLab is bound to `localhost` only and requires token authentication. Your API credentials are mounted into the container, so these protections prevent unauthorized access from other machines on your network.
```

## Using PyRIT in JupyterLab

Once JupyterLab is open:

1. **Navigate to the notebooks**: The PyRIT documentation notebooks will be automatically available in the `notebooks/` directory
2. **Check your PyRIT version**:

```python
import pyrit

print(pyrit.__version__)
```

3. **Match notebooks to your version**:
   - The image copies notebooks from the same checkout used to build PyRIT
   - After switching commits, repeat the source provenance setup and rebuild the image
   - This website documentation shows the latest development version (main branch)

4. **Start using PyRIT**:

```python
# Your PyRIT code here
```

## Directory Structure

The Docker setup includes these directories:

```
docker/
├── Dockerfile                       # Container configuration
├── docker-compose.yaml              # Docker Compose setup
├── requirements.txt                 # Python dependencies
├── start.sh                         # Startup script
├── notebooks/                       # Your Jupyter notebooks (auto-populated)
└── data/                           # Your data files
```

- **notebooks/**: Place your Jupyter notebooks here. They'll be available in JupyterLab.
- **data/**: Store datasets or other files here. Access them at `/app/data/` in notebooks.

## Configuration Options

### Environment Variables

Edit `.env.container.settings` to customize:

- **CLONE_DOCS**: Set to `true` (default) to automatically clone PyRIT documentation into the notebooks directory
- **ENABLE_GPU**: Set to `true` to enable GPU support (requires NVIDIA drivers and container toolkit)

### Adding Custom Notebooks

Simply place `.ipynb` files in the `notebooks/` directory, and they'll appear in JupyterLab automatically.

## Container Management

These commands assume the [source build variables](#3-set-source-build-provenance)
are still exported in the current shell.

### Stop the Container

```bash
docker compose --profile jupyter down
```

### Restart the Container

```bash
docker compose --profile jupyter restart
```

### View Logs

```bash
docker compose --profile jupyter logs -f
```

### Rebuild After Changes

After modifying source, the Dockerfile, or requirements, repeat
[Set Source Build Provenance](#3-set-source-build-provenance) before rebuilding:

```bash
docker compose --profile jupyter down
docker compose --profile jupyter build --no-cache
docker compose --profile jupyter up -d
```

## GPU Support (Optional)

To use NVIDIA GPUs with PyRIT:

### Prerequisites

1. Install [NVIDIA drivers](https://www.nvidia.com/Download/index.aspx)
2. Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

### Enable GPU in Container

1. Edit `.env.container.settings`:

   ```bash
   ENABLE_GPU=true
   ```

2. Restart the container:

   ```bash
   docker compose --profile jupyter down
   docker compose --profile jupyter up -d
   ```

3. Verify GPU access in a notebook:

   ```python
   import torch

   print(f"CUDA available: {torch.cuda.is_available()}")
   print(f"GPU count: {torch.cuda.device_count()}")
   ```

## Next Step: Configure PyRIT

After installing, configure your AI endpoint credentials.

```{tip}
Jump to [Configure PyRIT](./configuration.md) to set up your credentials.
```

## Troubleshooting

Having issues? See the [Docker Troubleshooting](./troubleshooting/docker.md) guide for common problems and solutions.
