# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer registry for discovering and cataloging PyRIT initializers.

A ``Registry`` for ``PyRITInitializer`` classes that discovers all available
subclasses from the ``pyrit/setup/initializers`` directory structure and from
uploaded custom scripts. Like ``ScenarioRegistry`` it is a class-only unified
``Registry``: it owns a validated class catalog and builds instances via
``create_instance``. Unlike the component registries it does not hold instances
(no ``.instances`` property) and has no ``ComponentIdentifier`` — its declared,
YAML-style inputs live on ``PyRITInitializer.supported_parameters`` and are
applied post-construction via ``set_params_from_args``. Because discovery is a
filesystem scan rather than a package import, ``_discover`` is overridden.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyrit.models import class_name_to_snake_case, validate_registry_name
from pyrit.registry.discovery import discover_in_directory
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata

# Compute PYRIT_PATH directly to avoid importing pyrit package
# (which triggers heavy imports from __init__.py)
PYRIT_PATH = Path(__file__).parent.parent.parent.resolve()

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

    from pyrit.models import Parameter
    from pyrit.models.identifiers.component_identifier import ComponentIdentifier
    from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InitializerMetadata(RegistryMetadata):
    """
    Metadata describing a registered PyRITInitializer class.

    Use get_class() to get the actual class.
    """

    # Environment variables required by the initializer.
    required_env_vars: tuple[str, ...] = field(kw_only=True)

    # Parameters accepted by the initializer (live, JSON-serializable Parameter objects).
    supported_parameters: tuple[Parameter, ...] = field(kw_only=True, default=())


