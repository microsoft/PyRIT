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
