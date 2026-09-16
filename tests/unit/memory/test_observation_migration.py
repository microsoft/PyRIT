# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def test_observation_migration_creates_and_drops_tables():
    migration = importlib.import_module("pyrit.memory.alembic.versions.2c4e6a8b0d1f_add_observations")
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    for table_name in (
        "ScorableContentEntries",
        "PromptMemoryEntries",
        "ScoreEntries",
    ):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.Uuid().with_variant(sa.CHAR(36), "sqlite"), primary_key=True),
        )
    metadata.create_all(engine)

    with engine.begin() as connection:
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
        table_names = set(sa.inspect(connection).get_table_names())
        assert {
            "ObservationEntries",
            "ObservationMessagePieceEntries",
            "ScoreObservationEntries",
        } <= table_names
        index_names = {
            index["name"]
            for table_name in (
                "ObservationEntries",
                "ObservationMessagePieceEntries",
                "ScoreObservationEntries",
            )
            for index in sa.inspect(connection).get_indexes(table_name)
        }
        assert {
            "ix_ObservationEntries_scorable_content_id",
            "ix_ObservationEntries_scored_message_piece_id",
            "ix_ObservationMessagePieceEntries_message_piece_id",
            "ix_ScoreObservationEntries_observation_id",
        } <= index_names
        trigger_names = set(
            connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).scalars()
        )
        assert {
            "trg_observation_prompt_immutable_update",
            "trg_observation_prompt_immutable_delete",
            "trg_observation_content_immutable_update",
            "trg_observation_content_immutable_delete",
        } <= trigger_names

        with patch.object(migration, "op", _operations(connection)):
            migration.downgrade()
        table_names = set(sa.inspect(connection).get_table_names())
        assert "ObservationEntries" not in table_names
        assert "ObservationMessagePieceEntries" not in table_names
        assert "ScoreObservationEntries" not in table_names
        assert (
            connection.execute(
                sa.text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_observation_%'")
            ).scalar_one()
            == 0
        )
