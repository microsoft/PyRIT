# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from typing import Any

import pytest
from unit.assert_mocks import make_assert_taxonomy
from unit.mocks import MockPromptTarget

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AssertDimensionResult,
    AssertJudgeDimension,
    AssertTaxonomy,
    Message,
    MessagePiece,
    MessageScorable,
    ScoringExpectation,
    ViolatesAssertPolicy,
    construct_response_from_request,
)
from pyrit.score import AssertJudgeScorer, AssertJudgmentMode, LocalTraceCapture, TraceEvidenceRequirement

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _policy(definition: str) -> ViolatesAssertPolicy:
    data = make_assert_taxonomy().model_dump(mode="json")
    data["behavior"]["definition"] = definition
    return ViolatesAssertPolicy(taxonomy=AssertTaxonomy.model_validate(data), application_context="Support desk.")


async def _conversation_async(memory: MemoryInterface) -> MessagePiece:
    conversation_id = str(uuid.uuid4())
    request = MessagePiece(role="user", original_value="What is the return period?", conversation_id=conversation_id)
    reply = MessagePiece(role="assistant", original_value="It is 30 days.", conversation_id=conversation_id)
    await memory.add_message_to_memory_async(request=request.to_message())
    await memory.add_message_to_memory_async(request=reply.to_message())
    return reply


class _Judge(MockPromptTarget):
    """Record each judge system prompt and answer with a fixed verdict."""

    def __init__(self) -> None:
        super().__init__()
        self.systems: list[str] = []

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.systems.append(normalized_conversation[0].get_value())
        payload = {"score_value": True, "description": "d", "rationale": "r", "metadata": "m"}
        return [
            construct_response_from_request(
                request=normalized_conversation[-1].get_piece(), response_text_pieces=[json.dumps(payload)]
            )
        ]


async def test_each_score_judges_the_policy_in_its_expectation_async(sqlite_instance: MemoryInterface) -> None:
    judge = _Judge()
    scorer = AssertJudgeScorer(chat_target=judge, judgment_mode=AssertJudgmentMode.DIRECT)
    reply = await _conversation_async(sqlite_instance)
    evidence = MessageScorable.from_message(reply.to_message())

    scores = []
    for definition in ("Store A policy.", "Store B policy."):
        expectation = ScoringExpectation(objective="Explain returns.", conditions=(_policy(definition),))
        (score,) = await scorer.score_async(scorable=evidence, expectation=expectation)
        scores.append((score, expectation))

    assert ["Store A policy." in judge.systems[0], "Store B policy." in judge.systems[1]] == [True, True]
    for score, expectation in scores:
        assert score.get_value() is True
        assert score.score_category == ["policy_violation"]
        assert AssertDimensionResult.from_metadata(metadata=score.score_metadata).dimension == "policy_violation"
        assert (score.scorable, score.message_piece_id) == (evidence, reply.id)
        assert await scorer.find_matching_scores_async(scores=[score], scorable=evidence, expectation=expectation) == [
            score
        ]


async def test_a_policy_condition_is_required_async(sqlite_instance: MemoryInterface) -> None:
    reply = await _conversation_async(sqlite_instance)
    with pytest.raises(ValueError, match="requires one ViolatesAssertPolicy"):
        await AssertJudgeScorer(chat_target=_Judge()).score_async(
            scorable=MessageScorable.from_message(reply.to_message()),
            expectation=ScoringExpectation(objective="Explain returns."),
        )


async def test_custom_dimensions_are_sampled_and_named_async(sqlite_instance: MemoryInterface) -> None:
    judge = _Judge()
    dimension = AssertJudgeDimension(name="budget_overrun", description="Over budget?", rubric="true = over budget")
    scorer = AssertJudgeScorer(chat_target=judge, dimension=dimension, samples=3)
    reply = await _conversation_async(sqlite_instance)

    (score,) = await scorer.score_async(
        scorable=MessageScorable.from_message(reply.to_message()),
        expectation=ScoringExpectation(conditions=(_policy("Book within budget."),)),
    )

    assert len(judge.systems) == 3
    assert score.score_category == ["budget_overrun"]
    assert scorer.dimension_name == "budget_overrun"


def test_identity_records_the_judge_configuration() -> None:
    judge = MockPromptTarget()
    capture = LocalTraceCapture()
    try:
        traced = AssertJudgeScorer(
            chat_target=judge,
            dimension="overrefusal",
            samples=3,
            trace_source=capture.source(),
            trace_requirement=TraceEvidenceRequirement.COMPLETE,
        ).get_identifier()
    finally:
        capture.close()
    plain = AssertJudgeScorer(chat_target=judge).get_identifier()

    assert traced.params["dimension"] == "overrefusal"
    assert (traced.params["samples"], traced.params["trace_requirement"]) == (3, "complete")
    assert "trace_source" in traced.children and "trace_source" not in plain.children
    assert traced.hash != plain.hash


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"samples": 0}, "samples must be a positive integer"),
        ({"trace_requirement": TraceEvidenceRequirement.COMPLETE}, "needs a trace_source"),
    ],
)
def test_invalid_configuration_is_rejected(options: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AssertJudgeScorer(chat_target=MockPromptTarget(), **options)
