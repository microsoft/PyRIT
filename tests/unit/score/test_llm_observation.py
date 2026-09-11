# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import uuid
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from unit.mocks import get_mock_target_identifier

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ObservationEntry, PromptMemoryEntry, ScoreEntry
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentEntryScorable,
    ContentScorable,
    JsonResponseConfig,
    MatchesObjective,
    Message,
    MessagePiece,
    MessageScorable,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
    UnvalidatedScore,
)
from pyrit.score import (
    AudioTrueFalseScorer,
    ContentClassifier,
    ContentClassifierPaths,
    JsonSchemaResponseHandler,
    LikertScalePaths,
    LlamaGuardScorer,
    NonReplayableObservationError,
    ResponseHandler,
    SelfAskCategoryScorer,
    SelfAskGeneralTrueFalseScorer,
    SelfAskLikertScorer,
    SelfAskTrueFalseScorer,
    ShieldGemmaGuideline,
    ShieldGemmaScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

_INVALID_RESPONSE = "not valid json"
_VALID_RESPONSE = '{"score_value":"true","description":"matched","rationale":"reason","metadata":"test"}'


def _response(value: str) -> list[Message]:
    return [Message(message_pieces=[MessagePiece(role="assistant", original_value=value)])]


def _scorer(
    *,
    target: MagicMock,
    target_name: str = "MockChatTarget",
) -> SelfAskTrueFalseScorer:
    target.get_identifier.return_value = get_mock_target_identifier(target_name)
    return SelfAskTrueFalseScorer(chat_target=target)


def _force_conversation_value_update(
    *,
    memory: MemoryInterface,
    conversation_id: str,
    converted_value: str,
) -> None:
    """Simulate a direct database edit that bypasses observation immutability checks."""
    with closing(memory.get_session()) as session:
        session.execute(text('DROP TRIGGER IF EXISTS "trg_observation_prompt_immutable_update"'))
        updated = (
            session.query(PromptMemoryEntry)
            .filter(PromptMemoryEntry.conversation_id == conversation_id)
            .update({"converted_value": converted_value}, synchronize_session=False)
        )
        session.commit()
    assert updated


class _LegacyResponseHandler(ResponseHandler):
    """A third-party handler that implements the pre-observation parse contract."""

    @property
    def json_response_config(self) -> JsonResponseConfig:
        """The disabled JSON request used by this test handler."""
        return JsonResponseConfig(enabled=False)

    def parse(
        self,
        *,
        response_text: str,
        scorer_identifier: ComponentIdentifier,
        scored_prompt_id: str | uuid.UUID,
        category: Sequence[str] | str | None = None,
        objective: str | None = None,
    ) -> UnvalidatedScore:
        """Return a fixed valid score without accepting new observation parameters."""
        return UnvalidatedScore(
            raw_score_value="true",
            score_value_description="legacy",
            score_category=[category] if isinstance(category, str) else list(category or []),
            score_rationale=response_text,
            score_metadata=None,
            scorer_class_identifier=scorer_identifier,
            message_piece_id=scored_prompt_id,
            objective=objective,
        )


class _MatchesObjectiveScorer(TrueFalseScorer):
    """A deterministic sibling that consumes ``MatchesObjective``."""

    MATCHED_CONDITIONS = frozenset({MatchesObjective})

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_type="true_false",
                scorable=cast("ScorableUnion", scorable),
            )
        ]


