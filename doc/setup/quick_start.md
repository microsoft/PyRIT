# Getting Started

Get PyRIT running in minutes. This guide walks you through the complete setup — install, configure, and verify.

## Step 1: Install

```bash
pip install pyrit
```

Or with uv:

```bash
uv pip install pyrit
```

See [Docker](./install_docker.md), [Local](./install_local.md), or the [Install PyRIT](./install.md) page for all installation options.

## Step 2: Create Your Configuration Directory

PyRIT looks for configuration files in `~/.pyrit/`:

```bash
mkdir -p ~/.pyrit
```

## Step 3: Set Up Your Environment (.env)

Create `~/.pyrit/.env` with your AI endpoint credentials. You only need **three variables** to get started — these work with any OpenAI-compatible API:

::::{tab-set}

:::{tab-item} OpenAI
```bash
OPENAI_CHAT_ENDPOINT="https://api.openai.com/v1"
OPENAI_CHAT_KEY="sk-your-key-here"
OPENAI_CHAT_MODEL="gpt-4o"
```
:::

:::{tab-item} Azure OpenAI
```bash
OPENAI_CHAT_ENDPOINT="https://your-resource.openai.azure.com/openai/v1"
OPENAI_CHAT_KEY="your-azure-key-here"
OPENAI_CHAT_MODEL="your-deployment-name"
```
:::

:::{tab-item} Ollama (Local)
```bash
OPENAI_CHAT_ENDPOINT="http://127.0.0.1:11434/v1"
OPENAI_CHAT_KEY="not-needed"
OPENAI_CHAT_MODEL="llama2"
```
:::

:::{tab-item} Groq
```bash
OPENAI_CHAT_ENDPOINT="https://api.groq.com/openai/v1"
OPENAI_CHAT_KEY="gsk_your-key-here"
OPENAI_CHAT_MODEL="llama3-8b-8192"
```
:::

::::

See [Populating Secrets](./populating_secrets.md) for the full list of supported environment variables and the `.env_example` file in the repo root.

## Step 4: Set Up Your Config File (.pyrit_conf)

Copy the example configuration file:

```bash
cp /path/to/PyRIT/.pyrit_conf_example ~/.pyrit/.pyrit_conf
```

Or create `~/.pyrit/.pyrit_conf` with this recommended content:

```yaml
memory_db_type: sqlite

initializers:
  - name: simple
  - name: load_default_datasets
  - name: scorers
  - name: targets
    args:
      tags:
        - default
        - scorer

silent: false
```

### Why Initializers Matter

The initializers above are what make PyRIT's full feature set work. Without them, features like `pyrit_scan` and automated scenarios won't have the targets, scorers, or datasets they need.

| Initializer | What It Does | When You Need It |
|---|---|---|
| `simple` | Sets up baseline defaults for converters and scorers using your `OPENAI_CHAT_*` env vars | Always — this is the foundation |
| `targets` | Registers prompt targets (OpenAI, Azure, etc.) into the TargetRegistry from your env vars | For `pyrit_scan`, scenarios, and any registry-based workflows |
| `scorers` | Registers scorers (refusal, content safety, harm-category, etc.) into the ScorerRegistry | For automated scoring and `pyrit_scan` evaluations |
| `load_default_datasets` | Loads seed datasets for all registered scenarios into memory | For `pyrit_scan` scenarios — they need data to run |

```{note}
Initializers run in a fixed order regardless of how you list them: `simple`/`targets` first, then `scorers`, then `load_default_datasets`. This ensures dependencies are satisfied (e.g., `scorers` needs targets to be registered first).
```

See [Configuration File (.pyrit_conf)](./pyrit_conf.md) for full configuration reference.

## Step 5: Verify It Works

```python
from pyrit.setup import initialize_from_config_async

# Loads ~/.pyrit/.pyrit_conf and ~/.pyrit/.env automatically
await initialize_from_config_async()
```

If you prefer not to use a config file, you can initialize directly:

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[SimpleInitializer()])
```

## What's Next?

- 📖 [Cookbooks](../cookbooks/README.md) — Walk through common red teaming workflows
- 🔌 [Targets](../code/targets/0_prompt_targets.md) — Connect to different AI systems
- 📦 [Scenarios](../code/scenarios/0_scenarios.ipynb) — Run standardized evaluation scenarios
- 🖥️ [CLI & Shell](../code/front_end/1_pyrit_scan.ipynb) — Use `pyrit_scan` for automated assessments
