# PyRIT Docker Container

This Docker container provides a pre-configured environment for running PyRIT (Python Risk Identification Tool for generative AI) with JupyterLab integration. It comes with pre-installed PyRIT, all necessary dependencies, and supports both CPU and optional GPU modes.

📚 **For complete installation instructions and troubleshooting, see the [Docker Installation Guide](./../doc/setup/1b_install_docker.md) on our documentation site.**

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

## Quick Start

```bash
# Build and start the container in detached mode
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the container
docker-compose down
```

**Access JupyterLab**: Navigate to `http://localhost:8888` in your browser.

> 💡 **New to Docker setup?** Check out the [step-by-step installation guide](./../doc/setup/1b_install_docker.md) with detailed explanations and troubleshooting tips.

## Configuration

### Environment Variables

- **CLONE_DOCS**: When set to `true` (default), the container automatically clones the PyRIT repository and copies the documentation files to the notebooks directory. To disable this behavior, set `CLONE_DOCS=false` in your environment or in the `.env.container.settings` file.
- **ENABLE_GPU**: Set to `true` to enable GPU support (requires NVIDIA drivers and container toolkit). The container defaults to CPU-only mode.

The container expects environment files to provide configuration. Create them by copying the provided examples:

```bash
cp ../.env.example ../.env
cp ../.env.local_example ../.env.local
# Note: Example file has underscores, but copy it to a file with dots
cp .env_container_settings_example .env.container.settings
```

- **`.env`** and **`.env.local`**: API keys and secrets (in parent directory)
- **`.env.container.settings`**: Container-specific settings like GPU and docs cloning


### Adding Your Own Notebooks and Data

- **Notebooks**: Place your Jupyter notebooks in the `notebooks/` directory. They will be available automatically in JupyterLab.
- **Data**: Place your datasets or other files in the `data/` directory. Access them from your notebooks at `/app/data/`.

### Important Permission Configuration

Ensure your `notebooks/` , `data/` and `../assets/` directories have the correct permissions to allow container access:

```bash
chmod -R 777 notebooks/ data/ ../assets
```

## Recommended Docker Compose Configuration

To correctly map your local notebooks and data directories into the container, use the following Docker Compose configuration:

```yaml
services:
  pyrit:
    build:
      context: .
      dockerfile: Dockerfile
    image: pyrit:latest
    container_name: pyrit-jupyter
    ports:
      - "8888:8888"
    volumes:
      - ./notebooks:/app/notebooks
      - ./data:/app/data
      - ../assets:/app/assets
    env_file:
      - ../.env
      - ../.env.local
      - .env.container.settings
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8888 || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  notebooks:
  data:
```

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
   docker-compose down
   docker-compose up -d
   ```

## Troubleshooting

For detailed troubleshooting steps, see the [Docker Installation Guide - Troubleshooting Section](./../doc/setup/1b_install_docker.md#troubleshooting).

**Quick fixes:**

- **JupyterLab not accessible**: Check logs with `docker-compose logs pyrit`
- **Permission issues**: Run `chmod -R 777 notebooks/ data/ ../assets/`
- **Environment file errors**: Ensure `.env`, `.env.local`, and `.env.container.settings` files exist

## Version Information

- **Base Image**: `mcr.microsoft.com/azureml/minimal-py312-inference:latest`
- **Python**: 3.12
- **PyTorch**: Latest version with CUDA support
- **PyRIT**: Installed from PyPI (latest version)

## Customization

You can further customize the container by:

1. Modifying the `Dockerfile` to add additional system or Python dependencies.
2. Adding your own notebooks to the `/app/notebooks` directory.
3. Changing startup options in the `start.sh` script.

## Security Note

The JupyterLab instance is configured to run without authentication by default for ease of use. For production deployments, consider adding authentication or running behind a secured proxy.

## Documentation & Support

- 📖 **[Docker Installation Guide](./../doc/setup/1b_install_docker.md)** - Complete user-friendly installation instructions
- 🚀 **[PyRIT Documentation](https://azure.github.io/PyRIT/)** - Full documentation site
- 🔧 **[Contributing Guide](https://azure.github.io/PyRIT/contributing/readme/)** - For developers and contributors
- 🐛 **[Issues](https://github.com/Azure/PyRIT/issues)** - Report bugs or request features
