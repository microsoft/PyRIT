# User Docker Installation

Docker provides the fastest way to get started with PyRIT — a pre-configured container with JupyterLab, no local Python environment setup needed.

```{important}
**Version Compatibility:** This Docker setup installs the **latest stable release** of PyRIT from PyPI. If you're using a specific release (like `v0.9.0`), download notebooks from the corresponding release branch.
```

## Prerequisites

Before starting, install:

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

```{note}
On Windows, we recommend Docker Desktop. On Linux, you can install Docker Engine directly.
```

## Quick Start

### 1. Clone the PyRIT Repository

```bash
git clone https://github.com/Azure/PyRIT
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

### 3. Build and Start the Container

```bash
# Build and start the container in detached mode
docker-compose up -d

# View logs to confirm it's running
docker-compose logs -f
```

### 4. Access JupyterLab

Once the container is running, open your browser and navigate to:

```
http://localhost:8888
```

By default, JupyterLab runs without authentication for ease of use.

```{warning}
The default configuration has no password. For production use, consider adding authentication.
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
   - If using a **release version** (e.g., `0.9.0`), download notebooks from the corresponding release branch: `https://github.com/Azure/PyRIT/tree/releases/v0.9.0/doc`
   - The automatically cloned notebooks from the main branch may not match your installed version
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

### Stop the Container

```bash
docker-compose down
```

### Restart the Container

```bash
docker-compose restart
```

### View Logs

```bash
docker-compose logs -f
```

### Rebuild After Changes

If you modify the Dockerfile or requirements:

```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
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
   docker-compose down
   docker-compose up -d
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
