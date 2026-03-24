# Install PyRIT

Choose the installation method that best fits your use case.

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 Docker
:link: ./1b_install_docker
**Quick Start** ⭐

Pre-configured container with JupyterLab. No Python setup needed. Works on all platforms.
::::

::::{card} ☀️ Local Pip/uv
:link: ./install_local
**Custom Setup**

Install directly into your Python environment. Lighter weight, integrates with existing workflows.
::::

::::{card} ☀️ Local Conda
:link: ./1c_install_conda
**Conda Environment**

Use conda to manage your Python environment. Same `pip install pyrit` under the hood.
::::

:::::

```{important}
**Version Compatibility:**
- **User installations** (Docker, Pip/Conda) install the **latest stable release** from PyPI
- **Contributor installations** (DevContainers, Local Development) use the **latest development code** from the `main` branch
- Always match your notebooks to your PyRIT version
```

## Next Step: Configure PyRIT

After installing, you need to configure your AI endpoint credentials before running PyRIT.

```{tip}
See the [Quick Start Guide](./quick_start.md) for a complete walkthrough, or jump directly to [Configuration](./configuration.md) to set up your `.env` and `.pyrit_conf` files.
```
