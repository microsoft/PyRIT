# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import tempfile
import types
from unittest import mock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from pyrit.common.apply_defaults import reset_default_values
from pyrit.common.singleton import Singleton
from pyrit.exceptions import KeyVaultInitializationException
from pyrit.registry import InitializerRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initialization import (
    _load_env_from_akv_async,
    _load_environment_files,
    _parse_akv_secret_url,
    _parse_environment_value_reference,
    _resolve_environment_files,
    _resolve_environment_references_async,
    _warn_about_akv_environment_files,
)


class TestLoadInitializersFromScripts:
    """Tests for InitializerRegistry.create_from_script_paths."""

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
            initializers = InitializerRegistry.get_registry_singleton().create_from_script_paths(
                script_paths=[script_path]
            )
            assert len(initializers) == 1
            assert initializers[0].name == "Test Initializer"
        finally:
            os.unlink(script_path)

    def test_script_not_found_raises_error(self):
        """Test that FileNotFoundError is raised for non-existent script."""
        with pytest.raises(FileNotFoundError):
            InitializerRegistry.get_registry_singleton().create_from_script_paths(
                script_paths=["nonexistent_script.py"]
            )

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

            initializers = InitializerRegistry.get_registry_singleton().create_from_script_paths(
                script_paths=[script_path]
            )

            assert len(initializers) == 1
            assert initializers[0].name == "Local"


