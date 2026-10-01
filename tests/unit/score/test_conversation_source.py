# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from unit.mocks import get_mock_target_identifier, store_message_async

from pyrit.exceptions import ScorerLLMResponseBlockedException
from pyrit.memory import MemoryInterface
from pyrit.models import (
    Acquisition,
    Contains,
    ContentEntryScorable,
    ConversationObservationPayload,
    ConversationScorable,
    MessagePiece,
    MessageScorable,
    Observation,
    OutputMatches,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    MessageScorer,
    NonReplayableObservationError,
    OutputMatchesScorer,
    SelfAskTrueFalseScorer,
    TrueFalseInverterScorer,
    create_conversation_scorer,
)
from pyrit.score.observation import ConversationSource
from pyrit.score.observation.execution import _ObservationEvidenceResolver

pytestmark = pytest.mark.usefixtures("patch_central_database")


async def test_conversation_finalization_preserves_fallback_anchor_async() -> None:
    message = await store_message_async(MessagePiece(role="assistant", original_value="retained answer").to_message())
    anchor = MessageScorable.from_message(message)
    scorer = create_conversation_scorer(scorer=OutputMatchesScorer())
    assert isinstance(scorer, MessageScorer)
    score = Score(score_type="true_false", status=ScoreStatus.UNDETERMINED)
    await scorer._finalize_message_scores_async(message=message, scores=[score], anchor=anchor, expectation=None)
    assert score.scorable == anchor
    assert Score.model_validate(score.model_dump()).scorable == anchor


@pytest.mark.parametrize("entry", ["conversation", "message_reference", "message"])
@pytest.mark.parametrize("retained_response", [False, True])
@pytest.mark.parametrize("raise_if_blocked", [False, True])
@pytest.mark.filterwarnings("ignore:Scorer.score_async:DeprecationWarning")
async def test_conversation_blocked_judge_policy_and_evidence_async(
    sqlite_instance: MemoryInterface, entry: str, retained_response: bool, raise_if_blocked: bool
) -> None:
    message = await store_message_async(MessagePiece(role="assistant", original_value="retained answer").to_message())
    piece = message.message_pieces[0]
    assert piece.conversation_id
    anchor = ConversationScorable(conversation_id=piece.conversation_id)
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("BlockedConversationJudge")
    target.send_prompt_async = AsyncMock(
        return_value=[
            MessagePiece(
                role="assistant",
                original_value="",
                original_value_data_type="error",
                response_error="blocked",
            ).to_message()
        ],
    )
    child = SelfAskTrueFalseScorer(chat_target=target)
    scorer = create_conversation_scorer(scorer=child)
    assert isinstance(scorer, MessageScorer)
    scorer.raise_if_scorer_blocks = raise_if_blocked
    expectation = ScoringExpectation(objective="Judge the conversation")
    evidence = anchor if entry == "conversation" else MessageScorable.from_message(message)
    with (
        patch.object(
            sqlite_instance, "add_scores_to_memory_async", wraps=sqlite_instance.add_scores_to_memory_async
        ) as persist,
        patch.object(
            child,
            "_score_nested_async",
            wraps=child._score_nested_async,
            side_effect=None if retained_response else ScorerLLMResponseBlockedException(message="Blocked judge"),
        ),
    ):
        if raise_if_blocked:
            with pytest.raises(ScorerLLMResponseBlockedException):
                if entry == "message":
                    await scorer.score_async(message, expectation=expectation)
                else:
                    await scorer.score_async(scorable=evidence, expectation=expectation)
            persist.assert_not_called()
            return
        scores = (
            await scorer.score_async(message, expectation=expectation)
            if entry == "message"
            else await scorer.score_async(scorable=evidence, expectation=expectation)
        )
    persist.assert_called_once()
    score = scores[0]
    assert score.is_undetermined
    assert score.scorable == anchor
    assert score.scored_expectation == expectation
    assert score.message_piece_id == (None if entry == "conversation" else piece.id)
    assert child.raise_if_scorer_blocks is True
    observations = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert len(observations) == (2 if retained_response else 1)
    snapshot = next(obs for obs in observations if isinstance(obs.payload, ConversationObservationPayload))
    assert snapshot.scorable == anchor
    assert snapshot.evidence_message_piece_ids == (piece.id,)
    if retained_response:
        judgment = next(obs for obs in observations if obs.acquisition is Acquisition.ERROR)
        assert isinstance(judgment.scorable, ContentEntryScorable)
        assert judgment.metadata["reason"] == "scorer_response_blocked"


@pytest.mark.parametrize("nested", [False, True])
async def test_conversation_snapshot_does_not_grow_async(sqlite_instance: MemoryInterface, nested: bool) -> None:
    conversation_id = str(uuid.uuid4())
    first = await store_message_async(
        MessagePiece(role="user", original_value="A", conversation_id=conversation_id).to_message()
    )
    await store_message_async(
        MessagePiece(role="assistant", original_value="B", conversation_id=conversation_id).to_message()
    )
    scorer = create_conversation_scorer(scorer=OutputMatchesScorer())
    root = TrueFalseInverterScorer(scorer=scorer) if nested else scorer
    anchor = ConversationScorable(conversation_id=conversation_id)
    expectation = ScoringExpectation(conditions=(OutputMatches(matcher=Contains(value="C")),))
    first_score = (await root.score_async(scorable=anchor, expectation=expectation))[0]
    assert first_score.get_value() is nested
    assert first_score.scorable == anchor
    assert first_score.message_piece_id is None
    snapshot = (await sqlite_instance.get_observations_async(observation_ids=first_score.observation_ids))[0]
    assert isinstance(snapshot.payload, ConversationObservationPayload)
    assert len(snapshot.payload.message_piece_ids) == 2
    assert Observation.model_validate_json(snapshot.model_dump_json()) == snapshot

    await store_message_async(
        MessagePiece(role="assistant", original_value="C", conversation_id=conversation_id).to_message()
    )
    # A legacy trigger is a locator, not a cutoff.
    second_score = (await scorer.score_async(scorable=MessageScorable.from_message(first), expectation=expectation))[0]
    assert second_score.get_value() is True
    assert second_score.scorable == anchor
    assert second_score.message_piece_id == first.message_pieces[0].id
    current = (await sqlite_instance.get_observations_async(observation_ids=second_score.observation_ids))[0]
    assert len(current.evidence_message_piece_ids) == 3
    with patch.object(
        sqlite_instance, "get_conversation_messages_async", side_effect=AssertionError("Must not reacquire")
    ):
        saved = await _ObservationEvidenceResolver(memory=sqlite_instance).resolve_async(observation=snapshot)
    assert isinstance(saved, tuple)
    assert [piece.converted_value for piece in saved] == ["A", "B"]
    with pytest.raises(NonReplayableObservationError):
        await scorer.score_observation_async(observation=snapshot, expectation=expectation)


