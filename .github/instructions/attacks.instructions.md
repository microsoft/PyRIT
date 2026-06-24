---
applyTo: "pyrit/executor/attack/**"
---

# PyRIT AttackStrategy Development Guidelines

`AttackStrategy` subclasses (single-turn attacks like `PromptSendingAttack`, multi-turn attacks like `RedTeamingAttack`, etc.) are pluggable bricks orchestrated by `AttackExecutor` and the `Scenario` framework. Style rules from `style-guide.instructions.md` (async `_async` suffix, keyword-only args, type hints, enums-over-Literals) still apply and are not repeated here.

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
- Existing subclasses that cannot adopt the contract immediately may set
  the class attribute ``_brick_legacy_init = True`` to opt into a
  one-release grace period that downgrades the error to a
  ``DeprecationWarning(removed_in="0.16.0")``. The opt-out is removed in
  0.16.0; classes that still violate the contract at that point will hard
  fail.
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

## Setting the objective target's system prompt

The objective target's system prompt is a ``system``-role message at the front
of the conversation, carried by ``prepended_conversation``. Build the message
with ``Message.from_system_prompt(text)`` and pass it as a list:

```python
await attack.execute_async(
    objective="...",
    prepended_conversation=[
        Message.from_system_prompt("You are a helpful chemistry tutor.")
    ],
)
```

Requirements:

- This is the standard way to set a system prompt. Do **not** add a separate
  ``system_prompt`` field/argument to a new attack — there is one channel,
  ``prepended_conversation``.
- ``prepended_conversation`` is a plain list, so multiple system prompts and
  interleaved seed turns compose: ``[Message.from_system_prompt("a"),
  Message.from_system_prompt("b"), user_msg, assistant_msg]``.
- ``SingleTurnAttackContext.system_prompt`` is **deprecated** (non-functional;
  removed in 0.17.0). Never read it or wire new behavior to it.
