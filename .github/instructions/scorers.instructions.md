---
applyTo: "pyrit/score/**"
---

# PyRIT Scorer Development Guidelines

Scorers evaluate model responses against an objective and live under `pyrit/score/`. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

**Does not own** (see [framework.md](../../doc/code/framework.md)): acting on its own result. A scorer evaluates a response and returns a score; branching on that score is the attack's job and aggregating scores across runs is analytics'. It may call a target to evaluate, but must not send the attack's objective prompt or manage the conversation. Flag such bleed in review.

## Constructor contract

`Scorer` subclasses MUST use the keyword-only constructor shape:

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

Requirements:

- All parameters after ``self`` are keyword-only (insert ``*`` immediately
  after ``self``). This is **enforced at class-definition time** by
  `Scorer.__init_subclass__` calling `enforce_keyword_only_init`
  (see `pyrit/common/brick_contract.py`). Non-conforming subclasses
  raise `TypeError` at import time.
- ``super().__init__(validator=..., chat_target=...)`` is required so the
  base class wires the validator and validates ``TARGET_REQUIREMENTS``
  against any provided ``chat_target``.

## Condition contract

- A condition-based leaf declares one `CONDITION_TYPE` subclass. The shared base requires exactly
  one condition of that type and provides `_get_required_condition` for typed access.
- A constructor-configured leaf leaves `CONDITION_TYPE = None`. It must not claim to consume a
  per-execution condition. All leaves reject unsupported conditions, including direct calls.
- Wrappers implement `_get_child_scorers()` and declare no criterion. The base derives coverage
  through `get_condition_types()`. Wrappers must cover every input condition and pass each child
  only its supported subset, preserving objective context. Shared validation checks every child
  before scoring. Wrappers that transform context expose the same inputs through
  `_get_child_expectations()` for preflight.
- Do not override the derived capability API, add plural declarations, or repeat missing/duplicate
  condition checks in leaves. Use shared selection helpers; leaves cannot read sibling conditions.
- Objective-only defaults are resolved before routing. Do not infer missing conditions from a
  filtered child input.
- Objective scoring owns required coverage. Optional auxiliary selection is an orchestration
  policy, not a permissive leaf mode. Generic flat helpers validate each root independently.

## Common pitfalls

- Forgetting ``*`` after ``self`` — the new check will surface this at
  import time with the exact list of positional parameters that need to be
  made keyword-only.
- Calling ``super().__init__`` with positional args — the base
  ``Scorer.__init__`` is already keyword-only, so positional calls raise
  ``TypeError`` at runtime. Always forward via kwargs.
