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
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from pyrit.memory.memory_models import (
    AttackResultEntry,
    FindingEvidenceEntry,
    OperationEntry,
    ScenarioResultEntry,
)
from pyrit.memory.migration import check_schema_migrations

PREVIOUS_REVISION = "3f6a9d2c8b1e"
REVISION = "c8d3e5f7a901"
TABLES = {"OperationEntries", "FindingEntries", "FindingEvidenceEntries"}
ACTIVITY_TABLES = ("AttackResultEntries", "ScenarioResultEntries")
# FindingEntry models the head revision, which no longer has this revision's finding columns.
FINDINGS = sa.table(
    "FindingEntries",
    *(
        sa.column(name)
        for name in (
            "id",
            "operation_id",
            "title",
            "description",
            "severity",
            "severity_other",
            "harm_type",
            "harm_type_other",
            "created_at",
        )
    ),
)


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
            after = {name: _columns(inspector, name) for name in existing}
            for table in ACTIVITY_TABLES:
                assert after[table] == [*existing[table], ("operation_id", "CHAR(36)", True)]
            assert {name: columns for name, columns in after.items() if name not in ACTIVITY_TABLES} == {
                name: columns for name, columns in existing.items() if name not in ACTIVITY_TABLES
            }
            # check_schema_migrations compares the database with the head models.
            command.upgrade(config, "head")
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
                    "INSERT INTO OperationEntries (id, name, name_key, created_at, description) "
                    "VALUES ('00000000-0000-0000-0000-000000000001', 'Retain', 'retain', '2026-10-09 12:00:00', '')"
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
            values = {"finding_id": uuid4(), "attack_result_id": uuid4(), "attached_at": datetime.now(UTC)}
            connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **values))
            with pytest.raises(IntegrityError):
                connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **values))
            connection.execute(sa.insert(FindingEvidenceEntry).values(id=uuid4(), **{**values, "finding_id": uuid4()}))
            connection.execute(
                sa.insert(FindingEvidenceEntry).values(id=uuid4(), **{**values, "attack_result_id": uuid4()})
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "invalid",
    [
        {"severity": "urgent"},
        {"severity": "other"},
        {"severity": "other", "severity_other": "  "},
        {"severity_other": "Custom"},
        {"harm_type": "Other"},
        {"harm_type": "Malware", "harm_type_other": "Custom"},
        {"harm_type_other": "Custom"},
    ],
)
def test_migration_rejects_inconsistent_finding_classifications(invalid: dict[str, str]) -> None:
    valid = {"title": "Finding", "description": "", "severity": "low", "created_at": "2026-10-09 12:00:00"}
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            command.upgrade(_config_for(connection), REVISION)
            insert = sa.insert(FINDINGS)
            connection.execute(insert.values(id=str(uuid4()), operation_id=str(uuid4()), **valid))
            # Harm types are unconstrained so HarmCategory can change without a migration.
            connection.execute(
                insert.values(id=str(uuid4()), operation_id=str(uuid4()), **valid, harm_type="Not a current category")
            )
            connection.execute(
                insert.values(
                    id=str(uuid4()),
                    operation_id=str(uuid4()),
                    **{**valid, "severity": "other", "severity_other": "Custom"},
                    harm_type="Other",
                    harm_type_other="Custom",
                )
            )
            with pytest.raises(IntegrityError, match="CHECK"):
                connection.execute(insert.values(id=str(uuid4()), operation_id=str(uuid4()), **{**valid, **invalid}))
    finally:
        engine.dispose()


def test_operation_name_key_is_binary_collated_only_on_sql_server() -> None:
    table = OperationEntry.__table__
    assert "name_key NVARCHAR(384) COLLATE Latin1_General_100_BIN2" in str(
        CreateTable(table).compile(dialect=mssql.dialect())
    )
    assert "COLLATE" not in str(CreateTable(table).compile(dialect=sqlite.dialect()))


def test_migration_compiles_bounded_keys_for_sql_server() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = _migration()
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert migration.down_revision == PREVIOUS_REVISION
    assert "NVARCHAR(128)" in ddl
    assert "name_key NVARCHAR(384) COLLATE Latin1_General_100_BIN2 NOT NULL" in ddl
    assert "UNIQUE (finding_id, attack_result_id)" in ddl
    for constraint in ("ck_findings_severity", "ck_findings_severity_other", "ck_findings_harm_type_other"):
        assert f"CONSTRAINT {constraint} CHECK" in ddl
    assert "CREATE UNIQUE INDEX [ix_OperationEntries_name_key]" in ddl
    assert ddl.count("FOREIGN KEY") == 4
    assert ddl.count("REFERENCES [OperationEntries]") == 3
    assert "REFERENCES [FindingEntries]" in ddl
    # Offline SQL cannot read existing names, so the legacy backfill is skipped.
    assert "INSERT INTO" not in ddl
    for model in (AttackResultEntry, ScenarioResultEntry):
        assert any(index.name == f"ix_{model.__tablename__}_operation_id" for index in model.__table__.indexes)
