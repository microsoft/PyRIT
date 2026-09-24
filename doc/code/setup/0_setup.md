# Setup

PyRIT setup involves three main components to get you started with security testing:

1. **Configuration** - Initialize PyRIT with environment variables, database, and defaults
2. **Resiliency** - Understand retry mechanisms and error handling
3. **Customization** - Learn about default values and custom initializers

## Quick Start

For the fastest setup, use `TargetInitializer` and `ScorerInitializer`, which require only basic OpenAI environment variables:

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[TargetInitializer(), ScorerInitializer()])
```

This configuration allows you to run most PyRIT notebooks immediately.

## Configuration Options

PyRIT offers flexible configuration through:
- **Environment variables** for API keys and endpoints
- **Database options** including InMemory, SQLite, and Azure SQL
- **Custom initializers** for project-specific defaults

See the detailed sections below for comprehensive setup guidance.

## Reinitializing a running backend

The CoPyRIT configuration page separates **Save** from **Reinitialize PyRIT**.
On a single-process, single-replica backend, administrators can apply saved
configuration, environment documents, and custom initializer scripts without
restarting Python. No configuration opt-in is required. See [the GUI reinitialization guide](../../gui/0_gui.md) for
stop confirmation, status, recovery, and deployment limits.

The backend reloads `ConfigurationLoader` and calls its existing
`initialize_pyrit_async(reinitialize=True)` path only after owned work drains.
This path rebuilds setup-owned registries and defaults, reloads stored scripts,
and reuses the same memory object, preserving in-memory and persisted history.
Memory type, connection, or storage changes require a process restart.
Library callers must provide their own admission and resource-draining boundary
before using this configuration-level reinitialization path.

Reinitialization explicitly replaces process environment values supplied by the
selected sources, including deployment-provided values. Omitted variables remain;
there is no historical tracking or removal. Key Vault selection and `.env.local`
priority still apply, and interpolation uses newly selected values. Ordinary
initialization retains its existing environment precedence. The lower-level
`initialize_pyrit_async(reinitialize=True)` option provides replacement values and
memory reuse; `ConfigurationLoader` owns the complete registry/script rebuild.

Failure after mutation blocks backend runtime operations until a successful
retry. Configuration repair stays available under the original authorization
policy. Initializer side effects and environment updates are not rolled back.
