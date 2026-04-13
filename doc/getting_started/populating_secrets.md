# Populating Secrets

PyRIT loads API credentials from environment variables or `.env` files. This page shows how to set up your `.env` file with credentials for your AI provider.

```{tip}
For the full configuration story — including `.env.local` overrides, custom env file paths, and environment variable precedence — see the [Configuration File](./pyrit_conf.md) guide.
```

## Creating Your .env File

```{tip}
**Using Docker?** Create and edit `~/.pyrit/.env` on your **host** machine — the Docker Compose setup automatically mounts it into the container. You don't need to run these commands inside the container.
```

1. Create the PyRIT config directory and copy the example file:

```bash
mkdir -p ~/.pyrit
cp .env_example ~/.pyrit/.env
```

2. Edit `~/.pyrit/.env` and fill in the credentials for your provider:

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

## What's in .env_example?

The `.env_example` file in the repository root contains entries for **all** supported targets — OpenAI chat, responses, realtime, image, TTS, video, Azure ML, embeddings, content safety, and more. Most users only need the three `OPENAI_CHAT_*` variables above. Fill in additional sections only as you need them.

## What's Next?

- [Configuration File (.pyrit_conf)](./pyrit_conf.md) — Set up the full configuration with initializers, database, and environment file management
- [Framework](../code/framework.md) — Start using PyRIT
