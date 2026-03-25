# Configuration

After installing PyRIT, you need to tell it where your AI targets are and how to initialize the framework. All configuration lives in `~/.pyrit/` by default.

## How Configuration Works

PyRIT's configuration has two parts:

1. **Secrets (`.env` file)** — Your API endpoints, keys, and model names. These are environment variables that tell PyRIT how to connect to AI providers like OpenAI, Azure, Ollama, etc.

2. **Settings (`.pyrit_conf` file)** — Your database type, initializers, and framework preferences. This YAML file tells PyRIT what to set up on startup — which targets to register, which scorers to load, and which datasets to make available.

When PyRIT starts, it loads the `.pyrit_conf`, reads the `.env` files it references, then runs the initializers in order. The result is a fully configured framework ready to use.

```{mermaid}
flowchart LR
    A[".pyrit_conf"] --> B["Load .env files"]
    B --> C["Configure database"]
    C --> D["Run initializers"]
    D --> E["Ready to use"]
```

## Choose Your Setup

:::::{grid} 1 1 3 3
:gutter: 3

::::{card} ⚡ Minimal Setup
:link: ./minimal_setup
**Try PyRIT Quickly**

Set 2 environment variables in your shell and run 3 lines of Python. No files needed. Great for a first look.
::::

::::{card} 🔑 Populating Secrets
:link: ./populating_secrets
**Set Up Your .env File**

Create `~/.pyrit/.env` with your provider credentials. Tabbed examples for OpenAI, Azure, Ollama, Groq, and OpenRouter.
::::

::::{card} 📄 Config File (Recommended)
:link: ./pyrit_conf
**Full Framework Setup** ⭐

Set up `.pyrit_conf` + `.env` for persistent config. Enables initializers that register targets, scorers, and datasets — required for `pyrit_scan` and automated scenarios.
::::

:::::

## What Goes Where?

| File | Location | Contains | Required? |
|------|----------|----------|-----------|
| `.env` | `~/.pyrit/.env` | API keys, endpoints, model names | Yes — PyRIT needs to know where your targets are |
| `.env.local` | `~/.pyrit/.env.local` | Personal overrides (optional) | No — useful when sharing a `.env` with a team |
| `.pyrit_conf` | `~/.pyrit/.pyrit_conf` | Database type, initializers, env file paths | No, but recommended — enables `pyrit_scan` and scenarios |
