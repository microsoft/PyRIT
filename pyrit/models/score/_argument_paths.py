# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dotted paths into the JSON-object arguments of a tool call.

A path is one top-level argument key, or dot-separated object keys into a nested argument, such
as ``recipient.email``. Each segment is an exact, case-sensitive object key. A path steps through
objects only: it does not index arrays and cannot name a key that itself contains a dot.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Annotated, cast

from pydantic import AfterValidator, Field

if TYPE_CHECKING:
    from pydantic import JsonValue

_KEPT_PATHS_ERROR = "Kept tool arguments must be distinct, nonempty paths, none inside another."


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


def _require_argument_path(path: str) -> str:
    """
    Reject a path with an empty segment, leaving the path unchanged.

    Returns:
        str: The unchanged path.
    """
    split_argument_path(path)
    return path


#: One top-level argument key, or dot-separated object keys into a nested argument.
ArgumentPath = Annotated[str, Field(strict=True, min_length=1), AfterValidator(_require_argument_path)]


def validate_kept_argument_paths(paths: Iterable[object]) -> tuple[str, ...]:
    """
    Check the argument paths a source keeps.

    Returns:
        tuple[str, ...]: The paths, unchanged.

    Raises:
        ValueError: If a path is not a nonempty dotted path, repeats, or lies inside another.
    """
    kept = tuple(paths)
    if not all(isinstance(path, str) for path in kept):
        raise ValueError(_KEPT_PATHS_ERROR)
    try:
        keys = [split_argument_path(cast("str", path)) for path in kept]
    except ValueError:
        raise ValueError(_KEPT_PATHS_ERROR) from None
    for index, path in enumerate(keys):
        if any(other[: len(path)] == path for other in keys[:index] + keys[index + 1 :]):
            raise ValueError(_KEPT_PATHS_ERROR)
    return cast("tuple[str, ...]", kept)


def is_argument_kept(path: str, *, kept: Iterable[str]) -> bool:
    """
    Decide whether the whole value at a path was kept.

    A path is kept when it, or an object that contains it, was kept. Keeping only a field inside
    it keeps part of its value, which is not enough to compare the whole.

    Returns:
        bool: True when a kept path equals the path or contains it.
    """
    keys = split_argument_path(path)
    return any(keys[: len(prefix)] == prefix for prefix in map(split_argument_path, kept))


def read_argument(arguments: Mapping[str, JsonValue], *, path: str) -> tuple[bool, JsonValue]:
    """
    Read the value a path names.

    Returns:
        tuple[bool, JsonValue]: Whether the field exists, and its value when it does. A missing
            key, or a value on the way that is not an object, means the field does not exist.
    """
    node: object = arguments
    for key in split_argument_path(path):
        if not isinstance(node, Mapping) or key not in node:
            return False, None
        node = node[key]
    return True, cast("JsonValue", node)


def keep_argument_paths(arguments: Mapping[str, JsonValue], *, kept: Iterable[str]) -> dict[str, JsonValue]:
    """
    Copy only the fields the kept paths name, nested as they were.

    Returns:
        dict[str, JsonValue]: The kept fields the call has; every other field is left out.
    """
    view: dict[str, JsonValue] = {}
    for path in kept:
        found, value = read_argument(arguments, path=path)
        if not found:
            continue
        *parents, leaf = split_argument_path(path)
        node = view
        for key in parents:
            # Kept paths never lie inside one another, so an entry made here is always an object.
            node = cast("dict[str, JsonValue]", node.setdefault(key, {}))
        node[leaf] = value
    return view


def holds_only_kept_arguments(arguments: Mapping[str, JsonValue], *, kept: Iterable[str]) -> bool:
    """
    Decide whether stored arguments hold nothing beyond the kept paths.

    Returns:
        bool: True when every stored field is a kept path or an object on the way to one.
    """
    kept_keys = {split_argument_path(path) for path in kept}

    def _holds_only(value: Mapping[str, JsonValue], *, prefix: tuple[str, ...]) -> bool:
        for key, child in value.items():
            path = (*prefix, key)
            if path in kept_keys:
                continue
            on_the_way = any(other[: len(path)] == path for other in kept_keys)
            if not on_the_way or not isinstance(child, Mapping) or not _holds_only(child, prefix=path):
                return False
        return True

    return _holds_only(arguments, prefix=())
