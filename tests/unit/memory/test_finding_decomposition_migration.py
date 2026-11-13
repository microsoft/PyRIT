# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
import uuid
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "pyrit.memory.alembic.versions.7e1c3a5b9d20_add_finding_decomposition"


def _legacy_findings(metadata: sa.MetaData) -> sa.Table:
    sa.Table("OperationEntries", metadata, sa.Column("id", sa.CHAR(36), primary_key=True))
    return sa.Table(
        "FindingEntries",
        metadata,
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("operation_id", sa.CHAR(36), sa.ForeignKey("OperationEntries.id"), nullable=False),
        sa.Column("title", sa.Unicode(), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.CheckConstraint("severity IN ('critical', 'low')", name="ck_findings_severity"),
        sa.Index("ix_FindingEntries_operation_id", "operation_id"),
    )


def test_finding_decomposition_migration_preserves_existing_findings() -> None:
    migration = importlib.import_module(MIGRATION)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    findings = _legacy_findings(metadata)
    metadata.create_all(engine)
    operation_id, first, second = (str(uuid.uuid4()) for _ in range(3))
    try:
        with engine.begin() as connection:
            connection.execute(sa.text('INSERT INTO "OperationEntries" (id) VALUES (:id)'), {"id": operation_id})
            connection.execute(
                findings.insert(),
                [
                    {"id": first, "operation_id": operation_id, "title": "Legacy", "severity": "critical"},
                    {"id": second, "operation_id": operation_id, "title": "Other", "severity": "low"},
                ],
            )
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
            inspector = sa.inspect(connection)
            columns = {column["name"]: column for column in inspector.get_columns(findings.name)}
            assert columns["parent_finding_id"]["nullable"]
            assert not columns["demonstrated"]["nullable"]
            assert columns["paths"]["nullable"]
            assert isinstance(columns["paths"]["type"], sa.JSON)
            assert {"ix_FindingEntries_operation_id", "ix_FindingEntries_parent_finding_id"} <= {
                index["name"] for index in inspector.get_indexes(findings.name)
            }
            foreign_keys = {key["name"]: key for key in inspector.get_foreign_keys(findings.name)}
            assert foreign_keys["fk_findings_parent"]["referred_table"] == "FindingEntries"
            assert foreign_keys["fk_findings_parent"]["constrained_columns"] == ["parent_finding_id"]
            assert [check["name"] for check in inspector.get_check_constraints(findings.name)] == [
                "ck_findings_severity"
            ]
            assert connection.execute(
                sa.text('SELECT title, parent_finding_id, demonstrated, paths FROM "FindingEntries" ORDER BY title')
            ).all() == [("Legacy", None, 1, None), ("Other", None, 1, None)]

            upgraded = sa.Table(findings.name, sa.MetaData(), autoload_with=connection)
            payload = [{"id": str(uuid.uuid4()), "name": None, "description": "", "steps": [second]}]
            connection.execute(upgraded.update().where(upgraded.c.id == second).values(parent_finding_id=first))
            connection.execute(upgraded.update().where(upgraded.c.id == first).values(paths=payload))
            assert connection.execute(sa.select(upgraded.c.paths).where(upgraded.c.id == first)).scalar_one() == payload

            with patch.object(migration, "op", operations):
                migration.downgrade()
            inspector = sa.inspect(connection)
            remaining = {column["name"] for column in inspector.get_columns(findings.name)}
            assert not {"parent_finding_id", "demonstrated", "paths"} & remaining
            assert [key["name"] for key in inspector.get_foreign_keys(findings.name)] == [None]
            assert connection.execute(sa.select(findings.c.title).order_by(findings.c.title)).scalars().all() == [
                "Legacy",
                "Other",
            ]
    finally:
        engine.dispose()


def test_finding_decomposition_migration_sql_server_adds_a_self_reference_without_rewriting_rows() -> None:
    migration = importlib.import_module(MIGRATION)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "ALTER TABLE [FindingEntries] ADD parent_finding_id UNIQUEIDENTIFIER NULL" in sql
    assert "ALTER TABLE [FindingEntries] ADD demonstrated BIT NOT NULL DEFAULT 1" in sql
    assert "ALTER TABLE [FindingEntries] ADD paths NVARCHAR(max) NULL" in sql
    assert (
        "ALTER TABLE [FindingEntries] ADD CONSTRAINT fk_findings_parent FOREIGN KEY(parent_finding_id) "
        "REFERENCES [FindingEntries] (id)"
    ) in sql
    assert "CREATE INDEX [ix_FindingEntries_parent_finding_id]" in sql
    assert "DROP INDEX [ix_FindingEntries_parent_finding_id]" in sql
    assert "ALTER TABLE [FindingEntries] DROP CONSTRAINT fk_findings_parent" in sql
    # SQL Server names the demonstrated default itself, so the downgrade looks it up before dropping the column.
    assert "from sys.default_constraints" in sql
    assert "UPDATE" not in sql
