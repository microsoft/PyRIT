# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Portable schema tests for stored prompt generation jobs."""

import importlib
import io
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable

from pyrit.memory.memory_models import PromptGenerationEntry
from pyrit.memory.migration import check_schema_migrations

REVISION = "c4a401cd8201"
PREVIOUS = "7aa74745cc9b"
_SCRIPT_LOCATION = str(Path(__file__).resolve().parents[3] / "pyrit" / "memory" / "alembic")


def _config(connection: sa.Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", _SCRIPT_LOCATION)
    config.attributes.update(connection=connection, version_table="pyrit_memory_alembic_version")
    return config


def test_prompt_generation_revision_follows_the_previous_head() -> None:
    config = Config()
    config.set_main_option("script_location", _SCRIPT_LOCATION)
    script = ScriptDirectory.from_config(config).get_revision(REVISION)
    assert script is not None
    assert script.down_revision == PREVIOUS


def test_prompt_generation_migration_and_guarded_downgrade() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, PREVIOUS)
            before = set(sa.inspect(connection).get_table_names())
            command.upgrade(config, REVISION)
            assert set(sa.inspect(connection).get_table_names()) == before | {"PromptGenerationEntries"}
            # Downgrade from this revision, not head: a sibling migration branch merged at head stays applied.
            command.downgrade(config, PREVIOUS)
            assert set(sa.inspect(connection).get_table_names()) == before
            command.upgrade(config, "head")
            indexes = {index["name"] for index in sa.inspect(connection).get_indexes("PromptGenerationEntries")}
            assert indexes == {
                "ix_PromptGenerationEntries_created_at_id",
                "ix_PromptGenerationEntries_dataset_name",
            }
        check_schema_migrations(engine=engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO PromptGenerationEntries (id, generator, dataset_name, status, request, targets, "
                    "pause_for_review, review_completed, group_index, progress, created_at, updated_at) VALUES "
                    "('00000000-0000-0000-0000-000000000001', 'assert', 'held', 'failed', '{}', '{}', 0, 0, 0, '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            with pytest.raises(RuntimeError, match="refusing to discard"):
                command.downgrade(_config(connection), PREVIOUS)
    finally:
        engine.dispose()


def test_prompt_generation_sql_server_migration_has_bounded_keys() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = importlib.import_module(f"pyrit.memory.alembic.versions.{REVISION}_add_prompt_generations")
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert "dataset_name NVARCHAR(256) NOT NULL" in ddl
    assert "status VARCHAR(32) NOT NULL" in ddl
    assert "CREATE INDEX [ix_PromptGenerationEntries_dataset_name]" in ddl
    assert "CREATE INDEX [ix_PromptGenerationEntries_created_at_id]" in ddl
    assert str(CreateTable(PromptGenerationEntry.__table__).compile(dialect=mssql.dialect()))
    indexed = {column for index in PromptGenerationEntry.__table__.indexes for column in index.columns}
    for column in PromptGenerationEntry.__table__.columns:
        if (column.primary_key or column in indexed) and isinstance(column.type, (sa.String, sa.Unicode)):
            assert column.type.length is not None


BATCH_POSITION_REVISION = "ccf97e771f04"


def test_batch_position_revision_follows_the_prompt_generation_table() -> None:
    config = Config()
    config.set_main_option("script_location", _SCRIPT_LOCATION)
    script = ScriptDirectory.from_config(config).get_revision(BATCH_POSITION_REVISION)
    assert script is not None
    assert script.down_revision == REVISION


def test_batch_position_migration_fills_existing_generations_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            config = _config(connection)
            command.upgrade(config, REVISION)
            connection.execute(
                sa.text(
                    "INSERT INTO PromptGenerationEntries (id, generator, dataset_name, status, request, targets, "
                    "pause_for_review, review_completed, progress, created_at, updated_at) VALUES "
                    "('00000000-0000-0000-0000-000000000001', 'assert', 'held', 'failed', '{}', '{}', 0, 0, '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

            command.upgrade(config, BATCH_POSITION_REVISION)
            assert connection.scalar(sa.text("SELECT group_index FROM PromptGenerationEntries")) == 0
            columns = {
                column["name"]: column for column in sa.inspect(connection).get_columns("PromptGenerationEntries")
            }
            assert not columns["group_index"]["nullable"]
            assert columns["group_index"]["default"] is None
            indexes = {index["name"] for index in sa.inspect(connection).get_indexes("PromptGenerationEntries")}
            assert indexes == {
                "ix_PromptGenerationEntries_created_at_id",
                "ix_PromptGenerationEntries_dataset_name",
            }

            command.downgrade(config, REVISION)
            columns = {column["name"] for column in sa.inspect(connection).get_columns("PromptGenerationEntries")}
            assert "group_index" not in columns
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM PromptGenerationEntries")) == 1
            command.upgrade(config, "head")
        check_schema_migrations(engine=engine)
    finally:
        engine.dispose()


def test_batch_position_sql_server_migration_drops_its_fill_default() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    migration = importlib.import_module(
        f"pyrit.memory.alembic.versions.{BATCH_POSITION_REVISION}_add_prompt_generation_batch_position"
    )
    with Operations.context(context):
        migration.upgrade()
    ddl = output.getvalue()
    assert "ADD group_index INTEGER NOT NULL DEFAULT 0" in ddl
    assert "drop constraint" in ddl
