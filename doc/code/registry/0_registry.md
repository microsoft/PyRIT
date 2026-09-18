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
| **Class Registry** | Classes (type[T]) | Components instantiated with user-provided parameters |
| **Instance Registry** | Pre-configured instances | Components requiring complex setup before use |

## Common API

Registries share a consistent interface for discovery and introspection:

| Method | Description |
|--------|-------------|
| `get_registry_singleton()` | Get the singleton registry instance |
| `get_names()` | List all registered names |
| `list_metadata()` | Get descriptive metadata for all items |
| `reset_instance()` | Reset the singleton (useful for testing) |

This makes it easy to write code that inspects any registry:

```python
from pyrit.registry import ScenarioRegistry


def show_registry_contents(registry) -> None:
    for name in registry.get_names():
        print(name)


show_registry_contents(ScenarioRegistry.get_registry_singleton())
```


## Key Difference with Class and Instance Registries

| Aspect | Class Registry | Instance Registry |
|--------|----------------|-------------------|
| Stores | Classes (type[T]) | Instances (T) |
| Registration | Automatic discovery | Explicit via `register()` |
| Returns | Class to instantiate | Ready-to-use instance |
| Instantiation | Caller provides parameters | Pre-configured by initializer |
| When to use | Self-contained components with deferred configuration | Components requiring constructor parameters or compositional setup |

## Named Component Construction

Converter, target, and scorer registries use `InstanceHoldingRegistry` to build
components and store them in their `.instances` registry. Use
`create_named_instance(name=..., type_name=..., params=...)` to build and register
a component in one operation. The instance registry stores objects; it does not
construct them.

Duplicate names raise `ValueError`. Use `.instances.register(..., replace=True)`
only when replacement is intended. Converter and target registries also reject
reserved route names such as `catalog` and `types`. Use `.instances.unregister(name)`
to remove an instance.

Constructor annotations define parameter metadata and coercion. Enum parameters
accept member names or values. An annotated input type can expose a safe set of
named structured variants through `get_registry_input_variants()`; the registry
derives each variant's constructor fields and accepts `{ "type": "<name>",
"parameters": { ... } }`. Word-selection strategies use this shared mechanism.
Both enums and structured inputs also accept existing Python objects. Use `Path` for a
local file input. Use `Path | str` when a component also supports a remote URL.
For this union, the registry preserves the supplied type: a `Path` stays a `Path`,
and a string stays a string. It never passes a URL through `Path`. Both union
orders have the same metadata, `type_name: "Path | str"`, including after a JSON
round-trip. Optional forms accept `None` in Python; the display type omits `None`,
as it does for other optional parameters.

The backend owns file-upload handling and cleanup, not the registry. See the
[registry API migration notes](../../gui/0_gui.md#registry-api-migration-notes)
for the REST contract and temporary compatibility behavior.

## See Also

- [Class Registries](1_class_registry.ipynb) - ScenarioRegistry, InitializerRegistry
- [Instance Registries](2_instance_registry.ipynb) - ConverterRegistry, ScorerRegistry, TargetRegistry
