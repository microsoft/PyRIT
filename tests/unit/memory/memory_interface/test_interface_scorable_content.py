# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for persisting the loose content a score is anchored on.

``score_text_async`` scores content that was never a conversation turn, so before
``ScorableContentEntries`` the score's anchor resolved to nothing.
"""

from uuid import uuid4

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ScorableContentEntry, ScoreEntry
from pyrit.models import (
    ComponentIdentifier,
    ContentEntryScorable,
    ContentScorable,
    Score,
)


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
