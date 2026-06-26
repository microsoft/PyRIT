---
name: prompt-target-development
description: PyRIT prompt target development guidelines for code under pyrit/prompt_target/. Use when creating, modifying, or reviewing a PromptTarget.
---

> Applies to `pyrit/prompt_target/**`.

# Prompt Target Development Guidelines

## Base Class Contract

All targets MUST inherit from `PromptTarget` (or one of its public subclasses such as `OpenAITarget` / `HTTPTarget`) and implement `_send_prompt_to_target_async`:

```python
from pyrit.prompt_target.common.prompt_target import PromptTarget


class MyTarget(PromptTarget):
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )
        self._api_key = api_key

    async def _send_prompt_to_target_async(
        self, *, normalized_conversation: list[Message]
    ) -> list[Message]:
        ...
```

`send_prompt_async` (the public entry point) is `@final` and MUST NOT be overridden. Override `_send_prompt_to_target_async` instead.

## Keyword-only `__init__` is enforced

Every `PromptTarget` subclass MUST make all `__init__` parameters keyword-only — place `*` as the first parameter after `self`. `PromptTarget.__init_subclass__` validates this via `enforce_keyword_only_init` and raises `TypeError` at class-definition time.

```python
def __init__(self, *, endpoint: str, api_key: str) -> None: ...   # OK
def __init__(self, *args: Any, **kwargs: Any) -> None: ...        # OK (*args after self)
def __init__(self, endpoint: str, api_key: str) -> None: ...      # rejected — missing *
```

> [!NOTE]
> `PromptTarget.__init__` *itself* is still positional — `__init_subclass__` only runs for subclasses, so the base is tolerated during the warn-first phase. The base `__init__` becomes keyword-only in 0.16.0 (BREAKING CHANGE).

A few legacy targets whose positional `__init__` is public API opt out with `_brick_legacy_init = True` (emits a `DeprecationWarning`; removed in **0.16.0**). Find the current set with `grep -rl "_brick_legacy_init = True" pyrit/prompt_target/`. Do not add new targets to it.

## Configuration and Capabilities

- Set `_DEFAULT_CONFIGURATION` at the class level when your target's capabilities differ from the base defaults (multi-turn support, non-text modalities, JSON-mode responses, etc.).
- Accept `custom_configuration: TargetConfiguration | None = None` in `__init__` and forward it to `super().__init__` so callers can override capabilities per-instance (this is required for HTTP / Playwright targets whose capabilities depend on deployment configuration).

## Identifiable Pattern

All targets inherit `Identifiable`. Override `_build_identifier()` to include parameters that affect target behaviour:

```python
def _build_identifier(self) -> ComponentIdentifier:
    return self._create_identifier(
        params={"endpoint": self._endpoint, "model_name": self._model_name},
    )
```

Include: endpoint, model_name, deployment identifiers, custom headers that affect routing.
Exclude: API keys, retry counts, logging config, timeouts.

## Exports

New targets MUST be added to `pyrit/prompt_target/__init__.py` — both the import and the `__all__` list.
