# Install PyRIT

Choose the installation method that best fits your use case.

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 User Docker Installation
:link: ./install_docker
**Quick Start** ⭐

Get started immediately with a pre-configured environment. No Python setup needed, JupyterLab built-in, works on all platforms.
::::

::::{card} ☀️ User Local Installation
:link: ./install_local
**Custom Setup**

Install with pip, uv, or conda directly into your Python environment. Lighter weight, full control, integrates with existing workflows.
::::

::::{card} 🐋 Contributor Docker Installation
:link: ./install_devcontainers
**Recommended for Contributors** ⭐

Pre-configured Docker container with VS Code. All extensions pre-installed, consistent environment across all contributors.
::::

::::{card} ☀️ Contributor Local Installation
:link: ./install_local_dev
**Custom Dev Setup**

Install from source in editable mode. Full development control, use any IDE or editor, customize your environment.
::::

:::::

```{important}
**Version Compatibility:**
- **User installations** (Docker, Local) install the **latest stable release** from PyPI
- **Contributor installations** (Docker, Local) use the **latest development code** from the `main` branch
- Always match your notebooks to your PyRIT version
```

## Next Step: Configure PyRIT

After installing, you need to configure your AI endpoint credentials before running PyRIT.

```{tip}
See the [Getting Started](./quick_start.md) guide for a complete walkthrough, or jump directly to [Configuration](./configuration.md) to set up your credentials.
```
