# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from pyrit.memory.memory_models import FindingEvidenceEntry
from pyrit.memory.migration import check_schema_migrations

PREVIOUS_REVISION = "901e6c7bf9d4"
REVISION = "c8d3e5f7a901"
TABLES = {"OperationEntries", "FindingEntries", "FindingEvidenceEntries"}


def _config_for(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["version_table"] = "pyrit_memory_alembic_version"
    return config


def _migration():
    return importlib.import_module("pyrit.memory.alembic.versions.c8d3e5f7a901_add_operations_and_findings")


def _columns(inspector: sa.Inspector, table: str) -> list[tuple[str, str, bool]]:
    return [(c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)]


def test_migration_adds_tables_without_changing_existing_ones() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            inspector = sa.inspect(connection)
            existing = {name: _columns(inspector, name) for name in inspector.get_table_names()}
            command.upgrade(config, REVISION)
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names()) == set(existing) | TABLES
            assert {name: _columns(inspector, name) for name in existing} == existing
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_migration_downgrades_only_without_operations() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, REVISION)
            command.downgrade(config, PREVIOUS_REVISION)
            assert TABLES.isdisjoint(sa.inspect(connection).get_table_names())
            command.upgrade(config, REVISION)
            connection.execute(
                sa.text(
                    "INSERT INTO OperationEntries (id, name, name_key, created_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', 'Retain', 'retain', '2026-10-09 12:00:00')"
                )
            )
            with pytest.raises(ValueError, match="operations"):
                command.downgrade(config, PREVIOUS_REVISION)
            assert connection.scalar(sa.text("SELECT name FROM OperationEntries")) == "Retain"
    finally:
        engine.dispose()


def test_migration_refuses_offline_downgrade() -> None:
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": io.StringIO()})
    with Operations.context(context), pytest.raises(ValueError, match="online"):
        _migration().downgrade()


def test_migration_enforces_unique_evidence_source() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            command.upgrade(_config_for(connection), REVISION)
            values = {
                "finding_id": uuid4(),
                "conversation_id": "source",
                "attack_result_id": uuid4(),
                "attached_at": datetime.now(UTC),
            }
            connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **values))
            with pytest.raises(IntegrityError):
                connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **values))
            connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **{**values, "finding_id": uuid4()}))
    finally:
        engine.dispose()


def test_migration_compiles_bounded_keys_for_sql_server() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = _migration()
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert migration.down_revision == PREVIOUS_REVISION
    assert "NVARCHAR(128)" in ddl
    assert "NVARCHAR(384)" in ddl
    assert "VARCHAR(128)" in ddl
    assert "UNIQUE (finding_id, conversation_id)" in ddl
    assert "CREATE UNIQUE INDEX [ix_OperationEntries_name_key]" in ddl
    assert ddl.count("FOREIGN KEY") == 2
    assert "REFERENCES [OperationEntries]" in ddl
    assert "REFERENCES [FindingEntries]" in ddl
