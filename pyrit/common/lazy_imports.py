# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Helpers for resolving public package exports on first access."""

from collections.abc import Mapping
from importlib import import_module
from typing import Any

LazyExport = str | tuple[str, str | None]


def resolve_lazy_export(
    *,
    name: str,
    module_name: str,
    module_globals: dict[str, Any],
    exports: Mapping[str, LazyExport],
) -> Any:
    """
    Resolve and cache one package export.

    Args:
        name (str): Public attribute requested from the package.
        module_name (str): Package name used in an ``AttributeError``.
        module_globals (dict[str, Any]): Package globals where the resolved value is cached.
        exports (Mapping[str, LazyExport]): Public names and their implementation locations.

    Returns:
        Any: The resolved public value.

    Raises:
        AttributeError: If ``name`` is not a declared lazy export.
    """
    try:
        export = exports[name]
    except KeyError:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}") from None

    if isinstance(export, str):
        target_module = export
        target_attribute: str | None = name
    else:
        target_module, target_attribute = export

    imported_module = import_module(target_module)
    value = imported_module if target_attribute is None else getattr(imported_module, target_attribute)
    module_globals[name] = value
    return value


def get_lazy_dir(*, module_globals: dict[str, Any], exports: Mapping[str, LazyExport]) -> list[str]:
    """
    Return package attributes and unresolved public exports.

    Args:
        module_globals (dict[str, Any]): The package globals.
        exports (Mapping[str, LazyExport]): Public names and their implementation locations.

    Returns:
        list[str]: Sorted package attribute names.
    """
    return sorted(module_globals.keys() | exports.keys())
