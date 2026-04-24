# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


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
        old_name = f"{old_item.__module__}.{old_item.__qualname__}"
    else:
        old_name = old_item

    # Get the qualified name for new item
    if callable(new_item) or isinstance(new_item, type):
        new_name = f"{new_item.__module__}.{new_item.__qualname__}"
    else:
        new_name = new_item

    warnings.warn(
        f"{old_name} is deprecated and will be removed in {removed_in}. Use {new_name} instead.",
        DeprecationWarning,
        stacklevel=3,
    )
