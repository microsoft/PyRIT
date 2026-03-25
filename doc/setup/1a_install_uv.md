# Install PyRIT

Choose the installation method that best fits your use case.

### For Users

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 Docker Installation
:link: ./1b_install_docker
**Quick Start** ⭐

Get started immediately with a pre-configured environment. No Python setup needed, JupyterLab built-in, works on all platforms.
::::

::::{card} ☀️ Local Pip/uv Installation
:link: ./install_local
**Custom Setup**

Install directly into your Python environment. Lighter weight, full control, integrates with existing workflows.
::::

:::::

### For Contributors

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 DevContainers in VS Code
:link: ../contributing/1b_install_devcontainers
**Recommended** ⭐

Pre-configured Docker container with VS Code. All extensions pre-installed, consistent environment across all contributors.
::::

::::{card} ☀️ Local uv Development
:link: ../contributing/1a_install_uv
**Custom Dev Setup**

Install from source in editable mode. Full development control, use any IDE or editor, customize your environment.
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