class TestInitializePyrit:
    """Tests for initialize_pyrit_async function - basic orchestration tests."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._resolve_environment_files", return_value=({}, False))
    async def test_initialize_basic(self, mock_resolve_env, mock_set_memory):
        """Test basic initialization."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)

        mock_resolve_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._resolve_environment_files", return_value=({}, False))
    async def test_initialize_with_script(self, mock_resolve_env, mock_set_memory):
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
            mock_resolve_env.assert_called_once()
            mock_set_memory.assert_called_once()
        finally:
            os.unlink(script_path)

    @mock.patch("pyrit.setup.initialization._resolve_environment_files", return_value=({}, False))
    async def test_invalid_memory_type_raises_error(self, mock_resolve_env):
        """Test that invalid memory type raises ValueError."""
        with pytest.raises(ValueError, match="is not a supported type"):
            await initialize_pyrit_async(memory_db_type="InvalidType", load_defaults=False)  # type: ignore[arg-type]

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._resolve_environment_files", return_value=({}, False))
    @mock.patch("pyrit.setup.initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_env_akv_ref(self, mock_load_akv, mock_resolve_env, mock_set_memory):
        """Test that env_akv_ref loads its bootstrap secret."""
        ref = "https://vault.vault.azure.net/secrets/test-secret"

        mock_load_akv.return_value = {}, "https://vault.vault.azure.net"

        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=ref, load_defaults=False)

        mock_load_akv.assert_awaited_once()
        assert mock_load_akv.await_args.kwargs["secret_url"] == ref
        assert mock_load_akv.await_args.kwargs["strict"] is True
        assert mock_load_akv.await_args.kwargs["silent"] is False
        mock_resolve_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization._resolve_environment_files", return_value=({}, False))
    @mock.patch("pyrit.setup.initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_empty_env_akv_ref_raises(self, mock_load_akv, mock_resolve_env, mock_set_memory):
        """Test that an empty env_akv_ref is rejected."""
        with pytest.raises(ValueError, match="env_akv_ref must be a non-empty"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref="", load_defaults=False)

        mock_load_akv.assert_not_called()
        mock_resolve_env.assert_not_called()
        mock_set_memory.assert_not_called()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_akv_and_local_files_are_applied_atomically(self, mock_set_memory):
        ref = "https://vault.vault.azure.net/secrets/bootstrap"
        nonexistent = pathlib.Path("/nonexistent/.env")

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("pyrit.setup.initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value=({"FROM_AKV": "resolved"}, "https://vault.vault.azure.net"),
                ),
                pytest.raises(ValueError, match="Environment file not found"),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=ref,
                    env_files=[nonexistent],
                    load_defaults=False,
                )

            assert "FROM_AKV" not in os.environ

        mock_set_memory.assert_not_called()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_stages_local_overrides_on_akv_environment(self, mock_set_memory):
        ref = "https://vault.vault.azure.net/secrets/bootstrap"
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = pathlib.Path(temp_dir) / ".env.local"
            local_file.write_text("DERIVED=${BASE}\nBASE=local")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("pyrit.setup.initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value=({"BASE": "akv", "ONLY_AKV": "shared"}, "https://vault.vault.azure.net"),
                ),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=ref,
                    env_files=[local_file],
                    load_defaults=False,
                )

                assert os.environ["BASE"] == "local"
                assert os.environ["DERIVED"] == "akv"
                assert os.environ["ONLY_AKV"] == "shared"

        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_default_files_override_akv_in_order(self, mock_set_memory):
        ref = "https://vault.vault.azure.net/secrets/bootstrap"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VALUE=env")
            (temp_path / ".env.local").write_text("VALUE=local")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch("pyrit.setup.initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value=({"VALUE": "akv"}, "https://vault.vault.azure.net"),
                ),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=ref,
                    load_defaults=False,
                    silent=True,
                )

                assert os.environ["VALUE"] == "local"

        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_resolves_only_winning_references_after_local_override(self, mock_set_memory):
        ref = "https://vault.vault.azure.net/secrets/bootstrap"
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value="bootstrap-secret-value"),
                types.SimpleNamespace(value="local-secret-value"),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = pathlib.Path(temp_dir) / ".env.local"
            local_file.write_text(
                "OVERRIDDEN=local\n"
                "LOCAL_SECRET=kv:https://vault.vault.azure.net/secrets/local-secret\n"
                "LOCAL_ENV=env:BOOTSTRAP_SOURCE"
            )
            bootstrap_environment = {
                "OVERRIDDEN": "kv:https://vault.vault.azure.net/secrets/unused-secret",
                "BOOTSTRAP_SECRET": "kv:https://vault.vault.azure.net/secrets/bootstrap-secret",
                "BOOTSTRAP_SOURCE": "bootstrap-value",
            }

            with (
                mock.patch.dict(os.environ, {"SOURCE_VALUE": "ambient-value"}, clear=True),
                mock.patch("pyrit.setup.initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value=(bootstrap_environment, "https://vault.vault.azure.net"),
                ),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=ref,
                    env_files=[local_file],
                    load_defaults=False,
                )

                assert os.environ["OVERRIDDEN"] == "local"
                assert os.environ["BOOTSTRAP_SECRET"] == "bootstrap-secret-value"
                assert os.environ["LOCAL_SECRET"] == "local-secret-value"
                assert os.environ["LOCAL_ENV"] == "bootstrap-value"

        assert client.get_secret.await_args_list == [
            mock.call("bootstrap-secret", version=None),
            mock.call("local-secret", version=None),
        ]
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_without_environment_file_uses_system_environment(self, mock_set_memory):
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[], load_defaults=False)

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

    @mock.patch("pyrit.setup.initialization._load_environment_files", return_value=True)
    async def test_initialize_silent_produces_no_output(self, mock_load_env, capsys):
        """initialize_pyrit_async with silent=True must not print anything to stdout."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True, load_defaults=False)

        captured = capsys.readouterr()
        assert captured.out == ""

    @mock.patch("pyrit.setup.initialization._load_environment_files", return_value=True)
    async def test_initialize_not_silent_prints_migration_message(self, mock_load_env, capsys):
        """Without silent, the Alembic schema-check message is printed and tagged as Alembic output."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=False, load_defaults=False)

        captured = capsys.readouterr()
        assert "[pyrit:alembic] No new upgrade operations detected." in captured.out


