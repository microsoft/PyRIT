# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Configuration loader for PyRIT initialization.

This module provides the ConfigurationLoader class that loads PyRIT configuration
from YAML files and initializes PyRIT accordingly.
"""

import copy
import math
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, ClassVar

import yaml

from pyrit.common.path import DEFAULT_CONFIG_PATH
from pyrit.common.utils import verify_and_resolve_path
from pyrit.common.yaml_loadable import YamlLoadable
from pyrit.models import class_name_to_snake_case
from pyrit.setup.environment_loading import validate_env_akv_strict
from pyrit.setup.initialization import AZURE_SQL, IN_MEMORY, SQLITE, initialize_pyrit_async

if TYPE_CHECKING:
    from pyrit.setup.pyrit_initializer import PyRITInitializer


# Type alias for YAML-serializable values that can be passed as initializer args
# This matches what YAML can represent: primitives, lists, and nested dicts
YamlPrimitive = str | int | float | bool | None
YamlValue = YamlPrimitive | list["YamlValue"] | dict[str, "YamlValue"]


class _RemovedConfigurationOptionError(ValueError):
    """Raised when a configuration contains an option removed at the v1 boundary."""


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
        startup_timeout: Seconds to wait for a locally launched backend to become healthy.
    """

    url: str = "http://localhost:8000"
    startup_timeout: float = 120.0


