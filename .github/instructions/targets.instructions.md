---
applyTo: "pyrit/prompt_target/**"
---

# Prompt Target Development Guidelines

**Responsibility**: A prompt target is "the thing we're sending the prompt to" — often an LLM, but it can be any endpoint (e.g. a storage account for cross-domain prompt injection). Targets use `message_normalizer` together with `TargetConfiguration` to transform `Message`s into the format the target supports.

**Does not own** (see [framework.md](../../doc/code/framework.md)): what to send or what to do with the response. A target sends a prepared `Message` and returns a response; it must not convert prompts (converters), score (scorers), or manage the conversation / decide the next turn (attacks). Flag such bleed in review.

## Base Class Contract

All targets MUST inherit from ``PromptTarget`` (or one of its public
subclasses such as ``OpenAITarget`` / ``HTTPTarget``) and implement
``_send_prompt_to_target_async``:

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

``send_prompt_async`` (the public entry point) is ``@final`` and MUST NOT
be overridden. Override ``_send_prompt_to_target_async`` instead.

## Keyword-only ``__init__`` is enforced

Every ``PromptTarget`` subclass MUST make all ``__init__`` parameters
keyword-only (i.e., place ``*`` as the first parameter after ``self``).
``PromptTarget.__init_subclass__`` validates this at class-definition time
via ``enforce_keyword_only_init`` and raises ``TypeError`` on violations.

The check is satisfied by either of:

```python
def __init__(self, *, endpoint: str, api_key: str) -> None: ...

def __init__(self, *args: Any, **kwargs: Any) -> None: ...  # *args after self
```

It rejects:

```python
def __init__(self, endpoint: str, api_key: str) -> None: ...    # missing *
```

> [!NOTE]
> ``PromptTarget.__init__`` *itself* still accepts positional parameters and
> is not currently keyword-only. The ``__init_subclass__`` hook only runs for
> subclasses, so the base class non-compliance is tolerated during the warn-
> first phase. The base ``__init__`` will be reshaped to be keyword-only in
> 0.16.0 as a BREAKING CHANGE.

## Temporary opt-out: ``_brick_legacy_init``

A handful of legacy targets whose positional ``__init__`` is part of the
public API are grandfathered with ``_brick_legacy_init = True``. They
emit a ``DeprecationWarning`` at import time and the opt-out is scheduled
for removal in **0.16.0**. Do not set this flag on new targets; new
targets MUST follow the keyword-only contract.

Currently grandfathered (slated for cleanup in 0.16.0):
``HTTPTarget``, ``OpenAICompletionTarget``, ``OpenAIImageTarget``,
``PromptShieldTarget``.

## Configuration and Capabilities

- Set ``_DEFAULT_CONFIGURATION`` at the class level when your target's
  capabilities differ from the base defaults (multi-turn support, non-text
  modalities, JSON-mode responses, etc.).
- Accept ``custom_configuration: TargetConfiguration | None = None`` in
  ``__init__`` and forward it to ``super().__init__`` so callers can
  override capabilities per-instance (this is required for HTTP / Playwright
  targets whose capabilities depend on deployment configuration).

## Identifiable Pattern

All targets inherit ``Identifiable``. Override ``_build_identifier()`` to
include parameters that affect target behaviour:

```python
def _build_identifier(self) -> ComponentIdentifier:
    return self._create_identifier(
        params={"endpoint": self._endpoint, "model_name": self._model_name},
    )
```

Include: endpoint, model_name, deployment identifiers, custom headers that
affect routing.
Exclude: API keys, retry counts, logging config, timeouts.

## Exports

New targets MUST be added to ``pyrit/prompt_target/__init__.py`` — both
the import and the ``__all__`` list.
