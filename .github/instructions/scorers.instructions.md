---
applyTo: "pyrit/score/**"
---

# PyRIT Scorer Development Guidelines

Scorers evaluate model responses against an objective and live under `pyrit/score/`. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

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
- Existing subclasses that cannot adopt the contract immediately may set
  the class attribute ``_brick_legacy_init = True`` to opt into a
  one-release grace period that downgrades the error to a
  ``DeprecationWarning(removed_in="0.16.0")``. The opt-out is removed in
  0.16.0; classes that still violate the contract at that point will hard
  fail.

### Currently grandfathered

- ``PlagiarismScorer`` (``pyrit/score/float_scale/plagiarism_scorer.py``) —
  accepts ``reference_text`` positionally as part of its public API. The
  positional shape is preserved through one release cycle via
  ``_brick_legacy_init = True`` and is scheduled to become
  keyword-only in 0.16.0 (``BREAKING CHANGE``).

## Common pitfalls

- Forgetting ``*`` after ``self`` — the new check will surface this at
  import time with the exact list of positional parameters that need to be
  made keyword-only.
- Calling ``super().__init__`` with positional args — the base
  ``Scorer.__init__`` is already keyword-only, so positional calls raise
  ``TypeError`` at runtime. Always forward via kwargs.