@pytest.mark.parametrize("change", ["missing", "value", "role", "sequence", "metadata", "conversation"])
async def test_conversation_snapshot_rejects_changed_evidence_async(
    sqlite_instance: MemoryInterface, change: str
) -> None:
    message = await store_message_async(MessagePiece(role="assistant", original_value="B").to_message())
    piece = message.message_pieces[0]
    assert piece.conversation_id
    observation = await ConversationSource().acquire_async(
        scorable=ConversationScorable(conversation_id=piece.conversation_id)
    )
    pieces = {stored.id: stored for stored in await sqlite_instance.get_message_pieces_async(prompt_ids=[piece.id])}
    changed = pieces[piece.id]
    if change == "missing":
        pieces.clear()
    elif change == "value":
        changed.converted_value = "different"
    elif change == "role":
        changed.role = "user"
    elif change == "sequence":
        changed.sequence += 1
    elif change == "metadata":
        changed.prompt_metadata["partial_content"] = "different"
    else:
        changed.conversation_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="missing or modified"):
        observation.validate_evidence(message_pieces=pieces)


async def test_conversation_source_does_not_filter_and_retains_references_async(
    sqlite_instance: MemoryInterface,
) -> None:
    message = await store_message_async(MessagePiece(role="system", original_value="system evidence").to_message())
    assert message.message_pieces[0].conversation_id
    anchor = ConversationScorable(conversation_id=message.message_pieces[0].conversation_id)
    observation = await ConversationSource().acquire_async(scorable=anchor)
    assert observation.evidence_message_piece_ids == (message.message_pieces[0].id,)
    scorer = create_conversation_scorer(scorer=OutputMatchesScorer())
    expectation = ScoringExpectation(conditions=(OutputMatches(matcher=Contains(value="evidence")),))
    assert await scorer.score_async(scorable=anchor, expectation=expectation) == []
    assert await sqlite_instance.get_observations_async(observation_ids=[observation.id]) == []
    with pytest.raises(ValueError, match="not found"):
        await ConversationSource().acquire_async(scorable=ConversationScorable(conversation_id="missing"))


@pytest.mark.parametrize("entry", ["conversation", "message"])
async def test_conversation_judge_keeps_child_content_and_persists_once_async(
    sqlite_instance: MemoryInterface,
    entry: str,
) -> None:
    message = await store_message_async(MessagePiece(role="assistant", original_value="retained answer").to_message())
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("ConversationJudge")
    target.send_prompt_async = AsyncMock(
        return_value=[
            MessagePiece(
                role="assistant",
                original_value='{"score_value":"true","description":"yes","rationale":"matched","metadata":""}',
            ).to_message()
        ]
    )
    child = SelfAskTrueFalseScorer(chat_target=target)
    scorer = create_conversation_scorer(scorer=child)
    assert scorer.get_chat_target() is target
    assert message.message_pieces[0].conversation_id
    anchor = ConversationScorable(conversation_id=message.message_pieces[0].conversation_id)
    with patch.object(
        sqlite_instance, "add_scores_to_memory_async", wraps=sqlite_instance.add_scores_to_memory_async
    ) as persist:
        evidence = anchor if entry == "conversation" else MessageScorable.from_message(message)
        score = (await scorer.score_async(scorable=evidence))[0]
    assert persist.call_count == 1
    stored = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
    assert len(stored) == 2
    intermediate = next(item for item in stored if item.id != score.id)
    assert isinstance(intermediate.scorable, ContentEntryScorable)
    assert intermediate.scorer_class_identifier == child.get_identifier()
    assert score.scorable == anchor
    assert score.scorer_class_identifier == scorer.get_identifier()
    assert score.message_piece_id == (None if entry == "conversation" else message.message_pieces[0].id)
    assert [item.id for item in await sqlite_instance.get_scores_async(score_type="true_false")] == [score.id]
    observations = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert len(observations) == 2
    judgment = next(obs for obs in observations if isinstance(obs.scorable, ContentEntryScorable))
    assert judgment.scorable != score.scorable
    replay = await child.score_observation_async(observation=judgment)
    assert replay[0].get_value() is True
    assert target.send_prompt_async.call_count == 1
    with pytest.raises(IntegrityError, match="observation"):
        await sqlite_instance.delete_conversation_pieces_after_sequence_async(
            conversation_id=anchor.conversation_id, sequence=-1
        )


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_conversation_scorable_rejects_empty_identity(value: str) -> None:
    with pytest.raises(ValidationError):
        ConversationScorable(conversation_id=value)
