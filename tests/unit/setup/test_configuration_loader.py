# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import tempfile
from unittest import mock

import pytest

from pyrit.setup.configuration_loader import (
    ConfigurationLoader,
    InitializerConfig,
    initialize_from_config_async,
)


class TestInitializerConfig:
    """Tests for InitializerConfig dataclass."""

    def test_initializer_config_with_name_only(self):
        """Test creating InitializerConfig with just a name."""
        config = InitializerConfig(name="simple")
        assert config.name == "simple"
        assert config.args is None

    def test_initializer_config_with_args(self):
        """Test creating InitializerConfig with name and args."""
        config = InitializerConfig(name="custom", args={"param1": "value1"})
        assert config.name == "custom"
        assert config.args == {"param1": "value1"}


class TestConfigurationLoader:
    """Tests for ConfigurationLoader class."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ConfigurationLoader()
        assert config.memory_db_type == "sqlite"
        assert config.initializers == []
        assert config.initialization_scripts is None  # None means "use defaults"
        assert config.env_files is None  # None means "use defaults"
        assert config.silent is False

    def test_valid_memory_db_types_snake_case(self):
        """Test all valid memory database types in snake_case."""
        for db_type in ["in_memory", "sqlite", "azure_sql"]:
            config = ConfigurationLoader(memory_db_type=db_type)
            assert config.memory_db_type == db_type

    def test_memory_db_type_normalization_from_pascal_case(self):
        """Test that PascalCase memory_db_type is normalized to snake_case."""
        config = ConfigurationLoader(memory_db_type="InMemory")
        assert config.memory_db_type == "in_memory"

        config = ConfigurationLoader(memory_db_type="SQLite")
        assert config.memory_db_type == "sqlite"

        config = ConfigurationLoader(memory_db_type="AzureSQL")
        assert config.memory_db_type == "azure_sql"

    def test_memory_db_type_normalization_case_insensitive(self):
        """Test that memory_db_type normalization is case-insensitive."""
        config = ConfigurationLoader(memory_db_type="SQLITE")
        assert config.memory_db_type == "sqlite"

        config = ConfigurationLoader(memory_db_type="In_Memory")
        assert config.memory_db_type == "in_memory"

    def test_invalid_memory_db_type_raises_error(self):
        """Test that invalid memory_db_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid memory_db_type"):
            ConfigurationLoader(memory_db_type="InvalidType")

    def test_initializer_as_string(self):
        """Test initializers specified as simple strings."""
        config = ConfigurationLoader(initializers=["simple", "airt"])
        assert len(config._initializer_configs) == 2
        assert config._initializer_configs[0].name == "simple"
        assert config._initializer_configs[0].args is None
        assert config._initializer_configs[1].name == "airt"

    def test_initializer_as_dict_with_name_only(self):
        """Test initializers specified as dicts with only name."""
        config = ConfigurationLoader(initializers=[{"name": "simple"}])
        assert len(config._initializer_configs) == 1
        assert config._initializer_configs[0].name == "simple"
        assert config._initializer_configs[0].args is None

    def test_initializer_as_dict_with_args(self):
        """Test initializers specified as dicts with name and args."""
        config = ConfigurationLoader(initializers=[{"name": "custom", "args": {"param1": "value1", "param2": 42}}])
        assert len(config._initializer_configs) == 1
        assert config._initializer_configs[0].name == "custom"
        assert config._initializer_configs[0].args == {"param1": "value1", "param2": 42}

    def test_mixed_initializer_formats(self):
        """Test initializers with mixed string and dict formats."""
        config = ConfigurationLoader(
            initializers=[
                "simple",
                {"name": "airt"},
                {"name": "custom", "args": {"key": "value"}},
            ]
        )
        assert len(config._initializer_configs) == 3
        assert config._initializer_configs[0].name == "simple"
        assert config._initializer_configs[1].name == "airt"
        assert config._initializer_configs[2].name == "custom"
        assert config._initializer_configs[2].args == {"key": "value"}

    def test_initializer_name_normalization_from_pascal_case(self):
        """Test that PascalCase initializer names are normalized to snake_case."""
        config = ConfigurationLoader(initializers=["SimpleInitializer", "AIRTInitializer"])
        assert config._initializer_configs[0].name == "simple_initializer"
        assert config._initializer_configs[1].name == "airt_initializer"

    def test_initializer_name_normalization_preserves_snake_case(self):
        """Test that snake_case names are preserved."""
        config = ConfigurationLoader(initializers=["simple_initializer", "airt_init"])
        assert config._initializer_configs[0].name == "simple_initializer"
        assert config._initializer_configs[1].name == "airt_init"

    def test_initializer_name_already_snake_case(self):
        """Test that snake_case names remain unchanged."""
        config = ConfigurationLoader(initializers=["load_default_datasets", "objective_list"])
        assert config._initializer_configs[0].name == "load_default_datasets"
        assert config._initializer_configs[1].name == "objective_list"

    def test_initializer_dict_without_name_raises_error(self):
        """Test that dict initializer without 'name' raises ValueError."""
        with pytest.raises(ValueError, match="must have a 'name' field"):
            ConfigurationLoader(initializers=[{"args": {"key": "value"}}])

    def test_initializer_invalid_type_raises_error(self):
        """Test that invalid initializer type raises ValueError."""
        with pytest.raises(ValueError, match="must be a string or dict"):
            ConfigurationLoader(initializers=[123])  # type: ignore[arg-type]

    def test_from_dict_with_all_fields(self):
        """Test from_dict with all configuration fields."""
        data = {
            "memory_db_type": "InMemory",
            "initializers": ["simple"],
            "initialization_scripts": ["/path/to/script.py"],
            "env_files": ["/path/to/.env"],
            "silent": True,
        }
        config = ConfigurationLoader.from_dict(data)
        assert config.memory_db_type == "in_memory"  # Normalized to snake_case
        assert config.initializers == ["simple"]
        assert config.initialization_scripts == ["/path/to/script.py"]
        assert config.env_files == ["/path/to/.env"]
        assert config.silent is True

    def test_from_dict_filters_none_values(self):
        """Test that from_dict filters out None values."""
        data = {
            "memory_db_type": "SQLite",
            "initializers": None,
            "env_files": [],
        }
        config = ConfigurationLoader.from_dict(data)
        assert config.memory_db_type == "sqlite"  # Normalized to snake_case
        assert config.initializers == []  # Uses default, not None

    def test_from_yaml_file(self):
        """Test loading configuration from a YAML file."""
        yaml_content = """
memory_db_type: in_memory
initializers:
  - simple
  - name: airt
    args:
      key: value
silent: true
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = f.name

        try:
            config = ConfigurationLoader.from_yaml_file(yaml_path)
            assert config.memory_db_type == "in_memory"
            assert len(config._initializer_configs) == 2
            assert config._initializer_configs[0].name == "simple"
            assert config._initializer_configs[1].name == "airt"
            assert config._initializer_configs[1].args == {"key": "value"}
            assert config.silent is True
        finally:
            pathlib.Path(yaml_path).unlink()

    def test_from_empty_yaml_file_raises_value_error(self, tmp_path):
        """Test that an empty YAML file raises a clear ValueError."""
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="is empty"):
            ConfigurationLoader.from_yaml_file(yaml_path)

    def test_get_default_config_path(self):
        """Test get_default_config_path returns expected path."""
        default_path = ConfigurationLoader.get_default_config_path()
        assert default_path.name == ".pyrit_conf"
        assert ".pyrit" in str(default_path)


class TestConfigurationLoaderResolvers:
    """Tests for ConfigurationLoader path resolution methods."""

    def test_resolve_initialization_scripts_none_returns_none(self):
        """Test that None (default) returns None to signal 'use defaults'."""
        config = ConfigurationLoader()
        assert config._resolve_initialization_scripts() is None

    def test_resolve_initialization_scripts_empty_list_returns_empty_list(self):
        """Test that explicit empty list [] returns empty list to signal 'load nothing'."""
        config = ConfigurationLoader(initialization_scripts=[])
        resolved = config._resolve_initialization_scripts()
        assert resolved is not None
        assert resolved == []

    def test_resolve_initialization_scripts_absolute_path(self):
        """Test resolving absolute script paths."""
        config = ConfigurationLoader(initialization_scripts=["/absolute/path/script.py"])
        resolved = config._resolve_initialization_scripts()
        assert resolved is not None
        assert len(resolved) == 1
        # Check path ends with expected components (Windows adds drive letter to Unix-style paths)
        assert resolved[0].parts[-3:] == ("absolute", "path", "script.py")

    def test_resolve_initialization_scripts_relative_path(self):
        """Test resolving relative script paths (converted to absolute)."""
        config = ConfigurationLoader(initialization_scripts=["relative/script.py"])
        resolved = config._resolve_initialization_scripts()
        assert resolved is not None
        assert len(resolved) == 1
        assert resolved[0].is_absolute()
        # Check path ends with expected components (works on both Unix and Windows)
        assert resolved[0].parts[-2:] == ("relative", "script.py")

    def test_resolve_env_files_none_returns_none(self):
        """Test that None (default) returns None to signal 'use defaults'."""
        config = ConfigurationLoader()
        assert config._resolve_env_files() is None

    def test_resolve_env_files_empty_list_returns_empty_list(self):
        """Test that explicit empty list [] returns empty list to signal 'load nothing'."""
        config = ConfigurationLoader(env_files=[])
        resolved = config._resolve_env_files()
        assert resolved is not None
        assert resolved == []

    def test_resolve_env_files_absolute_path(self):
        """Test resolving absolute env file paths."""
        config = ConfigurationLoader(env_files=["/path/to/.env"])
        resolved = config._resolve_env_files()
        assert resolved is not None
        assert len(resolved) == 1
        # Check path ends with expected components (Windows adds drive letter to Unix-style paths)
        assert resolved[0].parts[-3:] == ("path", "to", ".env")


@pytest.mark.usefixtures("patch_central_database")
class TestConfigurationLoaderInitialization:
    """Tests for ConfigurationLoader.initialize_pyrit_async method."""

    @pytest.mark.asyncio
    @mock.patch("pyrit.setup.configuration_loader.initialize_pyrit_async")
    async def test_initialize_pyrit_async_basic(self, mock_init):
        """Test basic initialization with minimal configuration."""
        config = ConfigurationLoader(memory_db_type="in_memory")
        await config.initialize_pyrit_async()

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        # Should map snake_case to internal constant
        assert call_kwargs["memory_db_type"] == "InMemory"
        assert call_kwargs["initialization_scripts"] is None
        assert call_kwargs["initializers"] is None
        assert call_kwargs["env_files"] is None
        assert call_kwargs["silent"] is False

    @pytest.mark.asyncio
    @mock.patch("pyrit.setup.configuration_loader.initialize_pyrit_async")
    @mock.patch("pyrit.registry.InitializerRegistry")
    async def test_initialize_pyrit_async_with_initializers(self, mock_registry_cls, mock_init):
        """Test initialization with initializers resolved from registry."""
        # Setup mock registry
        mock_registry = mock.MagicMock()
        mock_registry_cls.return_value = mock_registry

        # Mock an initializer class
        mock_initializer_class = mock.MagicMock()
        mock_initializer_instance = mock.MagicMock()
        mock_initializer_class.return_value = mock_initializer_instance
        mock_registry.get_class.return_value = mock_initializer_class

        config = ConfigurationLoader(
            memory_db_type="in_memory",
            initializers=["simple"],
        )
        await config.initialize_pyrit_async()

        # Verify registry was used to resolve initializer
        mock_registry.get_class.assert_called_once_with("simple")
        mock_initializer_class.assert_called_once_with()

        # Verify initialize was called with resolved initializers
        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["initializers"] == [mock_initializer_instance]

    @pytest.mark.asyncio
    @mock.patch("pyrit.registry.InitializerRegistry")
    async def test_initialize_pyrit_async_unknown_initializer_raises_error(self, mock_registry_cls):
        """Test that unknown initializer name raises ValueError."""
        mock_registry = mock.MagicMock()
        mock_registry_cls.return_value = mock_registry
        mock_registry.get_class.return_value = None
        mock_registry.get_names.return_value = ["simple", "airt"]

        config = ConfigurationLoader(
            memory_db_type="in_memory",
            initializers=["unknown_initializer"],
        )

        with pytest.raises(ValueError, match="not found in registry"):
            await config.initialize_pyrit_async()


@pytest.mark.usefixtures("patch_central_database")
class TestInitializeFromConfigAsync:
    """Tests for initialize_from_config_async function."""

    @pytest.mark.asyncio
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.from_yaml_file")
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.initialize_pyrit_async")
    async def test_initialize_from_config_with_path(self, mock_init, mock_from_yaml):
        """Test initialize_from_config_async with explicit path."""
        mock_config = ConfigurationLoader()
        mock_from_yaml.return_value = mock_config

        result = await initialize_from_config_async("/path/to/config.yaml")

        mock_from_yaml.assert_called_once_with(pathlib.Path("/path/to/config.yaml"))
        mock_init.assert_called_once()
        assert result is mock_config

    @pytest.mark.asyncio
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.from_yaml_file")
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.initialize_pyrit_async")
    async def test_initialize_from_config_with_string_path(self, mock_init, mock_from_yaml):
        """Test initialize_from_config_async with string path."""
        mock_config = ConfigurationLoader()
        mock_from_yaml.return_value = mock_config

        result = await initialize_from_config_async("/path/to/config.yaml")

        # Should convert string to Path
        call_args = mock_from_yaml.call_args[0][0]
        assert isinstance(call_args, pathlib.Path)

    @pytest.mark.asyncio
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.get_default_config_path")
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.from_yaml_file")
    @mock.patch("pyrit.setup.configuration_loader.ConfigurationLoader.initialize_pyrit_async")
    async def test_initialize_from_config_default_path(self, mock_init, mock_from_yaml, mock_default_path):
        """Test initialize_from_config_async uses default path when none specified."""
        mock_config = ConfigurationLoader()
        mock_from_yaml.return_value = mock_config
        mock_default_path.return_value = pathlib.Path("/default/path/.pyrit_conf")

        await initialize_from_config_async()

        mock_default_path.assert_called_once()
        mock_from_yaml.assert_called_once_with(pathlib.Path("/default/path/.pyrit_conf"))


class TestLoadWithOverrides:
    """Tests for ConfigurationLoader.load_with_overrides static method."""

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_defaults_when_no_config_file(self, mock_default_path):
        """Test default values when no config file exists."""
        mock_default_path.exists.return_value = False

        config = ConfigurationLoader.load_with_overrides()

        assert config.memory_db_type == "sqlite"
        assert config.initializers == []
        assert config.initialization_scripts is None
        assert config.env_files is None

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_memory_db_type_override(self, mock_default_path):
        """Test memory_db_type override takes precedence."""
        mock_default_path.exists.return_value = False

        config = ConfigurationLoader.load_with_overrides(memory_db_type="in_memory")

        assert config.memory_db_type == "in_memory"

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_initializers_override(self, mock_default_path):
        """Test initializers override takes precedence."""
        mock_default_path.exists.return_value = False

        config = ConfigurationLoader.load_with_overrides(initializers=["custom_init"])

        assert len(config._initializer_configs) == 1
        assert config._initializer_configs[0].name == "custom_init"

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_initialization_scripts_override(self, mock_default_path):
        """Test initialization_scripts override takes precedence."""
        mock_default_path.exists.return_value = False

        config = ConfigurationLoader.load_with_overrides(initialization_scripts=["/path/to/script.py"])

        assert config.initialization_scripts == ["/path/to/script.py"]

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_env_files_override(self, mock_default_path):
        """Test env_files override takes precedence."""
        mock_default_path.exists.return_value = False

        config = ConfigurationLoader.load_with_overrides(env_files=["/path/to/.env"])

        assert config.env_files == ["/path/to/.env"]

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_converts_sequence_to_list(self, mock_default_path):
        """Test that Sequence inputs are converted to list for dataclass compatibility."""
        mock_default_path.exists.return_value = False

        # Pass tuples (which are Sequences but not Lists)
        config = ConfigurationLoader.load_with_overrides(
            initializers=("init1", "init2"),
            initialization_scripts=("/path/script1.py", "/path/script2.py"),
            env_files=("/path/.env",),
        )

        # Verify they are stored as lists
        assert isinstance(config.initializers, list)
        assert isinstance(config.initialization_scripts, list)
        assert isinstance(config.env_files, list)
        assert config.initializers == ["init1", "init2"]
        assert config.initialization_scripts == ["/path/script1.py", "/path/script2.py"]
        assert config.env_files == ["/path/.env"]

    def test_load_with_overrides_explicit_config_file_not_found(self):
        """Test FileNotFoundError when explicit config file doesn't exist."""
        non_existent_path = pathlib.Path("/non/existent/config.yaml")

        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            ConfigurationLoader.load_with_overrides(config_file=non_existent_path)

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_explicit_config_file_overrides_default(self, mock_default_path):
        """Test explicit config file values override default config file."""
        mock_default_path.exists.return_value = False

        # Create a temp config file
        yaml_content = """
memory_db_type: azure_sql
initializers:
  - explicit_init
initialization_scripts:
  - /explicit/script.py
env_files:
  - /explicit/.env
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_path = pathlib.Path(f.name)

        try:
            config = ConfigurationLoader.load_with_overrides(config_file=config_path)

            assert config.memory_db_type == "azure_sql"
            assert config._initializer_configs[0].name == "explicit_init"
            assert config.initialization_scripts == ["/explicit/script.py"]
            assert config.env_files == ["/explicit/.env"]
        finally:
            config_path.unlink()

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_cli_overrides_config_file(self, mock_default_path):
        """Test CLI arguments override config file values."""
        mock_default_path.exists.return_value = False

        # Create a temp config file with some values
        yaml_content = """
memory_db_type: azure_sql
initializers:
  - file_init
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_path = pathlib.Path(f.name)

        try:
            # CLI overrides should take precedence
            config = ConfigurationLoader.load_with_overrides(
                config_file=config_path,
                memory_db_type="in_memory",
                initializers=["cli_init"],
            )

            assert config.memory_db_type == "in_memory"
            assert config._initializer_configs[0].name == "cli_init"
        finally:
            config_path.unlink()

    @mock.patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH")
    def test_load_with_overrides_preserves_silent_from_config_file(self, mock_default_path):
        """Test that load_with_overrides preserves the silent flag from config files."""
        mock_default_path.exists.return_value = False

        yaml_content = """
memory_db_type: sqlite
silent: true
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_path = pathlib.Path(f.name)

        try:
            config = ConfigurationLoader.load_with_overrides(config_file=config_path)
            assert config.silent is True
        finally:
            config_path.unlink()
