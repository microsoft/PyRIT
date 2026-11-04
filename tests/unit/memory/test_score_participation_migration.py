# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from pyrit.memory.migration import check_schema_migrations

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
            assert columns["snapshot_participation"]["nullable"] is True
            assert connection.scalar(sa.text("SELECT snapshot_participation FROM ScoreEntries WHERE id = 1")) is None
            connection.execute(
                sa.text("UPDATE ScoreEntries SET snapshot_participation = :value WHERE id = 1"),
                {"value": '{"0": null}'},
            )
            assert connection.scalar(sa.text("SELECT snapshot_participation FROM ScoreEntries")) == '{"0": null}'
            with patch.object(migration, "op", operations):
                migration.downgrade()
            assert [column["name"] for column in sa.inspect(connection).get_columns("ScoreEntries")] == [
                "id",
                "score_metadata",
            ]
            assert connection.scalar(sa.text("SELECT score_metadata FROM ScoreEntries")) == "{}"
    finally:
        engine.dispose()


def test_private_score_participation_migration_uses_sql_server_json_type() -> None:
    migration = importlib.import_module(MIGRATION)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
    assert "ADD snapshot_participation NVARCHAR(max) NULL" in output.getvalue()


def test_private_score_participation_has_unique_head_and_upgrades_current_parent() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["e7c4a2d9f013"]
    assert scripts.get_revision("e7c4a2d9f013").down_revision == "c8d3e5f7a901"
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            config.attributes["version_table"] = "pyrit_memory_alembic_version"
            command.upgrade(config, "c8d3e5f7a901")
            assert "snapshot_participation" not in {
                column["name"] for column in sa.inspect(connection).get_columns("ScoreEntries")
            }
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()
