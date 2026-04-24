# Default Values

Default values can be set in code, and this is often done in `PyRITInitializers`.

## How Default Values Work

When an initializer calls `set_default_value`, it registers a default value for a specific class and parameter combination. These defaults are stored in a global registry and are automatically applied when classes are instantiated.

One of the most important things to understand is that **explicitly provided values always override defaults**. Defaults only apply when:
1. A parameter is not provided at all, OR
2. A parameter is explicitly set to `None`

If you pass a value (even `0`, `False`, or `""`), that value will be used instead of the default.

## Using `apply_defaults` Decorator

First, it's good to be selective over which classes can use this. It is very powerful but can also make debugging more difficult.

Classes that want to participate in the default value system use the `@apply_defaults` decorator on their `__init__` method:

```python
from pyrit.common.apply_defaults import apply_defaults

class MyConverter(PromptConverter):
    @apply_defaults
    def __init__(self, *, converter_target: Optional[PromptChatTarget] = None, temperature: Optional[float] = None):
        self.converter_target = converter_target
        self.temperature = temperature
```

When you create an instance:

```python
# Uses defaults for both parameters (if configured)
converter1 = MyConverter()

# Uses provided value for converter_target, default for temperature
converter2 = MyConverter(converter_target=my_target)

# Uses provided values for both (defaults ignored)
converter3 = MyConverter(converter_target=my_target, temperature=0.8)
```

Defaults can be set on parent classes and will apply to subclasses (unless `include_subclasses=False` is specified).