class InitializerRegistry(Registry["PyRITInitializer", InitializerMetadata]):
    """
    Registry for discovering and managing available initializers.

    Discovers all ``PyRITInitializer`` subclasses from the
    ``pyrit/setup/initializers`` directory structure via a filesystem scan (so
    ``_discover`` is overridden rather than supplying ``_base_type`` /
    ``_discovery_package``). Initializers are identified by their suffix-stripped
    snake_case class name (e.g., ``"objective_target"``, ``"simple"``); the
    directory structure is used for organization but not exposed to users.
    """

    def __init__(self, *, discovery_path: Path | None = None, lazy_discovery: bool = False) -> None:
        """
        Initialize the initializer registry.

        Args:
            discovery_path: The path to discover initializers from.
                If None, defaults to pyrit/setup/initializers (discovers all).
            lazy_discovery: If True, discovery is deferred until first access.
                Defaults to False for backwards compatibility.
        """
        self._discovery_path: Path = (
            discovery_path if discovery_path is not None else Path(PYRIT_PATH) / "setup" / "initializers"
        )

        self._builtin_names: set[str] = set()
        super().__init__(lazy_discovery=lazy_discovery)

    def _metadata_class(self) -> type[InitializerMetadata]:
        """Return the concrete metadata dataclass this registry builds."""
        return InitializerMetadata

    def _identifier_type(self) -> type[ComponentIdentifier] | None:
        """Return ``None`` since initializers have no ``ComponentIdentifier``; declared params are their contract."""
        return None

    def is_builtin(self, name: str) -> bool:
        """Return True if *name* was registered during built-in discovery."""
        self._ensure_discovered()
        return name in self._builtin_names

    def _discover(self) -> None:
        """Discover all initializers from the specified discovery path."""
        discovery_path = self._discovery_path

        if not discovery_path.exists():
            logger.warning(f"Initializers directory not found: {discovery_path}")
            return

        # Import base class for discovery
        from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

        if discovery_path.is_file():
            self._process_file(file_path=discovery_path, base_class=PyRITInitializer, builtin=True)
        else:
            for _file_stem, _file_path, initializer_class in discover_in_directory(
                directory=discovery_path,
                base_class=PyRITInitializer,
                recursive=True,
            ):
                self._register_initializer(
                    initializer_class=initializer_class,
                    builtin=True,
                )

    def _process_file(self, *, file_path: Path, base_class: type[PyRITInitializer], builtin: bool = False) -> None:
        """
        Load a single Python file and register the initializers it defines.

        Args:
            file_path: Path to the Python file to process.
            base_class: The PyRITInitializer base class.
            builtin: Whether discovered classes should be marked as built-in.
        """
        try:
            module = self._load_module_from_path(file_path=file_path, module_name=f"initializer.{file_path.stem}")
        except Exception as e:
            logger.warning(f"Failed to load initializer module {file_path.stem}: {e}")
            return

        for initializer_class in self._module_defined_initializers(module=module, base_class=base_class):
            self._register_initializer(initializer_class=initializer_class, builtin=builtin)

    def _register_initializer(
        self,
        *,
        initializer_class: type[PyRITInitializer],
        builtin: bool = False,
    ) -> None:
        """
        Register an initializer class.

        Args:
            initializer_class: The initializer class to register.
            builtin: Whether this is a built-in initializer.
        """
        try:
            # Convert class name to snake_case for registry name
            registry_name = self._get_registry_name(initializer_class)

            # Check for registry key collision
            if registry_name in self._classes:
                logger.warning(
                    f"Initializer registry name collision: '{registry_name}' "
                    f"conflicts with an already-registered initializer. Original "
                    f"initializer is kept: {self._classes[registry_name].__name__}"
                )
                return

            self.register_class(initializer_class, name=registry_name)
            if builtin:
                self._builtin_names.add(registry_name)
            logger.debug(f"Registered initializer: {registry_name} ({initializer_class.__name__})")

        except Exception as e:
            logger.warning(f"Failed to register initializer {initializer_class.__name__}: {e}")

    def _get_registry_name(self, cls: type[PyRITInitializer]) -> str:
        """
        Key initializers by their suffix-stripped snake_case class name.

        Args:
            cls (type[PyRITInitializer]): The initializer class.

        Returns:
            str: The registry name (e.g. ``"objective_target"``).
        """
        return class_name_to_snake_case(cls.__name__, suffix="Initializer")

    def _build_metadata(self, name: str, cls: type[PyRITInitializer]) -> InitializerMetadata:
        """
        Build metadata for an initializer class.

        Instantiates the initializer with no arguments and reads its
        ``required_env_vars`` / ``supported_parameters`` off the instance.

        Args:
            name: The registry name of the initializer.
            cls: The initializer class to describe.

        Returns:
            InitializerMetadata describing the initializer class.
        """
        description = RegistryMetadata.description_from_docstring(cls, fallback="No description available")

        try:
            instance = cls()
            return InitializerMetadata(
                class_name=cls.__name__,
                class_module=cls.__module__,
                class_description=description,
                registry_name=name,
                required_env_vars=tuple(instance.required_env_vars),
                supported_parameters=tuple(instance.supported_parameters),
            )
        except Exception as e:
            logger.warning(f"Failed to get metadata for {name}: {e}")
            return InitializerMetadata(
                class_name=cls.__name__,
                class_module=cls.__module__,
                class_description="Error loading initializer metadata",
                registry_name=name,
                required_env_vars=(),
            )

    def create_and_configure(self, name: str, *, initializer_params: dict[str, Any] | None = None) -> PyRITInitializer:
        """
        Build and parameterize an initializer in one call.

        Parallels ``ScenarioRegistry.create_and_initialize_async`` (which takes
        ``scenario_params``): the registry — not the caller — owns the
        build → set-params → validate lifecycle. Unlike scenarios,
        ``initialize_async`` is invoked later by the PyRIT init flow, so this stops
        at ``configure`` and returns a *configured, not-yet-initialized* instance.

        Args:
            name (str): The registry name of the initializer (e.g. ``"objective_target"``).
            initializer_params (dict[str, Any] | None): Declared parameters to set
                before initialization. Coerced to ``self.params`` via
                ``set_params_from_args`` and validated against
                ``supported_parameters``. Defaults to no parameters.

        Returns:
            PyRITInitializer: The configured initializer, ready for ``initialize_async``.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If the configured parameters are invalid.
        """
        instance = self.create_instance(name)
        if initializer_params:
            instance.set_params_from_args(args=initializer_params)
            instance.validate_params()
        return instance

    @staticmethod
    def _load_module_from_path(*, file_path: Path, module_name: str) -> ModuleType:
        """
        Import a Python file as an anonymous module.

        Args:
            file_path: Path to the ``.py`` file to import.
            module_name: The synthetic module name to load it under.

        Returns:
            ModuleType: The executed module.

        Raises:
            ValueError: If an import spec could not be created for the file.
        """
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            raise ValueError(f"Could not load initializer script: {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _module_defined_initializers(
        *, module: ModuleType, base_class: type[PyRITInitializer]
    ) -> list[type[PyRITInitializer]]:
        """
        Find concrete ``PyRITInitializer`` subclasses defined in *module*.

        Only classes whose ``__module__`` is *module* are returned, so classes
        merely imported into the script are ignored.

        Args:
            module: The imported module to scan.
            base_class: The ``PyRITInitializer`` base class.

        Returns:
            list[type]: Concrete initializer classes defined in the module.
        """
        return [
            attr
            for attr_name in dir(module)
            if (
                inspect.isclass(attr := getattr(module, attr_name))
                and issubclass(attr, base_class)
                and attr is not base_class
                and not inspect.isabstract(attr)
                and attr.__module__ == module.__name__
            )
        ]

    def create_from_script_paths(self, *, script_paths: Sequence[str | Path]) -> list[PyRITInitializer]:
        """
        Load initializer instances from external Python script files.

        The registry owns turning script files into initializers: each ``.py``
        file is imported and every ``PyRITInitializer`` subclass *defined in that
        file* (imported ones are ignored) is instantiated. Instances are returned
        in load order, ready for the caller to validate and initialize; they are
        not added to the class catalog.

        Args:
            script_paths (Sequence[str | Path]): Python (.py) file paths to load
                initializers from. Relative paths resolve against the current
                working directory.

        Returns:
            list[PyRITInitializer]: Instantiated initializers, in load order.

        Raises:
            FileNotFoundError: If a script path does not exist.
            ValueError: If a path is not a ``.py`` file or defines no initializer.
        """
        from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

        resolved = self.resolve_script_paths(script_paths=[str(p) for p in script_paths])

        instances: list[PyRITInitializer] = []
        for script_path in resolved:
            if script_path.suffix != ".py":
                raise ValueError(f"Initialization script must be a Python file (.py): {script_path}")

            logger.info(f"Loading initializers from script: {script_path}")
            try:
                module = self._load_module_from_path(
                    file_path=script_path, module_name=f"init_script_{script_path.stem}"
                )
                file_instances: list[PyRITInitializer] = []
                for init_cls in self._module_defined_initializers(module=module, base_class=PyRITInitializer):
                    try:
                        file_instances.append(init_cls())
                        logger.debug(f"Found and instantiated {init_cls.__name__} in {script_path.name}")
                    except Exception as e:
                        logger.warning(f"Could not instantiate {init_cls.__name__} from {script_path.name}: {e}")

                if not file_instances:
                    raise ValueError(
                        f"Initialization script {script_path} must contain at least one PyRITInitializer subclass. "
                        f"Define a class that inherits from PyRITInitializer."
                    )

                instances.extend(file_instances)
                logger.debug(f"Loaded {len(file_instances)} initializer(s) from {script_path.name}")
            except Exception as e:
                logger.error(f"Error loading initializers from script {script_path}: {e}")
                raise

        return instances

    def register_from_content(self, *, name: str, script_content: str) -> str:
        """
        Register an initializer from uploaded Python source code.

        Writes *script_content* to a managed directory, loads it as a
        module, discovers the first concrete ``PyRITInitializer``
        subclass, and registers it under *name*.

        Note:
            Registrations are runtime-only and are not rediscovered on
            server restart.  Script files persist on disk as import
            artifacts for the current process.

        Args:
            name: Registry name for the new initializer.
            script_content: Python source code that defines a
                ``PyRITInitializer`` subclass.

        Returns:
            The registry name that was registered.

        Raises:
            ValueError: If the source cannot be compiled, does not
                contain a valid initializer class, or *name* collides
                with an existing entry.
        """
        self._ensure_discovered()

        validate_registry_name(name)

        if name in self._classes:
            raise ValueError(f"Initializer '{name}' is already registered. Unregister it first to replace it.")

        # Deferred: importing pyrit.setup triggers heavy __init__.py chain
        from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

        # Write to a managed directory so importlib can load it
        managed_dir = self._get_custom_scripts_dir()
        script_path = managed_dir / f"{name}.py"
        try:
            script_path.write_text(script_content, encoding="utf-8")
        except OSError as e:
            raise ValueError(f"Failed to write initializer script: {e}") from e

        try:
            module = self._load_module_from_path(file_path=script_path, module_name=f"custom_initializer.{name}")

            discovered_classes = self._module_defined_initializers(module=module, base_class=PyRITInitializer)
            if not discovered_classes:
                raise ValueError(f"Uploaded script for '{name}' does not contain a concrete PyRITInitializer subclass.")
            discovered = discovered_classes[0]
        except ValueError:
            script_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            script_path.unlink(missing_ok=True)
            raise ValueError(f"Failed to load initializer script '{name}': {e}") from e

        self.register_class(discovered, name=name)
        logger.info(f"Registered custom initializer: {name} ({discovered.__name__})")
        return name

    def unregister_and_cleanup(self, name: str) -> None:
        """
        Unregister a custom initializer and clean up its script file.

        Built-in initializers cannot be removed. For custom initializers
        added via ``register_from_content``, the saved script file is
        also deleted.

        Args:
            name: The registry name to remove.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If the name refers to a built-in initializer.
        """
        self._ensure_discovered()
        if name in self._builtin_names:
            raise ValueError(f"Cannot remove built-in initializer '{name}'.")
        if name not in self._classes:
            available = ", ".join(self.get_class_names())
            raise KeyError(f"'{name}' not found in registry. Available: {available}")
        del self._classes[name]
        self._metadata_cache = None

        script_path = self._get_custom_scripts_dir() / f"{name}.py"
        script_path.unlink(missing_ok=True)

    @staticmethod
    def _get_custom_scripts_dir() -> Path:
        """
        Get the directory for storing uploaded custom initializer scripts.

        Returns:
            Path to ``~/.pyrit/custom_initializers/``, created if needed.
        """
        # Deferred: importing pyrit.common.path triggers pyrit __init__.py
        from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

        custom_dir = CONFIGURATION_DIRECTORY_PATH / "custom_initializers"
        custom_dir.mkdir(parents=True, exist_ok=True)
        return custom_dir

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
