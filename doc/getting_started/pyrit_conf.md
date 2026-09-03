# Configuration File (.pyrit_conf)

The recommended way to configure PyRIT. A `.pyrit_conf` file declares your database, initializers, and environment files in one place. PyRIT loads it automatically on startup, so you don't have to pass options every time.

## Quick Setup

Create `~/.pyrit/.pyrit_conf` and configure a Key Vault bootstrap document:

```yaml
env_akv_ref:
  - https://my-vault.vault.azure.net/secrets/my-pyrit-env
```

The Key Vault secret value uses dotenv syntax. When Azure is unavailable or you need a quick local patch, put only those values in `~/.pyrit/.env.local`.

## File Location

The default configuration file path is:

```text
~/.pyrit/.pyrit_conf
```

PyRIT looks for this file automatically on startup (via the CLI, shell, or `ConfigurationLoader`). If the file does not exist, PyRIT falls back to built-in defaults.

## Environment Configuration

```{important}
Azure Key Vault is PyRIT's canonical environment source for shared, CI/CD, and deployed configuration. Auto-discovered `~/.pyrit/.env` remains supported, but PyRIT warns because plaintext files are less secure. Use `~/.pyrit/.env.local` for deliberate plaintext local iteration or when Azure is unavailable.
```

See [Populating Secrets](./populating_secrets.md) for provider-specific variable examples.

### Loading Order

PyRIT loads environment sources in this order:

1. Existing process environment variables.
2. A Key Vault bootstrap document, auto-discovered `.env`, or explicit `env_files`. These sources fill only missing values.
3. Files named `.env.local`. These are the only dotenv sources that override existing values.

When `env_akv_ref` is configured, PyRIT ignores an auto-discovered `~/.pyrit/.env`, emits a security warning, and still loads `~/.pyrit/.env.local`. Explicit `env_files` are never blocked based on their filename or location.

### Using .env.local for Overrides

Use `~/.pyrit/.env.local` to override process or Key Vault values deliberately. This is useful for:

- Testing different targets
- Using personal credentials instead of shared ones
- Switching between configurations quickly

Only put the values you need to patch in this file. Because it contains plaintext secrets, do not commit it.

### Authentication Options

**API keys:** Store shared API keys as Key Vault scalar secrets and reference them from the bootstrap document with `kv:`. For local-only work, place them in `.env.local`.

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

Most users should enable the following initializers. These are what the `.pyrit_conf_example` ships with and support normal `pyrit_scan` and automated scenario workflows.

| Initializer | What It Registers | When You Need It |
| --- | --- | --- |
| `target` | Prompt targets (OpenAI, Azure, AML, etc.) into the `TargetRegistry` | Recommended for `pyrit_scan` and registry-based workflows |
| `scorer` | Scorers (refusal, content safety, harm-category, Likert, etc.) into the `ScorerRegistry` | Recommended for automated scoring and `pyrit_scan` evaluations |
| `technique` | Attack techniques into the `AttackTechniqueRegistry` | Recommended for scenarios that select registered techniques |

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
```

#### Optional Full Dataset Preload

`load_default_datasets` is not required for `pyrit_scan`. Scenario `DatasetAttackConfiguration` objects fetch only their requested datasets from registered providers on demand, then add them to memory.

Use `load_default_datasets` only when you intentionally want to preload every registered dataset—for example, to warm a shared cache or prepare an offline environment:

```yaml
initializers:
  - name: target
  - name: scorer
  - name: technique
  - name: load_default_datasets
```

Full preload can take several minutes and may require network access, provider credentials, or acceptance of gated dataset licenses. When preloading during local backend startup, increase `server.startup_timeout` if the configured timeout is not long enough.

### `custom_initializers_source`

Stores custom initializer Python files in a local directory or Azure Blob container. An Azure URI may include a blob-name prefix, which behaves like a folder:

```yaml
custom_initializers_source: https://account.blob.core.windows.net/pyrit-storage/custom-initializers
```

With this configuration, PyRIT reads and writes scripts directly under the `custom-initializers/` prefix in the `pyrit-storage` container. A SAS query string may be included; otherwise, PyRIT uses `DefaultAzureCredential`. The default is `~/.pyrit/custom_initializers`.

### `initialization_scripts`

Local paths to custom Python scripts containing `PyRITInitializer` subclasses. Paths can be absolute or relative to the current working directory.

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

Optional local dotenv paths. Key Vault remains the canonical shared source; explicit files support local and non-Azure workflows.

| Value             | Behavior                                                 |
| ----------------- | -------------------------------------------------------- |
| Omitted or `null` | Auto-discover `~/.pyrit/.env` and `~/.pyrit/.env.local` |
| `[]` (empty list) | Load **no** environment files                            |
| List of paths     | Load **only** the specified files (defaults are skipped) |

```yaml
env_files:
  - /path/to/.env
  - /path/to/.env.local
