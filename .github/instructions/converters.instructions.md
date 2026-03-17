---
applyTo: "pyrit/prompt_converter/**"
---

# Prompt Converter Development Guidelines

## Base Class Contract

All converters MUST inherit from `PromptConverter` and implement:

```python
class MyConverter(PromptConverter):
    SUPPORTED_INPUT_TYPES = ("text",)    # Required — non-empty tuple of PromptDataType values
    SUPPORTED_OUTPUT_TYPES = ("text",)   # Required — non-empty tuple of PromptDataType values

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        ...
```

Missing or empty `SUPPORTED_INPUT_TYPES` / `SUPPORTED_OUTPUT_TYPES` raises `TypeError` at class definition time via `__init_subclass__`.

## ConverterResult

Always return a `ConverterResult` with matching `output_type`:

```python
return ConverterResult(output_text=result, output_type="text")
```

Valid `PromptDataType` values: `"text"`, `"image_path"`, `"audio_path"`, `"video_path"`, `"binary_path"`, `"url"`, `"error"`.

The `output_type` MUST match what was actually produced — e.g., if you wrote a file, return the path with `"image_path"`, not `"text"`.

## Input Validation

Check input type support in `convert_async`:

```python
if not self.input_supported(input_type):
    raise ValueError(f"Input type {input_type} not supported")
```

## Identifiable Pattern

All converters inherit `Identifiable`. Override `_build_identifier()` to include parameters that affect conversion behavior:

```python
def _build_identifier(self) -> ComponentIdentifier:
    return self._create_identifier(
        params={"encoding": self._encoding},           # Behavioral params only
        children={"target": self._target.get_identifier()}  # If converter wraps a target
    )
```

Include: encoding types, templates, offsets, model names.
Exclude: retry counts, logging config, timeouts.

## Standard Imports

```python
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.identifiers import ComponentIdentifier
```

For LLM-based converters, also import:
```python
from pyrit.prompt_target import PromptChatTarget
```

## Constructor Pattern

Use keyword-only arguments. Use `@apply_defaults` if the converter accepts targets or common config:

```python
from pyrit.common.apply_defaults import apply_defaults

class MyConverter(PromptConverter):
    @apply_defaults
    def __init__(self, *, target: PromptChatTarget, template: str = "default") -> None:
        ...
```

## Exports and External Updates

- New converters MUST be added to `pyrit/prompt_converter/__init__.py` — both the import and the `__all__` list.
- The modality table with new/updated converters `doc/code/converters/0_converters.ipynb` and the associated .py pct file must also be updated.
