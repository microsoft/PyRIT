# Troubleshooting: Contributor Local Installation

Common issues when setting up PyRIT for local development.

## uv Issues

### uv command not found

Make sure uv is in your `PATH`. Restart your terminal (PowerShell, bash, zsh, etc.) after installing uv so the updated `PATH` is picked up.

### Import errors

Ensure you're using `uv run python` or have activated the virtual environment:

::::{tab-set}

:::{tab-item} PowerShell (Windows)
```powershell
.\.venv\Scripts\Activate.ps1
```
:::

:::{tab-item} Bash (macOS / Linux)
```bash
source .venv/bin/activate
```
:::

::::

### Dependency conflicts

Try regenerating the lock file (`rm` works in PowerShell as an alias for `Remove-Item`, so the same commands work everywhere):

```bash
rm uv.lock
uv sync
```

### Module not found errors

PyRIT is installed in editable mode, so changes to the source code are immediately reflected. If you see import errors:

```bash
uv sync --reinstall-package pyrit
```
