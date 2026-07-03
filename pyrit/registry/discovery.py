# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Discovery utilities for PyRIT registries.

This module provides functions for discovering classes in directories and packages,
used by registries to find and register items automatically.
"""

import importlib.util
import inspect
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def discover_in_directory(
    *,
    directory: Path,
    base_class: type[T],
    recursive: bool = True,
) -> Iterator[tuple[str, Path, type[T]]]:
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


def _process_file(*, file_path: Path, base_class: type[T]) -> Iterator[tuple[str, Path, type[T]]]:
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
            if (
                inspect.isclass(attr)
                and issubclass(attr, base_class)
                and attr is not base_class
                and not inspect.isabstract(attr)
            ):
                yield (file_path.stem, file_path, attr)

    except Exception as e:
        logger.warning(f"Failed to load module from {file_path}: {e}")
