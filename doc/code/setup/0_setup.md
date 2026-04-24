# Setup

PyRIT setup involves three main components to get you started with security testing:

1. **Configuration** - Initialize PyRIT with environment variables, database, and defaults
2. **Resiliency** - Understand retry mechanisms and error handling
3. **Customization** - Learn about default values and custom initializers

## Quick Start

For the fastest setup, use the `SimpleInitializer` which requires only basic OpenAI environment variables:

```python
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers import SimpleInitializer

await initialize_pyrit_async(memory_db_type="InMemory", initializers=[SimpleInitializer()])
```

This configuration allows you to run most PyRIT notebooks immediately.

## Configuration Options

PyRIT offers flexible configuration through:
- **Environment variables** for API keys and endpoints
- **Database options** including InMemory, SQLite, and Azure SQL
- **Custom initializers** for project-specific defaults

See the detailed sections below for comprehensive setup guidance.