async def test_llm_observation_references_only_terminal_retry_response(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(
        side_effect=[
            _response(_INVALID_RESPONSE),
            _response(_VALID_RESPONSE),
        ]
    )
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Judge this response")

    scores = await scorer.score_async(
        scorable=ContentScorable(value="candidate response"),
        expectation=expectation,
    )

    assert target.send_prompt_async.call_count == 2
    assert len(scores[0].observation_ids) == 1
    observation = sqlite_instance.get_observations(observation_ids=scores[0].observation_ids)[0]
    referenced = sqlite_instance.get_message_pieces(prompt_ids=list(observation.payload.message_piece_ids))
    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.scorable == scores[0].scorable
    assert [piece.converted_value for piece in referenced] == [_VALID_RESPONSE]
    assert _INVALID_RESPONSE not in {piece.converted_value for piece in sqlite_instance.get_message_pieces()}


@pytest.mark.parametrize(
    ("system_prompt", "expected_observation_count"),
    [
        ("Judge this content: {prompt}", 1),
        ("Judge the piece created at {message_piece.timestamp}: {prompt}", 0),
    ],
)
async def test_general_scorer_collects_only_durable_content_observations(
    sqlite_instance: MemoryInterface,
    system_prompt: str,
    expected_observation_count: int,
):
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockGeneralTarget")
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=target,
        system_prompt_format_string=system_prompt,
    )

    score = (await scorer.score_async(scorable=ContentScorable(value="candidate response")))[0]

    assert len(score.observation_ids) == expected_observation_count
    assert len(sqlite_instance.get_observations(observation_ids=score.observation_ids)) == expected_observation_count


async def test_image_scoring_defers_observation_until_media_snapshot_support(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"test image bytes")
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)

    score = (await scorer.score_image_async(str(image_path), objective="Judge the image"))[0]

    assert score.score_value == "true"
    assert score.observation_ids == []
    assert sqlite_instance.get_observations(observation_ids=[]) == []


async def test_audio_transcript_scoring_persists_only_root_score_without_observation(
    sqlite_instance: MemoryInterface,
    tmp_path: Path,
):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"test audio bytes")
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = AudioTrueFalseScorer(text_capable_scorer=_scorer(target=target))

    with patch.object(
        scorer._audio_helper,
        "_transcribe_audio_async",
        new_callable=AsyncMock,
        return_value="transcript",
    ):
        score = (
            await scorer.score_async(
                scorable=ContentScorable(value=str(audio_path), data_type="audio_path"),
                expectation=ScoringExpectation(objective="Judge the audio"),
            )
        )[0]

    assert score.score_value == "true"
    assert score.observation_ids == []
    assert len(sqlite_instance._query_entries(ScoreEntry)) == 1
    assert sqlite_instance.get_observations(observation_ids=[]) == []


async def test_blocked_partial_content_scoring_defers_unreplayable_observation(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    blocked_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        response_error="blocked",
        prompt_metadata={"partial_content": "candidate partial response"},
    )

    score = (
        await scorer.score_message_async(
            message=blocked_piece.to_message(),
            expectation=ScoringExpectation(objective="Judge the partial response"),
        )
    )[0]

    sent_message = target.send_prompt_async.call_args.kwargs["message"]
    assert "candidate partial response" in sent_message.get_value()
    assert "content_filter" not in sent_message.get_value()
    assert score.observation_ids == []
    assert sqlite_instance.get_observations(observation_ids=[]) == []


async def test_llm_observation_replay_does_not_call_target(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Judge this response")
    live_score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=live_score.observation_ids)[0]

    replay_score = (
        await scorer.score_observation_async(
            observation=observation,
            expectation=expectation,
        )
    )[0]

    assert target.send_prompt_async.call_count == 1
    assert replay_score.score_value == live_score.score_value
    assert replay_score.scorable == observation.scorable
    assert replay_score.observation_ids == [observation.id]
    assert len(sqlite_instance._query_entries(ScoreEntry)) == 2


async def test_custom_response_handler_keeps_legacy_parse_signature(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response("legacy response"))
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskTrueFalseScorer(
        chat_target=target,
        response_handler=_LegacyResponseHandler(),
    )

    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=ScoringExpectation(objective="Judge this response"),
        )
    )[0]

    assert score.score_value == "true"
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    with pytest.raises(NonReplayableObservationError, match="stable replay contract"):
        await scorer.score_observation_async(
            observation=observation,
            expectation=ScoringExpectation(objective="Judge this response"),
        )


