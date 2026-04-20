# Configure PyRIT

After installing, you need to tell PyRIT where your AI endpoints are and how to initialize the framework.

## Quickest Start

Set three environment variables and run three lines of Python — no files needed.

```{tip}
**Using Docker?** Set these environment variables inside the container (e.g., in a JupyterLab terminal or notebook cell with `%env`), not on your host machine. In the container, `~` refers to `/home/vscode`. Alternatively, skip ahead to [Persistent Setup](#for-persistent-setup) — the Docker install already mounts your host `~/.pyrit/.env` into the container.
```

::::{tab-set}

:::{tab-item} PowerShell
```powershell
$env:OPENAI_CHAT_ENDPOINT = "https://api.openai.com/v1"
$env:OPENAI_CHAT_KEY = "sk-your-key-here"
$env:OPENAI_CHAT_MODEL = "gpt-4o"
```
:::

:::{tab-item} Bash / macOS
```bash
export OPENAI_CHAT_ENDPOINT="https://api.openai.com/v1"
export OPENAI_CHAT_KEY="sk-your-key-here"
export OPENAI_CHAT_MODEL="gpt-4o"
```
:::

::::

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[SimpleInitializer()])
```

This gives you an in-memory database and default converter/scorer config — enough to run most notebooks and examples. Replace the endpoint/key/model for your provider (Azure, Ollama, Groq, etc.).

## For Persistent Setup

For anything beyond a quick test — especially `pyrit_scan`, scenarios, and repeated use — you'll want to save your configuration to files in `~/.pyrit/`:

:::::{grid} 1 1 2 2
:gutter: 3

::::{card} 🔑 Populating Secrets
:link: ./populating_secrets
**Set Up Your .env File**

Create `~/.pyrit/.env` with your provider credentials. Tabbed examples for OpenAI, Azure, Ollama, Groq, and more.
::::

::::{card} 📄 Configuration File (Recommended)
:link: ./pyrit_conf
**Full Framework Setup** ⭐

Set up `~/.pyrit/.pyrit_conf` for persistent config with initializers that register targets, scorers, and datasets — required for `pyrit_scan` and scenarios.
::::

:::::

## What's Next?

Once you're configured, head to the [Framework](../code/framework.md) to start using PyRIT.
