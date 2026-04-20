# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer registry for discovering and cataloging PyRIT initializers.

This module provides a unified registry for discovering all available
PyRITInitializer subclasses from the pyrit/setup/initializers directory structure.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from pyrit.identifiers import Identifier
from pyrit.registry.class_registries.base_class_registry import (
    BaseClassRegistry,
    ClassEntry,
)
from pyrit.registry.discovery import discover_in_directory

# Compute PYRIT_PATH directly to avoid importing pyrit package
# (which triggers heavy imports from __init__.py)
PYRIT_PATH = Path(__file__).parent.parent.parent.resolve()

if TYPE_CHECKING:
    from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InitializerMetadata(Identifier):
    """
    Metadata describing a registered PyRITInitializer class.

    Use get_class() to get the actual class.
    """

    display_name: str
    required_env_vars: tuple[str, ...]
    execution_order: int


class InitializerRegistry(BaseClassRegistry["PyRITInitializer", InitializerMetadata]):
    """
    Registry for discovering and managing available initializers.

    This class discovers all PyRITInitializer subclasses from the
    pyrit/setup/initializers directory structure.

    Initializers are identified by their filename (e.g., "objective_target", "simple").
    The directory structure is used for organization but not exposed to users.
    """

    @classmethod
    def get_registry_singleton(cls) -> "InitializerRegistry":
        """
        Get the singleton instance of the InitializerRegistry.

        Returns:
            The singleton InitializerRegistry instance.
        """
        return super().get_registry_singleton()  # type: ignore[return-value]

    def __init__(self, *, discovery_path: Optional[Path] = None, lazy_discovery: bool = False) -> None:
        """
        Initialize the initializer registry.

        Args:
            discovery_path: The path to discover initializers from.
                If None, defaults to pyrit/setup/initializers (discovers all).
                To discover only scenarios, pass pyrit/setup/initializers/scenarios.
            lazy_discovery: If True, discovery is deferred until first access.
                Defaults to False for backwards compatibility.
        """
        self._discovery_path = discovery_path
        if self._discovery_path is None:
            self._discovery_path = Path(PYRIT_PATH) / "setup" / "initializers"

        # At this point _discovery_path is guaranteed to be a Path
        assert self._discovery_path is not None

        # Track file paths for collision detection and resolution
        self._initializer_paths: Dict[str, Path] = {}

        super().__init__(lazy_discovery=lazy_discovery)

    def _discover(self) -> None:
        """Discover all initializers from the specified discovery path."""
        discovery_path = self._discovery_path
        assert discovery_path is not None  # Set in __init__

        if not discovery_path.exists():
            logger.warning(f"Initializers directory not found: {discovery_path}")
            return

        # Import base class for discovery
        from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

        if discovery_path.is_file():
            self._process_file(file_path=discovery_path, base_class=PyRITInitializer)
        else:
            for file_stem, file_path, initializer_class in discover_in_directory(
                directory=discovery_path,
                base_class=PyRITInitializer,  # type: ignore[type-abstract]
                recursive=True,
            ):
                self._register_initializer(
                    short_name=file_stem,
                    file_path=file_path,
                    initializer_class=initializer_class,
                )

    def _process_file(self, *, file_path: Path, base_class: type) -> None:
        """
        Process a Python file to extract initializer subclasses.

        Args:
            file_path: Path to the Python file to process.
            base_class: The PyRITInitializer base class.
        """
        import inspect

        short_name = file_path.stem

        # Check for name collision
        if short_name in self._initializer_paths:
            existing_path = self._initializer_paths[short_name]
            logger.error(
                f"Initializer name collision: '{short_name}' found in both "
                f"'{file_path}' and '{existing_path}'. "
                f"Initializer filenames must be unique across all directories."
            )
            return

        try:
            spec = importlib.util.spec_from_file_location(f"initializer.{short_name}", file_path)
            if not spec or not spec.loader:
                return

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if inspect.isclass(attr) and issubclass(attr, base_class) and attr is not base_class:
                    if not inspect.isabstract(attr):
                        self._register_initializer(
                            short_name=short_name,
                            file_path=file_path,
                            initializer_class=attr,  # type: ignore[arg-type]
                        )

        except Exception as e:
            logger.warning(f"Failed to load initializer module {short_name}: {e}")

    def _register_initializer(
        self,
        *,
        short_name: str,
        file_path: Path,
        initializer_class: "type[PyRITInitializer]",
    ) -> None:
        """
        Register an initializer class.

        Args:
            short_name: The short name for the initializer (filename without extension).
            file_path: The path to the file containing the initializer.
            initializer_class: The initializer class to register.
        """
        # Check for name collision
        if short_name in self._initializer_paths:
            existing_path = self._initializer_paths[short_name]
            logger.error(
                f"Initializer name collision: '{short_name}' found in both '{file_path}' and '{existing_path}'."
            )
            return

        try:
            # Create the entry
            entry = ClassEntry(registered_class=initializer_class)
            self._class_entries[short_name] = entry
            self._initializer_paths[short_name] = file_path
            logger.debug(f"Registered initializer: {short_name} ({initializer_class.__name__})")

        except Exception as e:
            logger.warning(f"Failed to register initializer {initializer_class.__name__}: {e}")

    def _build_metadata(self, name: str, entry: ClassEntry["PyRITInitializer"]) -> InitializerMetadata:
        """
        Build metadata for an initializer class.

        Args:
            name: The registry name of the initializer.
            entry: The ClassEntry containing the initializer class.

        Returns:
            InitializerMetadata describing the initializer class.
        """
        initializer_class = entry.registered_class

        try:
            instance = initializer_class()
            return InitializerMetadata(
                identifier_type="class",
                class_name=initializer_class.__name__,
                class_module=initializer_class.__module__,
                class_description=instance.description,
                display_name=instance.name,
                required_env_vars=tuple(instance.required_env_vars),
                execution_order=instance.execution_order,
            )
        except Exception as e:
            logger.warning(f"Failed to get metadata for {name}: {e}")
            return InitializerMetadata(
                identifier_type="class",
                class_name=initializer_class.__name__,
                class_module=initializer_class.__module__,
                class_description="Error loading initializer metadata",
                display_name=name,
                required_env_vars=(),
                execution_order=100,
            )

    def resolve_initializer_paths(self, *, initializer_names: list[str]) -> list[Path]:
        """
        Resolve initializer names to their file paths.

        Args:
            initializer_names: List of initializer names to resolve.

        Returns:
            List of resolved file paths.

        Raises:
            ValueError: If any initializer name is not found or has no file path.
        """
        self._ensure_discovered()
        resolved_paths = []

        for initializer_name in initializer_names:
            if initializer_name not in self._class_entries:
                available = ", ".join(sorted(self.get_names()))
                raise ValueError(
                    f"Built-in initializer '{initializer_name}' not found.\n"
                    f"Available initializers: {available}\n"
                    f"Use 'pyrit_scan --list-initializers' to see detailed information."
                )

            initializer_file = self._initializer_paths.get(initializer_name)
            if initializer_file is None:
                raise ValueError(f"Could not locate file for initializer '{initializer_name}'.")

            resolved_paths.append(initializer_file)

        return resolved_paths

    @staticmethod
    def resolve_script_paths(*, script_paths: list[str]) -> list[Path]:
        """
        Resolve and validate custom script paths.

        Args:
            script_paths: List of script path strings to resolve.

        Returns:
            List of resolved Path objects.

        Raises:
            FileNotFoundError: If any script path does not exist.
        """
        resolved_paths = []

        for script in script_paths:
            script_path = Path(script)
            if not script_path.is_absolute():
                script_path = Path.cwd() / script_path

            if not script_path.exists():
                raise FileNotFoundError(
                    f"Initialization script not found: {script_path}\n  Looked in: {script_path.absolute()}"
                )

            resolved_paths.append(script_path)

        return resolved_paths
