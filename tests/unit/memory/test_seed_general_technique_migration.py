# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import io
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.memory_models import SeedEntry
from pyrit.memory.migration import check_schema_migrations, run_schema_migrations


def test_general_technique_migration_preserves_existing_rows() -> None:
    migration = importlib.import_module(
        "pyrit.memory.alembic.versions.064a1eff19fc_add_is_general_technique_to_seed_entries"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    'CREATE TABLE "SeedPromptEntries" (id INTEGER PRIMARY KEY, value TEXT, seed_type VARCHAR NOT NULL)'
                )
            )
            connection.execute(
                sa.text(
                    'INSERT INTO "SeedPromptEntries" VALUES '
                    "(1, 'legacy prompt', 'prompt'), "
                    "(2, 'legacy objective', 'objective'), "
                    "(3, 'legacy conversation', 'simulated_conversation')"
                )
            )
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()

            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("SeedPromptEntries")}
            assert "is_general_technique" in columns
            assert not columns["is_general_technique"]["nullable"]

            rows = connection.execute(
                sa.text('SELECT id, is_general_technique FROM "SeedPromptEntries" ORDER BY id')
            ).all()
            assert rows[0] == (1, 0)
            assert rows[1] == (2, 0)
            assert rows[2] == (3, 1)

            with patch.object(migration, "op", operations):
                migration.downgrade()

            assert "is_general_technique" not in {
                column["name"] for column in sa.inspect(connection).get_columns("SeedPromptEntries")
            }
            preserved = connection.execute(
                sa.text('SELECT id, value, seed_type FROM "SeedPromptEntries" ORDER BY id')
            ).all()
            assert len(preserved) == 3
            assert preserved[0] == (1, "legacy prompt", "prompt")
            assert preserved[2] == (3, "legacy conversation", "simulated_conversation")
    finally:
        engine.dispose()


def test_general_technique_migration_sql_server() -> None:
    migration = importlib.import_module(
        "pyrit.memory.alembic.versions.064a1eff19fc_add_is_general_technique_to_seed_entries"
    )
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert "is_general_technique BIT NOT NULL" in sql
    assert "DROP COLUMN is_general_technique" in sql
    assert SeedEntry.__table__.c.is_general_technique.type.python_type is bool


def test_schema_matches_models_after_migration() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        run_schema_migrations(engine=engine)
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()
