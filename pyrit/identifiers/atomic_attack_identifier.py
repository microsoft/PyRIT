# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deprecation shim — moved to pyrit.models.identifiers.atomic_attack_identifier in 0.14."""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers import atomic_attack_identifier as _new

if TYPE_CHECKING:
    from pyrit.models.identifiers.atomic_attack_identifier import (
        build_atomic_attack_identifier,
        build_seed_identifier,
    )

__all__ = ["build_atomic_attack_identifier", "build_seed_identifier"]

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers.atomic_attack_identifier' has no attribute {name!r}")
    if name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.atomic_attack_identifier.{name}",
            new_item=f"pyrit.models.identifiers.atomic_attack_identifier.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
