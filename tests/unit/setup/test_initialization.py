# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

import pytest

from pyrit.common.apply_defaults import reset_default_values
from pyrit.common.singleton import Singleton
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initialization import (
    _load_env_from_akv_async,
    _load_environment_files,
    _load_initializers_from_scripts,
    _parse_akv_secret_url,
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

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._load_environment_files")
    @mock.patch("pyrit.setup.initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_env_akv_ref(self, mock_load_akv, mock_load_env, mock_set_memory):
        """Test that env_akv_ref triggers AKV env loading."""
        refs = ["https://vault.vault.azure.net/secrets/test-secret"]

        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=refs)

        mock_load_akv.assert_awaited_once()
        assert mock_load_akv.await_args.kwargs["secret_urls"] == refs
        assert mock_load_akv.await_args.kwargs["silent"] is False
        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._load_environment_files")
    @mock.patch("pyrit.setup.initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_empty_env_akv_ref_does_not_load_akv(
        self, mock_load_akv, mock_load_env, mock_set_memory
    ):
        """Test that empty env_akv_ref does not invoke AKV loading."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=[])

        mock_load_akv.assert_not_called()
        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_loads_akv_before_env_files(self, mock_set_memory):
        """Test that AKV refs are loaded before env_files so env_files can override values."""
        call_order: list[str] = []

        async def _record_akv_call(*, secret_urls, silent=False):
            call_order.append("akv")

        def _record_env_file_call(*, env_files, silent=False):
            call_order.append("env_files")

        refs = ["https://vault.vault.azure.net/secrets/test-secret"]

        with (
            mock.patch("pyrit.setup.initialization._load_env_from_akv_async", side_effect=_record_akv_call),
            mock.patch("pyrit.setup.initialization._load_environment_files", side_effect=_record_env_file_call),
        ):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=refs)

        assert call_order == ["akv", "env_files"]
        mock_set_memory.assert_called_once()


@pytest.fixture
def reset_memory_singletons():
    """Force memory __init__ (and schema migration) to run by clearing cached singletons."""
    saved_instances = Singleton._instances.copy()
    Singleton._instances.clear()
    try:
        yield
    finally:
        Singleton._instances.clear()
        Singleton._instances.update(saved_instances)


@pytest.mark.usefixtures("reset_memory_singletons")
class TestInitializePyritSilent:
    """Tests that the silent flag suppresses all console output during initialization."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    async def test_initialize_silent_produces_no_output(self, capsys):
        """initialize_pyrit_async with silent=True must not print anything to stdout."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

        captured = capsys.readouterr()
        assert captured.out == ""

    async def test_initialize_not_silent_prints_migration_message(self, capsys):
        """Without silent, the Alembic schema-check message is printed and tagged as Alembic output."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=False)

        captured = capsys.readouterr()
        assert "[pyrit:alembic] No new upgrade operations detected." in captured.out


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


class TestAkvEnvironmentLoading:
    """Tests for AKV URL parsing and env loading helpers."""

    def test_parse_akv_secret_url_with_version(self):
        url = "https://myvault.vault.azure.net/secrets/my-secret/abc123"

        vault_url, secret_name, secret_version = _parse_akv_secret_url(url)

        assert vault_url == "https://myvault.vault.azure.net"
        assert secret_name == "my-secret"
        assert secret_version == "abc123"

    def test_parse_akv_secret_url_without_version(self):
        url = "https://myvault.vault.azure.net/secrets/my-secret"

        vault_url, secret_name, secret_version = _parse_akv_secret_url(url)

        assert vault_url == "https://myvault.vault.azure.net"
        assert secret_name == "my-secret"
        assert secret_version is None

    def test_parse_akv_secret_url_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid AKV secret URL"):
            _parse_akv_secret_url("https://myvault.vault.azure.net/not-secrets/my-secret")

    @mock.patch("pyrit.setup.initialization.dotenv.load_dotenv")
    async def test_load_env_from_akv_async_empty_urls_noop(self, mock_load_dotenv):
        await _load_env_from_akv_async(secret_urls=[])
        mock_load_dotenv.assert_not_called()

    async def test_load_env_from_akv_async_loads_secret_content(self):
        class FakeCredential:
            pass

        client_calls: list[tuple[str, object, object]] = []

        class FakeSecretClient:
            def __init__(self, *, vault_url, credential):
                client_calls.append(("init", vault_url, credential))

            async def get_secret(self, name, version=None):
                client_calls.append(("get_secret", name, version))
                return types.SimpleNamespace(value="AKV_VAR=from_secret\n")

        azure_module = types.ModuleType("azure")
        identity_module = types.ModuleType("azure.identity")
        identity_aio_module = types.ModuleType("azure.identity.aio")
        keyvault_module = types.ModuleType("azure.keyvault")
        keyvault_secrets_module = types.ModuleType("azure.keyvault.secrets")
        keyvault_secrets_aio_module = types.ModuleType("azure.keyvault.secrets.aio")

        identity_aio_module.DefaultAzureCredential = FakeCredential
        keyvault_secrets_aio_module.SecretClient = FakeSecretClient

        with (
            mock.patch.dict(
                sys.modules,
                {
                    "azure": azure_module,
                    "azure.identity": identity_module,
                    "azure.identity.aio": identity_aio_module,
                    "azure.keyvault": keyvault_module,
                    "azure.keyvault.secrets": keyvault_secrets_module,
                    "azure.keyvault.secrets.aio": keyvault_secrets_aio_module,
                },
            ),
            mock.patch("pyrit.setup.initialization.dotenv.load_dotenv") as mock_load_dotenv,
            mock.patch("pyrit.setup.initialization._print_msg") as mock_print_msg,
        ):
            await _load_env_from_akv_async(
                secret_urls=["https://myvault.vault.azure.net/secrets/my-secret/v1"],
                silent=True,
            )

        assert client_calls[0][0] == "init"
        assert client_calls[0][1] == "https://myvault.vault.azure.net"
        assert isinstance(client_calls[0][2], FakeCredential)
        assert client_calls[1] == ("get_secret", "my-secret", "v1")

        stream = mock_load_dotenv.call_args.kwargs["stream"]
        assert stream.getvalue() == "AKV_VAR=from_secret\n"
        assert mock_load_dotenv.call_args.kwargs["override"] is True
        assert mock_print_msg.call_count == 2
