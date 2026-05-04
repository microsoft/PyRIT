# Troubleshooting: Contributor Local Installation

Common issues when setting up PyRIT for local development.

## uv Issues

### uv command not found

Make sure uv is in your PATH. Restart PowerShell after installation.

### Import errors

Ensure you're using `uv run python` or have activated the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Dependency conflicts

Try regenerating the lock file:

```powershell
Remove-Item uv.lock
uv sync
```

### Module not found errors

PyRIT is installed in editable mode, so changes to the source code are immediately reflected. If you see import errors:

```bash
uv sync --reinstall-package pyrit
```
