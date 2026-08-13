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
  A["System environment"] --> B{"env_akv_ref configured?"}
  B -->|Yes| C["AKV bootstrap documents in order"]
  B -->|No| D{"Explicit env_files?"}
  C --> D
  D -->|Yes| E["Explicit files in order"]
  D -->|No| F["~/.pyrit/.env"]
  F --> G["~/.pyrit/.env.local"]
```

System environment variables are always the baseline. If no AKV bootstrap document or environment file is available, PyRIT continues initialization using the existing process environment only.

**Default file behavior** (no `env_akv_ref` or `env_files` field in `.pyrit_conf`):

| Priority | Source | Description |
| ---------- | -------- | ------------- |
| Lowest | System environment variables | Always loaded as the baseline |
| Medium | `~/.pyrit/.env` | Default config file (loaded if it exists) |
| Highest | `~/.pyrit/.env.local` | Local overrides (loaded if it exists) |

**AKV behavior** (with `env_akv_ref`): The referenced secrets load in list order before local files. Unless custom `env_files` are configured, `~/.pyrit/.env` loads afterward and `~/.pyrit/.env.local` loads last. Later bootstrap documents and local files may override earlier values.

PyRIT emits a warning when `env_akv_ref` is selected and default or explicit environment files coexist with it. The warning distinguishes files that are ignored from files that load afterward and override Key Vault values, making stale migration files visible at startup. When migrating to Key Vault, clear or remove `~/.pyrit/.env` and `~/.pyrit/.env.local`, remove explicit `env_files` when Key Vault should be the only source, and restart PyRIT so values already present in the process environment cannot mask the Key Vault configuration.

**Custom behavior** (with `env_files` field): Only your specified files are loaded, in order. They override Key Vault bootstrap values when both fields are configured, and default paths are completely ignored.

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

| Value             | Behavior                                                 |
| ----------------- | -------------------------------------------------------- |
| Omitted or `null` | Load default `~/.pyrit/.env` and `~/.pyrit/.env.local`   |
| `[]` (empty list) | Load **no** environment files                            |
| List of paths     | Load **only** the specified files (defaults are skipped) |

```yaml
env_files:
  - /path/to/.env
  - /path/to/.env.local
```

Local environment files use standard python-dotenv parsing and `${NAME}` interpolation. Interpolation follows assignment and file load order. The default `.env.local` can reference a value loaded earlier from `.env`, for example `FOOBAR=${OPENAI_CHAT_ENDPOINT}`. A `.env` value cannot reference a variable introduced only by the later `.env.local`; values are not resolved retroactively. Explicit `env_files` follow the order in which they are listed.

`env_akv_strict` does not apply to local files: malformed local lines retain python-dotenv's existing permissive skip-and-warn behavior. Local `kv:`, `akv:`, `azure_key_vault:`, and `env_akv_ref:` values remain literal; child-secret resolution is limited to Key Vault bootstrap documents. PyRIT does not define `env:` or `literal:` interpolation syntax. Use standard `${NAME}` interpolation instead.

Environment loading preserves the historical non-transactional dotenv behavior. Each bootstrap document and local file updates `os.environ` as it loads. If a later source or child-secret lookup fails, assignments made by earlier sources remain in the process environment.

When `env_akv_ref` is not configured, an empty `env_files` list or missing default files leaves existing process environment variables unchanged and initialization continues.

### `env_akv_ref`

Ordered Azure Key Vault secret URLs used to obtain bootstrap environment documents. Each secret value must contain dotenv-formatted entries. Authentication uses `DefaultAzureCredential`.

```yaml
env_akv_ref:
  - https://my-vault.vault.azure.net/secrets/shared-pyrit-env
  - https://my-vault.vault.azure.net/secrets/team-pyrit-env
