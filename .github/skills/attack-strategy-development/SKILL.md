---
name: attack-strategy-development
description: PyRIT AttackStrategy development contract and conventions for code under pyrit/executor/attack/. Use when creating, modifying, or reviewing an attack strategy or executor.
---

> Applies to `pyrit/executor/attack/**`.

# PyRIT AttackStrategy Development Guidelines

`AttackStrategy` subclasses (single-turn attacks like `PromptSendingAttack`, multi-turn like `RedTeamingAttack`) are pluggable bricks orchestrated by `AttackExecutor` and the `Scenario` framework. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

## Constructor contract

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

- Place `*` immediately after `self`. `AttackStrategy.__init_subclass__` enforces this via `enforce_keyword_only_init` (see `pyrit/common/brick_contract.py`) and raises `TypeError` at class-definition time, naming the offending positional params.
- Call `super().__init__(...)` with at least `objective_target` and `context_type`. The base `__init__` is keyword-only, so positional calls raise `TypeError` at runtime.
- A class that cannot adopt the contract yet may set `_brick_legacy_init = True` for a one-release grace period (downgrades the error to a `DeprecationWarning`; removed in **0.16.0**).
- Complementary check: `AttackTechniqueFactory` rejects `**kwargs` in attack `__init__` at factory-registration time (`pyrit/scenario/core/attack_technique_factory.py`) — that catches scenarios-side wiring mistakes, while `__init_subclass__` catches the `__init__` shape at class-definition time.
