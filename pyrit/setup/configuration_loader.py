# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Configuration loader for PyRIT initialization.

This module provides the ConfigurationLoader class that loads PyRIT configuration
from YAML files and initializes PyRIT accordingly.
"""

import pathlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Union

from pyrit.common.path import DEFAULT_CONFIG_PATH
from pyrit.common.yaml_loadable import YamlLoadable
from pyrit.identifiers.class_name_utils import class_name_to_snake_case
from pyrit.setup.initialization import (
    AZURE_SQL,
    IN_MEMORY,
    SQLITE,
    initialize_pyrit_async,
)

if TYPE_CHECKING:
    from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer


# Type alias for YAML-serializable values that can be passed as initializer args
# This matches what YAML can represent: primitives, lists, and nested dicts
YamlPrimitive = Union[str, int, float, bool, None]
YamlValue = Union[YamlPrimitive, list["YamlValue"], dict[str, "YamlValue"]]

# Mapping from snake_case config values to internal constants
_MEMORY_DB_TYPE_MAP: dict[str, str] = {
    "in_memory": IN_MEMORY,
    "sqlite": SQLITE,
    "azure_sql": AZURE_SQL,
}


@dataclass
class InitializerConfig:
    """
    Configuration for a single initializer.

    Attributes:
        name: The name of the initializer (must be registered in InitializerRegistry).
        args: Optional dictionary of YAML-serializable arguments to pass to the initializer constructor.
    """

    name: str
    args: Optional[dict[str, YamlValue]] = None


@dataclass
class ConfigurationLoader(YamlLoadable):
    """
    Loader for PyRIT configuration from YAML files.

    This class loads configuration from a YAML file and provides methods to
    initialize PyRIT with the loaded configuration.

    Attributes:
        memory_db_type: The type of memory database (in_memory, sqlite, azure_sql).
        initializers: List of initializer configurations (name + optional args).
        initialization_scripts: List of paths to custom initialization scripts.
            None means "use defaults", [] means "load nothing".
        env_files: List of environment file paths to load.
            None means "use defaults (.env, .env.local)", [] means "load nothing".
        silent: Whether to suppress initialization messages.

    Example YAML configuration:
        memory_db_type: sqlite

        initializers:
          - simple
          - name: airt
            args:
              some_param: value

        initialization_scripts:
          - /path/to/custom_initializer.py

        env_files:
          - /path/to/.env
          - /path/to/.env.local

        silent: false
    """

    memory_db_type: str = "sqlite"
    initializers: list[Union[str, dict[str, Any]]] = field(default_factory=list)
    initialization_scripts: Optional[list[str]] = None
    env_files: Optional[list[str]] = None
    silent: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize the configuration after loading."""
        self._normalize_memory_db_type()
        self._normalize_initializers()

    def _normalize_memory_db_type(self) -> None:
        """
        Normalize and validate memory_db_type.

        Converts the input to lowercase snake_case and validates against known types.
        Stores the normalized snake_case value for config consistency, but maps
        to internal constants when initializing.

        Raises:
            ValueError: If the memory_db_type is not a valid database type.
        """
        # Normalize to lowercase
        normalized = self.memory_db_type.lower().replace("-", "_")

        # Also handle PascalCase inputs (e.g., "InMemory" -> "in_memory")
        if normalized not in _MEMORY_DB_TYPE_MAP:
            # Try converting from PascalCase
            normalized = class_name_to_snake_case(self.memory_db_type)

        if normalized not in _MEMORY_DB_TYPE_MAP:
            valid_types = list(_MEMORY_DB_TYPE_MAP.keys())
            raise ValueError(
                f"Invalid memory_db_type '{self.memory_db_type}'. Must be one of: {', '.join(valid_types)}"
            )

        # Store normalized snake_case value
        self.memory_db_type = normalized

    def _normalize_initializers(self) -> None:
        """
        Normalize initializer entries to InitializerConfig objects.

        Converts initializer names to snake_case for consistent registry lookup.

        Raises:
            ValueError: If an initializer entry is missing a 'name' field or has an invalid type.
        """
        normalized: list[InitializerConfig] = []
        for entry in self.initializers:
            if isinstance(entry, str):
                # Simple string entry: normalize name to snake_case
                name = class_name_to_snake_case(entry)
                normalized.append(InitializerConfig(name=name))
            elif isinstance(entry, dict):
                # Dict entry: name and optional args
                if "name" not in entry:
                    raise ValueError(f"Initializer configuration must have a 'name' field. Got: {entry}")
                name = class_name_to_snake_case(entry["name"])
                normalized.append(
                    InitializerConfig(
                        name=name,
                        args=entry.get("args"),
                    )
                )
            else:
                raise ValueError(f"Initializer entry must be a string or dict, got: {type(entry).__name__}")
        self._initializer_configs = normalized

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigurationLoader":
        """
        Create a ConfigurationLoader from a dictionary.

        Args:
            data: Dictionary containing configuration values.

        Returns:
            A new ConfigurationLoader instance.
        """
        # Filter out None values only - empty lists are meaningful ("load nothing")
        filtered_data = {k: v for k, v in data.items() if v is not None}
        return cls(**filtered_data)

    @staticmethod
    def load_with_overrides(
        config_file: Optional[pathlib.Path] = None,
        *,
        memory_db_type: Optional[str] = None,
        initializers: Optional[Sequence[Union[str, dict[str, Any]]]] = None,
        initialization_scripts: Optional[Sequence[str]] = None,
        env_files: Optional[Sequence[str]] = None,
    ) -> "ConfigurationLoader":
        """
        Load configuration with optional overrides.

        This factory method implements a 3-layer configuration precedence:
        1. Default config file (~/.pyrit/.pyrit_conf) if it exists
        2. Explicit config_file argument if provided
        3. Individual override arguments (non-None values take precedence)

        This is a staticmethod (not classmethod) because it's a pure factory function
        that doesn't need access to class state and can be reused by multiple interfaces
        (CLI, shell, programmatic API).

        Args:
            config_file: Optional path to a YAML-formatted configuration file.
            memory_db_type: Override for database type (in_memory, sqlite, azure_sql).
            initializers: Override for initializer list.
            initialization_scripts: Override for initialization script paths.
            env_files: Override for environment file paths.

        Returns:
            A merged ConfigurationLoader instance.

        Raises:
            FileNotFoundError: If an explicitly specified config_file does not exist.
            ValueError: If the configuration is invalid.
        """
        import logging

        logger = logging.getLogger(__name__)

        # Start with defaults - None means "use defaults", [] means "load nothing"
        config_data: dict[str, Any] = {
            "memory_db_type": "sqlite",
            "initializers": [],
            "initialization_scripts": None,  # None = use defaults
            "env_files": None,  # None = use defaults
        }

        # 1. Try loading default config file if it exists
        default_config_path = DEFAULT_CONFIG_PATH
        if default_config_path.exists():
            try:
                default_config = ConfigurationLoader.from_yaml_file(default_config_path)
                config_data["memory_db_type"] = default_config.memory_db_type
                config_data["initializers"] = [
                    {"name": ic.name, "args": ic.args} if ic.args else ic.name
                    for ic in default_config._initializer_configs
                ]
                # Preserve None vs [] distinction from config file
                config_data["initialization_scripts"] = default_config.initialization_scripts
                config_data["env_files"] = default_config.env_files
            except Exception as e:
                logger.warning(f"Failed to load default config file {default_config_path}: {e}")

        # 2. Load explicit config file if provided (overrides default)
        if config_file is not None:
            if not config_file.exists():
                raise FileNotFoundError(f"Configuration file not found: {config_file}")
            explicit_config = ConfigurationLoader.from_yaml_file(config_file)
            config_data["memory_db_type"] = explicit_config.memory_db_type
            config_data["initializers"] = [
                {"name": ic.name, "args": ic.args} if ic.args else ic.name
                for ic in explicit_config._initializer_configs
            ]
            # Preserve None vs [] distinction from config file
            config_data["initialization_scripts"] = explicit_config.initialization_scripts
            config_data["env_files"] = explicit_config.env_files

        # 3. Apply overrides (non-None values take precedence)
        # Convert Sequence to list to match dataclass field types
        if memory_db_type is not None:
            # Normalize to snake_case
            normalized_db = memory_db_type.lower().replace("-", "_")
            if normalized_db == "inmemory":
                normalized_db = "in_memory"
            elif normalized_db == "azuresql":
                normalized_db = "azure_sql"
            config_data["memory_db_type"] = normalized_db

        if initializers is not None:
            config_data["initializers"] = list(initializers)

        if initialization_scripts is not None:
            config_data["initialization_scripts"] = list(initialization_scripts)

        if env_files is not None:
            config_data["env_files"] = list(env_files)

        return ConfigurationLoader.from_dict(config_data)

    @classmethod
    def get_default_config_path(cls) -> pathlib.Path:
        """
        Get the default configuration file path.

        Returns:
            Path to the default config file in ~/.pyrit/.pyrit_conf
        """
        return DEFAULT_CONFIG_PATH

    def _resolve_initializers(self) -> Sequence["PyRITInitializer"]:
        """
        Resolve initializer names to PyRITInitializer instances.

        Uses the InitializerRegistry to look up initializer classes by name
        and instantiate them with optional arguments.

        Returns:
            Sequence of PyRITInitializer instances.

        Raises:
            ValueError: If an initializer name is not found in the registry.
        """
        from pyrit.registry import InitializerRegistry

        if not self._initializer_configs:
            return []

        registry = InitializerRegistry()
        resolved: list[PyRITInitializer] = []

        for config in self._initializer_configs:
            initializer_class = registry.get_class(config.name)
            if initializer_class is None:
                available = ", ".join(sorted(registry.get_names()))
                raise ValueError(
                    f"Initializer '{config.name}' not found in registry.\nAvailable initializers: {available}"
                )

            # Instantiate with args if provided
            instance = initializer_class(**config.args) if config.args else initializer_class()

            resolved.append(instance)

        return resolved

    def _resolve_initialization_scripts(self) -> Optional[Sequence[pathlib.Path]]:
        """
        Resolve initialization script paths.

        Returns:
            None if field is None (use defaults), empty list if field is [],
            or Sequence of resolved Path objects if paths are specified.
        """
        # None means "use defaults" - return None to signal this
        if self.initialization_scripts is None:
            return None

        # Empty list means "load nothing" - return empty list
        if len(self.initialization_scripts) == 0:
            return []

        resolved: list[pathlib.Path] = []
        for script_str in self.initialization_scripts:
            script_path = pathlib.Path(script_str)
            if not script_path.is_absolute():
                script_path = pathlib.Path.cwd() / script_path
            resolved.append(script_path)

        return resolved

    def _resolve_env_files(self) -> Optional[Sequence[pathlib.Path]]:
        """
        Resolve environment file paths.

        Returns:
            None if field is None (use defaults), empty list if field is [],
            or Sequence of resolved Path objects if paths are specified.
        """
        # None means "use defaults" - return None to signal this
        if self.env_files is None:
            return None

        # Empty list means "load nothing" - return empty list
        if len(self.env_files) == 0:
            return []

        resolved: list[pathlib.Path] = []
        for env_str in self.env_files:
            env_path = pathlib.Path(env_str)
            if not env_path.is_absolute():
                env_path = pathlib.Path.cwd() / env_path
            resolved.append(env_path)

        return resolved

    async def initialize_pyrit_async(self) -> None:
        """
        Initialize PyRIT with the loaded configuration.

        This method resolves all initializer names to instances and calls
        the core initialize_pyrit_async function.

        Raises:
            ValueError: If configuration is invalid or initializers cannot be resolved.
        """
        resolved_initializers = self._resolve_initializers()
        resolved_scripts = self._resolve_initialization_scripts()
        resolved_env_files = self._resolve_env_files()

        # Map snake_case memory_db_type to internal constant
        internal_memory_db_type = _MEMORY_DB_TYPE_MAP[self.memory_db_type]

        await initialize_pyrit_async(
            memory_db_type=internal_memory_db_type,
            initialization_scripts=resolved_scripts,
            initializers=resolved_initializers if resolved_initializers else None,
            env_files=resolved_env_files,
            silent=self.silent,
        )


async def initialize_from_config_async(
    config_path: Optional[Union[str, pathlib.Path]] = None,
) -> ConfigurationLoader:
    """
    Initialize PyRIT from a configuration file.

    This is a convenience function that loads a ConfigurationLoader from
    a YAML file and initializes PyRIT.

    Args:
        config_path: Path to the configuration file. If None, uses the default
            path (~/.pyrit/.pyrit_conf). Can be a string or pathlib.Path.

    Returns:
        The loaded ConfigurationLoader instance.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If the configuration is invalid.
    """
    if config_path is None:
        config_path = ConfigurationLoader.get_default_config_path()
    elif isinstance(config_path, str):
        config_path = pathlib.Path(config_path)

    config = ConfigurationLoader.from_yaml_file(config_path)
    await config.initialize_pyrit_async()
    return config
