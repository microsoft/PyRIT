# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deprecation shim — moved to pyrit.models.identifiers.class_name_utils in 0.14."""

from typing import TYPE_CHECKING, Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers import class_name_utils as _new

if TYPE_CHECKING:
    from pyrit.models.identifiers.class_name_utils import (
        REGISTRY_NAME_PATTERN,
        class_name_to_snake_case,
        snake_case_to_class_name,
        validate_registry_name,
    )

__all__ = [
    "class_name_to_snake_case",
    "REGISTRY_NAME_PATTERN",
    "snake_case_to_class_name",
    "validate_registry_name",
]

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module 'pyrit.identifiers.class_name_utils' has no attribute {name!r}")
    if name not in _warned:
        print_deprecation_message(
            old_item=f"pyrit.identifiers.class_name_utils.{name}",
            new_item=f"pyrit.models.identifiers.class_name_utils.{name}",
            removed_in="0.16.0",
        )
        _warned.add(name)
    return getattr(_new, name)


def __dir__() -> list[str]:
    return sorted(__all__)
