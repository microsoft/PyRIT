# Local Installation with Pip/uv

Install PyRIT directly into your Python environment for full control and easy integration with existing workflows.

## Prerequisites

- Python 3.10, 3.11, 3.12, or 3.13 (check with `python --version`)

## Install

```bash
pip install pyrit
```

Or with uv:

```bash
uv pip install pyrit
```

Or sync from the repository root (installs all dependencies):

```bash
uv sync
```

## Matching Notebooks to Your Version

```{important}
Notebooks and your PyRIT installation must be on the same version. This pip installation gives you the **latest stable release** from PyPI.
```

1. **Check your installed version:**
   ```bash
   pip freeze | grep pyrit
   ```

   Or in Python:
   ```python
   import pyrit
   print(pyrit.__version__)
   ```

2. **Match notebooks to your version**:
   - If using a **release version** (e.g., `0.9.0`), download notebooks from the corresponding release branch: `https://github.com/Azure/PyRIT/tree/releases/v0.9.0/doc`
   - The automatically cloned notebooks from the `main` branch may not match your installed version
   - This website documentation shows the latest development version (`main` branch).

3. **If you installed from source:** The notebooks in your cloned repository will already match your code version.

## Next Step: Configure PyRIT

After installing, you need to configure your AI endpoint credentials before running PyRIT.

```{tip}
See the [Quick Start Guide](./quick_start.md) for a complete walkthrough, or jump directly to [Configuration](./configuration.md) to set up your `.env` and `.pyrit_conf` files.
```
