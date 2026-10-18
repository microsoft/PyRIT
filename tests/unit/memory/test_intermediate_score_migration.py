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


@pytest.mark.parametrize("is_intermediate", [False, True])
def test_score_storage_marker_stays_private(is_intermediate: bool) -> None:
    score = Score(score_value="true", score_type="true_false")
    entry = ScoreEntry(entry=score, is_intermediate=is_intermediate)
    assert entry.is_intermediate is is_intermediate
    assert entry.to_dict()["is_intermediate"] is is_intermediate
    for field in ("is_root", "is_intermediate"):
        assert field not in Score.model_fields
        assert field not in Score.model_json_schema()["properties"]
        assert field not in entry.get_score().model_dump()


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
