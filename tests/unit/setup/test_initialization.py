# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import tempfile
from unittest import mock

import pytest

from pyrit.common.apply_defaults import reset_default_values
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initialization import (
    _load_environment_files,
    _load_initializers_from_scripts,
)


class TestLoadInitializersFromScripts:
    """Tests for _load_initializers_from_scripts function."""

    def test_load_initializer_from_script(self):
        """Test loading an initializer from a Python script."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
from pyrit.setup.initializers import PyRITInitializer

class TestInitializer(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Test Initializer"

    @property
    def description(self) -> str:
        return "Test description"

    async def initialize_async(self) -> None:
        pass
"""
            )
            script_path = f.name

        try:
            initializers = _load_initializers_from_scripts(script_paths=[script_path])
            assert len(initializers) == 1
            assert initializers[0].name == "Test Initializer"
        finally:
            os.unlink(script_path)

    def test_script_not_found_raises_error(self):
        """Test that FileNotFoundError is raised for non-existent script."""
        with pytest.raises(FileNotFoundError):
            _load_initializers_from_scripts(script_paths=["nonexistent_script.py"])

    def test_ignores_imported_initializer_classes(self):
        """Test that imported initializer classes are not instantiated from the script."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            helper_path = temp_path / "helper_init.py"
            script_path = temp_path / "script_init.py"

            helper_path.write_text(
                """
from pyrit.setup.initializers import PyRITInitializer

class ImportedInitializer(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Imported"

    @property
    def description(self) -> str:
        return "Imported initializer"

    async def initialize_async(self) -> None:
        pass
"""
            )

            script_path.write_text(
                f"""
import sys

sys.path.insert(0, {temp_dir!r})

from helper_init import ImportedInitializer
from pyrit.setup.initializers import PyRITInitializer

class LocalInitializer(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Local"

    @property
    def description(self) -> str:
        return "Local initializer"

    async def initialize_async(self) -> None:
        pass
"""
            )

            initializers = _load_initializers_from_scripts(script_paths=[script_path])

            assert len(initializers) == 1
            assert initializers[0].name == "Local"


class TestInitializePyrit:
    """Tests for initialize_pyrit_async function - basic orchestration tests."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._load_environment_files")
    async def test_initialize_basic(self, mock_load_env, mock_set_memory):
        """Test basic initialization."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY)

        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._load_environment_files")
    async def test_initialize_with_script(self, mock_load_env, mock_set_memory):
        """Test initialization with a script."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
from pyrit.setup.initializers import PyRITInitializer

class ScriptInit(PyRITInitializer):
    @property
    def name(self) -> str:
        return "Script"

    @property
    def description(self) -> str:
        return "From script"

    async def initialize_async(self) -> None:
        pass
"""
            )
            script_path = f.name

        try:
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, initialization_scripts=[script_path])
            mock_load_env.assert_called_once()
            mock_set_memory.assert_called_once()
        finally:
            os.unlink(script_path)

    async def test_invalid_memory_type_raises_error(self):
        """Test that invalid memory type raises ValueError."""
        with pytest.raises(ValueError, match="is not a supported type"):
            await initialize_pyrit_async(memory_db_type="InvalidType")  # type: ignore[arg-type]


class TestLoadEnvironmentFiles:
    """Tests for _load_environment_files function and env_files parameter in initialize_pyrit_async."""

    @mock.patch("pyrit.setup.initialization.dotenv.load_dotenv")
    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_loads_default_env_files_when_none_provided(self, mock_config_path, mock_load_dotenv):
        """Test that default .env and .env.local files are loaded when env_files is None."""
        # Create temporary directory and files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"

            # Create the files
            env_file.write_text("VAR1=value1")
            env_local_file.write_text("VAR2=value2")

            # Mock CONFIGURATION_DIRECTORY_PATH to point to our temp directory
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            # Call the function with None (default behavior)
            _load_environment_files(env_files=None)

            # Verify both files were loaded
            assert mock_load_dotenv.call_count == 2
            calls = [call[0][0] for call in mock_load_dotenv.call_args_list]
            assert env_file in calls
            assert env_local_file in calls

    @mock.patch("pyrit.setup.initialization.dotenv.load_dotenv")
    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_only_loads_existing_default_files(self, mock_config_path, mock_load_dotenv):
        """Test that only existing default files are loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"

            # Only create .env, not .env.local
            env_file.write_text("VAR1=value1")

            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            _load_environment_files(env_files=None)

            # Verify only one file was loaded
            assert mock_load_dotenv.call_count == 1
            assert mock_load_dotenv.call_args[0][0] == env_file

    @mock.patch("pyrit.setup.initialization.dotenv.load_dotenv")
    async def test_loads_custom_env_files_in_order(self, mock_load_dotenv):
        """Test that custom env_files are loaded in the order provided."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env1 = temp_path / ".env.test"
            env2 = temp_path / ".env.prod"
            env3 = temp_path / ".env.local"

            # Create files
            env1.write_text("VAR=test")
            env2.write_text("VAR=prod")
            env3.write_text("VAR=local")

            # Pass custom files
            _load_environment_files(env_files=[env1, env2, env3])

            # Verify all three files were loaded in order
            assert mock_load_dotenv.call_count == 3
            call_args = [call[0][0] for call in mock_load_dotenv.call_args_list]
            assert call_args == [env1, env2, env3]

    async def test_raises_error_for_nonexistent_env_file(self):
        """Test that ValueError is raised for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/path/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            _load_environment_files(env_files=[nonexistent])

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_pyrit_with_custom_env_files(self, mock_set_memory):
        """Test initialize_pyrit_async with custom env_files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env.custom"
            env_file.write_text("CUSTOM_VAR=custom_value")

            # Should not raise an error
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[env_file])

            mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_pyrit_raises_for_nonexistent_env_file(self, mock_set_memory):
        """Test that initialize_pyrit_async raises ValueError for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[nonexistent])

    @mock.patch("pyrit.setup.initialization.dotenv.load_dotenv")
    @mock.patch("pyrit.setup.initialization.path.HOME_PATH")
    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_custom_env_files_override_default_behavior(self, mock_set_memory, mock_home_path, mock_load_dotenv):
        """Test that passing custom env_files prevents loading default files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)

            # Create default files
            default_env = temp_path / ".env"
            default_env_local = temp_path / ".env.local"
            default_env.write_text("DEFAULT=value")
            default_env_local.write_text("DEFAULT_LOCAL=value")

            # Create custom file
            custom_env = temp_path / ".env.custom"
            custom_env.write_text("CUSTOM=value")

            mock_home_path.__truediv__ = lambda self, other: temp_path / other

            # Pass custom env_files - should NOT load defaults
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[custom_env])

            # Verify only custom file was loaded, not the default ones
            assert mock_load_dotenv.call_count == 1
            assert mock_load_dotenv.call_args[0][0] == custom_env
