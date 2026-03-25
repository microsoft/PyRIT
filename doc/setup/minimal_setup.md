# Minimal Setup

The fastest way to try PyRIT — no configuration files needed. Just set two environment variables and run three lines of Python.

## Set Your Environment Variables

Set these in your shell before running Python:

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

These work with any OpenAI-compatible API — just change the endpoint and key for your provider (Azure, Ollama, Groq, etc.). See [Populating Secrets](./populating_secrets.md) for provider-specific examples.

## Initialize PyRIT

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[SimpleInitializer()])
```

That's it! This gives you:
- In-memory database (no persistence, but no setup needed)
- Default converter target, objective scorer, and attack configs
- Enough to run most PyRIT notebooks and examples

## Limitations

This minimal setup is great for trying PyRIT, but it **does not** register targets, scorers, or datasets into the registries. Features like `pyrit_scan` and automated scenarios require the full [Configuration File](./pyrit_conf.md) setup.

## Next Steps

When you're ready for persistent storage, the full registry, and `pyrit_scan` support, see the [Configuration File](./pyrit_conf.md) guide — the recommended setup for regular use.
