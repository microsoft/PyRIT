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
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.path import DEFAULT_CONFIG_PATH
from pyrit.common.yaml_loadable import YamlLoadable
from pyrit.models import class_name_to_snake_case
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
YamlPrimitive = str | int | float | bool | None
YamlValue = YamlPrimitive | list["YamlValue"] | dict[str, "YamlValue"]


@dataclass
class InitializerConfig:
    """
    Configuration for a single initializer.

    Attributes:
        name: The name of the initializer (must be registered in InitializerRegistry).
        args: Optional dictionary of YAML-serializable arguments to pass to the initializer constructor.
    """

    name: str
    args: dict[str, YamlValue] | None = None


@dataclass
class ServerConfig:
    """
    Configuration for connecting to (or launching) a PyRIT backend server.

    Attributes:
        url: Base URL of the backend (e.g. ``http://localhost:8000``).
    """

    url: str = "http://localhost:8000"


@dataclass
class ScenarioConfig:
    """
    Configuration for a scenario referenced by a config file.

    Attributes:
        name: Scenario name (registered in ScenarioRegistry; normalized to snake_case).
        args: Optional map of scenario-declared parameter values.
    """

    name: str
    args: dict[str, YamlValue] | None = None


def _scenario_config_to_dict(config: ScenarioConfig) -> dict[str, Any]:
    """
    Serialize a ``ScenarioConfig`` back to the YAML-style dict shape.

    Args:
        config (ScenarioConfig): The config to serialize.

    Returns:
        dict[str, Any]: ``{"name": ..., "args": ...}`` (args omitted when empty).
    """
    if config.args:
        return {"name": config.name, "args": config.args}
    return {"name": config.name}


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
        operator: Name for the current operator, e.g. a team or username.
        operation: Name for the current operation.

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

        operator: my_team
        operation: my_operation
    """

    # Mapping from snake_case config values to internal constants
    _MEMORY_DB_TYPE_MAP: ClassVar[dict[str, str]] = {
        "in_memory": IN_MEMORY,
        "sqlite": SQLITE,
        "azure_sql": AZURE_SQL,
    }

    memory_db_type: str = "sqlite"
    initializers: list[str | dict[str, Any]] = field(default_factory=list)
    initialization_scripts: list[str] | None = None
    env_files: list[str] | None = None
    env_akv_ref: list[str] | None = None
    silent: bool = False
    operator: str | None = None
    operation: str | None = None
    scenario: str | dict[str, Any] | None = None
    max_concurrent_scenario_runs: int = 3
    allow_custom_initializers: bool = False
    server: dict[str, Any] | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalize the configuration after loading."""
        self._normalize_memory_db_type()
        self._normalize_initializers()
        self._normalize_scenario()
        self._normalize_server()

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
        if normalized not in self._MEMORY_DB_TYPE_MAP:
            # Try converting from PascalCase
            normalized = class_name_to_snake_case(self.memory_db_type)

        if normalized not in self._MEMORY_DB_TYPE_MAP:
            valid_types = list(self._MEMORY_DB_TYPE_MAP.keys())
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

    def _normalize_scenario(self) -> None:
        """
        Normalize the optional ``scenario`` block to a ``ScenarioConfig``.

        Accepts:
        - ``None``: no scenario configured at the config-file layer.
        - ``"name"``: shorthand for ``ScenarioConfig(name="name", args=None)``.
        - ``{"name": "name", "args": {...}}``: full form. ``args`` is optional.

        The name is normalized to snake_case (matching initializer naming).

        Raises:
            ValueError: For any other shape.
        """
        if self.scenario is None:
            self._scenario_config: ScenarioConfig | None = None
            return

        if isinstance(self.scenario, str):
            self._scenario_config = ScenarioConfig(name=class_name_to_snake_case(self.scenario, suffix="Scenario"))
            return

        if isinstance(self.scenario, dict):
            if "name" not in self.scenario:
                raise ValueError(f"Scenario configuration must have a 'name' field. Got: {self.scenario}")
            name = self.scenario["name"]
            if not isinstance(name, str):
                raise ValueError(f"Scenario 'name' must be a string. Got: {type(name).__name__}")
            args = self.scenario.get("args")
            if args is not None and not isinstance(args, dict):
                raise ValueError(f"Scenario 'args' must be a dict or omitted. Got: {type(args).__name__}")
            self._scenario_config = ScenarioConfig(
                name=class_name_to_snake_case(name, suffix="Scenario"),
                args=args,
            )
            return

        raise ValueError(f"Scenario entry must be a string or dict, got: {type(self.scenario).__name__}")

    def _normalize_server(self) -> None:
        """
        Normalize the optional ``server`` block to a ``ServerConfig``.

        Accepts ``None`` (no server configured) or ``{"url": "..."}`` form.

        Raises:
            ValueError: If ``server`` is not ``None`` or a dict, or if ``url`` is not a string.
        """
        if self.server is None:
            self._server_config: ServerConfig | None = None
            return

        if isinstance(self.server, dict):
            url = self.server.get("url", "http://localhost:8000")
            if not isinstance(url, str):
                raise ValueError(f"Server 'url' must be a string. Got: {type(url).__name__}")
            self._server_config = ServerConfig(url=url.rstrip("/"))
            return

        raise ValueError(f"Server entry must be a dict, got: {type(self.server).__name__}")

    @property
    def server_config(self) -> ServerConfig | None:
        """The normalized ``server:`` block, or ``None`` when not configured."""
        return self._server_config

    @property
    def scenario_config(self) -> ScenarioConfig | None:
        """The normalized ``scenario:`` block, or ``None`` when not configured."""
        return self._scenario_config

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigurationLoader":
        """
        Create a ConfigurationLoader from a dictionary.

        Args:
            data: Dictionary containing configuration values.

        Returns:
            A new ConfigurationLoader instance.

        Raises:
            ValueError: If ``extensions`` is present but not a dict.
        """
        # Filter out None values only - empty lists are meaningful ("load nothing")
        filtered_data = {k: v for k, v in data.items() if v is not None}
        known_fields = set(cls.__dataclass_fields__.keys())
        known_data = {k: v for k, v in filtered_data.items() if k in known_fields and k != "extensions"}
        extra_data = {k: v for k, v in filtered_data.items() if k not in known_fields}
        if "extensions" in filtered_data:
            extensions = filtered_data["extensions"]
            if not isinstance(extensions, dict):
                raise ValueError(f"ConfigurationLoader.extensions must be a dict. Got: {type(extensions).__name__}")
            extra_data = {**extra_data, **extensions}
        return cls(**known_data, extensions=extra_data)

    @staticmethod
    def load_with_overrides(
        config_file: pathlib.Path | None = None,
        *,
        memory_db_type: str | None = None,
        initializers: Sequence[str | dict[str, Any]] | None = None,
        initialization_scripts: Sequence[str] | None = None,
        env_files: Sequence[str] | None = None,
        env_akv_ref: Sequence[str] | None = None,
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
            env_akv_ref: Override for Azure Key Vault secret URLs.

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
            "env_akv_ref": None,
            "silent": False,
        }

        # 1. Try loading default config file if it exists
        default_config_path = DEFAULT_CONFIG_PATH
        if default_config_path.exists():
            try:
                logger.info(f"Loading default configuration file: {default_config_path}")
                print(f"Loading default configuration file: {default_config_path}")
                default_config = ConfigurationLoader.from_yaml_file(default_config_path)
                config_data["memory_db_type"] = default_config.memory_db_type
                config_data["initializers"] = [
                    {"name": ic.name, "args": ic.args} if ic.args else ic.name
                    for ic in default_config._initializer_configs
                ]
                # Preserve None vs [] distinction from config file
                config_data["initialization_scripts"] = default_config.initialization_scripts
                config_data["env_files"] = default_config.env_files
                config_data["env_akv_ref"] = default_config.env_akv_ref
                config_data["silent"] = default_config.silent
                if default_config.operator:
                    config_data["operator"] = default_config.operator
                if default_config.operation:
                    config_data["operation"] = default_config.operation
                if default_config._scenario_config is not None:
                    config_data["scenario"] = _scenario_config_to_dict(default_config._scenario_config)
            except Exception as e:
                logger.warning(f"Failed to load default config file {default_config_path}: {e}")

        # 2. Load explicit config file if provided (overrides default)
        if config_file is not None:
            if not config_file.exists():
                raise FileNotFoundError(f"Configuration file not found: {config_file}")
            logger.info(f"Loading configuration file: {config_file}")
            print(f"Loading configuration file: {config_file}")
            explicit_config = ConfigurationLoader.from_yaml_file(config_file)
            config_data["memory_db_type"] = explicit_config.memory_db_type
            config_data["initializers"] = [
                {"name": ic.name, "args": ic.args} if ic.args else ic.name
                for ic in explicit_config._initializer_configs
            ]
            # Preserve None vs [] distinction from config file
            config_data["initialization_scripts"] = explicit_config.initialization_scripts
            config_data["env_files"] = explicit_config.env_files
            config_data["env_akv_ref"] = explicit_config.env_akv_ref
            config_data["silent"] = explicit_config.silent
            if explicit_config.operator:
                config_data["operator"] = explicit_config.operator
            if explicit_config.operation:
                config_data["operation"] = explicit_config.operation
            # Explicit config wins over default config for scenario block too.
            config_data["scenario"] = (
                _scenario_config_to_dict(explicit_config._scenario_config)
                if explicit_config._scenario_config is not None
                else None
            )

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

        if env_akv_ref is not None:
            config_data["env_akv_ref"] = list(env_akv_ref)

        return ConfigurationLoader.from_dict(config_data)

    @classmethod
    def get_default_config_path(cls) -> pathlib.Path:
        """
        Get the default configuration file path.

        Returns:
            Path to the default config file in ~/.pyrit/.pyrit_conf
        """
        return DEFAULT_CONFIG_PATH

    def resolve_initializers(self) -> Sequence["PyRITInitializer"]:
        """
        Resolve initializer names to PyRITInitializer instances.

        Uses the InitializerRegistry to look up initializer classes by name
        and instantiate them with optional arguments.

        Returns:
            Sequence of PyRITInitializer instances.

        Raises:
            ValueError: If an initializer name is not found in the registry.
        """
        import logging

        from pyrit.registry import InitializerRegistry

        if not self._initializer_configs:
            return []

        registry = InitializerRegistry()
        resolved: list[PyRITInitializer] = []

        logging.getLogger(__name__).info("Running %d initializer(s)...", len(self._initializer_configs))

        for config in self._initializer_configs:
            initializer_class = registry.get_class(config.name)
            if initializer_class is None:
                available = ", ".join(sorted(registry.get_names()))
                raise ValueError(
                    f"Initializer '{config.name}' not found in registry.\nAvailable initializers: {available}"
                )

            # Instantiate and set params if provided
            instance = initializer_class()
            if config.args:
                instance.set_params_from_args(args=config.args)
                # Validate params early against supported_parameters to fail fast
                instance._validate_params(params=instance.params)

            resolved.append(instance)

        return resolved

    def resolve_initialization_scripts(self) -> Sequence[pathlib.Path] | None:
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

    def resolve_env_files(self) -> Sequence[pathlib.Path] | None:
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

    def resolve_env_akv_ref(self) -> list[str] | None:
        """
        Return the list of AKV secret URLs, or ``None`` when not configured.

        Returns:
            list[str] | None: The configured AKV secret URLs, or ``None``.
        """
        return self.env_akv_ref

    async def initialize_pyrit_async(self) -> None:
        """
        Initialize PyRIT with the loaded configuration.

        This method resolves all initializer names to instances and calls
        the core initialize_pyrit_async function.

        Raises:
            ValueError: If configuration is invalid or initializers cannot be resolved.
        """
        resolved_initializers = self.resolve_initializers()
        resolved_scripts = self.resolve_initialization_scripts()
        resolved_env_files = self.resolve_env_files()

        # Map snake_case memory_db_type to internal constant
        internal_memory_db_type = self._MEMORY_DB_TYPE_MAP[self.memory_db_type]

        await initialize_pyrit_async(
            memory_db_type=internal_memory_db_type,
            initialization_scripts=resolved_scripts,
            initializers=resolved_initializers if resolved_initializers else None,
            env_files=resolved_env_files,
            env_akv_ref=self.env_akv_ref,
            silent=self.silent,
        )


async def initialize_from_config_async(
    config_path: str | pathlib.Path | None = None,
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
