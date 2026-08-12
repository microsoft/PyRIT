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
  A["1. System Environment"] --> B{"env_akv_ref configured?"}
  B -->|Yes| C["2. First AKV secret"]
  B -->|No| D["2. ~/.pyrit/.env"]
  C --> E["3. Explicit env_files or ~/.pyrit/.env.local"]
  D --> F["3. ~/.pyrit/.env.local"]
```

System environment variables are always the baseline, but initialization requires either an AKV root or at least one environment file. A system-environment-only configuration is not considered a complete source.

**Default file behavior** (no `env_akv_ref` or `env_files` field in `.pyrit_conf`):

| Priority | Source | Description |
| ---------- | -------- | ------------- |
| Lowest | System environment variables | Always loaded as the baseline |
| Medium | `~/.pyrit/.env` | Default config file (loaded if it exists) |
| Highest | `~/.pyrit/.env.local` | Local overrides (loaded if it exists) |

**AKV behavior** (with `env_akv_ref`): The first referenced secret replaces `~/.pyrit/.env` as the root document. `~/.pyrit/.env.local` is loaded afterward if present. If multiple AKV URLs are configured, only the first is used.

PyRIT emits a warning when `env_akv_ref` is selected and default or explicit environment files coexist with it. The warning distinguishes files that are ignored from files that load afterward and override Key Vault values, making stale migration files visible at startup.

**Custom behavior** (with `env_files` field): Only your specified files are loaded, in order. They override the AKV root when both fields are configured, and default paths are completely ignored.

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
  - scorer
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
| --- | --- | --- |
| `target` | Prompt targets (OpenAI, Azure, AML, etc.) into the `TargetRegistry` | **Required for `pyrit_scan`** and any registry-based workflows |
| `scorer` | Scorers (refusal, content safety, harm-category, Likert, etc.) into the `ScorerRegistry` | **Required for automated scoring** and `pyrit_scan` evaluations |
| `technique` | Attack techniques into the `AttackTechniqueRegistry` | **Required for `pyrit_scan` scenarios** that select techniques |
| `load_default_datasets` | Seed datasets for all registered scenarios into memory | **Required for `pyrit_scan` scenarios** — they need data to run |

```{note}
**Execution order follows listing order.** Initializers execute in the order they appear in the config. Ensure dependencies are satisfied — for example, list `target` before `scorer` since scorers need targets to be registered first.
```

```{important}
The default initializers were consolidated and renamed as part of the cleanup leading up to the **v1.0.0** release. Configurations created before v1.0.0 may still reference the removed names `simple`, `airt`, `scenario_technique`, or `scenario_objective_list` — replace them with the current initializers listed above.
```

The recommended config:

```yaml
initializers:
  - name: target
    args:
      tags:
        - default
        - scorer
  - name: scorer
  - name: technique
  - name: load_default_datasets
```

```{note}
**`load_default_datasets` is optional.** Scenarios fetch their datasets from the registered provider on demand the first time they run, so you no longer need this initializer for everyday runs. Add it only when you want to preload every scenario's datasets up front — for example, to warm memory for repeated runs or to populate a database for offline use.
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
| Omitted or `null` | Load default `~/.pyrit/.env` and `~/.pyrit/.env.local`, or only `.env.local` after an AKV root |
| `[]` (empty list) | Load **no** environment files                                        |
| List of paths     | Load **only** the specified files (defaults are skipped)             |

```yaml
env_files:
  - /path/to/.env
  - /path/to/.env.local
```

When `env_akv_ref` is not configured, an empty list or missing default files causes initialization to fail because no environment source is available.

### `env_akv_ref`

Azure Key Vault secret URLs used to obtain the root environment document. The first URL is used; its secret value must contain dotenv-formatted entries. Authentication uses `DefaultAzureCredential`.

```yaml
env_akv_ref:
  - https://my-vault.vault.azure.net/secrets/my-pyrit-env
```

The root document can mix literal values with references to ambient environment variables and scalar secrets in the same vault:

```dotenv
OPENAI_CHAT_ENDPOINT="https://example.openai.azure.com/openai/v1"
OPENAI_CHAT_KEY="kv:openai-chat-key"
OPENAI_CHAT_MODEL="env:PYRIT_OPENAI_CHAT_MODEL"
```

Resolution is deliberately limited to two levels:

1. PyRIT fetches the first `env_akv_ref` secret and parses it as the bootstrap dotenv document.
2. For each reference in that document, PyRIT either copies one ambient `env:` value or fetches one scalar secret from the same vault. The resulting value is final and is not parsed as another reference.

For example, if `OPENAI_CHAT_KEY="kv:openai-chat-key"`, the value of the `openai-chat-key` secret becomes `OPENAI_CHAT_KEY` verbatim. If that secret happens to contain `kv:another-secret`, the final environment value is the string `kv:another-secret`; PyRIT does not fetch `another-secret`.

References must occupy the entire value. `kv:` is the canonical Key Vault prefix; `akv:`, `azure_key_vault:`, and `env_akv_ref:` are accepted aliases.

`literal:` is an escape hatch for a bootstrap value that begins with a reserved reference prefix. PyRIT removes `literal:` and returns the remainder without interpreting it as a reference. Quoting does not provide this escape because dotenv removes quotes while parsing. Values fetched from child secrets are already terminal and do not need this escape.

```dotenv
REFERENCE="kv:openai-chat-key"
LITERAL_VALUE="literal:kv:not-a-secret-name"
```

Here, `REFERENCE` retrieves `openai-chat-key`, while `LITERAL_VALUE` becomes the string `kv:not-a-secret-name`.

The AKV document is loaded before explicit `env_files` or the default `~/.pyrit/.env.local`, allowing local values to override shared configuration without writing the fetched document to disk.

### `silent`

If `true`, suppresses print statements during initialization. Useful for non-interactive environments or when embedding PyRIT in other tools. Defaults to `false`.

### `server`

Client settings for connecting to or launching a PyRIT backend.

| Field | Description | Default |
|---|---|---|
| `url` | Backend URL used when `--server-url` is omitted | `http://localhost:8000` |
| `startup_timeout` | Seconds `pyrit_scan --start-server` waits for a healthy backend before terminating the spawned process | `120` |

`startup_timeout` must be a finite number greater than zero. The `--startup-timeout` CLI option overrides the configured value for an individual scanner invocation.

Set `server: null` to reset all server settings, including values inherited from an earlier configuration layer, to their defaults.

```yaml
server:
  url: http://localhost:8000
  startup_timeout: 120
```

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

1. The AKV root or environment files are loaded, followed by local overrides
2. Default values are reset
3. Memory database is configured (from `memory_db_type`)
4. Initializers are executed in listed order

Because initializers run last, they can modify anything set up in earlier steps — including environment variables and the memory instance. In practice, built-in initializers like `target` and `scorer` only call `set_default_value` and `set_global_variable` and do not touch memory or environment variables. However, a custom initializer could override those if needed. When this happens, the initializer's changes take effect because it runs after the other settings have been applied.

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
    initializers=["target", "scorer"],      # Layer 3: override initializers
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
  - name: target
    args:
      tags:
        - default
        - scorer
  - name: scorer
  - name: technique
  - name: load_default_datasets

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

# Optional Azure Key Vault root environment document
# env_akv_ref:
#   - https://my-vault.vault.azure.net/secrets/my-pyrit-env

# Suppress initialization messages
silent: false

# Backend connection and local startup settings
server:
  url: http://localhost:8000
  startup_timeout: 120
```

## What's Next?

Once you're configured, head to the [Framework](../code/framework.md) to start using PyRIT.