@dataclass(frozen=True)
class _ConfigurationLayer:
    """A parsed configuration value and the YAML key provenance needed for merging."""

    configuration: "ConfigurationLoader"
    present_fields: frozenset[str]
    top_level_extension_fields: frozenset[str]
    nested_extension_fields: frozenset[str]
    reset_extensions: bool


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
            None means auto-discover supported ``.env`` and ``.env.local``;
            [] means "load nothing".
        env_akv_ref: List containing at most one Key Vault bootstrap secret URL.
        env_akv_strict: Whether malformed or valueless entries in a Key Vault
            bootstrap document should fail initialization.
        custom_initializers_source: Local directory or Azure Blob container URI,
            optionally followed by a blob prefix, used to persist custom initializer Python scripts.
        silent: Whether to suppress initialization messages.
        seed: Optional root seed for deterministic converter operations.
        operator: Name for the current operator, e.g. a team or username.
        operation: Name for the current operation.

    Example YAML configuration:
        memory_db_type: sqlite

        initializers:
          - scorer
          - name: target
            args:
              tags:
                - default

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
    env_akv_strict: bool = True
    silent: bool = False
    seed: int | None = None
    operator: str | None = None
    operation: str | None = None
    max_concurrent_scenario_runs: int = 3
    allow_custom_initializers: bool = False
    custom_initializers_source: str | None = None
    server: dict[str, Any] | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalize the configuration after loading."""
        validate_env_akv_strict(env_akv_strict=self.env_akv_strict)
        self._validate_allow_custom_initializers()
        self._normalize_memory_db_type()
        self._normalize_initializers()
        self._validate_env_akv_ref()
        self._validate_custom_initializers_source()
        self._normalize_server()

    def _validate_allow_custom_initializers(self) -> None:
        """
        Validate that the custom initializer kill switch is a boolean.

        Raises:
            TypeError: If allow_custom_initializers is not a boolean.
        """
        if not isinstance(self.allow_custom_initializers, bool):
            raise TypeError("allow_custom_initializers must be a bool.")

    def _validate_custom_initializers_source(self) -> None:
        """
        Validate the optional custom initializer storage source.

        Raises:
            ValueError: If the source is not a non-empty string.
        """
        if self.custom_initializers_source is not None and (
            not isinstance(self.custom_initializers_source, str) or not self.custom_initializers_source.strip()
        ):
            raise ValueError("custom_initializers_source must be a non-empty local directory or container URI.")

    def _validate_env_akv_ref(self) -> None:
        """
        Validate the Key Vault bootstrap secret reference.

        Raises:
            ValueError: If env_akv_ref is not a list of non-empty strings.
        """
        if self.env_akv_ref is None:
            return
        if not isinstance(self.env_akv_ref, list):
            raise ValueError("env_akv_ref must be a list of Azure Key Vault secret URLs.")
        if len(self.env_akv_ref) > 1:
            raise ValueError("env_akv_ref supports at most one Azure Key Vault bootstrap secret URL.")
        if any(not isinstance(secret_url, str) or not secret_url.strip() for secret_url in self.env_akv_ref):
            raise ValueError("env_akv_ref must contain only non-empty Azure Key Vault secret URLs.")

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

    def _normalize_server(self) -> None:
        """
        Normalize the optional ``server`` block to a ``ServerConfig``.

        Accepts ``None`` (no server configured) or a mapping with ``url`` and
        ``startup_timeout`` fields.

        Raises:
            ValueError: If ``server`` is invalid.
        """
        if self.server is None:
            self._server_config: ServerConfig | None = None
            return

        if isinstance(self.server, dict):
            url = self.server.get("url", "http://localhost:8000")
            if not isinstance(url, str):
                raise ValueError(f"Server 'url' must be a string. Got: {type(url).__name__}")
            startup_timeout = self.server.get("startup_timeout", 120.0)
            if (
                isinstance(startup_timeout, bool)
                or not isinstance(startup_timeout, int | float)
                or not math.isfinite(startup_timeout)
                or startup_timeout <= 0
            ):
                raise ValueError("Server 'startup_timeout' must be a finite number greater than 0.")
            self._server_config = ServerConfig(
                url=url.rstrip("/"),
                startup_timeout=float(startup_timeout),
            )
            return

        raise ValueError(f"Server entry must be a dict, got: {type(self.server).__name__}")

    @property
    def server_config(self) -> ServerConfig | None:
        """The normalized ``server:`` block, or ``None`` when not configured."""
        return self._server_config

    @property
    def initializer_configs(self) -> Sequence[InitializerConfig]:
        """The ordered initializer configurations with registry names normalized to snake case."""
        return self._initializer_configs

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigurationLoader":
        """
        Create a ConfigurationLoader from a dictionary.

        Args:
            data: Dictionary containing configuration values.

        Returns:
            A new ConfigurationLoader instance.

        Raises:
            _RemovedConfigurationOptionError: If the removed ``scenario`` block is present.
            ValueError: If ``extensions`` is present but not a dict.
        """
        return cls._parse_layer(data=data).configuration

    @classmethod
    def _parse_layer(cls, *, data: dict[str, Any]) -> _ConfigurationLayer:
        """
        Parse a configuration layer while retaining its YAML key provenance.

        Args:
            data: Dictionary containing one configuration layer.

        Returns:
            _ConfigurationLayer: The validated value and its merge provenance.

        Raises:
            _RemovedConfigurationOptionError: If the removed ``scenario`` block is present.
            ValueError: If ``extensions`` is present but invalid.
        """
        if "scenario" in data:
            raise _RemovedConfigurationOptionError(
                "The 'scenario' configuration block is no longer supported. "
                "Pass the scenario name positionally and its parameters as CLI flags."
            )
        present_fields = frozenset(data)
        # Filter out None values only - empty lists are meaningful ("load nothing")
        filtered_data = {k: v for k, v in data.items() if v is not None}
        known_fields = {config_field.name for config_field in fields(cls) if config_field.init}
        top_level_extension_fields = frozenset(data.keys() - known_fields)
        known_data = {k: v for k, v in filtered_data.items() if k in known_fields and k != "extensions"}
        extra_data = {k: v for k, v in filtered_data.items() if k not in known_fields}
        nested_extension_fields: frozenset[str] = frozenset()
        reset_extensions = False
        if "extensions" in data:
            raw_extensions = data["extensions"]
            reset_extensions = raw_extensions is None or raw_extensions == {}
            if isinstance(raw_extensions, dict):
                if not all(isinstance(key, str) for key in raw_extensions):
                    raise ValueError("ConfigurationLoader.extensions keys must be strings.")
                nested_extension_fields = frozenset(key for key in raw_extensions if isinstance(key, str))
        if "extensions" in filtered_data:
            extensions = filtered_data["extensions"]
            if not isinstance(extensions, dict):
                raise ValueError(f"ConfigurationLoader.extensions must be a dict. Got: {type(extensions).__name__}")
            extra_data = {**extra_data, **extensions}
        config = cls(**known_data, extensions=extra_data)
        return _ConfigurationLayer(
            configuration=config,
            present_fields=present_fields,
            top_level_extension_fields=top_level_extension_fields,
            nested_extension_fields=nested_extension_fields,
            reset_extensions=reset_extensions,
        )

    @classmethod
    def _load_layer_from_yaml_file(cls, *, file: pathlib.Path | str) -> _ConfigurationLayer:
        """
        Load a configuration layer from YAML without attaching merge state to its value.

        Args:
            file: YAML configuration file path.

        Returns:
            _ConfigurationLayer: The validated value and its merge provenance.

        Raises:
            ValueError: If the YAML is invalid or empty.
        """
        file_path = verify_and_resolve_path(file)
        try:
            yaml_data = yaml.safe_load(file_path.read_text("utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML file '{file_path}': {exc}") from exc
        if yaml_data is None:
            raise ValueError(f"YAML file '{file_path}' is empty.")
        return cls._parse_layer(data=yaml_data)

    @staticmethod
    def _merge_server_blocks(
        *,
        inherited: dict[str, Any] | None,
        overlay: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Merge server fields while preserving an explicit null reset.

        Args:
            inherited: Server fields inherited from earlier layers.
            overlay: Server fields from the current layer.

        Returns:
            dict[str, Any] | None: The merged server block, or ``None`` for an explicit reset.
        """
        if overlay is None:
            return None
        inherited_fields = inherited if isinstance(inherited, dict) else {}
        return {**copy.deepcopy(inherited_fields), **copy.deepcopy(overlay)}

    @classmethod
    def _apply_layer(
        cls,
        *,
        config_data: dict[str, Any],
        layer: _ConfigurationLayer,
        init_fields: set[str],
    ) -> None:
        """Apply a parsed layer to mutable merged configuration data."""
        explicit_data = {name: copy.deepcopy(getattr(layer.configuration, name)) for name in init_fields}
        for field_name in layer.present_fields & (init_fields - {"extensions"}):
            if field_name == "server":
                config_data["server"] = cls._merge_server_blocks(
                    inherited=config_data["server"],
                    overlay=layer.configuration.server,
                )
            else:
                config_data[field_name] = explicit_data[field_name]

        if layer.reset_extensions:
            config_data["extensions"] = {}
        for field_name in layer.top_level_extension_fields:
            if field_name in layer.configuration.extensions:
                config_data["extensions"][field_name] = layer.configuration.extensions[field_name]
            else:
                config_data["extensions"].pop(field_name, None)
        for field_name in layer.nested_extension_fields:
            config_data["extensions"][field_name] = layer.configuration.extensions[field_name]

    @classmethod
    def load_with_overrides(
        cls,
        config_file: pathlib.Path | None = None,
        *,
        memory_db_type: str | None = None,
        initializers: Sequence[str | dict[str, Any]] | None = None,
        initialization_scripts: Sequence[str] | None = None,
        env_files: Sequence[str] | None = None,
        env_akv_ref: Sequence[str] | None = None,
        env_akv_strict: bool | None = None,
    ) -> "ConfigurationLoader":
        """
        Load configuration with optional overrides.

        This factory method implements a 3-layer configuration precedence:
        1. Default config file (~/.pyrit/.pyrit_conf) if it exists
        2. Explicit config_file argument if provided
        3. Individual override arguments (non-None values take precedence)

        Args:
            config_file: Optional path to a YAML-formatted configuration file.
            memory_db_type: Override for database type (in_memory, sqlite, azure_sql).
            initializers: Override for initializer list.
            initialization_scripts: Override for initialization script paths.
            env_files: Override for environment file paths.
            env_akv_ref: Override containing at most one Azure Key Vault bootstrap secret URL.
            env_akv_strict: Override for strict Key Vault bootstrap validation.

        Returns:
            A merged ConfigurationLoader instance.

        Raises:
            FileNotFoundError: If an explicitly specified config_file does not exist.
            _RemovedConfigurationOptionError: If the removed ``scenario`` block is present.
            ValueError: If the configuration is invalid.
        """
        import logging

        logger = logging.getLogger(__name__)

        init_fields = {config_field.name for config_field in fields(cls) if config_field.init}

        def to_init_data(config: ConfigurationLoader) -> dict[str, Any]:
            return {name: copy.deepcopy(getattr(config, name)) for name in init_fields}

        # 1. Try loading default config file if it exists
        config_data = to_init_data(cls())
        default_config_path = DEFAULT_CONFIG_PATH
        if default_config_path.exists():
            try:
                logger.info(f"Loading default configuration file: {default_config_path}")
                print(f"Loading default configuration file: {default_config_path}")
                config_data = to_init_data(cls.from_yaml_file(default_config_path))
            except _RemovedConfigurationOptionError:
                raise
            except Exception as e:
                logger.warning(f"Failed to load default config file {default_config_path}: {e}")

        # 2. Load explicit config file if provided (overrides default)
        if config_file is not None:
            if not config_file.exists():
                raise FileNotFoundError(f"Configuration file not found: {config_file}")
            logger.info(f"Loading configuration file: {config_file}")
            print(f"Loading configuration file: {config_file}")
            explicit_layer = cls._load_layer_from_yaml_file(file=config_file)
            cls._apply_layer(config_data=config_data, layer=explicit_layer, init_fields=init_fields)
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
            if isinstance(env_akv_ref, str):
                raise ValueError("env_akv_ref must be a sequence of Azure Key Vault secret URLs.")
            config_data["env_akv_ref"] = list(env_akv_ref)

        if env_akv_strict is not None:
            config_data["env_akv_strict"] = env_akv_strict

        return cls.from_dict(config_data)

    @classmethod
    def get_default_config_path(cls) -> pathlib.Path:
        """
        Get the default configuration file path.

        Returns:
            Path to the default config file in ~/.pyrit/.pyrit_conf
        """
        return DEFAULT_CONFIG_PATH

    def resolve_initializers(self, *, raise_on_initializer_error: bool = True) -> Sequence["PyRITInitializer"]:
        """
        Resolve initializer names to PyRITInitializer instances.

        Uses the InitializerRegistry to look up initializer classes by name
        and instantiate them with optional arguments.

        Args:
            raise_on_initializer_error: Whether to raise when an initializer cannot be resolved. If False,
                log the failure and continue resolving the remaining initializers.

        Returns:
            Sequence of PyRITInitializer instances.

        Raises:
            ValueError: If an initializer name is not found in the registry.
        """
        import logging

        from pyrit.registry import InitializerRegistry

        configs = self._initializer_configs
        resolved: list[PyRITInitializer] = []
        if not configs:
            return resolved

        registry = InitializerRegistry.get_registry_singleton()

        logging.getLogger(__name__).info("Running %d initializer(s)...", len(configs))

        for config in configs:
            try:
                instance = registry.create_and_configure(config.name, initializer_params=config.args)
            except KeyError as exc:
                available = ", ".join(sorted(registry.get_class_names()))
                error = ValueError(
                    f"Initializer '{config.name}' not found in registry.\nAvailable initializers: {available}"
                )
                if raise_on_initializer_error:
                    raise error from exc
                logging.getLogger(__name__).exception("Skipping initializer '%s': resolution failed.", config.name)
                continue
            except Exception:
                if raise_on_initializer_error:
                    raise
                logging.getLogger(__name__).exception("Skipping initializer '%s': resolution failed.", config.name)
                continue

            resolved.append(instance)

        return resolved

    def resolve_initialization_scripts(self) -> Sequence[pathlib.Path] | None:
        """
        Resolve initialization script paths.

        Returns:
            None if field is None (use defaults), empty list if field is [],
            or a sequence of resolved Path objects.
        """
        # None means "use defaults" - return None to signal this
        if self.initialization_scripts is None:
            return None

        # Empty list means "load nothing" - return empty list
        if len(self.initialization_scripts) == 0:
            return list[pathlib.Path]()

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
            return list[pathlib.Path]()

        resolved: list[pathlib.Path] = []
        for env_str in self.env_files:
            env_path = pathlib.Path(env_str)
            if not env_path.is_absolute():
                env_path = pathlib.Path.cwd() / env_path
            resolved.append(env_path)

        return resolved

    def resolve_env_akv_ref(self) -> list[str] | None:
        """
        Return the AKV bootstrap secret URLs, or ``None`` when not configured.

        Returns:
            list[str] | None: The configured AKV bootstrap secret URLs, or ``None``.
        """
        return self.env_akv_ref

    async def initialize_pyrit_async(self, *, raise_on_initializer_error: bool = True) -> None:
        """
        Initialize PyRIT with the loaded configuration.

        Resolves the ``.pyrit_conf`` baseline initializers to instances and calls the core
        ``initialize_pyrit_async`` function. This method is intentionally unaware of any
        persisted additional initializers: consumers such as ``pyrit.backend.main.lifespan``
        run those after the baseline.

        Args:
            raise_on_initializer_error: Whether initializer resolution, loading, validation, or execution
                failures should abort initialization. Defaults to True.

        Raises:
            ValueError: If configuration is invalid or initializers cannot be resolved.
        """
        resolved_initializers = self.resolve_initializers(
            raise_on_initializer_error=raise_on_initializer_error,
        )
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
            env_akv_strict=self.env_akv_strict,
            silent=self.silent,
            seed=self.seed,
            raise_on_initializer_error=raise_on_initializer_error,
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