async def test_llm_observation_rejects_changed_response_handler(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskTrueFalseScorer(
        chat_target=target,
        response_handler=JsonSchemaResponseHandler(),
    )
    expectation = ScoringExpectation(objective="Judge this response")
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    other_target = MagicMock()
    other_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    other_scorer = SelfAskTrueFalseScorer(
        chat_target=other_target,
        response_handler=JsonSchemaResponseHandler(
            score_value_output_key="verdict",
        ),
    )

    with pytest.raises(NonReplayableObservationError, match="handler or category"):
        await other_scorer.score_observation_async(
            observation=observation,
            expectation=expectation,
        )

    other_target.send_prompt_async.assert_not_called()


async def test_multi_piece_observations_replay_the_scored_piece(
    sqlite_instance: MemoryInterface,
):
    conversation_id = str(uuid.uuid4())
    pieces = [
        MessagePiece(
            role="assistant",
            original_value=value,
            conversation_id=conversation_id,
            sequence=0,
        )
        for value in ("first", "second")
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    target.send_prompt_async = AsyncMock(side_effect=lambda **_: _response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Judge both pieces")

    score = (
        await scorer.score_async(
            scorable=MessageScorable(message_piece_ids=tuple(piece.id for piece in pieces)),
            expectation=expectation,
        )
    )[0]
    observations = sqlite_instance.get_observations(observation_ids=score.observation_ids)

    assert {observation.payload.scored_piece_id for observation in observations} == {piece.id for piece in pieces}
    second_observation = next(
        observation for observation in observations if observation.payload.scored_piece_id == pieces[1].id
    )
    replay = (
        await scorer.score_observation_async(
            observation=second_observation,
            expectation=expectation,
        )
    )[0]
    assert replay.message_piece_id == pieces[1].id


async def test_in_hand_modified_piece_is_snapshotted_as_content(
    sqlite_instance: MemoryInterface,
):
    stored_piece = MessagePiece(
        role="assistant",
        original_value="stored response",
        conversation_id=str(uuid.uuid4()),
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[stored_piece])
    supplied_piece = stored_piece.model_copy(
        update={
            "original_value": "modified response",
            "converted_value": "modified response",
        }
    )
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)

    score = (
        await scorer.score_message_async(
            message=Message(message_pieces=[supplied_piece]),
            expectation=ScoringExpectation(objective="Judge this response"),
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]

    assert isinstance(score.scorable, ContentEntryScorable)
    assert score.message_piece_id is None
    assert observation.scorable == score.scorable
    stored_content = sqlite_instance.get_scorable_content(content_ids=[score.scorable.content_id])
    assert stored_content[score.scorable.content_id].value == "modified response"


@pytest.mark.parametrize("legacy_api", [False, True])
@pytest.mark.parametrize("piece_count", [1, 2])
async def test_in_hand_message_preserves_links_after_timestamp_rounding(
    sqlite_instance: MemoryInterface,
    legacy_api: bool,
    piece_count: int,
):
    conversation_id = str(uuid.uuid4())
    timestamp = datetime(2026, 9, 11, 12, 0, 0, 123456, tzinfo=timezone.utc)
    pieces = [
        MessagePiece(
            role="assistant",
            original_value=f"candidate response {index}",
            conversation_id=conversation_id,
            timestamp=timestamp,
        )
        for index in range(piece_count)
    ]
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[
            piece.model_copy(update={"timestamp": timestamp.replace(microsecond=123000)}) for piece in pieces
        ]
    )
    target = MagicMock()
    target.send_prompt_async = AsyncMock(side_effect=lambda **_: _response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Judge this response")
    message = Message(message_pieces=pieces)

    if legacy_api:
        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            scores = await scorer.score_async(message=message, expectation=expectation)
    else:
        scores = await scorer.score_message_async(message=message, expectation=expectation)

    score = scores[0]
    assert score.message_piece_id == pieces[0].id
    assert score.scorable == MessageScorable.from_message(message)
    stored_score = sqlite_instance.get_scores(score_ids=[score.id])[0]
    assert stored_score.message_piece_id == pieces[0].id
    assert stored_score.scorable == score.scorable
    if piece_count == 1:
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        assert isinstance(observation.scorable, ContentEntryScorable)
        replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
        assert replay.score_value == score.score_value
    else:
        assert score.observation_ids == []


@pytest.mark.parametrize("use_reference", [False, True])
async def test_timestamp_template_requires_exact_observation_evidence(
    sqlite_instance: MemoryInterface,
    use_reference: bool,
):
    timestamp = datetime(2026, 9, 11, 12, 0, 0, 123456, tzinfo=timezone.utc)
    piece = MessagePiece(
        role="assistant",
        original_value="candidate response",
        conversation_id=str(uuid.uuid4()),
        timestamp=timestamp,
    )
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[piece.model_copy(update={"timestamp": timestamp.replace(microsecond=123000)})]
    )
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=target,
        system_prompt_format_string="Judge the timestamp.",
        prompt_format_string="{message_piece.timestamp}",
    )
    if use_reference:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(piece.to_message()))
    else:
        scores = await scorer.score_message_async(message=piece.to_message())

    score = scores[0]
    assert score.message_piece_id == piece.id
    assert score.scorable == MessageScorable.from_message(piece.to_message())
    assert bool(score.observation_ids) is use_reference
    if use_reference:
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        replay = (await scorer.score_observation_async(observation=observation))[0]
        assert replay.score_value == score.score_value


