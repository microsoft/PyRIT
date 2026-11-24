# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dotted paths into the JSON-object arguments of a tool call.

A path is one top-level argument key, or dot-separated object keys into a nested argument, such
as ``recipient.email``. Each segment is an exact, case-sensitive object key. A path steps through
objects only: it does not index arrays and cannot name a key that itself contains a dot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pydantic import JsonValue


def split_argument_path(path: str) -> tuple[str, ...]:
    """
    Split a dotted argument path into its object keys.

    Returns:
        tuple[str, ...]: The keys from the top level down.

    Raises:
        ValueError: If the path is empty or has an empty segment.
    """
    keys = tuple(path.split("."))
    if "" in keys:
        raise ValueError("Argument paths must be nonempty keys separated by single dots.")
    return keys


def _requirement_keys(name: str) -> tuple[str, ...]:
    """
    Split a requirement's argument name into object keys.

    A requirement stored before names were paths can hold a name that is not a valid path, such
    as ``a..b``. It keeps the meaning it was stored with: one literal top-level key.

    Returns:
        tuple[str, ...]: The keys from the top level down.
    """
    try:
        return split_argument_path(name)
    except ValueError:
        return (name,)


def read_argument(arguments: Mapping[str, JsonValue], *, path: str) -> tuple[bool, JsonValue]:
    """
    Read the value a path names.

    Returns:
        tuple[bool, JsonValue]: Whether the field exists, and its value when it does. A missing
            key, or a value on the way that is not an object, means the field does not exist.
    """
    node: object = arguments
    for key in _requirement_keys(path):
        if not isinstance(node, Mapping) or key not in node:
            return False, None
        node = node[key]
    return True, cast("JsonValue", node)
