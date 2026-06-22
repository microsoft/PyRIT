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
classifies it as compliant or non-compliant. Non-compliant subclasses either
raise ``TypeError`` at class definition time, or, if they opt in via the
``_brick_legacy_init`` class attribute, emit a ``DeprecationWarning``
via ``print_deprecation_message`` and continue.
The opt-out is intended to be removed in ``0.16.0``.
"""

from __future__ import annotations

import inspect
from inspect import Parameter

from pyrit.common.deprecation import print_deprecation_message

#: Class attribute name that opts a subclass into the legacy-init grace period.
#: When ``True`` on a class, ``enforce_keyword_only_init`` downgrades the
#: ``TypeError`` to a ``DeprecationWarning`` until ``_LEGACY_REMOVED_IN``.
LEGACY_INIT_OPT_OUT_ATTR = "_brick_legacy_init"

#: Version in which the legacy-init opt-out is removed; non-conforming
#: subclasses will hard-fail at that point.
_LEGACY_REMOVED_IN = "0.16.0"


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
            positional-or-keyword parameters after ``self``, and ``cls`` does
            not opt into the legacy-init grace period via the
            ``_brick_legacy_init`` class attribute. The opt-out is only
            honored when set directly on ``cls`` (it is not inherited from a
            base class), so new subclasses always get the hard check by
            default.
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

    if cls.__dict__.get(LEGACY_INIT_OPT_OUT_ATTR, False):
        # Opt-in legacy period: warn rather than break, so existing users
        # whose code calls these constructors positionally have one release
        # cycle to migrate.
        print_deprecation_message(
            old_item=(f"{cls.__module__}.{cls.__qualname__}.__init__ with positional parameters {offenders!r}"),
            new_item=(f"keyword-only parameters per the {base_name} contract (insert ``*`` after ``self``)"),
            removed_in=_LEGACY_REMOVED_IN,
        )
        return

    raise TypeError(
        f"{cls.__name__}.__init__ violates the {base_name} contract: "
        f"all parameters after ``self`` must be keyword-only, but the "
        f"following are positional: {offenders!r}. Insert ``*,`` after "
        f"``self`` to fix, or set ``{LEGACY_INIT_OPT_OUT_ATTR} = True`` on "
        f"the class to opt into a temporary deprecation period (removed in "
        f"{_LEGACY_REMOVED_IN})."
    )
