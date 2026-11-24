# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dotted paths into the JSON-object arguments of a tool call.

A path is one top-level argument key, or dot-separated object keys into a nested argument, such
as ``recipient.email``. Each segment is an exact, case-sensitive object key. A path steps through
objects only and does not index arrays. A top-level key that is exactly the whole name is read
first, so a name written when names were plain keys, such as ``recipient.email`` or ``a..b``,
still reads the key it always read.
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


def read_argument(arguments: Mapping[str, JsonValue], *, path: str) -> tuple[bool, JsonValue]:
    """
    Read the value a path names.

    A top-level key that is exactly ``path`` wins over the nested field the path names.

    Returns:
        tuple[bool, JsonValue]: Whether the field exists, and its value when it does. A missing
            key, or a value on the way that is not an object, means the field does not exist.
    """
    if path in arguments:
        return True, arguments[path]
    try:
        keys = split_argument_path(path)
    except ValueError:
        return False, None
    node: object = arguments
    for key in keys:
        if not isinstance(node, Mapping) or key not in node:
            return False, None
        node = node[key]
    return True, cast("JsonValue", node)
