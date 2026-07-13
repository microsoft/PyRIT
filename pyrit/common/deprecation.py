# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import importlib
import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def print_deprecation_message(
    *,
    old_item: type | Callable[..., Any] | str,
    new_item: type | Callable[..., Any] | str,
    removed_in: str,
) -> None:
    """
    Emit a deprecation warning.

    Args:
        old_item: The deprecated class, function, or its string name
        new_item: The replacement class, function, or its string name
        removed_in: The version in which the deprecated item will be removed
    """
    # Get the qualified name for old item
    if callable(old_item) or isinstance(old_item, type):
        old_name = f"{old_item.__module__}.{old_item.__qualname__}"  # type: ignore[ty:unresolved-attribute]
    else:
        old_name = old_item

    # Get the qualified name for new item
    if callable(new_item) or isinstance(new_item, type):
        new_name = f"{new_item.__module__}.{new_item.__qualname__}"  # type: ignore[ty:unresolved-attribute]
    else:
        new_name = new_item

    warnings.warn(
        f"{old_name} is deprecated and will be removed in {removed_in}. Use {new_name} instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def deprecated_kwarg(
    values: Any,
    *,
    old_name: str,
    new_name: str,
    removed_in: str,
    model: str,
) -> Any:
    """
    Promote a deprecated kwarg to its new name and emit a DeprecationWarning.

    Designed for use inside a Pydantic ``@model_validator(mode="before")``. If
    ``values`` is not a dict (e.g. Pydantic passed a model instance), it is
    returned unchanged. If ``old_name`` is present, it is popped; its value is
    assigned to ``new_name`` only when ``new_name`` is not already set.

    Args:
        values: The pre-validation values dict from a Pydantic validator.
        old_name: The deprecated kwarg name.
        new_name: The replacement kwarg name.
        removed_in: The version in which ``old_name`` will be removed.
        model: A label for the model receiving the kwarg, used in the warning.

    Returns:
        The (possibly modified) ``values`` argument.
    """
    if not isinstance(values, dict):
        return values
    if old_name in values:
        old_value = values.pop(old_name)
        if new_name not in values:
            values[new_name] = old_value
        warnings.warn(
            f"The '{old_name}' argument to {model} is deprecated and will be "
            f"removed in {removed_in}. Use '{new_name}' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
    return values


def module_deprecation_getattr(
    *,
    old_module: str,
    target_module: str,
    names: Iterable[str],
    removed_in: str,
) -> Callable[[str], Any]:
    """
    Build a module-level ``__getattr__`` that re-exports names from ``target_module``.

    Each name in ``names`` is resolved from ``target_module`` on first access,
    with a one-time ``DeprecationWarning`` per name. Attribute access for names
    outside the configured set raises ``AttributeError``. Intended for use as
    ``__getattr__ = module_deprecation_getattr(...)`` in a shim module's
    ``__init__.py`` or top-level file.

    Args:
        old_module: The fully-qualified name of the deprecated module (the shim).
        target_module: The fully-qualified name of the module to forward to.
        names: The names to expose via the shim.
        removed_in: The version in which the shim will be removed.

    Returns:
        A ``__getattr__`` function suitable for module-level assignment.
    """
    name_set = frozenset(names)
    warned: set[str] = set()

    def __getattr__(name: str) -> Any:  # noqa: N807 - module __getattr__ hook must use this name
        if name not in name_set:
            raise AttributeError(f"module {old_module!r} has no attribute {name!r}")
        if name not in warned:
            warned.add(name)
            print_deprecation_message(
                old_item=f"{old_module}.{name}",
                new_item=f"{target_module}.{name}",
                removed_in=removed_in,
            )
        module = importlib.import_module(target_module)
        return getattr(module, name)

    return __getattr__
