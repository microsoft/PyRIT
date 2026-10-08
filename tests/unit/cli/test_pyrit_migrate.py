# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pyrit.memory
import pyrit.memory.migration
import pyrit.setup.configuration_loader
import pyrit.setup.environment_loading
from pyrit.cli import pyrit_migrate

SERVER = "copyrit.database.windows.net"
DATABASE = "airtdev"
CONNECTION_STRING = f"mssql+pyodbc://@{SERVER}/{DATABASE}?driver=ODBC+Driver+18+for+SQL+Server"
ARGS = ["--config-file", "runtime.yaml", "--expected-server", SERVER, "--expected-database", DATABASE]


@pytest.fixture
def mocks() -> Iterator[dict[str, MagicMock]]:
    config = MagicMock(memory_db_type="azure_sql", env_akv_strict=True)
    memory = MagicMock(engine="engine", dispose_engine_async=AsyncMock())
    with ExitStack() as stack:
        loader = stack.enter_context(patch.object(pyrit.setup.configuration_loader, "ConfigurationLoader"))
        loader.load_with_overrides.return_value = config
        stack.enter_context(patch.dict("os.environ", {"AZURE_SQL_DB_CONNECTION_STRING": CONNECTION_STRING}))
        memory_class = stack.enter_context(patch.object(pyrit.memory, "AzureSQLMemory", return_value=memory))
        memory_class.AZURE_SQL_DB_CONNECTION_STRING = "AZURE_SQL_DB_CONNECTION_STRING"
        yield {
            "loader": loader,
            "config": config,
            "memory": memory,
            "load_env": stack.enter_context(
                patch.object(pyrit.setup.environment_loading, "load_environment_async", new_callable=AsyncMock)
            ),
            "memory_class": memory_class,
            "run": stack.enter_context(patch.object(pyrit.memory.migration, "run_schema_migrations")),
            "check": stack.enter_context(patch.object(pyrit.memory.migration, "check_schema_migrations")),
        }


def test_main_upgrades_and_checks_schema(mocks: dict[str, MagicMock]) -> None:
    assert pyrit_migrate.main(args=ARGS) == 0

    mocks["load_env"].assert_awaited_once()
    mocks["memory_class"].assert_called_once_with(
        connection_string=CONNECTION_STRING, _defer_initialization=True, skip_schema_migration=True, silent=True
    )
    mocks["run"].assert_called_once_with(engine="engine", silent=True)
    mocks["check"].assert_called_once_with(engine="engine", silent=True)
    mocks["memory"].dispose_engine_async.assert_awaited_once()


def test_main_rejects_non_azure_sql_memory(mocks: dict[str, MagicMock]) -> None:
    mocks["config"].memory_db_type = "sqlite"

    assert pyrit_migrate.main(args=ARGS) == 1
    mocks["memory_class"].assert_not_called()


def test_main_disposes_engine_when_migration_fails(mocks: dict[str, MagicMock]) -> None:
    mocks["run"].side_effect = RuntimeError("migration failed")

    assert pyrit_migrate.main(args=ARGS) == 1
    mocks["check"].assert_not_called()
    mocks["memory"].dispose_engine_async.assert_awaited_once()


@pytest.mark.parametrize(
    "connection_string",
    [
        CONNECTION_STRING.replace(SERVER, "other.database.windows.net"),
        CONNECTION_STRING.replace(DATABASE, "airtprod"),
        "mssql+pyodbc:///?odbc_connect=encoded",
        CONNECTION_STRING + "&SERVER=other.database.windows.net",
        CONNECTION_STRING + "&host=other.database.windows.net",
        CONNECTION_STRING + "&database=airtprod",
        "sqlite:///memory.db",
    ],
)
def test_main_rejects_wrong_target_before_creating_memory(
    *, mocks: dict[str, MagicMock], connection_string: str
) -> None:
    with patch.dict("os.environ", {"AZURE_SQL_DB_CONNECTION_STRING": connection_string}):
        assert pyrit_migrate.main(args=ARGS) == 1
    mocks["memory_class"].assert_not_called()
    mocks["run"].assert_not_called()


def test_validate_database_target_accepts_server_case_and_explicit_port() -> None:
    pyrit_migrate._validate_database_target(
        connection_string=CONNECTION_STRING.replace(SERVER, SERVER.upper() + ":1433"),
        expected_server=SERVER,
        expected_database=DATABASE,
    )


def test_main_resolves_blob_configuration_and_removes_local_copy(mocks: dict[str, MagicMock]) -> None:
    from pyrit.backend.services.configuration_file_service import ConfigurationFileService

    source = "https://copyrit.blob.core.windows.net/config/runtime.yaml"
    args = ["--config-file", source, *ARGS[2:]]
    with patch.object(ConfigurationFileService, "read_async", new_callable=AsyncMock) as read:
        read.return_value = "memory_db_type: AzureSQL\n"
        assert pyrit_migrate.main(args=args) == 0
        read.assert_awaited_once()
    file = mocks["loader"].load_with_overrides.call_args.kwargs["config_file"]
    assert isinstance(file, Path)
    assert not file.exists()


def test_main_loads_real_configuration_without_running_initializers(tmp_path: Path) -> None:
    from pyrit.memory import AzureSQLMemory
    from pyrit.setup import configuration_loader

    file = tmp_path / "runtime.yaml"
    file.write_text("memory_db_type: AzureSQL\nenv_files: []\nenv_akv_ref: []\n", encoding="utf-8")
    memory = MagicMock(spec=AzureSQLMemory)
    memory.engine = "engine"
    memory.dispose_engine_async = AsyncMock()
    args = ["--config-file", str(file), *ARGS[2:]]
    with (
        patch.object(configuration_loader, "DEFAULT_CONFIG_PATH", tmp_path / "absent-default.yaml"),
        patch.dict("os.environ", {"AZURE_SQL_DB_CONNECTION_STRING": CONNECTION_STRING}),
        patch.object(pyrit.setup.environment_loading, "load_environment_async", new_callable=AsyncMock) as load,
        patch.object(pyrit.memory, "AzureSQLMemory", return_value=memory) as memory_class,
        patch.object(pyrit.memory.migration, "run_schema_migrations") as run,
        patch.object(pyrit.memory.migration, "check_schema_migrations") as check,
        patch.object(
            configuration_loader.ConfigurationLoader, "initialize_pyrit_async", new_callable=AsyncMock
        ) as init,
    ):
        memory_class.AZURE_SQL_DB_CONNECTION_STRING = "AZURE_SQL_DB_CONNECTION_STRING"
        assert pyrit_migrate.main(args=args) == 0
        assert load.call_args.kwargs["env_files"] == []
        assert load.call_args.kwargs["env_akv_ref"] == []
        run.assert_called_once()
        check.assert_called_once()
        init.assert_not_called()
        memory.dispose_engine_async.assert_awaited_once()
