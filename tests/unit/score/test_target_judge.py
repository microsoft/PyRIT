# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier, store_message

from pyrit.memory import MemoryInterface
from pyrit.models import Contains, MessagePiece, MessageScorable, OutputMatches, ScoringExpectation
from pyrit.prompt_target import PromptTarget, TargetRequirements
from pyrit.score import (
    JsonSchemaResponseHandler,
    MessageScorer,
    NonReplayableObservationError,
    Scorer,
    SelfAskTrueFalseScorer,
)
from pyrit.score.observation.execution import (
    _observation_collection,
    _scoring_expectation_context,
    _scoring_message_context,
    _scoring_scorable_context,
)
from pyrit.score.observation.target_judge import JudgmentRequest, TargetJudge

pytestmark = pytest.mark.usefixtures("patch_central_database")


def test_judgment_request_does_not_capture_ambient_evidence() -> None:
    piece = MessagePiece(role="assistant", original_value="unrelated evidence")
    with (
        _scoring_scorable_context(MessageScorable(message_piece_ids=(piece.id,))),
        _scoring_message_context(piece.to_message()),
    ):
        request = JudgmentRequest(
            expectation=None,
            system_prompt=None,
            value="prepared prompt",
            data_type="text",
            scored_prompt_id=piece.id,
            scorer_identifier=get_mock_target_identifier("Caller"),
        )
    assert request.scorable is None
    assert request.scored_message_piece is None
    with (
        _scoring_scorable_context(MessageScorable(message_piece_ids=(piece.id,))),
        _scoring_message_context(piece.to_message()),
    ):
        captured = MessageScorer._capture_judgment_evidence(request)
    assert captured.scorable == MessageScorable(message_piece_ids=(piece.id,))
    assert captured.scored_message_piece is piece
    assert request.scorable is None
    assert request.scored_message_piece is None


@pytest.mark.parametrize("include_piece", [False, True])
async def test_judge_uses_explicit_evidence_after_context_change_async(
    sqlite_instance: MemoryInterface, include_piece: bool
) -> None:
    messages = [
        store_message(MessagePiece(role="assistant", original_value=value).to_message()) for value in ("A", "B")
    ]
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("ExplicitEvidenceJudge")
    target.send_prompt_async = AsyncMock(
        side_effect=lambda **kwargs: [
            MessagePiece(
                role="assistant",
                original_value='{"score_value":"true","description":"match","rationale":"ok","metadata":""}',
            ).to_message()
        ]
    )
    judge = TargetJudge(target=target, requirements=MagicMock(spec=TargetRequirements))
    requests = [
        JudgmentRequest(
            expectation=ScoringExpectation(objective=f"Judge {message.get_value()}"),
            system_prompt=None,
            value=f"Rendered prompt for {message.get_value()}",
            data_type="text",
            scored_prompt_id=message.message_pieces[0].id,
            scorer_identifier=get_mock_target_identifier("Caller"),
            scorable=MessageScorable.from_message(message),
            scored_message_piece=message.message_pieces[0] if include_piece else None,
        )
        for message in messages
    ]
    unrelated = MessagePiece(role="assistant", original_value="unrelated evidence")
    with (
        _observation_collection() as collector,
        _scoring_scorable_context(MessageScorable(message_piece_ids=(unrelated.id,))),
        _scoring_message_context(unrelated.to_message()),
        _scoring_expectation_context(ScoringExpectation(objective="unrelated criterion")),
    ):
        results = await asyncio.gather(
            *(judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler()) for request in requests)
        )
        scores = [result.to_score(score_value=result.raw_score_value, score_type="true_false") for result in results]
        observations = collector.referenced_by(scores=scores)
    sqlite_instance.add_scores_to_memory(scores=scores, observations=observations)
    assert len(observations) == 2
    for request, score in zip(requests, scores, strict=True):
        assert score.scorable == request.scorable
        assert score.scored_expectation == request.expectation
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        assert observation.scorable == request.scorable
        assert observation.scored_message_piece_id == request.scored_prompt_id


async def test_judge_uses_explicit_criteria_not_ambient_context_async() -> None:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("ExplicitJudge")
    target.send_prompt_async = AsyncMock(
        side_effect=lambda **kwargs: [
            MessagePiece(
                role="assistant",
                original_value='{"score_value":"true","description":"match","rationale":"ok","metadata":""}',
            ).to_message()
        ]
    )
    requirements = MagicMock(spec=TargetRequirements)
    judge = TargetJudge(target=target, requirements=requirements)
    requirements.validate.assert_called_once_with(target=target)
    expectations = [
        ScoringExpectation(objective=value, conditions=(OutputMatches(matcher=Contains(value=value)),))
        for value in ("A", "B")
    ]
    requests = [
        JudgmentRequest(
            expectation=expectation,
            system_prompt="Judge this value.",
            value="candidate",
            data_type="text",
            scored_prompt_id=uuid.uuid4(),
            scorer_identifier=get_mock_target_identifier("Caller"),
        )
        for expectation in expectations
    ]
    with _scoring_expectation_context(ScoringExpectation(objective="wrong ambient criterion")):
        scores = await asyncio.gather(
            *(judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler()) for request in requests)
        )
    assert [score.scored_expectation for score in scores] == expectations
    conversations = [call.kwargs["conversation_id"] for call in target.set_system_prompt.call_args_list]
    assert len(set(conversations)) == 2


async def test_judge_rejects_missing_explicit_evidence_before_send_async() -> None:
    piece_id = uuid.uuid4()
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    judge = TargetJudge(target=target, requirements=MagicMock(spec=TargetRequirements))
    request = JudgmentRequest(
        expectation=None,
        system_prompt=None,
        value="prepared prompt",
        data_type="text",
        scored_prompt_id=piece_id,
        scorer_identifier=get_mock_target_identifier("Caller"),
        scorable=MessageScorable(message_piece_ids=(piece_id,)),
    )
    with pytest.raises(NonReplayableObservationError, match="missing"):
        await judge.judge_async(request=request, response_handler=JsonSchemaResponseHandler())
    target.send_prompt_async.assert_not_awaited()


def test_concrete_scorer_owns_target_validation() -> None:
    target = MagicMock(spec=PromptTarget)
    with patch.object(TargetRequirements, "validate", side_effect=ValueError("unsupported target")):
        with pytest.raises(ValueError, match="unsupported target"):
            SelfAskTrueFalseScorer(chat_target=target)
    assert "chat_target" not in inspect.signature(Scorer.__init__).parameters
    assert "chat_target" not in inspect.signature(MessageScorer.__init__).parameters
