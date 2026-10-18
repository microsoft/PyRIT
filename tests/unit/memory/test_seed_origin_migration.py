# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.memory_models import SeedEntry


def test_origin_migration_preserves_legacy_rows() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.a6c8e0f2b4d7_add_seed_origin")
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE "SeedPromptEntries" (id INTEGER PRIMARY KEY, value TEXT)'))
            connection.execute(sa.text("INSERT INTO \"SeedPromptEntries\" VALUES (1, 'legacy')"))
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
            row = connection.execute(sa.text('SELECT id, value, origin FROM "SeedPromptEntries"')).one()
            assert tuple(row) == (1, "legacy", "unknown")
            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            assert columns["origin"]["type"].length == 16
            assert not columns["origin"]["nullable"]
            assert sa.inspect(connection).get_indexes("SeedPromptEntries")[0]["column_names"] == ["origin"]
            connection.execute(sa.text("UPDATE \"SeedPromptEntries\" SET origin = 'generated'"))
            with patch.object(migration, "op", operations):
                migration.downgrade()
            assert connection.execute(sa.text('SELECT id, value FROM "SeedPromptEntries"')).one() == (1, "legacy")
            assert "origin" not in {
                column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")
            }
    finally:
        engine.dispose()


def test_origin_migration_sql_server() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.a6c8e0f2b4d7_add_seed_origin")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "origin VARCHAR(16) NOT NULL DEFAULT 'unknown'" in sql
    assert "CREATE INDEX [ix_SeedPromptEntries_origin]" in sql
    assert "sys.default_constraints" in sql
    assert "DROP COLUMN origin" in sql
    assert SeedEntry.__table__.c.origin.type.length == 16
