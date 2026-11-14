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
OPENAI_CHAT_MODEL="openai/gpt-oss-20b"
```

Get your API key from [console.groq.com](https://console.groq.com/).
:::

:::{tab-item} HuggingFace
```bash
OPENAI_CHAT_ENDPOINT="https://router.huggingface.co/v1"
OPENAI_CHAT_KEY="hf_your-token-here"
OPENAI_CHAT_MODEL="meta-llama/Llama-3.1-8B-Instruct"
```

Get your token from [huggingface.co/docs/hub/security-tokens](https://huggingface.co/docs/hub/security-tokens). Browse available models at [huggingface.co/models](https://huggingface.co/models).
:::

:::{tab-item} Anthropic
```bash
OPENAI_CHAT_ENDPOINT="https://api.anthropic.com/v1"
OPENAI_CHAT_KEY="sk-ant-your-key-here"
OPENAI_CHAT_MODEL="claude-opus-5-5"
```

Get your API key from [console.anthropic.com](https://console.anthropic.com/). Uses Anthropic's [OpenAI SDK compatibility layer](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).
:::

:::{tab-item} Google Gemini
```bash
OPENAI_CHAT_ENDPOINT="https://generativelanguage.googleapis.com/v1beta/openai"
OPENAI_CHAT_KEY="your-gemini-key-here"
OPENAI_CHAT_MODEL="gemini-3.8-flash"
```

Get your API key from [Google AI Studio](https://aistudio.google.com/). Uses the Gemini API's [OpenAI compatibility endpoint](https://ai.google.dev/gemini-api/docs/openai).
:::

:::{tab-item} Amazon Bedrock
```bash
OPENAI_CHAT_ENDPOINT="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"
OPENAI_CHAT_KEY="your-bedrock-api-key-here"
OPENAI_CHAT_MODEL="openai.gpt-oss-120b-1:0"
```

Create an Amazon Bedrock API key in the AWS console and adjust the region in the endpoint as needed. Bedrock's [Chat Completions API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html) serves only the models that support it (e.g., the OpenAI open-weight models); other Bedrock models use different APIs.
:::

:::{tab-item} OpenRouter
```bash
OPENAI_CHAT_ENDPOINT="https://openrouter.ai/api/v1"
OPENAI_CHAT_KEY="sk-or-v1-your-key-here"
OPENAI_CHAT_MODEL="anthropic/claude-sonnet-5"
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
