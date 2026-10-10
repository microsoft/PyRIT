# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import pathlib
import tempfile
from unittest import mock

import pytest

from pyrit.common.apply_defaults import reset_default_values
from pyrit.common.random_context import get_configured_random_seed
from pyrit.common.singleton import Singleton
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.models import MessagePiece
from pyrit.registry import InitializerRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.pyrit_initializer import PyRITInitializer


@pytest.mark.parametrize("existing_memory", [False, True])
async def test_initializer_failure_closes_only_newly_installed_memory(
    existing_memory: bool, sqlite_instance: SQLiteMemory
) -> None:
    previous = sqlite_instance if existing_memory else None
    instances = {SQLiteMemory: sqlite_instance} if existing_memory else {}
    initializer = mock.MagicMock(spec=PyRITInitializer)
    initializer.validate.side_effect = ValueError("invalid initializer")
    with (
        mock.patch.object(CentralMemory, "_memory_instance", previous),
        mock.patch.object(Singleton, "_instances", instances),
        mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock),
        mock.patch.object(SQLiteMemory, "dispose_engine_async", new_callable=mock.AsyncMock) as dispose,
    ):
        with pytest.raises(ValueError, match="invalid initializer"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[initializer])
        assert CentralMemory._memory_instance is previous
        memory = instances[SQLiteMemory]
    if existing_memory:
        dispose.assert_not_awaited()
    else:
        dispose.assert_awaited_once()
        await memory.dispose_engine_async()


@pytest.mark.parametrize("central_is_set", [False, True])
async def test_repeated_setup_preserves_rows_without_repeating_schema_initialization(
    sqlite_instance: SQLiteMemory, central_is_set: bool
) -> None:
    piece = MessagePiece(role="user", original_value="keep this row", conversation_id="repeated-setup")
    await sqlite_instance.add_message_to_memory_async(request=piece.to_message())
    with (
        mock.patch.object(CentralMemory, "_memory_instance", sqlite_instance if central_is_set else None),
        mock.patch.object(Singleton, "_instances", {SQLiteMemory: sqlite_instance}),
        mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock),
        mock.patch.object(
            sqlite_instance, "_run_schema_migration", side_effect=RuntimeError("schema check failed")
        ) as migrate,
        mock.patch.object(sqlite_instance, "dispose_engine_async", new_callable=mock.AsyncMock) as dispose,
    ):
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)
        migrate.assert_not_called()
        dispose.assert_not_awaited()
        assert CentralMemory.get_memory_instance() is sqlite_instance
        assert Singleton._instances[SQLiteMemory] is sqlite_instance
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id="repeated-setup")
        assert [message.get_piece().id for message in messages] == [piece.id]


@pytest.mark.usefixtures("reset_memory_singletons")
async def test_setup_retries_after_new_memory_is_disposed_on_initializer_failure() -> None:
    initializer = mock.MagicMock(spec=PyRITInitializer)
    initializer.validate.side_effect = ValueError("invalid initializer")
    with mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock):
        with pytest.raises(ValueError, match="invalid initializer"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[initializer])
        memory = Singleton._instances[SQLiteMemory]
        assert not memory._initialized
        assert CentralMemory._memory_instance is None
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)
        assert CentralMemory.get_memory_instance() is memory
        piece = MessagePiece(role="user", original_value="after retry", conversation_id="setup-retry")
        await memory.add_message_to_memory_async(request=piece.to_message())
        assert [row.id for row in await memory.get_message_pieces_async()] == [piece.id]


@pytest.mark.parametrize("cached_kind", ["missing", "different", "wrong_backend"])
async def test_setup_rejects_disagreeing_memory_singletons(sqlite_instance: SQLiteMemory, cached_kind: str) -> None:
    instances = {} if cached_kind == "missing" else {SQLiteMemory: object()}
    if cached_kind == "wrong_backend":
        instances = {SQLiteMemory: sqlite_instance}
    with (
        mock.patch.object(Singleton, "_instances", instances),
        mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock),
        mock.patch.object(sqlite_instance, "dispose_engine_async", new_callable=mock.AsyncMock) as dispose,
        pytest.raises(ValueError, match="singleton disagree"),
    ):
        await initialize_pyrit_async(
            memory_db_type="AzureSQL" if cached_kind == "wrong_backend" else IN_MEMORY, load_defaults=False
        )
    assert CentralMemory.get_memory_instance() is sqlite_instance
    dispose.assert_not_awaited()


async def test_setup_rejects_switching_sqlite_modes(sqlite_instance: SQLiteMemory) -> None:
    with (
        mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock),
        pytest.raises(ValueError, match="Cannot switch"),
    ):
        await initialize_pyrit_async(memory_db_type="SQLite", load_defaults=False)
    assert CentralMemory.get_memory_instance() is sqlite_instance


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


