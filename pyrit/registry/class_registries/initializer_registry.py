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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pyrit.identifiers.class_name_utils import class_name_to_snake_case
from pyrit.registry.base import ClassRegistryEntry
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
class InitializerMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered PyRITInitializer class.

    Use get_class() to get the actual class.
    """

    # Environment variables required by the initializer.
    required_env_vars: tuple[str, ...] = field(kw_only=True)

    # Supported parameters as tuples of (name, description, required, default).
    supported_parameters: tuple[tuple[str, str, bool, Optional[list[str]]], ...] = field(kw_only=True, default=())


class InitializerRegistry(BaseClassRegistry["PyRITInitializer", InitializerMetadata]):
    """
    Registry for discovering and managing available initializers.

    This class discovers all PyRITInitializer subclasses from the
    pyrit/setup/initializers directory structure.

    Initializers are identified by their filename (e.g., "objective_target", "simple").
    The directory structure is used for organization but not exposed to users.
    """

    def __init__(self, *, discovery_path: Optional[Path] = None, lazy_discovery: bool = False) -> None:
        """
        Initialize the initializer registry.

        Args:
            discovery_path: The path to discover initializers from.
                If None, defaults to pyrit/setup/initializers (discovers all).
            lazy_discovery: If True, discovery is deferred until first access.
                Defaults to False for backwards compatibility.

        Raises:
            ValueError: If the discovery path could not be resolved.
        """
        self._discovery_path = discovery_path
        if self._discovery_path is None:
            self._discovery_path = Path(PYRIT_PATH) / "setup" / "initializers"

        # At this point _discovery_path is guaranteed to be a Path
        if self._discovery_path is None:
            raise ValueError("self._discovery_path is not initialized")

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
            for _file_stem, _file_path, initializer_class in discover_in_directory(
                directory=discovery_path,
                base_class=PyRITInitializer,
                recursive=True,
            ):
                self._register_initializer(
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

        try:
            spec = importlib.util.spec_from_file_location(f"initializer.{short_name}", file_path)
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
                    self._register_initializer(
                        initializer_class=attr,
                    )

        except Exception as e:
            logger.warning(f"Failed to load initializer module {short_name}: {e}")

    def _register_initializer(
        self,
        *,
        initializer_class: type[PyRITInitializer],
    ) -> None:
        """
        Register an initializer class.

        Args:
            initializer_class: The initializer class to register.
        """
        try:
            # Convert class name to snake_case for registry name
            registry_name = class_name_to_snake_case(initializer_class.__name__, suffix="Initializer")

            # Check for registry key collision
            if registry_name in self._class_entries:
                logger.warning(
                    f"Initializer registry name collision: '{registry_name}' "
                    f"conflicts with an already-registered initializer. Original "
                    f"initializer is kept: {self._class_entries[registry_name].registered_class.__name__}"
                )
                return

            entry = ClassEntry(registered_class=initializer_class)
            self._class_entries[registry_name] = entry
            logger.debug(f"Registered initializer: {registry_name} ({initializer_class.__name__})")

        except Exception as e:
            logger.warning(f"Failed to register initializer {initializer_class.__name__}: {e}")

    def _build_metadata(self, name: str, entry: ClassEntry[PyRITInitializer]) -> InitializerMetadata:
        """
        Build metadata for an initializer class.

        Args:
            name: The registry name of the initializer.
            entry: The ClassEntry containing the initializer class.

        Returns:
            InitializerMetadata describing the initializer class.
        """
        initializer_class = entry.registered_class

        description = entry.get_description(fallback="No description available")

        try:
            instance = initializer_class()
            return InitializerMetadata(
                class_name=initializer_class.__name__,
                class_module=initializer_class.__module__,
                class_description=description,
                registry_name=name,
                required_env_vars=tuple(instance.required_env_vars),
                supported_parameters=tuple(
                    (p.name, p.description, p.required, p.default) for p in instance.supported_parameters
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to get metadata for {name}: {e}")
            return InitializerMetadata(
                class_name=initializer_class.__name__,
                class_module=initializer_class.__module__,
                class_description="Error loading initializer metadata",
                registry_name=name,
                required_env_vars=(),
            )

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
