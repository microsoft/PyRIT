---
name: scorer-development
description: PyRIT scorer development contract and conventions for code under pyrit/score/. Use when creating, modifying, or reviewing a Scorer.
---

> Applies to `pyrit/score/**`.

# PyRIT Scorer Development Guidelines

Scorers evaluate model responses against an objective and live under `pyrit/score/`. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

## Constructor contract

`Scorer` subclasses MUST use a keyword-only constructor:

```python
class MyScorer(Scorer):
    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        threshold: float = 0.5,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        super().__init__(
            validator=validator or self._DEFAULT_VALIDATOR,
            chat_target=chat_target,
        )
```

- Place `*` immediately after `self`. `Scorer.__init_subclass__` enforces this via `enforce_keyword_only_init` (see `pyrit/common/brick_contract.py`) and raises `TypeError` at class-definition time, naming the offending positional params.
- Call `super().__init__(validator=..., chat_target=...)` with keywords — the base wires the validator and validates `TARGET_REQUIREMENTS` against any provided `chat_target`. The base `__init__` is itself keyword-only, so positional calls raise `TypeError` at runtime.
- A class that cannot adopt the contract yet may set `_brick_legacy_init = True` for a one-release grace period (downgrades the error to a `DeprecationWarning`; removed in **0.16.0**). `PlagiarismScorer` currently uses this to keep `reference_text` positional — do not add new scorers to it.
