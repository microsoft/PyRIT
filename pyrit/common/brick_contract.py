# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Constructor contract enforcement for PyRIT's pluggable brick base classes.

Several PyRIT base classes (``Converter``, ``Scorer``, ``PromptTarget``,
``Scenario``, ``AttackStrategy``, ``SeedDatasetProvider``) are extension
points that users routinely swap in and out. To make those swaps predictable,
every subclass must use the keyword-only constructor shape mandated by the
style guide: ``def __init__(self, *, ...)``.

``enforce_keyword_only_init`` validates subclass signatures.
``forward_init_parameters`` explicitly marks constructors that pass their
``**kwargs`` to the next constructor in the MRO, allowing registries to derive
the complete strict build contract without interpreting arbitrary keyword bags.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from inspect import Parameter
from typing import TypeVar

_InitMethodT = TypeVar("_InitMethodT", bound=Callable[..., None])
_FORWARD_INIT_PARAMETERS_ATTRIBUTE = "__pyrit_forward_init_parameters__"


def forward_init_parameters(init: _InitMethodT) -> _InitMethodT:
    """
    Declare that a constructor forwards ``**kwargs`` to the next MRO constructor.

    The registry uses this explicit declaration to merge parent constructor
    parameters into the class's build contract without treating every variadic
    keyword bag as parent arguments.

    Args:
        init (_InitMethodT): The forwarding constructor.

    Returns:
        _InitMethodT: The unchanged constructor with registry metadata attached.

    Raises:
        TypeError: If the constructor does not accept ``**kwargs``.
    """
    if not any(param.kind is Parameter.VAR_KEYWORD for param in inspect.signature(init).parameters.values()):
        raise TypeError("forward_init_parameters requires a constructor that accepts **kwargs.")
    setattr(init, _FORWARD_INIT_PARAMETERS_ATTRIBUTE, True)
    return init


def init_parameters_are_forwarded(init: Callable[..., object]) -> bool:
    """
    Return whether a constructor declares that it forwards ``**kwargs``.

    Args:
        init (Callable[..., object]): The constructor to inspect.

    Returns:
        bool: True when ``forward_init_parameters`` marked the constructor.
    """
    return bool(getattr(init, _FORWARD_INIT_PARAMETERS_ATTRIBUTE, False))


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
