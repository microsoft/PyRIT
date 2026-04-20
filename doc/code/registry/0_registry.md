# Registry

Registries in PyRIT provide a centralized way to discover, manage, and access components. They support lazy loading, singleton access, and metadata introspection.

## Why Registries?

- **Discovery**: Automatically find available components (scenarios, scorers, etc.)
- **Consistency**: Access components through a uniform API
- **Metadata**: Inspect what's available without instantiating everything
- **Extensibility**: Register custom components alongside built-in ones

## Two Types of Registries

PyRIT has two registry patterns for different use cases:

| Type | Stores | Use Case |
|------|--------|----------|
| **Class Registry** | Classes (Type[T]) | Components instantiated with user-provided parameters |
| **Instance Registry** | Pre-configured instances | Components requiring complex setup before use |

## Common API (RegistryProtocol)

Both registry types implement `RegistryProtocol`, sharing a consistent interface:

| Method | Description |
|--------|-------------|
| `get_registry_singleton()` | Get the singleton registry instance |
| `get_names()` | List all registered names |
| `list_metadata()` | Get descriptive metadata for all items |
| `reset_instance()` | Reset the singleton (useful for testing) |

This protocol enables writing code that works with any registry type:

```python
from pyrit.registry import RegistryProtocol

def show_registry_contents(registry: RegistryProtocol) -> None:
    for name in registry.get_names():
        print(name)
```


## Key Difference with Class and Instance Registries

| Aspect | Class Registry | Instance Registry |
|--------|----------------|-------------------|
| Stores | Classes (Type[T]) | Instances (T) |
| Registration | Automatic discovery | Explicit via `register_instance()` |
| Returns | Class to instantiate | Ready-to-use instance |
| Instantiation | Caller provides parameters | Pre-configured by initializer |
| When to use | Self-contained components with deferred configuration | Components requiring constructor parameters or compositional setup |

## See Also

- [Class Registries](1_class_registry.ipynb) - ScenarioRegistry, InitializerRegistry
- [Instance Registries](2_instance_registry.ipynb) - ScorerRegistry
