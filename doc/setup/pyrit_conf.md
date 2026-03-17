# Configuration File (.pyrit_conf)

PyRIT supports an optional YAML configuration file that declares initialization settings — database type, initializers, environment files, and more. When present, these settings are loaded automatically so you don't have to pass them every time you start PyRIT. It (`.pyrit_conf`) is basically just a YAML file specifying how to call `initialize_pyrit`. You can try it yourself in the [PyRIT Configuration Notebook](../code/setup/1_configuration.ipynb)

## File Location

The default configuration file path is:

```
~/.pyrit/.pyrit_conf
```

PyRIT looks for this file automatically on startup (via the CLI, shell, or `ConfigurationLoader`). If the file does not exist, PyRIT falls back to built-in defaults.

To get started, copy the example file from the repository root into your home directory:

```bash
mkdir -p ~/.pyrit
cp .pyrit_conf_example ~/.pyrit/.pyrit_conf
```

Then edit `~/.pyrit/.pyrit_conf` to match your environment.

## Configuration Fields

The `.pyrit_conf` file is YAML-formatted with the following fields:

### `memory_db_type`

The database backend for storing prompts and results.

| Value       | Description                                                 |
| ----------- | ----------------------------------------------------------- |
| `in_memory` | Temporary in-memory database (data lost on exit)            |
| `sqlite`    | Persistent local SQLite database **(default)**              |
| `azure_sql` | Azure SQL database (requires connection string in env vars) |

Values are case-insensitive and accept underscores or hyphens (e.g., `in_memory`, `in-memory`, `InMemory` all work).

### `initializers`

A list of built-in initializers to run during PyRIT initialization. Initializers configure default values for converters, scorers, and targets. Names are automatically normalized to snake_case.

Each entry can be:

- **A simple string** — just the initializer name
- **A dictionary** — with `name` and optional `args` (each arg is a list of strings passed to `initialize_async`)

Example:

```yaml
initializers:
  - simple
  - name: target
    args:
      tags:
        - default
        - scorer
```

Use `pyrit list initializers` in the CLI to see all registered initializers. See the [initializer documentation notebook](../code/setup/pyrit_initializer.ipynb) for reference.

### `initialization_scripts`

Paths to custom Python scripts containing `PyRITInitializer` subclasses. Paths can be absolute or relative to the current working directory.

| Value             | Behavior                           |
| ----------------- | ---------------------------------- |
| Omitted or `null` | No custom scripts loaded (default) |
| `[]` (empty list) | Explicitly load no scripts         |
| List of paths     | Load the specified scripts         |

```yaml
initialization_scripts:
  - /path/to/my_custom_initializer.py
  - ./local_initializer.py
```

### `env_files`

Environment file paths to load during initialization. Later files override values from earlier files.

| Value             | Behavior                                                             |
| ----------------- | -------------------------------------------------------------------- |
| Omitted or `null` | Load default `~/.pyrit/.env` and `~/.pyrit/.env.local` if they exist |
| `[]` (empty list) | Load **no** environment files                                        |
| List of paths     | Load **only** the specified files (defaults are skipped)             |

```yaml
env_files:
  - /path/to/.env
  - /path/to/.env.local
```

### `silent`

If `true`, suppresses print statements during initialization. Useful for non-interactive environments or when embedding PyRIT in other tools. Defaults to `false`.

## Configuration Precedence

PyRIT uses a 3-layer configuration precedence model. **Later layers override earlier ones:**

```{mermaid}
flowchart LR
    A["1. Default config\n~/.pyrit/.pyrit_conf"] --> B["2. Explicit config file\n--config-file path"]
    B --> C["3. Individual arguments\nCLI flags / API params"]
```

| Priority | Source                 | Description                                                             |
| -------- | ---------------------- | ----------------------------------------------------------------------- |
| Lowest   | `~/.pyrit/.pyrit_conf` | Loaded automatically if it exists                                       |
| Medium   | Explicit config file   | Passed via `--config-file` (CLI) or `config_file` parameter             |
| Highest  | Individual arguments   | CLI flags like `--database`, `--initializers`, or API keyword arguments |

This means you can set sensible defaults in `~/.pyrit/.pyrit_conf` and override specific values on a per-run basis without modifying the file.

### Execution Order Within Resolved Configuration

The 3-layer model above determines **which config values are selected**. Once resolved, the values are applied in a fixed runtime order:

1. Environment files are loaded
2. Default values are reset
3. Memory database is configured (from `memory_db_type`)
4. Initializers are executed (sorted by `execution_order`)

Because initializers run last, they can modify anything set up in earlier steps — including environment variables and the memory instance. In practice, built-in initializers like `simple` and `airt` only call `set_default_value` and `set_global_variable` and do not touch memory or environment variables. However, a custom initializer could override those if needed. When this happens, the initializer's changes take effect because it runs after the other settings have been applied.

## Usage

### From the CLI

The CLI and shell automatically load `~/.pyrit/.pyrit_conf`. You can also point to a different config file:

```bash
pyrit scan run --config-file ./my_project_config.yaml --database InMemory
```

Individual CLI arguments (like `--database`) override values from the config file.

### From Python

Use `initialize_from_config_async` to initialize PyRIT directly from a config file:

```python
from pyrit.setup import initialize_from_config_async

# Uses ~/.pyrit/.pyrit_conf by default
await initialize_from_config_async()

# Or specify a custom path
await initialize_from_config_async("/path/to/my_config.yaml")
```

For more control, use `ConfigurationLoader.load_with_overrides` which implements the full 3-layer precedence model:

```python
from pathlib import Path
from pyrit.setup import ConfigurationLoader

# Layer 1 (~/.pyrit/.pyrit_conf) is always loaded automatically if it exists.
# Layer 2 and 3 overrides are optional keyword arguments:
config = ConfigurationLoader.load_with_overrides(
    config_file=Path("./my_project.yaml"),  # Layer 2: explicit config file (omit to skip)
    memory_db_type="in_memory",             # Layer 3: override database type
    initializers=["simple"],                # Layer 3: override initializers
)

await config.initialize_pyrit_async()
```

## Full Example

Below is an annotated example showing all available fields. Copy this to `~/.pyrit/.pyrit_conf` and customize as needed, or copy over from `.pyrit_conf_example` in the base PyRIT folder (i.e. `PYRIT_PATH`).

```yaml
# Memory Database Type
# Options: in_memory, sqlite, azure_sql
memory_db_type: sqlite

# Built-in initializers to run
# Each can be a string or a dict with name + args
initializers:
  - simple

# Custom initialization scripts (optional)
# Omit or set to null for no scripts; [] to explicitly load nothing
# initialization_scripts:
#   - /path/to/my_custom_initializer.py

# Environment files (optional)
# Omit or set to null to use defaults (~/.pyrit/.env, ~/.pyrit/.env.local)
# Set to [] to load no env files
# env_files:
#   - /path/to/.env
#   - /path/to/.env.local

# Suppress initialization messages
silent: false
```

## Next Steps

- [Populating Secrets](./populating_secrets.md) — Setting up environment variables and `.env` files
- [Configuration Guide](../code/setup/1_configuration.ipynb) — Interactive examples of `initialize_pyrit_async` options
- [PyRIT Initializers](../code/setup/pyrit_initializer.ipynb) — Creating and using built-in and custom initializers
- [Default Values](../code/setup/default_values.md) — How initializer defaults work under the hood
