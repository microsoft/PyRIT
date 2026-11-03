# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.memory_models import SeedEntry


def test_seed_is_general_technique_migration_preserves_legacy_rows() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.e7a1b3c5d9f0_add_seed_is_general_technique")
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE "SeedPromptEntries" (id INTEGER PRIMARY KEY, value TEXT)'))
            connection.execute(sa.text("INSERT INTO \"SeedPromptEntries\" VALUES (1, 'legacy')"))
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
            row = connection.execute(sa.text('SELECT id, value, is_general_technique FROM "SeedPromptEntries"')).one()
            assert tuple(row) == (1, "legacy", 0)

            connection.execute(
                sa.text("INSERT INTO \"SeedPromptEntries\" (id, value, is_general_technique) VALUES (2, 'general', 1)")
            )
            row2 = connection.execute(
                sa.text('SELECT id, value, is_general_technique FROM "SeedPromptEntries" WHERE id = 2')
            ).one()
            assert tuple(row2) == (2, "general", 1)

            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            assert "is_general_technique" in columns
            assert not columns["is_general_technique"]["nullable"]

            with patch.object(migration, "op", operations):
                migration.downgrade()
            assert connection.execute(sa.text('SELECT id, value FROM "SeedPromptEntries"')).fetchall() == [
                (1, "legacy"),
                (2, "general"),
            ]
            assert "is_general_technique" not in {
                column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")
            }
    finally:
        engine.dispose()


def test_seed_is_general_technique_migration_uses_sql_server_bit() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.e7a1b3c5d9f0_add_seed_is_general_technique")
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "ADD is_general_technique BIT NOT NULL DEFAULT 0" in sql
    assert "DROP COLUMN is_general_technique" in sql
    assert SeedEntry.__table__.c.is_general_technique.type.python_type is bool
