# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
import json
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from pyrit.memory.memory_models import FindingEntry
from pyrit.memory.migration import check_schema_migrations

PREVIOUS_REVISION = "c8d3e5f7a901"
REVISION = "d43b1bcf9c56"
CHECKS = ("ck_findings_severity", "ck_findings_severity_other", "ck_findings_harm_type_other")
OPERATION_ID = "00000000-0000-0000-0000-000000000001"
ATTACK_ID = "00000000-0000-0000-0000-0000000000aa"
A, B, C, D = (f"00000000-0000-0000-0000-00000000010{number}" for number in range(1, 5))
STAMP = "2026-10-09 12:00:00"


def _config_for(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    config.attributes["connection"] = connection
    config.attributes["version_table"] = "pyrit_memory_alembic_version"
    return config


def _migration():
    return importlib.import_module("pyrit.memory.alembic.versions.d43b1bcf9c56_store_finding_field_values")


def _columns(inspector: sa.Inspector, table: str) -> list[tuple[str, str, bool]]:
    return [(c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)]


def _insert_operation(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO OperationEntries (id, name, name_key, created_at, description) "
            "VALUES (:id, 'Contoso review', 'contoso review', :stamp, '')"
        ),
        {"id": OPERATION_ID, "stamp": STAMP},
    )


def _insert_finding(*, connection: sa.Connection, **values: str) -> None:
    row = {"operation_id": OPERATION_ID, "title": "Contoso finding", "created_at": STAMP, **values}
    connection.execute(
        sa.text(f"INSERT INTO FindingEntries ({', '.join(row)}) VALUES ({', '.join(f':{name}' for name in row)})"),
        row,
    )


def test_upgrade_moves_existing_findings_into_field_values() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            _insert_operation(connection)
            _insert_finding(connection=connection, id=A, severity="important", description="")
            _insert_finding(
                connection=connection,
                id=B,
                severity="other",
                severity_other="Sev 2.5",
                harm_type="Other",
                harm_type_other="Model theft",
                description="Steps to reproduce",
            )
            connection.execute(
                sa.text(
                    "INSERT INTO FindingEvidenceEntries (id, finding_id, attack_result_id, attached_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000201', :finding_id, :attack_id, :stamp)"
                ),
                {"finding_id": B, "attack_id": ATTACK_ID, "stamp": STAMP},
            )
            _insert_finding(connection=connection, id=C, severity="low", harm_type="Jailbreak", description="   ")
            _insert_finding(connection=connection, id=D, severity="critical", description='Quote " and \\ backslash')

            command.upgrade(config, REVISION)

            rows = {
                finding_id: (severity, json.loads(field_values))
                for finding_id, severity, field_values in connection.execute(
                    sa.text("SELECT id, severity, field_values FROM FindingEntries")
                )
            }
            assert rows == {
                A: ("important", {}),
                B: ("Sev 2.5", {"harm_type": "Model theft", "description": "Steps to reproduce"}),
                C: ("low", {"harm_type": "Jailbreak"}),
                D: ("critical", {"description": 'Quote " and \\ backslash'}),
            }
            kept = {
                finding_id: (operation_id, created_at)
                for finding_id, operation_id, created_at in connection.execute(
                    sa.text("SELECT id, operation_id, created_at FROM FindingEntries")
                )
            }
            assert kept == dict.fromkeys((A, B, C, D), (OPERATION_ID, STAMP))
            evidence_rows = [
                tuple(row)
                for row in connection.execute(
                    sa.text("SELECT finding_id, attack_result_id FROM FindingEvidenceEntries")
                )
            ]
            assert evidence_rows == [(B, ATTACK_ID)]
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_upgrade_moves_an_other_severity_stored_in_any_case() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            _insert_operation(connection)
            # SQL Server's default case-insensitive collation let the old checks accept 'Other'.
            connection.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
            _insert_finding(connection=connection, id=A, severity="Other", severity_other="Sev 2.5", description="")
            connection.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))

            command.upgrade(config, REVISION)

            assert connection.execute(sa.text("SELECT severity FROM FindingEntries")).scalar_one() == "Sev 2.5"
    finally:
        engine.dispose()


def test_upgrade_drops_the_finding_check_constraints() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            command.upgrade(_config_for(connection), REVISION)
            _insert_operation(connection)
            _insert_finding(connection=connection, id=A, severity="sev1", field_values='{"category": "x"}')
            assert connection.execute(sa.text("SELECT severity, field_values FROM FindingEntries")).one() == (
                "sev1",
                '{"category": "x"}',
            )
    finally:
        engine.dispose()


def test_downgrade_restores_the_old_columns_only_without_findings() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config_for(connection)
            command.upgrade(config, PREVIOUS_REVISION)
            columns = sorted(_columns(sa.inspect(connection), "FindingEntries"))
            command.upgrade(config, REVISION)
            command.downgrade(config, PREVIOUS_REVISION)
            assert sorted(_columns(sa.inspect(connection), "FindingEntries")) == columns
            with pytest.raises(IntegrityError, match="CHECK"):
                _insert_finding(connection=connection, id=A, severity="urgent", description="")

            command.upgrade(config, REVISION)
            _insert_operation(connection)
            _insert_finding(connection=connection, id=A, severity="low", field_values="{}")
            with pytest.raises(ValueError, match="findings"):
                command.downgrade(config, PREVIOUS_REVISION)
            assert connection.scalar(sa.text("SELECT id FROM FindingEntries")) == A
    finally:
        engine.dispose()


def test_downgrade_refuses_offline() -> None:
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": io.StringIO()})
    with Operations.context(context), pytest.raises(ValueError, match="online"):
        _migration().downgrade()


def test_upgrade_compiles_for_sql_server() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        _migration().upgrade()
    ddl = output.getvalue()
    assert _migration().down_revision == "c8d3e5f7a901"
    for name in CHECKS:
        assert f"DROP CONSTRAINT {name}" in ddl
    drops = [match.start() for match in re.finditer("DROP CONSTRAINT", ddl)]
    assert len(drops) == 3 and max(drops) < ddl.index("ALTER COLUMN severity")
    assert "COLLATE Latin1_General_100_BIN2" in ddl
    assert "WHERE LOWER(severity) = 'other'" in ddl
    assert "JSON_MODIFY" in ddl
    for column in ("description", "severity_other", "harm_type", "harm_type_other"):
        assert f"DROP COLUMN {column}" in ddl


def test_finding_severity_is_binary_collated_only_on_sql_server() -> None:
    table = FindingEntry.__table__
    assert "severity NVARCHAR(max) COLLATE Latin1_General_100_BIN2 NOT NULL" in str(
        CreateTable(table).compile(dialect=mssql.dialect())
    )
    assert "COLLATE" not in str(CreateTable(table).compile(dialect=sqlite.dialect()))
