# Install PyRIT

Choose the installation method that best fits your use case.

## For Users

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 User Docker Installation
:link: ./install_docker
**Quick Start** ⭐

Pre-configured container with JupyterLab. Best if you want to get started immediately without Python setup, prefer an isolated environment, or are new to PyRIT.
::::

::::{card} ☀️ User Local Installation
:link: ./install_local
**Custom Setup**

Install with pip, uv, or conda. Best if you need to integrate PyRIT into existing Python workflows, prefer lighter-weight installations, or want direct access from your system Python.
::::

:::::

## For Contributors

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🐋 Contributor Docker Installation
:link: ./install_devcontainers
**Recommended for Contributors** ⭐

Pre-configured Docker container with VS Code. Best if you use VS Code, want consistency with other contributors, and prefer not to manage Python environments manually.
::::

::::{card} ☀️ Contributor Local Installation
:link: ./install_local_dev
**Custom Dev Setup**

Install from source with uv in editable mode. Best if you use a different IDE, want full control over your environment, or need to customize beyond what DevContainers offer.
::::

:::::

```{important}
**Version Compatibility:**
- **User installations** (Docker, Local) install the **latest stable release** from PyPI
- **Contributor installations** (Docker, Local) use the **latest development code** from the `main` branch
- Always match your notebooks to your PyRIT version
```

## Next Step: Configure PyRIT

After installing, configure your AI endpoint credentials.

```{tip}
Jump to [Configure PyRIT](./configuration.md) to set up your credentials.
```
