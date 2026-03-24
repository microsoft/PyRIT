# Configuration

After installing PyRIT, configure your AI endpoint credentials and framework settings. PyRIT reads configuration from `~/.pyrit/` by default.

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} ⚡ Minimal Setup
:link: ./populating_secrets
**Just 2 Environment Variables**

Set `OPENAI_CHAT_ENDPOINT` and `OPENAI_CHAT_KEY` in a `.env` file. Works with OpenAI, Azure, Ollama, Groq, or any OpenAI-compatible API. Enough to run most notebooks.
::::

::::{card} 📄 Config File (Recommended)
:link: ./pyrit_conf
**Full Framework Setup** ⭐

Copy `.pyrit_conf_example` and `.env_example` to `~/.pyrit/`. This enables initializers that register targets, scorers, and datasets — required for `pyrit_scan` and automated scenarios.
::::

:::::

See the [Quick Start Guide](./quick_start.md) for a step-by-step walkthrough of setting up both files.
