# Populating Secrets - Quick Start Guide

Before running PyRIT, you need to configure access to AI targets. This guide will help you get started quickly.

## Fastest Way to Get Started

The simplest way to configure PyRIT requires just two environment variables and three lines of code:

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[SimpleInitializer()])
```

This sets up PyRIT with sensible defaults using in-memory storage. You just need to set two environment variables:
- `OPENAI_CHAT_ENDPOINT` - Your AI endpoint URL
- `OPENAI_CHAT_KEY` - Your API key

With this setup, you can run most PyRIT notebooks and examples!

## Setting Up Environment Variables

PyRIT loads secrets and endpoints from environment variables or `.env` files. The `.env_example` file shows the format and available options.

### Environment Variable Precedence

When `initialize_pyrit_async` runs, environment variables are loaded in a specific order. **Later sources override earlier ones:**

```{mermaid}
flowchart LR
    A["1. System Environment"] --> B{"env_files provided?"}
    B -->|No| C["2. ~/.pyrit/.env"]
    C --> D["3. ~/.pyrit/.env.local"]
    B -->|Yes| E["2. Your specified files (in order)"]
```

**Default behavior** (no `env_files` argument):

| Priority | Source | Description |
|----------|--------|-------------|
| Lowest | System environment variables | Always loaded as the baseline |
| Medium | `~/.pyrit/.env` | Default config file (loaded if it exists) |
| Highest | `~/.pyrit/.env.local` | Local overrides (loaded if it exists) |

**Custom behavior** (with `env_files` argument): Only your specified files are loaded, in order. Default paths are completely ignored.

### Creating Your .env File

1. Copy `.env_example` to `~/.pyrit/.env`
2. Add your API credentials for your provider of choice:

::::{tab-set}

:::{tab-item} OpenAI
```bash
OPENAI_CHAT_ENDPOINT="https://api.openai.com/v1"
OPENAI_CHAT_KEY="sk-your-key-here"
OPENAI_CHAT_MODEL="gpt-4o"
```

Get your API key from [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
:::

:::{tab-item} Azure OpenAI
```bash
OPENAI_CHAT_ENDPOINT="https://your-resource.openai.azure.com/openai/v1"
OPENAI_CHAT_KEY="your-azure-key-here"
OPENAI_CHAT_MODEL="your-deployment-name"
```

Find these values in Azure Portal: `Azure AI Services > Azure OpenAI > Your Resource > Keys and Endpoint`.
:::

:::{tab-item} Ollama (Local)
```bash
OPENAI_CHAT_ENDPOINT="http://127.0.0.1:11434/v1"
OPENAI_CHAT_KEY="not-needed"
OPENAI_CHAT_MODEL="llama2"
```

Requires [Ollama](https://ollama.com/) running locally. No API key needed.
:::

:::{tab-item} Groq
```bash
OPENAI_CHAT_ENDPOINT="https://api.groq.com/openai/v1"
OPENAI_CHAT_KEY="gsk_your-key-here"
OPENAI_CHAT_MODEL="llama3-8b-8192"
```

Get your API key from [console.groq.com](https://console.groq.com/).
:::

:::{tab-item} OpenRouter
```bash
OPENAI_CHAT_ENDPOINT="https://openrouter.ai/api/v1"
OPENAI_CHAT_KEY="sk-or-v1-your-key-here"
OPENAI_CHAT_MODEL="anthropic/claude-3.7-sonnet"
```

Get your API key from [openrouter.ai](https://openrouter.ai/).
:::

::::

```{note}
All these providers use the same three environment variables (`OPENAI_CHAT_ENDPOINT`, `OPENAI_CHAT_KEY`, `OPENAI_CHAT_MODEL`) because PyRIT's `OpenAIChatTarget` works with any OpenAI-compatible API. Just point the endpoint to your provider and you're set.
```

### Using .env.local for Overrides

You can use `~/.pyrit/.env.local` to override values in `~/.pyrit/.env` without modifying the base file. This is useful for:
- Testing different targets
- Using personal credentials instead of shared ones
- Switching between configurations quickly

Simply create `.env.local` in your `~/.pyrit/` directory and add any variables you want to override.

### Custom Environment Files

You can also specify exactly which `.env` files to load using the `env_files` parameter:

```python
from pathlib import Path
from pyrit.setup import initialize_pyrit_async

await initialize_pyrit_async(
    memory_db_type="InMemory",
    env_files=[Path("./project-config.env"), Path("./local-overrides.env")]
)
```

When `env_files` is provided:
- **Only** the specified files are loaded (default paths are skipped entirely)
- Files are loaded in order—later files override earlier ones
- A `ValueError` is raised if any specified file doesn't exist

The CLI also supports custom environment files via the `--env-files` flag.

## Authentication Options

### API Keys (Default)
The simplest approach is using API keys as shown above. Most targets support this method.

### Azure Entra Authentication (Optional)
For Azure resources, you can use Entra auth instead of API keys. This requires:

1. Install Azure CLI for your OS:
   - [Windows](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows)
   - [Linux](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux)
   - [macOS](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-macos)

2. Log in to Azure:
   ```bash
   az login
   ```

When using Entra auth, you don't need to set API keys for Azure resources.

## Next Steps

- For detailed configuration options, see the [Configuration Guide](../code/setup/1_configuration.ipynb)
- For database options beyond in-memory storage, see the [Memory Documentation](../code/memory/0_memory.md)
