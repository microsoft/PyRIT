---
applyTo: "pyrit/executor/attack/**"
---

# PyRIT AttackStrategy Development Guidelines

`AttackStrategy` subclasses (single-turn attacks like `PromptSendingAttack`, multi-turn attacks like `RedTeamingAttack`, etc.) are pluggable bricks orchestrated by `AttackExecutor` and the `Scenario` framework. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

**Does not own** (see [framework.md](../../doc/code/framework.md)): packaging the attack. Prepended/system prompts, role-play framing, the converter stack, and dataset selection are passed in as configuration by the **attack technique** — an attack must accept them as parameters, not assemble them itself (e.g. `RolePlayAttack` building its own prompt scaffolding is attack-technique work bleeding into the executor). It also must not branch on raw responses (use a scorer), construct its own components (use the registry), or format/persist results itself (output/memory). Flag such bleed in review.

## Constructor contract

`AttackStrategy` subclasses MUST follow the keyword-only constructor shape:

```python
class MyAttack(AttackStrategy[MyContext, MyResult]):
    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        custom_param: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            objective_target=objective_target,
            context_type=MyContext,
            **kwargs,
        )
```

Requirements:

- All parameters after ``self`` are keyword-only (insert ``*`` immediately
  after ``self``). This is **enforced at class-definition time** by
  `AttackStrategy.__init_subclass__` calling `enforce_keyword_only_init`
  (see `pyrit/common/brick_contract.py`). Non-conforming subclasses
  raise `TypeError` at import time.
- ``super().__init__(...)`` must be invoked with at minimum
  ``objective_target`` and ``context_type``.
- ``AttackTechniqueFactory`` already rejects ``**kwargs`` in attack
  ``__init__`` at factory-registration time
  (`pyrit/scenario/core/attack_technique_factory.py`); the new
  ``__init_subclass__`` check is complementary — the factory check catches
  scenarios-side wiring mistakes, the subclass check catches the
  ``__init__`` shape at class-definition time.

## Common pitfalls

- Forgetting ``*`` after ``self`` — the new check will surface this at
  import time with the exact list of positional parameters that need to be
  made keyword-only.
- Calling ``super().__init__`` with positional arguments — the base
  ``AttackStrategy.__init__`` is already keyword-only, so positional calls
  raise ``TypeError`` at runtime. Always forward via kwargs.
