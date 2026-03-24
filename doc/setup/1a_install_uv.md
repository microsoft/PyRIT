# Install PyRIT Library

## Choose Your Installation Method

PyRIT offers three installation options for users:

### Option 1: Docker (Recommended for Quick Start)

If you want to get started quickly without setting up a Python environment, use our pre-configured Docker container. See the [Docker Installation Guide](./1b_install_docker.md) for complete instructions for running PyRIT in Docker with JupyterLab
- Pre-installed PyRIT with all dependencies
- No Python environment setup required
- Works on Windows, macOS, and Linux

**Use Docker if you:**
- Want to get started immediately
- Prefer not to manage Python environments
- Want a consistent, isolated environment
- Plan to use JupyterLab for interactive work

### Option 2: Local Installation with Pip/uv

For a traditional Python installation on your local machine, follow the instructions below.

**Use local installation if you:**
- Want to integrate PyRIT into existing Python workflows
- Prefer a lighter-weight installation
- Need to customize your Python environment
- Want direct access to PyRIT from your system Python

## Local Installation Instructions

To install PyRIT using pip, make sure you have Python 3.10, 3.11, 3.12, or 3.13 installed using `python --version`.
Alternatively, create an environment from the root directory as follows

```
uv sync

```

Next, check out our [docs](../index.md) and run the notebooks in your environment!

{important}
**Matching Notebooks to Your Version:**

Notebooks and your PyRIT installation must be on the same version. This pip installation gives you the **latest stable release** from PyPI.

1. **Check your installed version:**
   ```bash
   uv pip freeze | grep pyrit
   ```

   Or in Python:
   ```python
   import pyrit
   print(pyrit.__version__)
   ```

3. **Match notebooks to your version**:
   - If using a **release version** (e.g., `0.9.0`), download notebooks from the corresponding release branch: `https://github.com/Azure/PyRIT/tree/releases/v0.9.0/doc`
   - The automatically cloned notebooks from the `main` branch may not match your installed version
   - This website documentation shows the latest development version (`main` branch).

4. **If you installed from source:** The notebooks in your cloned repository will already match your code version.

### Option 3: Local Installation with Pip/Conda

We highly recommend using Option 2, but if you would prefer to use conda, See the [Conda Installation Guide](./1c_install_conda.md) for complete instructions for running PyRIT in a conda environment

## Next Step: Configure PyRIT

After installing, you need to configure your AI endpoint credentials before running PyRIT.

```{tip}
See the [Quick Start Guide](./quick_start.md) for a complete walkthrough, or jump directly to [Populating Secrets](./populating_secrets.md) to set up your `.env` file with your provider credentials.
```
