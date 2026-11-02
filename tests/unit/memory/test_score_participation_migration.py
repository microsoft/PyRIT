# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "pyrit.memory.alembic.versions.e7c4a2d9f013_persist_private_score_participation"


def test_private_score_participation_migration_adds_nullable_column_and_downgrades() -> None:
    migration = importlib.import_module(MIGRATION)
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE ScoreEntries (id INTEGER PRIMARY KEY, score_metadata JSON)"))
            connection.execute(sa.text("INSERT INTO ScoreEntries (id, score_metadata) VALUES (1, '{}')"))
            operations = Operations(MigrationContext.configure(connection))

            with patch.object(migration, "op", operations):
                migration.upgrade()

            columns = {column["name"]: column for column in sa.inspect(connection).get_columns("ScoreEntries")}
            assert "snapshot_participation" in columns
            assert columns["snapshot_participation"]["nullable"] is True
            assert (
                connection.execute(
                    sa.text('SELECT snapshot_participation FROM "ScoreEntries" WHERE id = 1')
                ).scalar_one()
                is None
            )

            connection.execute(
                sa.text('UPDATE "ScoreEntries" SET snapshot_participation = :participation WHERE id = 1'),
                {"participation": '{"0": null}'},
            )
            with patch.object(migration, "op", operations):
                migration.downgrade()

            columns = [column["name"] for column in sa.inspect(connection).get_columns("ScoreEntries")]
            assert columns == ["id", "score_metadata"]
            assert connection.execute(sa.text('SELECT score_metadata FROM "ScoreEntries" WHERE id = 1')).scalar_one()
    finally:
        engine.dispose()


def test_private_score_participation_migration_uses_sql_server_json_type() -> None:
    migration = importlib.import_module(MIGRATION)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
    assert "ADD snapshot_participation NVARCHAR(max) NULL" in output.getvalue()
