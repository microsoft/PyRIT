# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Discovery utilities for PyRIT registries.

This module provides functions for discovering classes in directories and packages,
used by registries to find and register items automatically.
"""

import importlib
import importlib.util
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def discover_in_directory(
    *,
    directory: Path,
    base_class: Type[T],
    recursive: bool = True,
) -> Iterator[Tuple[str, Path, Type[T]]]:
    """
    Discover all subclasses of base_class in a directory by loading Python files.

    This function walks a directory, loads Python files dynamically, and yields
    any classes that are subclasses of the specified base_class.

    Args:
        directory: The directory to search for Python files.
        base_class: The base class to filter subclasses of.
        recursive: Whether to recursively search subdirectories. Defaults to True.

    Yields:
        Tuples of (filename_stem, file_path, class) for each discovered subclass.
    """
    if not directory.exists():
        logger.warning(f"Discovery directory not found: {directory}")
        return

    for item in directory.iterdir():
        if item.is_file() and item.suffix == ".py" and item.stem != "__init__":
            yield from _process_file(file_path=item, base_class=base_class)
        elif recursive and item.is_dir() and item.name != "__pycache__":
            yield from discover_in_directory(directory=item, base_class=base_class, recursive=True)


def _process_file(*, file_path: Path, base_class: Type[T]) -> Iterator[Tuple[str, Path, Type[T]]]:
    """
    Process a Python file and yield subclasses of the base class.

    Args:
        file_path: Path to the Python file to process.
        base_class: The base class to filter subclasses of.

    Yields:
        Tuples of (filename_stem, file_path, class) for each discovered subclass.
    """
    try:
        spec = importlib.util.spec_from_file_location(f"discovered_module.{file_path.stem}", file_path)
        if not spec or not spec.loader:
            return

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if inspect.isclass(attr) and issubclass(attr, base_class) and attr is not base_class:
                # Check it's not abstract
                if not inspect.isabstract(attr):
                    yield (file_path.stem, file_path, attr)

    except Exception as e:
        logger.warning(f"Failed to load module from {file_path}: {e}")


def discover_in_package(
    *,
    package_path: Path,
    package_name: str,
    base_class: Type[T],
    recursive: bool = True,
    name_builder: Optional[Callable[[str, str], str]] = None,
    _prefix: str = "",
) -> Iterator[Tuple[str, Type[T]]]:
    """
    Discover all subclasses using pkgutil.iter_modules on a package.

    This function uses Python's package infrastructure to discover modules,
    making it suitable for discovering classes in installed packages.

    Args:
        package_path: The filesystem path to the package directory.
        package_name: The dotted module name of the package (e.g., "pyrit.scenario.scenarios").
        base_class: The base class to filter subclasses of.
        recursive: Whether to recursively search subpackages. Defaults to True.
        name_builder: Optional callable to build the registry name from (prefix, module_name).
            Defaults to returning just the module_name.
        _prefix: Internal parameter to track the current subdirectory prefix.

    Yields:
        Tuples of (registry_name, class) for each discovered subclass.
    """
    if name_builder is None:
        name_builder = lambda prefix, name: name if not prefix else f"{prefix}.{name}"

    for _, module_name, is_pkg in pkgutil.iter_modules([str(package_path)]):
        if module_name.startswith("_"):
            continue

        full_module_name = f"{package_name}.{module_name}"

        try:
            module = importlib.import_module(full_module_name)

            # For non-package modules, find and yield subclasses
            if not is_pkg:
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, base_class) and obj is not base_class:
                        if not inspect.isabstract(obj):
                            # Build the registry name including any prefix
                            registry_name = name_builder(_prefix, module_name)
                            yield (registry_name, obj)

            # Recursively discover in subpackages
            if recursive and is_pkg:
                sub_path = package_path / module_name
                # Pass the current module_name as part of the prefix for nested discoveries
                new_prefix = name_builder(_prefix, module_name)
                yield from discover_in_package(
                    package_path=sub_path,
                    package_name=full_module_name,
                    base_class=base_class,
                    recursive=True,
                    name_builder=name_builder,
                    _prefix=new_prefix,
                )

        except Exception as e:
            logger.warning(f"Failed to load package module {full_module_name}: {e}")


def discover_subclasses_in_loaded_modules(
    *,
    base_class: Type[T],
    exclude_module_prefixes: Optional[Tuple[str, ...]] = None,
) -> Iterator[Tuple[str, Type[T]]]:
    """
    Discover subclasses of a base class from already-loaded modules.

    This is useful for discovering user-defined classes that were loaded
    via initialization scripts or dynamic imports.

    Args:
        base_class: The base class to filter subclasses of.
        exclude_module_prefixes: Module prefixes to exclude from search.
            Defaults to common system modules.

    Yields:
        Tuples of (module_name, class) for each discovered subclass.
    """
    import sys

    if exclude_module_prefixes is None:
        exclude_module_prefixes = ("builtins", "_", "sys", "os", "importlib")

    # Create a snapshot to avoid dictionary changed size during iteration
    modules_snapshot = list(sys.modules.items())

    for module_name, module in modules_snapshot:
        if module is None or not hasattr(module, "__dict__"):
            continue

        # Skip excluded modules
        if any(module_name.startswith(prefix) for prefix in exclude_module_prefixes):
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, base_class) and obj is not base_class:
                if not inspect.isabstract(obj):
                    yield (module_name, obj)
