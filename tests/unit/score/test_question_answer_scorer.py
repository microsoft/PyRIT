# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import store_message

from pyrit.models import (
    AnswerMatches,
    ContentScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    MessageScorable,
    PromptResponseError,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score import (
    QuestionAnswerScorer,
    Scorer,
    ScorerPromptValidator,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.observation.execution import _scoring_expectation_context

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def expectation() -> ScoringExpectation:
    return ScoringExpectation(
        objective="What is the capital of France?",
        conditions=[AnswerMatches(correct_answer="Paris", correct_answer_index="0")],
    )


@pytest.mark.parametrize(
    ("response", "expected_score"),
    [
        ("0: Paris", True),
        ("Paris", True),
        ("1: London", False),
        ("London", False),
        ("The answer is 0: Paris", True),
        ("The answer is PARIS", True),
    ],
)
async def test_question_answer_scorer_score_async(
    response: str, expected_score: bool, expectation: ScoringExpectation
) -> None:
    scorer = QuestionAnswerScorer(category=["new_category"])
    message = store_message(Message.from_prompt(prompt=response, role="assistant"))

    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)

    assert len(scores) == 1
    assert scores[0].get_value() is expected_score
    assert scores[0].score_type == "true_false"
    assert scores[0].score_category == ["new_category"]
    assert scores[0].scored_expectation == expectation
    assert not message.get_piece().prompt_metadata


@pytest.mark.parametrize("expectation", [None, ScoringExpectation(), ScoringExpectation(objective="Paris")])
async def test_question_answer_requires_typed_condition_async(expectation: ScoringExpectation | None) -> None:
    scorer = QuestionAnswerScorer()
    message = store_message(
        Message.from_prompt(
            prompt="Paris",
            role="assistant",
            prompt_metadata={"correct_answer": "Paris", "correct_answer_index": "0"},
        )
    )
    with pytest.raises(ValueError, match="requires.*AnswerMatches"):
        await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)


async def test_question_answer_ignores_conflicting_metadata_async(expectation: ScoringExpectation) -> None:
    scorer = QuestionAnswerScorer()
    message = store_message(
        Message.from_prompt(
            prompt="Paris",
            role="assistant",
            prompt_metadata={"correct_answer": "London", "correct_answer_index": "1"},
        )
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)
    assert scores[0].get_value() is True
    assert message.get_piece().prompt_metadata["correct_answer"] == "London"


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("criteria", ["missing", "duplicate", "unrelated"])
def test_question_answer_group_validates_before_scoring(
    wrapped: bool, criteria: str, expectation: ScoringExpectation
) -> None:
    leaf = QuestionAnswerScorer()
    scorer = (
        TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[TrueFalseInverterScorer(scorer=leaf), SubStringScorer(substring="Paris")],
        )
        if wrapped
        else leaf
    )
    supplied = {
        "missing": None,
        "duplicate": ScoringExpectation(conditions=expectation.conditions * 2),
        "unrelated": ScoringExpectation(conditions=[MatchesObjective()]),
    }[criteria]
    assert AnswerMatches in scorer.matched_conditions()
    assert AnswerMatches in scorer.required_conditions()
    with pytest.raises(ValueError, match="AnswerMatches|does not match"):
        Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=supplied)


@pytest.mark.parametrize(("response", "expected"), [("[0] PARIS", True), ("0: Paris", False)])
async def test_question_answer_custom_patterns_async(
    response: str, expected: bool, expectation: ScoringExpectation
) -> None:
    scorer = QuestionAnswerScorer(correct_answer_matching_patterns=["[{correct_answer_index}] {correct_answer}"])
    scores = await scorer.score_async(scorable=ContentScorable(value=response), expectation=expectation)
    assert scores[0].get_value() is expected


