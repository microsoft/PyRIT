# User Local Installation

Install PyRIT directly into your Python environment for full control and easy integration with existing workflows.

## Prerequisites

- Python 3.10, 3.11, 3.12, 3.13, or 3.14 (check with `python --version`)

## Install with pip or uv

```bash
pip install pyrit
```

Or with uv:

```bash
uv pip install pyrit
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
   - If using a **release version** (e.g., `0.9.0`), download notebooks from the corresponding release branch: `https://github.com/microsoft/PyRIT/tree/releases/v0.9.0/doc`
   - The automatically cloned notebooks from the `main` branch may not match your installed version
   - This website documentation shows the latest development version (`main` branch).

3. **If you installed from source:** The notebooks in your cloned repository will already match your code version.

## Next Step: Configure PyRIT

After installing, configure your AI endpoint credentials.

```{tip}
Jump to [Configure PyRIT](./configuration.md) to set up your credentials.
```
