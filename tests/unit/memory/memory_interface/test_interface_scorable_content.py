# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for persisting the loose content a score is anchored on.

``score_text_async`` scores content that was never a conversation turn, so before
``ScorableContentEntries`` the score's anchor resolved to nothing.
"""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import mssql

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ScorableContentEntry, ScoreEntry
from pyrit.memory.storage import StorageIO
from pyrit.models import (
    ComponentIdentifier,
    ContentEntryScorable,
    ContentScorable,
    Score,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver


def _scorer_id() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.memory")


def _content_score(content: ContentScorable, *, value: str = "true") -> Score:
    return Score(
        id=uuid4(),
        score_value=value,
        score_type="true_false",
        score_rationale="because",
        scorer_class_identifier=_scorer_id(),
        scorable=content,
    )


def test_loose_content_is_persisted_and_anchor_becomes_a_reference(sqlite_instance: MemoryInterface):
    content = ContentScorable(value="loose text", data_type="text")
    score = _content_score(content)

    sqlite_instance.add_scores_to_memory(scores=[score])

    # The in-hand score now names the stored row rather than carrying the payload.
    assert isinstance(score.scorable, ContentEntryScorable)

    stored = sqlite_instance.get_scores(score_ids=[str(score.id)])[0]
    assert isinstance(stored.scorable, ContentEntryScorable)
    assert sqlite_instance.get_scorable_content(content_ids=[stored.scorable.content_id]) == {
        stored.scorable.content_id: content
    }


def test_scores_over_the_same_content_share_one_row(sqlite_instance: MemoryInterface):
    content = ContentScorable(value="shared text")
    scores = [_content_score(content), _content_score(content, value="false")]

    sqlite_instance.add_scores_to_memory(scores=scores)

    anchors = {score.scorable.content_id for score in scores}  # type: ignore[union-attr]
    assert len(anchors) == 1
    assert len(sqlite_instance._query_entries(ScorableContentEntry)) == 1


def test_content_reference_is_promoted_to_a_foreign_key_column(sqlite_instance: MemoryInterface):
    score = _content_score(ContentScorable(value="joinable text"))

    sqlite_instance.add_scores_to_memory(scores=[score])

    entry = sqlite_instance._query_entries(ScoreEntry, conditions=ScoreEntry.id == score.id)[0]
    # The id is promoted out of the JSON so the reference is enforced and joinable.
    assert entry.scorable_content_id == score.scorable.content_id  # type: ignore[union-attr]
    assert entry.scorable["content_id"] == str(entry.scorable_content_id)


def test_message_anchored_score_leaves_the_content_column_null(sqlite_instance: MemoryInterface):
    score = Score(
        id=uuid4(),
        score_value="true",
        score_type="true_false",
        score_rationale="because",
        scorer_class_identifier=_scorer_id(),
    )

    sqlite_instance.add_scores_to_memory(scores=[score])

    entry = sqlite_instance._query_entries(ScoreEntry, conditions=ScoreEntry.id == score.id)[0]
    assert entry.scorable_content_id is None


def test_unicode_content_round_trips_with_sha256(sqlite_instance: MemoryInterface):
    content = ContentScorable(value="Zażółć gęślą jaźń — 你好")
    score = _content_score(content)

    sqlite_instance.add_scores_to_memory(scores=[score])

    entry = sqlite_instance._query_entries(ScorableContentEntry)[0]
    assert entry.value == content.value
    assert entry.value_sha256 == hashlib.sha256(content.value.encode("utf-8")).hexdigest()
    assert sqlite_instance.get_scorable_content(content_ids=[entry.id])[entry.id] == content


def test_content_value_uses_unicode_on_sql_server():
    column_type = ScorableContentEntry.__table__.c.value.type.compile(dialect=mssql.dialect())

    assert column_type.startswith("NVARCHAR")


def test_sync_persistence_rejects_unmanaged_file_content(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    source = tmp_path / "source.png"
    source.write_bytes(b"image")

    with pytest.raises(ValueError, match="add_scores_to_memory_async"):
        sqlite_instance.add_scores_to_memory(
            scores=[_content_score(ContentScorable(value=str(source), data_type="image_path"))]
        )


async def test_file_content_is_copied_hashed_shared_and_resolvable(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    source = tmp_path / "source.png"
    image_bytes = b"\x89PNG durable evidence"
    source.write_bytes(image_bytes)
    content = ContentScorable(value=str(source), data_type="image_path")
    scores = [_content_score(content), _content_score(content, value="false")]

    await sqlite_instance.add_scores_to_memory_async(scores=scores)

    assert all(isinstance(score.scorable, ContentEntryScorable) for score in scores)
    assert scores[0].scorable == scores[1].scorable
    entries = sqlite_instance._query_entries(ScorableContentEntry)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.value_sha256 == hashlib.sha256(image_bytes).hexdigest()

    stored_content = sqlite_instance.get_scorable_content(content_ids=[entry.id])[entry.id]
    managed_path = Path(stored_content.value)
    assert managed_path != source
    assert "scorable-content-entries" in managed_path.parts
    assert managed_path.read_bytes() == image_bytes

    source.unlink()
    resolved = MessageScorableResolver().resolve(
        scorable=scores[0].scorable,  # type: ignore[arg-type]
        memory=sqlite_instance,
    )
    assert Path(resolved.get_piece().converted_value).read_bytes() == image_bytes


async def test_file_content_missing_source_fails_before_database_write(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    missing = tmp_path / "missing.wav"
    score = _content_score(ContentScorable(value=str(missing), data_type="audio_path"))

    with pytest.raises(FileNotFoundError, match="missing.wav"):
        await sqlite_instance.add_scores_to_memory_async(scores=[score])

    assert sqlite_instance.get_scores(score_ids=[str(score.id)]) == []
    assert sqlite_instance._query_entries(ScorableContentEntry) == []


async def test_file_content_uses_configured_azure_results_storage(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    source = tmp_path / "source.bin"
    content_bytes = b"binary evidence"
    source.write_bytes(content_bytes)
    digest = hashlib.sha256(content_bytes).hexdigest()
    storage = AsyncMock(spec=StorageIO)
    results_path = "https://account.blob.core.windows.net/container/results"
    score = _content_score(ContentScorable(value=str(source), data_type="binary_path"))

    with (
        patch.object(sqlite_instance, "results_path", results_path),
        patch.object(sqlite_instance, "results_storage_io", storage),
    ):
        await sqlite_instance.add_scores_to_memory_async(scores=[score])

    expected_path = f"{results_path}/scorable-content-entries/binaries/{digest}.bin"
    storage.write_file_async.assert_awaited_once_with(expected_path, content_bytes)
    entry = sqlite_instance._query_entries(ScorableContentEntry)[0]
    assert entry.value == expected_path
    assert entry.value_sha256 == digest
