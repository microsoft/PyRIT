# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Portable schema and lock-contract tests for dataset ownership and cleanup."""

import importlib
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from pyrit.memory.dataset_lifecycle import lock_dataset_lifecycle
from pyrit.memory.memory_models import DatasetLifecycleEntry, DatasetLifecycleLock, DatasetMediaEntry
from pyrit.memory.migration import check_schema_migrations

REVISION = "f8a2c6e0b419"
PREVIOUS = "e7c4a2d9f013"


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes.update(connection=connection, version_table="pyrit_memory_alembic_version")
    return config


def test_dataset_lifecycle_migration_and_guarded_downgrade() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            before = set(sa.inspect(connection).get_table_names())
            command.upgrade(config, REVISION)
            assert set(sa.inspect(connection).get_table_names()) == before | {
                "DatasetLifecycleLock",
                "DatasetLifecycleEntries",
                "DatasetMediaEntries",
            }
            assert connection.scalar(sa.select(DatasetLifecycleLock.id)) == 1
            command.downgrade(config, PREVIOUS)
            assert set(sa.inspect(connection).get_table_names()) == before
            command.upgrade(config, REVISION)
        check_schema_migrations(engine=engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO DatasetLifecycleEntries (id, dataset_name, kind, status, deleted_seeds) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', 'retained', 'delete', 'pending', 1)"
                )
            )
            with pytest.raises(RuntimeError, match="refusing to discard"):
                command.downgrade(_config(connection), PREVIOUS)
    finally:
        engine.dispose()


def test_dataset_lifecycle_sql_server_migration_has_bounded_keys() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = importlib.import_module(f"pyrit.memory.alembic.versions.{REVISION}_add_dataset_lifecycle")
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert "dataset_name NVARCHAR(256)" in ddl
    assert "[key] VARCHAR(64)" in ddl
    assert ddl.count("FOREIGN KEY") == 2
    assert "INSERT INTO [DatasetLifecycleLock]" in ddl
    for model in (DatasetLifecycleLock, DatasetLifecycleEntry, DatasetMediaEntry):
        assert str(CreateTable(model.__table__).compile(dialect=mssql.dialect()))
        for column in model.__table__.columns:
            if (column.primary_key or column.foreign_keys or column.index) and isinstance(
                column.type, (sa.String, sa.Unicode)
            ):
                assert column.type.length is not None


def test_sql_server_lifecycle_lock_is_exclusive_until_commit() -> None:
    session = MagicMock(spec=Session)
    session.info = {}
    session.get_bind.return_value.dialect.name = "mssql"
    session.scalar.return_value = 1
    lock_dataset_lifecycle(session)
    statement = session.scalar.call_args.args[0]
    assert "WITH (XLOCK, HOLDLOCK)" in str(statement.compile(dialect=mssql.dialect()))
    lock_dataset_lifecycle(session)
    assert session.scalar.call_count == 1
