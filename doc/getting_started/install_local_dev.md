# Contributor Local Installation

Set up a PyRIT development environment on your local machine.

```{note}
**Development Version:** Contributor installations use the **latest development code** from the `main` branch, not a stable release. The notebooks in your cloned repository will match your code version.
```

## Setup with uv

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver that we use for PyRIT development.

**Why uv?**
- **Much faster** than pip (10-100x faster dependency resolution)
- **Simpler** environment management for pure Python projects
- **Native Windows support** — no WSL required, although if using a devcontainer, WSL is recommended
- **Automatic virtual environment management**
- **Compatible with existing pyproject.toml**

### Prerequisites

1. **Install uv**: Download from [https://github.com/astral-sh/uv](https://github.com/astral-sh/uv) or use:
   for windows:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   for macOS and Linux
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   or
   ```
   wget -qO- https://astral.sh/uv/install.sh | sh
   ```

2. **Python 3.12**: uv will automatically download and use the correct Python version based on `.python-version`

3. **Git**. Git is required to clone the repo locally. It is available to download [here](https://git-scm.com/downloads).
    ```bash
    git clone https://github.com/microsoft/PyRIT
    ```

4. **Node.js and npm**. Required for building the TypeScript/React frontend. Download [Node.js](https://nodejs.org/) (which includes npm). Version 18 or higher is recommended.

### Installation

1. Navigate to the directory where you cloned the PyRIT repo.

2. The repository includes a `.python-version` file that pins Python 3.12. Run:

```bash
uv sync
```

This command will:
- Create a `.venv` directory with a virtual environment
- Install Python 3.12 if not already available
- Install PyRIT in editable mode; `uv sync` by default installs in editable mode so no extra flag is necessary
- Install all dependencies including dev tools (pytest, ruff, etc.) via the `dev` dependency group
- Create a `uv.lock` file for reproducible builds


3. Verify Installation

```bash
uv pip show pyrit
```

You should see output showing the most recent PyRIT version and your Python dependencies.

### VS Code Integration

VS Code should automatically detect the `.venv` virtual environment. If not:

1. Press `Ctrl+Shift+P`
2. Type "Python: Select Interpreter"
3. Choose `.venv\Scripts\python.exe`

#### Running Jupyter Notebooks
You can create a Jupyter kernel using:
```bash
uv run ipython kernel install --user --env VIRTUAL_ENV $(pwd)/.venv --name=pyrit-dev
```
Start the server using
```bash
uv run jupyter lab
```
or using VS Code, open a Jupyter Notebook (.ipynb file) window, in the top search bar of VS Code, type `>Notebook: Select Notebook Kernel` > `Python Environments...` to choose the `pyrit-dev` kernel when executing code in the notebooks, like those in `examples`. You can also choose a kernel with the "Select Kernel" button on the top-right corner of a Notebook.

This will be the kernel that runs all code examples in Python Notebooks.


#### Running Python Scripts

Use `uv run` to execute Python with the virtual environment:

```bash
uv run python your_script.py
```

#### Running Tests

```bash
uv run pytest tests/
```

#### Running Specific Test Files

```bash
uv run pytest tests/unit/test_something.py
```

#### Using PyRIT CLI Tools

```bash
uv run pyrit_scan --help
uv run pyrit_shell
```

#### Running Jupyter Notebooks

```bash
uv run jupyter lab
```

#### Installing Additional Extras

PyRIT has several optional dependency groups. Install them as needed:

```bash
# For Hugging Face models
uv sync --extra huggingface

# For all extras
uv sync --extra all

# Multiple extras (dev dependencies are always included automatically)
uv sync --extra playwright --extra gcg
```

### Development Workflow

#### Adding New Dependencies

Edit `pyproject.toml` to add dependencies, then run:

```bash
uv sync
```

#### Updating Dependencies

```bash
uv lock --upgrade
uv sync
```

#### Running Code Formatters

```bash
uv run ruff format .
uv run ruff check --fix .
```

#### Running Type Checker

```bash
uv run ty check pyrit/
```

#### Pre-commit Hooks

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

## Next Step: Configure PyRIT

After installing, configure your AI endpoint credentials.

```{tip}
Jump to [Configure PyRIT](./configuration.md) to set up your credentials.
```


## Troubleshooting

Having issues? See the [Local Dev Troubleshooting](./troubleshooting/local_dev.md) guide for common problems and solutions.
