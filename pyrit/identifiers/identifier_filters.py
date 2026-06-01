# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deprecation shim — moved to pyrit.models.identifiers.identifier_filters in 0.14."""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers import identifier_filters as _new

if TYPE_CHECKING:
    from pyrit.models.identifiers.identifier_filters import (
        IdentifierFilter,
        IdentifierType,
    )

__all__ = ["IdentifierFilter", "IdentifierType"]

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers.identifier_filters' has no attribute {name!r}")
    if name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.identifier_filters.{name}",
            new_item=f"pyrit.models.identifiers.identifier_filters.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