```

Local files use standard python-dotenv parsing and `${NAME}` interpolation. Ordinary files fill missing values; any file whose basename is `.env.local` overrides existing values. Explicit files load in their listed order.

Complete-value `kv:`, `akv:`, `azure_key_vault:`, and `env_akv_ref:` references resolve in local files as well as the remote bootstrap document. Local references may use any validated supported Key Vault URL; remote child references must remain in the bootstrap document's vault. A local assignment that loses to an existing value does not fetch its secret.

Ordinary malformed dotenv lines retain python-dotenv's permissive behavior. `env_akv_strict` controls malformed Key Vault reference syntax in all sources: strict mode raises; non-strict mode warns and skips that assignment. Authentication, authorization, transport, missing-secret, and missing-value failures always raise.

Environment loading preserves the historical non-transactional dotenv behavior. The bootstrap document and each local file update `os.environ` as they load. If a later source or child-secret lookup fails, assignments made by earlier sources remain in the process environment.

When `env_akv_ref` is not configured, an empty `env_files` list or missing default files leaves existing process environment variables unchanged and initialization continues.

`PYTHON_DOTENV_DISABLED` disables the complete PyRIT environment-loading step using python-dotenv's accepted true values (`1`, `true`, `t`, `yes`, and `y`, case-insensitive). When enabled, default discovery and configured `env_akv_ref` or `env_files` sources are skipped. Existing process environment variables remain unchanged.

### `env_akv_ref`

List-shaped Azure Key Vault bootstrap configuration. It may be omitted, empty, or contain one secret URL; multiple bootstrap URLs are rejected. The secret value contains dotenv-formatted entries, and authentication uses `DefaultAzureCredential`.

```yaml
env_akv_ref:
  - https://my-vault.vault.azure.net/secrets/shared-pyrit-env
```

The bootstrap document fills values missing from the process environment and uses native dotenv interpolation against the process environment and assignments already parsed. It can mix literal values, `${NAME}` interpolation, and complete-value references to scalar secrets in the same vault:

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

PyRIT does not cache referenced secrets. Each winning `kv:` occurrence performs a Key Vault read during initialization. References that lose to an existing process or earlier source are not fetched. The standalone exporter described below resolves bootstrap references independently and does not change runtime precedence.

```dotenv
LATEST_KEY_URI="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key"
PINNED_KEY="kv:https://my-vault.vault.azure.net/secrets/openai-chat-key/version-id"
```

The bootstrap document stays in memory. Use `.env.local` when an intentional local override is required.

### `env_akv_strict`

Controls Key Vault bootstrap validation and Key Vault reference syntax in local files. It defaults to `true`.

```yaml
env_akv_strict: false
```

In strict mode, malformed bootstrap dotenv lines, valueless bootstrap entries, and malformed Key Vault references stop initialization. Empty assignments such as `OPTIONAL_VALUE=` remain valid. With `env_akv_strict: false`, PyRIT warns and skips malformed bootstrap entries and malformed reference assignments without logging secret values.

Non-strict mode does not suppress operational failures. Missing secrets, authentication, authorization, transport errors, and a bootstrap document with no valid assignments still stop initialization. Loading remains non-transactional, so earlier successful assignments remain.

Key Vault clients use an explicit Azure retry policy with up to three retries and exponential backoff. Bootstrap parsing, invalid or missing secrets, authentication, authorization, and Azure transport failures are raised as `KeyVaultInitializationException` with the original exception preserved as the cause. The exception remains `ValueError`-compatible for callers migrating from the previous contract.

### Exporting AKV Configuration for Debugging

Environment initialization never writes secrets to disk. To inspect an AKV-only configuration explicitly from a source checkout, run the standalone helper:

```powershell
python -m build_scripts.export_akv_environment `
  --secret-url https://my-vault.vault.azure.net/secrets/my-pyrit-env
```

The helper accepts exactly one `--secret-url` and writes `~/.pyrit/.env_akv` by default. This file is not auto-loaded by PyRIT and excludes process, `.env`, explicit file, and `.env.local` values.