```

Bootstrap documents load in list order with `override=True`; local environment files load afterward. Each document uses native dotenv interpolation against the process environment and assignments already parsed. A bootstrap document can mix literal values, `${NAME}` interpolation, and complete-value references to scalar secrets in the same vault:

```dotenv
OPENAI_CHAT_ENDPOINT="https://example.openai.azure.com/openai/v1"
OPENAI_CHAT_KEY="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key"
PINNED_OPENAI_CHAT_KEY="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key/version-id"
OPENAI_CHAT_MODEL="${PYRIT_OPENAI_CHAT_MODEL}"
```

Resolution is limited to one child-secret lookup:

1. PyRIT validates and loads the bootstrap dotenv document.
2. For each complete-value Key Vault reference in that document, PyRIT fetches the same-vault scalar secret and replaces the environment value.

For example, if `OPENAI_CHAT_KEY="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key"`, the value of the `openai-chat-key` secret becomes `OPENAI_CHAT_KEY` verbatim. If that secret happens to contain `kv:another-secret`, the final environment value is the string `kv:another-secret`; PyRIT does not fetch `another-secret`.

References must occupy the entire value. `kv:` is the canonical Key Vault prefix; `akv:`, `azure_key_vault:`, and `env_akv_ref:` are accepted aliases.

A Key Vault reference must use a full HTTPS secret URL from the bootstrap document's vault. Supported vault DNS suffixes are `.vault.azure.net`, `.vault.azure.cn`, and `.vault.usgovcloudapi.net`. An unversioned URL reads the latest secret version at initialization. Include the version in the URL to pin it. Short names, malformed paths, arbitrary hosts, and cross-vault child references are rejected before a client is created.

PyRIT does not cache referenced secrets. Each `kv:` occurrence in a bootstrap document performs a Key Vault read during initialization, including repeated references to the same URI. A later bootstrap or local file may override a reference after it has already been fetched.

```dotenv
LATEST_KEY_URI="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key"
PINNED_KEY="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key/version-id"
```

The bootstrap documents are held in memory and never written to disk. They load before explicit `env_files` or the default `~/.pyrit/.env` and `~/.pyrit/.env.local`, allowing local values to override shared configuration.

### `env_akv_strict`

Controls validation only of the Key Vault bootstrap document and defaults to `true`. It does not change parsing of `.env`, `.env.local`, or explicit `env_files`.

```yaml
env_akv_strict: false
```

In strict mode, any malformed dotenv line or variable without an equals sign stops that bootstrap document before it mutates the environment. Empty assignments such as `OPTIONAL_VALUE=` remain valid and set the variable to an empty string. A referenced Key Vault secret whose value is an empty string is also valid. A missing value represented by `None` is treated as an error. With `env_akv_strict: false`, PyRIT emits a warning containing only malformed line numbers and valueless variable names, skips those entries, and loads the valid assignments. Secret values are never included in the warning.

Non-strict mode does not suppress Key Vault or reference failures. Missing secrets, invalid `kv:` URLs, and bootstrap documents with no valid assignments still stop initialization. Because loading is non-transactional, values from earlier bootstrap documents remain if a later document fails, and raw values from the current document may remain if a child-secret lookup fails.

Key Vault clients use an explicit Azure retry policy with up to three retries and exponential backoff. Bootstrap parsing, invalid or missing secrets, authentication, authorization, and Azure transport failures are raised as `KeyVaultInitializationException` with the original exception preserved as the cause. The exception remains `ValueError`-compatible for callers migrating from the previous contract.

### `silent`

If `true`, suppresses print statements during initialization. Useful for non-interactive environments or when embedding PyRIT in other tools. Defaults to `false`.

### `server`

Client settings for connecting to or launching a PyRIT backend.

| Field | Description | Default |
| --- | --- | --- |
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

1. Configured AKV bootstrap documents load in order, followed by selected environment files
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

# Optional ordered Azure Key Vault bootstrap environment documents
# env_akv_ref:
#   - https://my-vault.vault.azure.net/secrets/my-pyrit-env
# env_akv_strict: false  # Optional; defaults to true

# Suppress initialization messages
silent: false

# Backend connection and local startup settings
server:
  url: http://localhost:8000
  startup_timeout: 120
```

## What's Next?

Once you're configured, head to the [Framework](../code/framework.md) to start using PyRIT.