@pytest.mark.usefixtures("reset_memory_singletons")
class TestInitializePyrit:
    """Tests for initialize_pyrit_async function - basic orchestration tests."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_basic(self, mock_load_environment, mock_set_memory):
        """Test basic initialization."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)

        mock_load_environment.assert_awaited_once()
        mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_configures_root_seed(self, mock_load_environment, mock_set_memory):
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False, seed=42)

        assert get_configured_random_seed() == 42

        await initialize_pyrit_async(memory_db_type=IN_MEMORY, load_defaults=False)

        assert get_configured_random_seed() is None

    @pytest.mark.parametrize("invalid_seed", [True, 1.5, "42", []])
    async def test_initialize_rejects_invalid_seed(self, invalid_seed):
        with pytest.raises(TypeError, match="seed must be an int or None"):
            await initialize_pyrit_async(
                memory_db_type=IN_MEMORY,
                load_defaults=False,
                seed=invalid_seed,  # type: ignore[arg-type]
            )

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_with_script(self, mock_load_environment, mock_set_memory):
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
            mock_load_environment.assert_awaited_once()
            mock_set_memory.assert_called_once()
        finally:
            os.unlink(script_path)

    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_invalid_memory_type_raises_error(self, mock_load_environment):
        """Test that invalid memory type raises ValueError."""
        with pytest.raises(ValueError, match="is not a supported type"):
            await initialize_pyrit_async(memory_db_type="InvalidType", load_defaults=False)  # type: ignore[arg-type]

        mock_load_environment.assert_awaited_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_forwards_environment_options(self, mock_load_environment, mock_set_memory):
        refs = ["https://vault.vault.azure.net/secrets/bootstrap"]
        env_files = [pathlib.Path("custom.env")]

        await initialize_pyrit_async(
            memory_db_type=IN_MEMORY,
            env_akv_ref=refs,
            env_files=env_files,
            env_akv_strict=False,
            silent=True,
            load_defaults=False,
        )

        mock_load_environment.assert_awaited_once_with(
            env_akv_ref=refs,
            env_files=env_files,
            env_akv_strict=False,
            silent=True,
        )
        mock_set_memory.assert_called_once()

    @pytest.mark.parametrize("invalid_value", ["false", "true", 0, 1, None, [], {}])
    async def test_initialize_rejects_non_boolean_env_akv_strict_before_loading(self, invalid_value):
        with mock.patch(
            "pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock
        ) as mock_load_environment:
            with pytest.raises(TypeError, match=r"env_akv_strict must be a bool"):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_akv_strict=invalid_value,  # type: ignore[arg-type]
                    load_defaults=False,
                )

        mock_load_environment.assert_not_awaited()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initializer_failure_raises_by_default(self, mock_load_environment, mock_set_memory):
        failing = mock.MagicMock(spec=PyRITInitializer)
        failing.validate.side_effect = ValueError("invalid initializer")
        healthy = mock.MagicMock(spec=PyRITInitializer)
        healthy.initialize_with_tracking_async = mock.AsyncMock()

        with pytest.raises(ValueError, match="invalid initializer"):
            await initialize_pyrit_async(
                memory_db_type=IN_MEMORY,
                initializers=[failing, healthy],
            )

        healthy.validate.assert_not_called()
        healthy.initialize_with_tracking_async.assert_not_awaited()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initializer_failure_can_be_logged_and_skipped(self, mock_load_environment, mock_set_memory, caplog):
        failing = mock.MagicMock(spec=PyRITInitializer)
        failing.validate.side_effect = ValueError("invalid initializer")
        healthy = mock.MagicMock(spec=PyRITInitializer)
        healthy.initialize_with_tracking_async = mock.AsyncMock()

        with caplog.at_level(logging.ERROR, logger="pyrit.setup.initialization"):
            await initialize_pyrit_async(
                memory_db_type=IN_MEMORY,
                initializers=[failing, healthy],
                raise_on_initializer_error=False,
            )

        healthy.validate.assert_called_once_with()
        healthy.initialize_with_tracking_async.assert_awaited_once_with()
        assert "Error executing initializer" in caplog.text


@pytest.fixture
async def reset_memory_singletons():
    """Force memory __init__ (and schema migration) to run by clearing cached singletons."""
    with (
        mock.patch.object(Singleton, "_instances", {}),
        mock.patch.object(CentralMemory, "_memory_instance", None),
    ):
        try:
            yield
        finally:
            for memory in Singleton._instances.values():
                if isinstance(memory, SQLiteMemory):
                    await memory.dispose_engine_async()


@pytest.mark.usefixtures("reset_memory_singletons")
class TestInitializePyritSilent:
    """Tests that initialization keeps the console free of schema-migration noise."""

    def setup_method(self) -> None:
        """Clear default values before each test."""
        reset_default_values()

    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_silent_produces_no_output(self, mock_load_environment, capsys):
        """initialize_pyrit_async with silent=True must not print anything to stdout."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True, load_defaults=False)

        captured = capsys.readouterr()
        assert captured.out == ""

    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_not_silent_produces_no_migration_output(self, mock_load_environment, capsys):
        """An in-memory database is built from nothing, so initialization reports no migration work."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=False, load_defaults=False)

        captured = capsys.readouterr()
        assert "[pyrit:alembic]" not in captured.out

    @pytest.mark.parametrize("silent", [True, False])
    @mock.patch("pyrit.setup.initialization.load_environment_async", new_callable=mock.AsyncMock)
    async def test_initialize_forwards_silent_to_memory(self, mock_load_environment, silent):
        """An in-memory database is quiet either way, so stdout alone cannot prove silent is wired."""
        await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=silent, load_defaults=False)

        assert CentralMemory.get_memory_instance()._silent is silent