@pytest.mark.parametrize(("response", "expected"), [("Paris", True), ("0: London", False)])
async def test_question_answer_open_ended_answer_async(response: str, expected: bool) -> None:
    scorer = QuestionAnswerScorer()
    open_ended = ScoringExpectation(conditions=[AnswerMatches(correct_answer="Paris")])

    scores = await scorer.score_async(scorable=ContentScorable(value=response), expectation=open_ended)

    assert scores[0].get_value() is expected


def test_question_answer_rejects_unknown_pattern_field() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        QuestionAnswerScorer(correct_answer_matching_patterns=["{not_a_field}"])


async def test_question_answer_wrappers_forward_full_expectation_async(expectation: ScoringExpectation) -> None:
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[
            TrueFalseInverterScorer(scorer=QuestionAnswerScorer()),
            SubStringScorer(substring="Paris"),
        ],
    )
    scores = await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert scores[0].scored_expectation == expectation


@pytest.mark.parametrize("use_and", [False, True])
async def test_question_answer_preserves_piece_aggregation_async(
    use_and: bool, expectation: ScoringExpectation
) -> None:
    scorer = QuestionAnswerScorer(
        score_aggregator=TrueFalseScoreAggregator.AND if use_and else TrueFalseScoreAggregator.OR
    )
    conversation_id = "qa-piece-aggregation"
    message = store_message(
        Message(
            message_pieces=[
                MessagePiece(role="assistant", original_value=value, conversation_id=conversation_id)
                for value in ["Paris", "London"]
            ]
        )
    )
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)
    assert len(scores) == 1
    assert scores[0].get_value() is (not use_and)


async def test_question_answer_concurrent_expectations_are_isolated_async() -> None:
    scorer = QuestionAnswerScorer()
    expectations = [
        ScoringExpectation(conditions=[AnswerMatches(correct_answer=answer, correct_answer_index=str(index))])
        for index, answer in enumerate(["Paris", "London", "Berlin"])
    ]
    original = scorer._score_piece_with_expectation_async

    async def delayed_leaf_async(message_piece: MessagePiece, *, expectation: ScoringExpectation | None) -> list[Score]:
        await asyncio.sleep(0)
        return await original(message_piece, expectation=expectation)

    with patch.object(scorer, "_score_piece_with_expectation_async", side_effect=delayed_leaf_async):
        results = await asyncio.gather(
            *[
                scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
                for expectation in expectations
            ]
        )
    assert [scores[0].get_value() for scores in results] == [True, False, False]
    assert [scores[0].scored_expectation for scores in results] == expectations


def test_question_answer_legacy_piece_override_fails_clearly() -> None:
    class LegacyQuestionAnswerScorer(QuestionAnswerScorer):
        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            raise AssertionError("The custom policy must not be silently skipped.")

    with pytest.raises(TypeError, match="Move the custom policy to _score_piece_with_expectation_async"):
        LegacyQuestionAnswerScorer()


async def test_question_answer_custom_expectation_hook_runs_async(expectation: ScoringExpectation) -> None:
    class StrictQuestionAnswerScorer(QuestionAnswerScorer):
        async def _score_piece_with_expectation_async(
            self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
        ) -> list[Score]:
            scores = await super()._score_piece_with_expectation_async(message_piece, expectation=expectation)
            scores[0].score_value = "false"
            return scores

    [score] = await StrictQuestionAnswerScorer().score_async(
        scorable=ContentScorable(value="Paris"), expectation=expectation
    )
    assert score.get_value() is False


async def test_aggregation_uses_explicit_expectation_not_observation_context_async(
    expectation: ScoringExpectation,
) -> None:
    scorer = QuestionAnswerScorer()
    unrelated = ScoringExpectation(conditions=(AnswerMatches(correct_answer="London"),))
    with _scoring_expectation_context(unrelated):
        [score] = await scorer._score_async(
            Message.from_prompt(prompt="Paris", role="assistant"),
            objective=expectation.objective,
            expectation=expectation,
        )
    assert score.get_value() is True


