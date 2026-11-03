# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
from io import StringIO
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory.memory_models import ScoreEntry
from pyrit.models import Score
from unit.mocks import run_memory_session_async


@pytest.mark.parametrize("is_intermediate", [False, True])
def test_score_storage_marker_stays_private(is_intermediate: bool) -> None:
    score = Score(score_value="true", score_type="true_false")
    score._snapshot_participation = {"0": None}
    entry = ScoreEntry(entry=score, is_intermediate=is_intermediate)
    assert entry.is_intermediate is is_intermediate
    assert entry.snapshot_participation == {"0": None}
    assert entry.to_dict()["is_intermediate"] is is_intermediate
    assert "pyrit_internal_snapshot_participation" not in score.score_metadata
    for field in ("is_root", "is_intermediate"):
        assert field not in Score.model_fields
        assert field not in Score.model_json_schema()["properties"]
        assert field not in entry.get_score().model_dump()
    assert "_snapshot_participation" not in Score.model_fields
    assert "snapshot_participation" not in Score.model_json_schema()["properties"]
    assert "snapshot_participation" not in score.model_dump()
    assert entry.get_score()._snapshot_participation == {"0": None}


@pytest.mark.parametrize("value", ["not-json", '{"0": null}', '{"1": null}', 17, 1.5])
async def test_score_preserves_reserved_name_as_caller_metadata_async(value, sqlite_instance) -> None:
    metadata = {"pyrit_internal_snapshot_participation": value}
    score = Score(score_value="true", score_type="true_false", score_metadata=metadata)

    assert score.score_metadata == metadata
    assert score._snapshot_participation is None
    assert Score.model_validate(score.model_dump()).score_metadata == metadata
    assert Score.model_validate_json(score.model_dump_json()).score_metadata == metadata

    await sqlite_instance.add_scores_to_memory_async(scores=[score])
    [persisted] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert persisted.score_metadata == metadata
    assert persisted._snapshot_participation is None


async def test_private_participation_survives_commit_expire_and_canonical_root_reload_async(sqlite_instance) -> None:
    score = Score(
        score_value="true",
        score_type="true_false",
        score_metadata={"pyrit_internal_snapshot_participation": '{"1": null}'},
    )
    score._snapshot_participation = {"0": {"0": None}, "1": None}
    await sqlite_instance.add_scores_to_memory_async(scores=[score])

    def inspect_raw_round_trip(session) -> None:
        entry = session.get(ScoreEntry, score.id)
        assert entry.id == score.id
        assert entry.snapshot_participation == score._snapshot_participation
        session.commit()
        session.expire_all()
        reloaded = session.get(ScoreEntry, score.id)
        assert reloaded.id == score.id
        assert reloaded.snapshot_participation == {"0": {"0": None}, "1": None}
        assert reloaded.score_metadata == score.score_metadata

    await run_memory_session_async(memory=sqlite_instance, operation=inspect_raw_round_trip)
    [canonical] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert canonical.id == score.id
    assert canonical._snapshot_participation == score._snapshot_participation
    assert canonical.score_metadata == score.score_metadata
    assert "_snapshot_participation" not in canonical.model_dump()


def test_intermediate_score_migration_preserves_legacy_results() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.6d8f0a2c4e61_mark_intermediate_scores")
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE ScoreEntries (id INTEGER PRIMARY KEY)"))
            connection.execute(sa.text("INSERT INTO ScoreEntries (id) VALUES (1)"))
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, "op", operations):
                migration.upgrade()
                assert (
                    connection.execute(sa.text("SELECT is_intermediate FROM ScoreEntries WHERE id = 1")).scalar_one()
                    == 0
                )
                connection.execute(sa.text("INSERT INTO ScoreEntries (id, is_intermediate) VALUES (2, 1)"))
                with pytest.raises(ValueError, match="Remove retained intermediate scores"):
                    migration.downgrade()
                connection.execute(sa.text("DELETE FROM ScoreEntries WHERE is_intermediate = 1"))
                migration.downgrade()
            assert [column["name"] for column in sa.inspect(connection).get_columns("ScoreEntries")] == ["id"]
            assert connection.execute(sa.text("SELECT id FROM ScoreEntries")).scalar_one() == 1
    finally:
        engine.dispose()


def test_intermediate_score_migration_uses_sql_server_bit() -> None:
    migration = importlib.import_module("pyrit.memory.alembic.versions.6d8f0a2c4e61_mark_intermediate_scores")
    output = StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with patch.object(migration, "op", Operations(context)):
        migration.upgrade()
    assert "ADD is_intermediate BIT NOT NULL DEFAULT 0" in output.getvalue()
