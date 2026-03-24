# Configuration

PyRIT configuration determines how the framework connects to AI targets, which initializers to run, and where to store data. There are two main configuration files, both stored in `~/.pyrit/` by default:

- **[Populating Secrets (.env files)](./populating_secrets.md)** — Set your AI endpoint credentials. These environment variables tell PyRIT how to connect to OpenAI, Azure, Ollama, or any other provider.

- **[Configuration File (.pyrit_conf)](./pyrit_conf.md)** — Declare your database type, initializers, and other settings in a YAML file. PyRIT loads this automatically on startup, so you don't have to pass options every time.

See the [Quick Start Guide](./quick_start.md) for a walkthrough of setting up both files.