async def test_likert_replay_preserves_live_metadata(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    target.send_prompt_async = AsyncMock(
        return_value=_response('{"score_value":"1","description":"matched","rationale":"reason"}')
    )
    scorer = SelfAskLikertScorer.from_likert_scale(
        chat_target=target,
        likert_scale=LikertScalePaths.CYBER_SCALE.load(),
    )
    live = (await scorer.score_text_async("candidate response"))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]

    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert replay.score_metadata == live.score_metadata


async def test_category_replay_preserves_live_result(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    target.send_prompt_async = AsyncMock(
        return_value=_response(
            '{"score_value":"true","description":"matched","rationale":"reason","category":"bullying"}'
        )
    )
    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=target,
        content_classifier=ContentClassifier.from_yaml(ContentClassifierPaths.HARMFUL_CONTENT_CLASSIFIER.value),
    )
    live = (await scorer.score_text_async("candidate response"))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]

    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert replay.score_value == live.score_value
    assert replay.score_category == live.score_category


async def test_llamaguard_replay_preserves_live_result(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockLlamaGuardTarget")
    target.send_prompt_async = AsyncMock(return_value=_response("unsafe\nS1"))
    scorer = LlamaGuardScorer(chat_target=target)
    live = (await scorer.score_text_async("candidate response"))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]

    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert replay.score_value == live.score_value
    assert replay.score_metadata == live.score_metadata


async def test_shieldgemma_replay_preserves_live_metadata(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockShieldGemmaTarget")
    target.send_prompt_async = AsyncMock(return_value=_response("Yes, this is harmful."))
    scorer = ShieldGemmaScorer(
        chat_target=target,
        guideline=ShieldGemmaGuideline(
            name="Custom harm",
            description="harmful content.",
        ),
    )
    live = (await scorer.score_text_async("candidate response"))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]

    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert replay.score_metadata == live.score_metadata


async def test_shieldgemma_duplicate_replay_preserves_live_metadata(
    sqlite_instance: MemoryInterface,
):
    original = MessagePiece(
        role="assistant",
        original_value="candidate response",
        conversation_id=str(uuid.uuid4()),
        sequence=0,
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[original])
    sqlite_instance.duplicate_conversation(conversation_id=original.conversation_id)
    duplicate = next(piece for piece in sqlite_instance.get_message_pieces() if piece.id != original.id)
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockShieldGemmaTarget")
    target.send_prompt_async = AsyncMock(return_value=_response("Yes, this is harmful."))
    scorer = ShieldGemmaScorer(
        chat_target=target,
        guideline=ShieldGemmaGuideline(
            name="Custom harm",
            description="harmful content.",
        ),
    )

    live = (await scorer.score_async(scorable=MessageScorable(message_piece_ids=(duplicate.id,))))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]
    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert observation.payload.scored_piece_id == duplicate.id
    assert replay.score_metadata == live.score_metadata