async def test_legacy_aggregation_override_cannot_drop_typed_criteria_async(expectation: ScoringExpectation) -> None:
    class LegacyAggregationScorer(QuestionAnswerScorer):
        async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
            raise AssertionError("Typed criteria must not be lost.")

    with pytest.raises(RuntimeError, match="must accept and forward expectation"):
        await LegacyAggregationScorer().score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)


class _LegacyMessageSubstringScorer(SubStringScorer):
    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        return await self._score_piece_async(message.get_piece(), objective=objective)


@pytest.mark.parametrize("scorer_type", [SubStringScorer, _LegacyMessageSubstringScorer])
async def test_legacy_hooks_reject_their_own_typed_criteria_async(
    scorer_type: type[SubStringScorer], expectation: ScoringExpectation
) -> None:
    scorer = scorer_type(substring="Paris")
    with (
        patch.object(scorer_type, "MATCHED_CONDITIONS", frozenset({AnswerMatches})),
        patch.object(scorer, "_score_piece_async", new_callable=AsyncMock) as leaf,
        pytest.raises(RuntimeError, match="matched typed conditions"),
    ):
        await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
    leaf.assert_not_awaited()


@pytest.mark.parametrize("scorer_type", [SubStringScorer, _LegacyMessageSubstringScorer])
async def test_legacy_hooks_ignore_sibling_criteria_async(
    scorer_type: type[SubStringScorer], expectation: ScoringExpectation
) -> None:
    legacy = scorer_type(substring="Paris")
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[QuestionAnswerScorer(), legacy],
    )
    with patch.object(legacy, "_score_piece_async", wraps=legacy._score_piece_async) as leaf:
        [score] = await composite.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
    leaf.assert_awaited_once()
    assert score.get_value() is True
    assert score.scored_expectation == expectation


@pytest.mark.parametrize("scorer_type", [SubStringScorer, _LegacyMessageSubstringScorer])
@pytest.mark.parametrize("conditions", [(), (MatchesObjective(),)])
async def test_legacy_hooks_keep_objective_scoring_async(
    scorer_type: type[SubStringScorer], conditions: tuple[MatchesObjective, ...]
) -> None:
    scorer = scorer_type(substring="Paris")
    expectation = ScoringExpectation(objective="Find Paris", conditions=conditions)
    with patch.object(scorer_type, "MATCHED_CONDITIONS", frozenset({MatchesObjective})):
        [score] = await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
    assert score.get_value() is True
    assert score.scored_expectation == expectation


async def test_question_answer_preserves_role_filter_async(expectation: ScoringExpectation) -> None:
    scorer = QuestionAnswerScorer(
        validator=ScorerPromptValidator(supported_data_types=["text"], supported_roles=["assistant"])
    )
    message = store_message(Message.from_prompt(prompt="Paris", role="user"))
    assert await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation) == []


@pytest.mark.parametrize("error", ["blocked", "processing"])
async def test_question_answer_preserves_error_policy_async(
    error: PromptResponseError, expectation: ScoringExpectation
) -> None:
    scorer = QuestionAnswerScorer()
    message = store_message(
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="unavailable",
                    original_value_data_type="error",
                    response_error=error,
                )
            ]
        )
    )
    with patch.object(scorer, "_score_piece_with_expectation_async", new_callable=AsyncMock) as leaf:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation)
    leaf.assert_not_called()
    assert len(scores) == 1
    assert scores[0].scored_expectation == expectation
    if error == "blocked":
        assert scores[0].get_value() is False
    else:
        assert scores[0].status == ScoreStatus.UNDETERMINED


async def test_question_answer_adds_to_memory_async(expectation: ScoringExpectation) -> None:
    scorer = QuestionAnswerScorer()
    with patch.object(scorer._memory, "add_scores_to_memory", new_callable=MagicMock) as add:
        await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
    add.assert_called_once()


async def test_question_answer_unsupported_type_is_empty_async(expectation: ScoringExpectation) -> None:
    scorer = QuestionAnswerScorer(validator=ScorerPromptValidator(supported_data_types=["image_path"]))
    assert await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation) == []
