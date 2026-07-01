# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Constructor contract enforcement for PyRIT's pluggable brick base classes.

Several PyRIT base classes (``PromptConverter``, ``Scorer``, ``PromptTarget``,
``Scenario``, ``AttackStrategy``, ``SeedDatasetProvider``) are extension
points that users routinely swap in and out. To make those swaps predictable,
every subclass must use the keyword-only constructor shape mandated by the
style guide: ``def __init__(self, *, ...)``.

This module provides one shared helper, ``enforce_keyword_only_init``,
that bases invoke from their own ``__init_subclass__`` hook. The helper
inspects the subclass's directly-defined ``__init__`` (not inherited) and
classifies it as compliant or non-compliant. Non-compliant subclasses
raise ``TypeError`` at class definition time.
"""

from __future__ import annotations

import inspect
from inspect import Parameter


def enforce_keyword_only_init(cls: type, *, base_name: str) -> None:
    """
    Validate that ``cls.__init__`` only accepts keyword-only parameters.

    Intended to be called from a base class's ``__init_subclass__`` hook to
    enforce the brick constructor contract on subclasses.

    The helper only inspects ``__init__`` defined directly on ``cls`` (i.e.
    ``"__init__" in cls.__dict__``). Subclasses that inherit ``__init__``
    from their parent are not re-checked — the parent will already have been
    checked at its own definition time.

    Args:
        cls: The subclass being defined. Pass through from
            ``__init_subclass__``.
        base_name: Display name of the base class (e.g. ``"Scenario"``).
            Used in error messages so the user knows which contract was
            violated.

    Raises:
        TypeError: If ``cls.__init__`` accepts any positional or
            positional-or-keyword parameters after ``self``.
    """
    if "__init__" not in cls.__dict__:
        # Subclass inherits __init__ from its parent; the parent has already
        # been validated. Nothing to check here.
        return

    sig = inspect.signature(cls.__init__)
    # Skip ``self`` (always the first parameter on an unbound method).
    params = list(sig.parameters.values())[1:]

    offenders = [p.name for p in params if p.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)]
    if not offenders:
        return

    raise TypeError(
        f"{cls.__name__}.__init__ violates the {base_name} contract: "
        f"all parameters after ``self`` must be keyword-only, but the "
        f"following are positional: {offenders!r}. Insert ``*,`` after "
        f"``self`` to fix."
    )