class TestLoadEnvironmentFiles:
    """Tests for _load_environment_files function and env_files parameter in initialize_pyrit_async."""

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_loads_default_env_files_when_none_provided(self, mock_config_path):
        """Test that default .env and .env.local files are loaded when env_files is None."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR1=value1")
            env_local_file.write_text("VAR2=value2")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"
                assert os.environ["VAR2"] == "value2"

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_only_loads_existing_default_files(self, mock_config_path):
        """Test that only existing default files are loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VAR1=value1")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_excludes_default_env_when_loading_local_override(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR=base")
            env_local_file.write_text("VAR=local")

            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None, include_default_base=False)

                assert loaded is True
                assert os.environ["VAR"] == "local"

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_returns_false_when_no_default_files_exist(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None)

                assert loaded is False
                assert os.environ == {}

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_warns_when_default_files_coexist_with_akv(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR=base")
            env_local_file.write_text("VAR=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with caplog.at_level("WARNING", logger="pyrit.setup.initialization"):
                _warn_about_akv_environment_files(env_files=None)

        output = capsys.readouterr().out
        assert output.startswith("WARNING: env_akv_ref is configured")
        assert f"{env_file} will load after Key Vault and override matching values" in output
        assert f"{env_local_file} will load after Key Vault and override matching values" in output
        assert "clear or remove ~/.pyrit/.env and ~/.pyrit/.env.local" in output
        assert "remove explicit env_files when Key Vault should be the only source" in output
        assert "restart PyRIT" in output
        assert caplog.records[0].levelname == "WARNING"

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_warns_when_explicit_files_replace_defaults_with_akv(self, mock_config_path, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            custom_file = temp_path / ".env.custom"
            env_file.write_text("VAR=base")
            env_local_file.write_text("VAR=local")
            custom_file.write_text("VAR=custom")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            _warn_about_akv_environment_files(env_files=[custom_file])

        output = capsys.readouterr().out
        assert f"{env_file} exists but will be ignored because env_files was explicitly configured" in output
        assert f"{env_local_file} exists but will be ignored because env_files was explicitly configured" in output
        assert f"explicit env_files will load after Key Vault and override matching values: {[custom_file]}" in output

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_akv_environment_file_warning_respects_silent(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=base")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with caplog.at_level("WARNING", logger="pyrit.setup.initialization"):
                _warn_about_akv_environment_files(env_files=None, silent=True)

        assert capsys.readouterr().out == ""
        assert "will load after Key Vault and override matching values" in caplog.text
        assert "restart PyRIT" in caplog.text

    async def test_loads_custom_env_files_in_order(self):
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

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=[env1, env2, env3])

                assert loaded is True
                assert os.environ["VAR"] == "local"

    async def test_load_environment_files_honors_python_dotenv_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("DISABLED_VALUE=not-loaded")

            with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "true"}, clear=True):
                loaded = _load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert "DISABLED_VALUE" not in os.environ

    async def test_direct_local_file_loader_keeps_pyrit_references_literal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text(
                "BASE_VALUE=base\nKV_REFERENCE=kv:api-key\nENV_REFERENCE=env:SOURCE_VALUE\nINTERPOLATED=${BASE_VALUE}"
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert os.environ["KV_REFERENCE"] == "kv:api-key"
                assert os.environ["ENV_REFERENCE"] == "env:SOURCE_VALUE"
                assert os.environ["INTERPOLATED"] == "base"

    @mock.patch("pyrit.setup.initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_default_local_file_can_interpolate_base_file_but_not_reverse(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text(
                "OPENAI_CHAT_ENDPOINT=https://example.openai.azure.com/openai/v1\nFROM_LATER_LOCAL=${LOCAL_ONLY}"
            )
            env_local_file.write_text("FOOBAR=${OPENAI_CHAT_ENDPOINT}\nLOCAL_ONLY=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            resolved, loaded = _resolve_environment_files(
                env_files=None,
                base_environment={},
                silent=True,
            )

            assert loaded is True
            assert resolved["FOOBAR"] == "https://example.openai.azure.com/openai/v1"
            assert resolved["FROM_LATER_LOCAL"] == ""
            assert resolved["LOCAL_ONLY"] == "local"

    async def test_env_akv_strict_does_not_validate_local_environment_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("GOOD=resolved\n=malformed\nOTHER=also-resolved")

            with mock.patch.dict(os.environ, {}, clear=True):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_files=[env_file],
                    env_akv_strict=True,
                    load_defaults=False,
                    silent=True,
                )

                assert os.environ["GOOD"] == "resolved"
                assert os.environ["OTHER"] == "also-resolved"

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_local_file_resolves_full_akv_reference_without_bootstrap(self, mock_set_memory):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="local-secret-value"))
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("API_KEY=kv:https://myvault.vault.azure.net/secrets/api-key")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_files=[env_file],
                    load_defaults=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "local-secret-value"

        _assert_mock_akv_client_created(
            mock_client_cls,
            vault_url="https://myvault.vault.azure.net",
            credential=credential,
        )
        client.get_secret.assert_awaited_once_with("api-key", version=None)
        mock_set_memory.assert_called_once()

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
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[env_file], load_defaults=False)

            mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_pyrit_raises_for_nonexistent_env_file(self, mock_set_memory):
        """Test that initialize_pyrit_async raises ValueError for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[nonexistent])

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_custom_env_files_override_default_behavior(self, mock_set_memory):
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

            with mock.patch.dict(os.environ, {}, clear=True):
                await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[custom_env], load_defaults=False)

                assert os.environ["CUSTOM"] == "value"
                assert "DEFAULT" not in os.environ
                assert "DEFAULT_LOCAL" not in os.environ


def _create_mock_akv_clients() -> tuple[mock.MagicMock, mock.MagicMock]:
    credential = mock.MagicMock()
    credential.__aenter__ = mock.AsyncMock(return_value=credential)
    credential.__aexit__ = mock.AsyncMock(return_value=None)
    client = mock.MagicMock()
    client.__aenter__ = mock.AsyncMock(return_value=client)
    client.__aexit__ = mock.AsyncMock(return_value=None)
    return credential, client


def _assert_mock_akv_client_created(
    mock_client_cls: mock.MagicMock,
    *,
    vault_url: str,
    credential: mock.MagicMock,
) -> None:
    mock_client_cls.assert_called_once()
    call_kwargs = mock_client_cls.call_args.kwargs
    assert call_kwargs["vault_url"] == vault_url
    assert call_kwargs["credential"] is credential
    retry_policy = call_kwargs["retry_policy"]
    assert retry_policy.total_retries == 3
    assert retry_policy.connect_retries == 3
    assert retry_policy.read_retries == 3
    assert retry_policy.status_retries == 3
    assert retry_policy.backoff_factor == 0.8


class TestAkvEnvironmentLoading:
    """Tests for AKV URL parsing and env loading helpers."""

    @pytest.mark.parametrize("prefix", ["kv", "akv", "azure_key_vault", "env_akv_ref"])
    def test_parse_environment_value_reference_accepts_akv_aliases(self, prefix):
        secret_url = "https://myvault.vault.azure.net/secrets/api-key"

        assert _parse_environment_value_reference(f"{prefix}:{secret_url}") == ("akv", secret_url)

    def test_parse_environment_value_reference_rejects_azure_app_service_syntax(self):
        value = "@Microsoft.KeyVault(SecretUri=https://myvault.vault.azure.net/secrets/api-key)"

        assert _parse_environment_value_reference(value) is None

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

    async def test_load_env_from_akv_async_returns_unresolved_bootstrap(self):
        credential, client = _create_mock_akv_clients()
        root_document = (
            "DIRECT=from-bootstrap\n"
            "FROM_ENV=env:SOURCE_VALUE\n"
            "FROM_KV=kv:https://myvault.vault.azure.net/secrets/api-key\n"
            "PINNED_KV=kv:https://myvault.vault.azure.net/secrets/api-key/version-2\n"
            "ESCAPED=literal:kv:not-a-secret"
        )
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=root_document))
        secret_url = "https://myvault.vault.azure.net/secrets/bootstrap/v1"

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential) as mock_credential_cls,
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
            mock.patch("pyrit.setup.initialization._print_msg") as mock_print_msg,
        ):
            parsed_environment, vault_url = await _load_env_from_akv_async(secret_url=secret_url, silent=True)

            assert parsed_environment == {
                "DIRECT": "from-bootstrap",
                "FROM_ENV": "env:SOURCE_VALUE",
                "FROM_KV": "kv:https://myvault.vault.azure.net/secrets/api-key",
                "PINNED_KV": "kv:https://myvault.vault.azure.net/secrets/api-key/version-2",
                "ESCAPED": "literal:kv:not-a-secret",
            }
            assert vault_url == "https://myvault.vault.azure.net"

        mock_credential_cls.assert_called_once_with()
        _assert_mock_akv_client_created(
            mock_client_cls,
            vault_url="https://myvault.vault.azure.net",
            credential=credential,
        )
        client.get_secret.assert_awaited_once_with("bootstrap", version="v1")
        credential.__aenter__.assert_awaited_once()
        credential.__aexit__.assert_awaited_once()
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()
        mock_print_msg.assert_called_once()

    async def test_resolve_environment_references_async_resolves_local_values(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value="local-secret-value"),
                types.SimpleNamespace(value="pinned-secret-value"),
            ]
        )
        values = {
            "DIRECT": "from-local",
            "FROM_ENV": "env:SOURCE_VALUE",
            "DECLARED": "merged-value",
            "FROM_DECLARED": "env:DECLARED",
            "SHADOWED": "merged-wins",
            "FROM_SHADOWED": "env:SHADOWED",
            "FROM_KV": "kv:https://myvault.vault.azure.net/secrets/api-key",
            "PINNED_KV": "akv:https://myvault.vault.azure.net/secrets/api-key/version-2",
            "ESCAPED": "literal:kv:not-a-secret",
        }

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
        ):
            resolved = await _resolve_environment_references_async(
                values=values,
                ambient_environment={"SOURCE_VALUE": "ambient-value", "SHADOWED": "ambient-loses"},
            )

        assert resolved == {
            "DIRECT": "from-local",
            "FROM_ENV": "ambient-value",
            "DECLARED": "merged-value",
            "FROM_DECLARED": "merged-value",
            "SHADOWED": "merged-wins",
            "FROM_SHADOWED": "merged-wins",
            "FROM_KV": "local-secret-value",
            "PINNED_KV": "pinned-secret-value",
            "ESCAPED": "kv:not-a-secret",
        }
        _assert_mock_akv_client_created(
            mock_client_cls,
            vault_url="https://myvault.vault.azure.net",
            credential=credential,
        )
        assert client.get_secret.await_args_list == [
            mock.call("api-key", version=None),
            mock.call("api-key", version="version-2"),
        ]

    async def test_resolve_environment_references_async_rejects_self_reference(self):
        with pytest.raises(ValueError, match="cannot reference itself"):
            await _resolve_environment_references_async(
                values={"MODEL": "env:MODEL"},
                ambient_environment={"MODEL": "ambient-model"},
            )

    async def test_resolve_environment_references_async_preserves_windows_case_insensitive_lookup(self):
        with mock.patch("pyrit.setup.initialization.os.name", "nt"):
            resolved = await _resolve_environment_references_async(
                values={"ALIAS": "env:Path"},
                ambient_environment={"PATH": "windows-path"},
            )

        assert resolved["ALIAS"] == "windows-path"

    async def test_resolve_environment_references_async_rejects_windows_case_variant_self_reference(self):
        with (
            mock.patch("pyrit.setup.initialization.os.name", "nt"),
            pytest.raises(ValueError, match="cannot reference itself"),
        ):
            await _resolve_environment_references_async(
                values={"MODEL": "env:model"},
                ambient_environment={},
            )

    async def test_resolve_environment_references_async_rejects_short_secret_name(self):
        with pytest.raises(ValueError, match="must use a full secret URL"):
            await _resolve_environment_references_async(
                values={"API_KEY": "kv:api-key"},
                ambient_environment={},
            )

    @pytest.mark.parametrize(
        "reference_url",
        [
            "https://other-vault.vault.azure.net/secrets/api-key",
            "https://other-vault.vault.azure.net/secrets/api-key/version-1",
        ],
    )
    async def test_resolve_environment_references_async_rejects_cross_vault_reference(self, reference_url):
        with pytest.raises(ValueError, match="Cross-vault AKV reference"):
            await _resolve_environment_references_async(
                values={"API_KEY": f"kv:{reference_url}"},
                ambient_environment={},
                bootstrap_vault_url="https://myvault.vault.azure.net",
            )

    async def test_load_env_from_akv_async_empty_secret_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=None))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="has no value"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/my-secret",
                silent=True,
            )

        credential.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    async def test_load_env_from_akv_async_without_entries_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="# comments only\n"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="contains no environment entries"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/my-secret",
                silent=True,
            )

        credential.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    @pytest.mark.parametrize(
        ("document", "error"),
        [
            ("GOOD=resolved\n=malformed\nOTHER=resolved", "malformed entries at lines: 2"),
            ("MISSING_VALUE\n", "variables without values: MISSING_VALUE"),
        ],
    )
    async def test_load_env_from_akv_async_rejects_non_assignments(self, document, error):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(ValueError, match=error),
            ):
                await _load_env_from_akv_async(
                    secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                    silent=True,
                )

            assert "GOOD" not in os.environ
            assert "OTHER" not in os.environ

    async def test_load_env_from_akv_async_wraps_malformed_bootstrap(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="=malformed"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(KeyVaultInitializationException, match="malformed entries") as exc_info,
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

        assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_resolve_environment_references_async_wraps_missing_secret(self):
        credential, client = _create_mock_akv_clients()
        missing_error = ResourceNotFoundError(message="Secret was not found")
        client.get_secret = mock.AsyncMock(side_effect=missing_error)

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(KeyVaultInitializationException, match="Failed to resolve Key Vault reference") as exc_info,
        ):
            await _resolve_environment_references_async(
                values={"API_KEY": "kv:https://myvault.vault.azure.net/secrets/missing"},
                ambient_environment={},
            )

        assert exc_info.value.__cause__ is missing_error

    async def test_load_env_from_akv_async_allows_empty_assignment(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="EMPTY="))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            resolved_environment, _ = await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

            assert resolved_environment["EMPTY"] == ""
            assert "EMPTY" not in os.environ

    async def test_resolve_environment_references_async_allows_empty_child_secret(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=""))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            resolved_environment = await _resolve_environment_references_async(
                values={"EMPTY": "kv:https://myvault.vault.azure.net/secrets/empty-secret"},
                ambient_environment={},
            )

        assert resolved_environment["EMPTY"] == ""
        client.get_secret.assert_awaited_once_with("empty-secret", version=None)

    async def test_load_env_from_akv_async_non_strict_warns_and_skips_invalid_entries(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        document = "GOOD=resolved\n=malformed\nMISSING_VALUE\nOTHER=also-resolved"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.initialization"),
        ):
            resolved_environment, _ = await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=False,
            )

            assert resolved_environment == {"GOOD": "resolved", "OTHER": "also-resolved"}
            assert "GOOD" not in os.environ

        output = capsys.readouterr().out
        assert "WARNING: AKV environment document contains invalid entries that will be skipped" in output
        assert "malformed entries at lines: 2" in output
        assert "variables without values: MISSING_VALUE" in output
        assert "GOOD" not in caplog.text
        assert "resolved" not in caplog.text

    async def test_load_env_from_akv_async_non_strict_silent_logs_warning(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="GOOD=resolved\nMISSING_VALUE"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.initialization"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=True,
            )

        assert capsys.readouterr().out == ""
        assert "variables without values: MISSING_VALUE" in caplog.text

    async def test_resolve_environment_references_async_failure_returns_no_partial_mapping(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=None))

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(ValueError, match="has no value"),
            ):
                await _resolve_environment_references_async(
                    values={
                        "GOOD": "resolved",
                        "BAD": "kv:https://myvault.vault.azure.net/secrets/missing-value",
                    },
                    ambient_environment={},
                )

            assert "GOOD" not in os.environ
