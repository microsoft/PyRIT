# Configuration File (.pyrit_conf)

The recommended way to configure PyRIT. A `.pyrit_conf` file declares your database, initializers, and environment files in one place. PyRIT loads it automatically on startup, so you don't have to pass options every time.

## Quick Setup

```bash
mkdir -p ~/.pyrit
cp .pyrit_conf_example ~/.pyrit/.pyrit_conf
cp .env_example ~/.pyrit/.env
```

Then edit both files for your environment. The `.pyrit_conf` tells PyRIT _how_ to initialize; the `.env` tells it _where_ your AI targets are.

## File Location

The default configuration file path is:

```
~/.pyrit/.pyrit_conf
```

PyRIT looks for this file automatically on startup (via the CLI, shell, or `ConfigurationLoader`). If the file does not exist, PyRIT falls back to built-in defaults.

## Setting Up Secrets (.env files)

The `.pyrit_conf` file works hand-in-hand with `.env` files for your API credentials. See [Populating Secrets](./populating_secrets.md) for provider-specific examples of what to put in your `.env` file.

### Environment Variable Precedence

When PyRIT initializes, environment variables are loaded in a specific order. **Later sources override earlier ones:**

```{mermaid}
flowchart LR
    A["1. System Environment"] --> B{"env_files in .pyrit_conf?"}
    B -->|No| C["2. ~/.pyrit/.env"]
    C --> D["3. ~/.pyrit/.env.local"]
    B -->|Yes| E["2. Your specified files (in order)"]
```

**Default behavior** (no `env_files` field in `.pyrit_conf`):

| Priority | Source | Description |
|----------|--------|-------------|
| Lowest | System environment variables | Always loaded as the baseline |
| Medium | `~/.pyrit/.env` | Default config file (loaded if it exists) |
| Highest | `~/.pyrit/.env.local` | Local overrides (loaded if it exists) |

**Custom behavior** (with `env_files` field): Only your specified files are loaded, in order. Default paths are completely ignored.

### Using .env.local for Overrides

You can use `~/.pyrit/.env.local` to override values in `~/.pyrit/.env` without modifying the base file. This is useful for:
- Testing different targets
- Using personal credentials instead of shared ones
- Switching between configurations quickly

Simply create `.env.local` in your `~/.pyrit/` directory and add any variables you want to override.

### Authentication Options

**API Keys (Default):** The simplest approach — set `OPENAI_CHAT_KEY` and similar variables in your `.env` file. Most targets support this method.

**Azure Entra Authentication (Optional):** For Azure resources, you can use Entra auth instead of API keys. This requires the [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) and `az login`. When using Entra auth, you don't need to set API keys for Azure resources.

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

#### Recommended Defaults

Most users should enable the following initializers. These are what the `.pyrit_conf_example` ships with and are required for features like `pyrit_scan` and automated scenarios.

| Initializer | What It Registers | When You Need It |
|---|---|---|
| `simple` | Baseline defaults for converters, scorers, and attack configs using your `OPENAI_CHAT_*` env vars | Always — provides the foundation for most PyRIT operations |
| `targets` | Prompt targets (OpenAI, Azure, AML, etc.) into the `TargetRegistry` | **Required for `pyrit_scan`** and any registry-based workflows |
| `scorers` | Scorers (refusal, content safety, harm-category, Likert, etc.) into the `ScorerRegistry` | **Required for automated scoring** and `pyrit_scan` evaluations |
| `load_default_datasets` | Seed datasets for all registered scenarios into memory | **Required for `pyrit_scan` scenarios** — they need data to run |

```{note}
**Execution order is automatic.** Initializers are sorted by their built-in `execution_order` regardless of how you list them in the config: `simple`/`targets` run first (order 1), then `scorers` (order 2), then `load_default_datasets` (order 10). This ensures dependencies are satisfied — for example, `scorers` needs targets to be registered first.
```

The recommended config:

```yaml
initializers:
  - name: simple
  - name: load_default_datasets
  - name: scorers
  - name: targets
    args:
      tags:
        - default
        - scorer
```

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
pyrit_scan run --config-file ./my_project_config.yaml --database InMemory
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

## What's Next?

Once you're configured, head to the [User Guide](../code/user_guide.md) to start using PyRIT.
