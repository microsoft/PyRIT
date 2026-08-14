# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import tempfile
from unittest import mock

import pytest

from pyrit.common.apply_defaults import reset_default_values
from pyrit.common.singleton import Singleton
from pyrit.registry import InitializerRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async


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
    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=False)
    async def test_initialize_basic(self, mock_load_env, mock_set_memory):
        """Test basic initialization."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)

        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=False)
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

    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=False)
    async def test_invalid_memory_type_raises_error(self, mock_load_env):
        """Test that invalid memory type raises ValueError."""
        with pytest.raises(ValueError, match="is not a supported type"):
            await initialize_pyrit_async(memory_db_type="InvalidType", load_defaults=False)  # type: ignore[arg-type]

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=False)
    @mock.patch("pyrit.setup.akv_initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_env_akv_ref(self, mock_load_akv, mock_load_env, mock_set_memory):
        """Test that env_akv_ref loads bootstrap secrets in order."""
        refs = [
            "https://vault.vault.azure.net/secrets/first",
            "https://vault.vault.azure.net/secrets/second/version",
        ]

        mock_load_akv.return_value = None

        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=refs, load_defaults=False)

        assert mock_load_akv.await_args_list == [
            mock.call(secret_url=refs[0], strict=True, silent=False),
            mock.call(secret_url=refs[1], strict=True, silent=False),
        ]
        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=False)
    @mock.patch("pyrit.setup.akv_initialization._load_env_from_akv_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_empty_env_akv_ref_does_not_load_akv(
        self, mock_load_akv, mock_load_env, mock_set_memory
    ):
        """Test that an empty env_akv_ref list skips AKV loading."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_akv_ref=[], load_defaults=False)

        mock_load_akv.assert_not_called()
        mock_load_env.assert_called_once()
        mock_set_memory.assert_called_once()

    @pytest.mark.parametrize("env_akv_ref", ["https://vault.vault.azure.net/secrets/one", [""], [None]])
    async def test_initialize_rejects_invalid_env_akv_ref(self, env_akv_ref):
        with pytest.raises(ValueError, match="env_akv_ref must"):
            await initialize_pyrit_async(
                memory_db_type=IN_MEMORY,
                env_akv_ref=env_akv_ref,  # type: ignore[arg-type]
                load_defaults=False,
            )

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_keeps_akv_values_when_local_file_loading_fails(self, mock_set_memory):
        refs = ["https://vault.vault.azure.net/secrets/bootstrap"]
        nonexistent = pathlib.Path("/nonexistent/.env")

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("pyrit.setup.akv_initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    side_effect=lambda **_: os.environ.update({"FROM_AKV": "resolved"}),
                ),
                pytest.raises(ValueError, match="Environment file not found"),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=refs,
                    env_files=[nonexistent],
                    load_defaults=False,
                )

            assert os.environ["FROM_AKV"] == "resolved"

        mock_set_memory.assert_not_called()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_loads_local_overrides_on_akv_environment(self, mock_set_memory):
        refs = ["https://vault.vault.azure.net/secrets/bootstrap"]
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = pathlib.Path(temp_dir) / ".env.local"
            local_file.write_text("DERIVED=${BASE}\nBASE=local")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("pyrit.setup.akv_initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    side_effect=lambda **_: os.environ.update({"BASE": "akv", "ONLY_AKV": "shared"}),
                ),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=refs,
                    env_files=[local_file],
                    load_defaults=False,
                )

                assert os.environ["BASE"] == "local"
                assert os.environ["DERIVED"] == "akv"
                assert os.environ["ONLY_AKV"] == "shared"

        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_default_files_override_akv_in_order(self, mock_set_memory):
        refs = ["https://vault.vault.azure.net/secrets/bootstrap"]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VALUE=env")
            (temp_path / ".env.local").write_text("VALUE=local")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch("pyrit.setup.akv_initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    side_effect=lambda **_: os.environ.update({"VALUE": "akv"}),
                ),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=refs,
                    load_defaults=False,
                    silent=True,
                )

                assert os.environ["VALUE"] == "local"

        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_resolves_bootstrap_references_before_local_overrides(self, mock_set_memory):
        refs = ["https://vault.vault.azure.net/secrets/bootstrap"]
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = pathlib.Path(temp_dir) / ".env.local"
            local_file.write_text(
                "OVERRIDDEN=local\n"
                "LOCAL_SECRET=kv:https://vault.vault.azure.net/secrets/local-secret\n"
                "LOCAL_ENV=env:BOOTSTRAP_SOURCE"
            )
            bootstrap_environment = {
                "OVERRIDDEN": "unused-secret-value",
                "BOOTSTRAP_SECRET": "bootstrap-secret-value",
                "BOOTSTRAP_SOURCE": "bootstrap-value",
            }

            with (
                mock.patch.dict(os.environ, {"SOURCE_VALUE": "ambient-value"}, clear=True),
                mock.patch("pyrit.setup.akv_initialization._warn_about_akv_environment_files"),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    side_effect=lambda **_: os.environ.update(bootstrap_environment),
                ),
            ):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_ref=refs,
                    env_files=[local_file],
                    load_defaults=False,
                )

                assert os.environ["OVERRIDDEN"] == "local"
                assert os.environ["BOOTSTRAP_SECRET"] == "bootstrap-secret-value"
                assert os.environ["LOCAL_SECRET"] == "kv:https://vault.vault.azure.net/secrets/local-secret"
                assert os.environ["LOCAL_ENV"] == "env:BOOTSTRAP_SOURCE"

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

    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=True)
    async def test_initialize_silent_produces_no_output(self, mock_load_env, capsys):
        """initialize_pyrit_async with silent=True must not print anything to stdout."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True, load_defaults=False)

        captured = capsys.readouterr()
        assert captured.out == ""

    @mock.patch("pyrit.setup.akv_initialization._load_environment_files", return_value=True)
    async def test_initialize_not_silent_prints_migration_message(self, mock_load_env, capsys):
        """Without silent, the Alembic schema-check message is printed and tagged as Alembic output."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=False, load_defaults=False)

        captured = capsys.readouterr()
        assert "[pyrit:alembic] No new upgrade operations detected." in captured.out