async def test_shieldgemma_ephemeral_duplicate_replay_preserves_live_metadata(
    sqlite_instance: MemoryInterface,
):
    original_prompt_id = uuid.uuid4()
    duplicate = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="candidate response",
                original_prompt_id=original_prompt_id,
            )
        ]
    ).duplicate()
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockShieldGemmaTarget")
    target.send_prompt_async = AsyncMock(return_value=_response("Yes, this is harmful."))
    scorer = ShieldGemmaScorer(
        chat_target=target,
        guideline=ShieldGemmaGuideline(
            name="Custom harm",
            description="harmful content.",
        ),
    )

    live = (await scorer.score_message_async(message=duplicate))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]
    replay = (await scorer.score_observation_async(observation=observation))[0]

    assert observation.payload.scored_piece_id == duplicate.get_piece().id
    assert observation.metadata["shieldgemma_scope"] == str(original_prompt_id)
    assert replay.score_metadata == live.score_metadata


async def test_composite_persists_only_final_score_with_child_observation(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[_scorer(target=target)],
    )

    scores = await composite.score_async(
        scorable=ContentScorable(value="candidate response"),
        expectation=ScoringExpectation(objective="Judge this response"),
    )

    assert len(sqlite_instance._query_entries(ScoreEntry)) == 1
    assert len(scores[0].observation_ids) == 1
    assert sqlite_instance.get_observations(observation_ids=scores[0].observation_ids)


async def test_llm_observation_rejects_changed_expectation(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Original expectation")
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]

    with pytest.raises(NonReplayableObservationError, match="exact expectation"):
        await scorer.score_observation_async(
            observation=observation,
            expectation=ScoringExpectation(objective="Changed expectation"),
        )

    assert target.send_prompt_async.call_count == 1
    assert len(sqlite_instance._query_entries(ScoreEntry)) == 1


async def test_generic_replay_delegates_compatibility_to_the_matcher(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    llm_scorer = _scorer(target=target)
    live = (
        await llm_scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=ScoringExpectation(objective="Original expectation"),
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]
    scorer = _MatchesObjectiveScorer()
    expectation = ScoringExpectation(objective="Different expectation")
    result = Score(score_value="true", score_type="true_false")

    with patch.object(scorer, "_score_observation", return_value=[result]) as match:
        replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]

    match.assert_called_once()
    assert match.call_args.kwargs["expectation"] == expectation
    assert replay.scored_expectation == expectation
    assert replay.observation_ids == [observation.id]
    assert target.send_prompt_async.call_count == 1


