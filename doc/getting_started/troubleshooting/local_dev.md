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
uv sync --extra dev
```

### Module not found errors

PyRIT is installed in editable mode, so changes to the source code are immediately reflected. If you see import errors:

```bash
uv sync --reinstall-package pyrit
```

## Conda Issues

### Conda environment not activating

Make sure you're using the correct activation command for your shell:

```bash
conda activate pyrit-dev
```

If `conda activate` doesn't work, you may need to initialize conda for your shell first:

```bash
conda init powershell   # Windows PowerShell
conda init bash         # macOS/Linux
```

Then restart your terminal.

### Package conflicts with conda and pip

When using conda environments with `pip install -e .[dev]`, you may see dependency conflicts. To resolve:

```bash
conda deactivate
conda remove -n pyrit-dev --all
conda create -y -n pyrit-dev python=3.12
conda activate pyrit-dev
pip install -e .[dev]
```

### Jupyter kernel not finding PyRIT

If Jupyter can't find your PyRIT installation, make sure the kernel is registered from within the activated conda environment:

```bash
conda activate pyrit-dev
pip install ipykernel
python -m ipykernel install --user --name=pyrit-dev --display-name "PyRIT Dev"
```

Then select the "PyRIT Dev" kernel in Jupyter or VS Code.