The helper resolves child-secret references and writes plaintext secrets with owner-only permissions where supported. It refuses to overwrite an existing path; remove the file when debugging is complete. Use `--output` to select a different path and `--non-strict` to skip malformed entries or references with warnings.

### `silent`

If `true`, suppresses print statements during initialization. Useful for non-interactive environments or when embedding PyRIT in other tools. Defaults to `false`.

### `server`

Client settings for connecting to or launching a PyRIT backend.

| Field | Description | Default |
| --- | --- | --- |
| `url` | Backend URL used when `--server-url` is omitted | `http://localhost:8000` |
| `startup_timeout` | Seconds `pyrit_scan start-server` waits for a healthy backend before terminating the spawned process | `120` |
| `auth_mode` | Backend authentication mode: `auto`, `azure_cli`, `device_code`, or `none` | `auto` |

`startup_timeout` must be a finite number greater than zero. The `--startup-timeout` CLI option overrides the configured value for an individual scanner invocation.

Set `server: null` to reset all server settings, including values inherited from an earlier configuration layer, to their defaults.

```yaml
server:
  url: http://localhost:8000
  startup_timeout: 120
  auth_mode: auto
```

In `auto` mode, the CLI reads the backend's public `/api/auth/config` endpoint. It sends no
token when authentication is disabled. For an authenticated backend, it uses Entra device-code
login with the exact Microsoft Graph `User.Read` scope. The encrypted persistent token cache
normally prevents a new prompt on each run. A non-interactive process fails instead of waiting
for a prompt.

Use an explicit mode when needed:

```yaml
server:
  url: https://copyrit.example.com/
  auth_mode: azure_cli
```

`azure_cli` is an explicit compatibility mode. It can send a Microsoft Graph token with
permissions beyond `User.Read` because the Azure CLI application controls the token's granted
permissions. Prefer `auto` or `device_code`. If you accept this behavior, sign in to the
backend's tenant before using `azure_cli`:

```bash
az login --tenant <tenant-id>
pyrit_scan --config-file ./.pyrit_conf list-scenarios
```

Use `device_code` to require the same exact-scope interactive flow as `auto`. Use `none` only
when you intentionally need to suppress authentication discovery. Access tokens are not stored
in `.pyrit_conf`.

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
| Highest  | Individual arguments   | CLI flags like `--initializers` or API keyword arguments               |

This means you can set sensible defaults in `~/.pyrit/.pyrit_conf` and override specific values on a per-run basis without modifying the file.

### Execution Order Within Resolved Configuration

The 3-layer model above determines **which config values are selected**. Once resolved, the values are applied in a fixed runtime order:

1. Process values are retained, AKV or ordinary local sources fill gaps, and `.env.local` applies final overrides
2. Default values are reset
3. Memory database is configured (from `memory_db_type`)
4. Initializers are executed in listed order

Because initializers run last, they can modify anything set up in earlier steps — including environment variables and the memory instance. In practice, built-in initializers like `target` and `scorer` only call `set_default_value` and `set_global_variable` and do not touch memory or environment variables. However, a custom initializer could override those if needed. When this happens, the initializer's changes take effect because it runs after the other settings have been applied.

## Usage

### From the CLI

The CLI and shell automatically load `~/.pyrit/.pyrit_conf`. You can also point to a different config file:

```bash
pyrit_scan run airt.scam --config-file ./my_project_config.yaml
```

Individual CLI arguments (like `--initializers`) override values from the config file.

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
    memory_db_type="in_memory",  # Layer 3: override database type
    initializers=["target", "scorer"],  # Layer 3: override initializers
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
  # Optional full preload/cache warming; scenarios fetch requested datasets on demand.
  # Full preload can take several minutes and may require network access,
  # provider credentials, accepted dataset licenses, and a larger startup_timeout.
  # - name: load_default_datasets

# Custom initialization scripts (optional)
# Omit or set to null for no scripts; [] to explicitly load nothing
# initialization_scripts:
#   - /path/to/my_custom_initializer.py

# Canonical: zero or one Azure Key Vault bootstrap environment document
# env_akv_ref:
#   - https://my-vault.vault.azure.net/secrets/my-pyrit-env
# env_akv_strict: true

# Optional plaintext local patch or non-Azure workflow
# env_files:
#   - /path/to/.env.local

# Suppress initialization messages
silent: false

# Backend connection and local startup settings
server:
  url: http://localhost:8000
  startup_timeout: 120
```

## What's Next?

Once you're configured, head to the [Framework](../code/framework.md) to start using PyRIT.