async def test_llm_observation_rejects_modified_caller_copy(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Original expectation")
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    modified = observation.model_copy(update={"metadata": {"modified": "true"}})

    with pytest.raises(NonReplayableObservationError, match="canonical stored evidence"):
        await scorer.score_observation_async(
            observation=modified,
            expectation=expectation,
        )


async def test_llm_observation_rejects_modified_referenced_response(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Original expectation")
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    response_piece = sqlite_instance.get_message_pieces(prompt_ids=list(observation.payload.message_piece_ids))[0]
    changed_value = '{"score_value":"false","description":"changed","rationale":"changed"}'
    with pytest.raises(ValueError, match="immutable scorer observation evidence"):
        sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=response_piece.conversation_id,
            update_fields={"converted_value": changed_value},
        )
    _force_conversation_value_update(
        memory=sqlite_instance,
        conversation_id=response_piece.conversation_id,
        converted_value=changed_value,
    )

    with pytest.raises(NonReplayableObservationError, match="modified message pieces"):
        await scorer.score_observation_async(
            observation=observation,
            expectation=expectation,
        )


async def test_llm_observation_rejects_modified_scored_evidence(
    sqlite_instance: MemoryInterface,
):
    input_piece = MessagePiece(
        role="assistant",
        original_value="candidate response",
        conversation_id=str(uuid.uuid4()),
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[input_piece])
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Original expectation")
    score = (
        await scorer.score_async(
            scorable=MessageScorable(message_piece_ids=(input_piece.id,)),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]

    with pytest.raises(ValueError, match="immutable scorer observation evidence"):
        sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=input_piece.conversation_id,
            update_fields={"converted_value": "modified response"},
        )
    _force_conversation_value_update(
        memory=sqlite_instance,
        conversation_id=input_piece.conversation_id,
        converted_value="modified response",
    )

    with pytest.raises(NonReplayableObservationError, match="modified scored evidence"):
        await scorer.score_observation_async(
            observation=observation,
            expectation=expectation,
        )


async def test_llm_observation_rejects_evidence_changed_after_resolution(
    sqlite_instance: MemoryInterface,
):
    input_piece = MessagePiece(
        role="assistant",
        original_value="candidate response",
        conversation_id=str(uuid.uuid4()),
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[input_piece])
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    score_piece_started = asyncio.Event()
    continue_scoring = asyncio.Event()
    score_piece_async = scorer._score_piece_async

    async def _delayed_score_piece_async(
        message_piece: MessagePiece,
        *,
        objective: str | None = None,
    ) -> list[Score]:
        score_piece_started.set()
        await continue_scoring.wait()
        return await score_piece_async(message_piece, objective=objective)

    with patch.object(scorer, "_score_piece_async", new=_delayed_score_piece_async):
        scoring_task = asyncio.create_task(
            scorer.score_async(
                scorable=MessageScorable(message_piece_ids=(input_piece.id,)),
                expectation=ScoringExpectation(objective="Judge this response"),
            )
        )
        await score_piece_started.wait()
        sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=input_piece.conversation_id,
            update_fields={
                "original_value": "changed response",
                "converted_value": "changed response",
            },
        )
        continue_scoring.set()
        with pytest.raises(ValueError, match="modified scored evidence"):
            await scoring_task

    assert sqlite_instance._query_entries(ObservationEntry) == []
    assert sqlite_instance._query_entries(ScoreEntry) == []


async def test_llm_observation_rejects_different_scorer_configuration(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    scorer = _scorer(target=target)
    expectation = ScoringExpectation(objective="Original expectation")
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    other_target = MagicMock()
    other_scorer = _scorer(target=other_target, target_name="OtherTarget")

    with pytest.raises(NonReplayableObservationError, match="scorer configuration"):
        await other_scorer.score_observation_async(
            observation=observation,
            expectation=expectation,
        )

    other_target.send_prompt_async.assert_not_called()


async def test_llm_leaf_replays_full_composite_expectation(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(return_value=_response(_VALID_RESPONSE))
    leaf = _scorer(target=target)
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[leaf, _MatchesObjectiveScorer()],
    )
    expectation = ScoringExpectation(
        objective="Original expectation",
        conditions=(MatchesObjective(),),
    )
    score = (
        await composite.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]

    replay = (
        await leaf.score_observation_async(
            observation=observation,
            expectation=expectation,
        )
    )[0]

    assert replay.score_value == "true"


async def test_blocked_llm_fallback_retains_error_observation(
    sqlite_instance: MemoryInterface,
):
    target = MagicMock()
    target.send_prompt_async = AsyncMock(
        return_value=[
            Message(
                message_pieces=[
                    MessagePiece(
                        role="assistant",
                        original_value="",
                        original_value_data_type="error",
                        converted_value="",
                        converted_value_data_type="error",
                        response_error="blocked",
                    )
                ]
            )
        ]
    )
    scorer = _scorer(target=target)
    scorer.raise_if_scorer_blocks = False
    expectation = ScoringExpectation(objective="Judge this response")

    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]

    assert score.is_undetermined
    assert observation.acquisition is Acquisition.ERROR
    assert observation.metadata == {"reason": "scorer_response_blocked"}

    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replay.is_undetermined

    scorer.raise_if_scorer_blocks = True
    with pytest.raises(NonReplayableObservationError, match="raise_if_scorer_blocks=False"):
        await scorer.score_observation_async(observation=observation, expectation=expectation)
