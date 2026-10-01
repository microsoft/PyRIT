# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import store_message_async

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AnswerMatches,
    ComponentIdentifier,
    ContentScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    Score,
    ScoringExpectation,
    UnvalidatedScore,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    MessageScorable,
    NonReplayableObservationError,
    Scorer,
    ScorerPromptValidator,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.self_ask_question_answer_scorer import SelfAskQuestionAnswerScorer

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def mock_chat_target(patch_central_database):
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockChatTarget", class_module="mock")
    return target


async def test_score_async_returns_score_from_unvalidated(mock_chat_target):
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)

    unvalidated = UnvalidatedScore(
        raw_score_value="True",
        score_value_description="answer matches",
        score_category=["question_answering"],
        score_rationale="the response matches the expected answer",
        score_metadata=None,
        scorer_class_identifier=ComponentIdentifier(
            class_name="SelfAskQuestionAnswerScorer",
            class_module="pyrit.score",
        ),
        message_piece_id="abc",
        objective="2+2=?\nanswer: 4",
    )

    message = MessagePiece(role="assistant", original_value="4").to_message()
    with patch.object(scorer._memory, "add_scores_to_memory_async", new=AsyncMock()):
        with patch(
            "pyrit.score.true_false.self_ask_question_answer_scorer._run_llm_scoring_async",
            new=AsyncMock(return_value=unvalidated),
        ):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(await store_message_async(message)),
                expectation=ScoringExpectation(objective="2+2=?", conditions=[AnswerMatches(correct_answer="4")]),
            )

    assert len(scores) == 1
    assert isinstance(scores[0], Score)
    assert scores[0].score_type == "true_false"
    assert scores[0].get_value() is True


@pytest.mark.parametrize("objective", [None, "What is the capital of France?"])
async def test_typed_answer_supplies_judge_ground_truth_async(
    mock_chat_target: MagicMock, objective: str | None
) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    expectation = ScoringExpectation(
        objective=objective,
        conditions=[AnswerMatches(correct_answer="Paris", correct_answer_label="B")],
    )
    assert scorer.condition_type is AnswerMatches
    assert scorer.get_condition_types() == frozenset({AnswerMatches})
    Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=expectation)
    unvalidated = UnvalidatedScore(
        raw_score_value="true",
        score_value_description="correct",
        score_category=["question_answering"],
        score_rationale="Matches Paris",
        score_metadata=None,
        scorer_class_identifier=scorer.get_identifier(),
        message_piece_id=None,
    )
    with patch(
        "pyrit.score.true_false.self_ask_question_answer_scorer._run_llm_scoring_async",
        new_callable=AsyncMock,
        return_value=unvalidated,
    ) as judge:
        scores = await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)

    assert '"B: Paris"' in judge.call_args.kwargs["value"]
    assert "Evaluate against this correct answer." in judge.call_args.kwargs["value"]
    assert scores[0].scored_expectation == expectation
    assert scores[0].get_value() is True


@pytest.mark.parametrize(
    "expectation",
    [
        None,
        ScoringExpectation(),
        ScoringExpectation(objective="Capital of France? Answer: Paris"),
        ScoringExpectation(objective="Capital of France? Answer: Paris", conditions=[MatchesObjective()]),
    ],
)
async def test_llm_question_answer_requires_answer_condition_async(
    mock_chat_target: MagicMock, expectation: ScoringExpectation | None
) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    with patch(
        "pyrit.score.true_false.self_ask_question_answer_scorer._run_llm_scoring_async", new_callable=AsyncMock
    ) as judge:
        with pytest.raises(ValueError, match="requires one AnswerMatches condition"):
            await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
        with pytest.raises(ValueError, match="requires one AnswerMatches condition"):
            await scorer._score_piece_with_expectation_async(
                MessagePiece(role="assistant", original_value="Paris"), expectation=expectation
            )
    judge.assert_not_awaited()


def test_objective_validator_does_not_add_another_condition(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(
        chat_target=mock_chat_target, validator=ScorerPromptValidator(is_objective_required=True)
    )
    assert scorer.condition_type is AnswerMatches
    assert scorer.get_condition_types() == frozenset({AnswerMatches})


async def test_legacy_objective_argument_requires_answer_condition_async(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    mock_chat_target.send_prompt_async = AsyncMock()
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="requires one AnswerMatches condition"):
        await scorer.score_async(
            Message.from_prompt(prompt="Paris", role="assistant"),
            objective="Capital of France? The answer is Paris.",
        )
    mock_chat_target.send_prompt_async.assert_not_awaited()


def test_llm_question_answer_rejects_duplicate_answers(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    answer = AnswerMatches(correct_answer="Paris", correct_answer_label="B")
    with pytest.raises(ValueError, match="2 AnswerMatches"):
        Scorer.validate_expectation_for_scorers(
            scorers=[scorer], expectation=ScoringExpectation(conditions=[answer, answer])
        )


@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize("objective_met", [False, True])
async def test_llm_question_answer_requires_composition_for_objective_condition_async(
    mock_chat_target: MagicMock, composite: bool, objective_met: bool
) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    mock_chat_target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt='{"score_value":"true","description":"correct","rationale":"Paris matches","metadata":""}',
                role="assistant",
            )
        ]
    )
    expectation = ScoringExpectation(
        objective="Answer in German.",
        conditions=(MatchesObjective(), AnswerMatches(correct_answer="Paris")),
    )
    with pytest.raises(ValueError, match="does not support.*MatchesObjective"):
        Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=expectation)

    objective_target = MagicMock(spec=PromptTarget)
    objective_target.get_identifier.return_value = ComponentIdentifier(
        class_name="ObjectiveChatTarget", class_module="mock"
    )
    objective_target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt=(
                    f'{{"score_value":"{str(objective_met).lower()}",'
                    '"description":"language","rationale":"Objective judgment","metadata":""}'
                ),
                role="assistant",
            )
        ]
    )
    objective_scorer = SelfAskTrueFalseScorer(
        chat_target=objective_target, validator=ScorerPromptValidator(is_objective_required=True)
    )
    root = (
        TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[scorer, objective_scorer])
        if composite
        else scorer
    )
    if composite:
        Scorer.validate_expectation_for_scorers(scorers=[root], expectation=expectation)
    else:
        with pytest.raises(ValueError, match="does not support.*MatchesObjective"):
            await root.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)
        mock_chat_target.send_prompt_async.assert_not_awaited()
        objective_target.send_prompt_async.assert_not_awaited()
        return
    [score] = await root.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)

    assert score.get_value() is (objective_met if composite else True)
    assert score.scored_expectation == expectation
    mock_chat_target.send_prompt_async.assert_awaited_once()
    question_prompt = mock_chat_target.send_prompt_async.call_args.kwargs["message"].get_value()
    assert 'The correct answer is "Paris".' in question_prompt
    assert "not as a separate success criterion" in question_prompt
    if composite:
        objective_target.send_prompt_async.assert_awaited_once()
        assert "Answer in German." in objective_target.send_prompt_async.call_args.kwargs["message"].get_value()
    else:
        objective_target.send_prompt_async.assert_not_awaited()


@pytest.mark.parametrize("has_answer", [False, True])
@pytest.mark.parametrize("canonical_input", [False, True])
async def test_inferred_objective_is_context_not_ground_truth_async(
    sqlite_instance: MemoryInterface, mock_chat_target: MagicMock, canonical_input: bool, has_answer: bool
) -> None:
    question = "Capital of France? The correct answer is Paris."
    request = MessagePiece(role="user", original_value=question, conversation_id="inferred-qa", sequence=0).to_message()
    (await sqlite_instance.add_message_to_memory_async(request=request))
    response = MessagePiece(
        role="assistant",
        original_value="Paris",
        conversation_id=request.get_piece().conversation_id,
        sequence=1,
    ).to_message()
    (await sqlite_instance.add_message_to_memory_async(request=response))
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    mock_chat_target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt='{"score_value":"true","description":"correct","rationale":"Paris matches","metadata":""}',
                role="assistant",
            )
        ]
    )
    expectation = ScoringExpectation(conditions=[AnswerMatches(correct_answer="Paris")]) if has_answer else None

    async def score_async() -> list[Score]:
        if canonical_input:
            return await scorer.score_async(
                scorable=MessageScorable.from_message(response),
                expectation=expectation,
                infer_objective_from_request=True,
            )
        return await scorer.score_async(response, expectation=expectation, infer_objective_from_request=True)

    if not has_answer:
        with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="requires one AnswerMatches condition"):
            await score_async()
        mock_chat_target.send_prompt_async.assert_not_awaited()
        return

    with pytest.warns(DeprecationWarning):
        scores = await score_async()
    mock_chat_target.send_prompt_async.assert_awaited_once()
    assert question in mock_chat_target.send_prompt_async.call_args.kwargs["message"].get_value()
    assert scores[0].get_value() is True
    assert scores[0].scored_expectation == ScoringExpectation(
        objective=question, conditions=[AnswerMatches(correct_answer="Paris")]
    )
    [stored] = await sqlite_instance.get_scores_async(score_ids=[scores[0].id])
    assert stored.scored_expectation == scores[0].scored_expectation


async def test_missing_inferred_objective_still_fails_async(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="requires one AnswerMatches condition"):
        await scorer.score_async(
            Message.from_prompt(prompt="Paris", role="assistant"), infer_objective_from_request=True
        )


async def test_typed_answer_observation_replays_full_expectation_async(
    sqlite_instance: MemoryInterface, mock_chat_target: MagicMock
) -> None:
    mock_chat_target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt='{"score_value":"true","description":"correct","rationale":"Paris matches","metadata":""}',
                role="assistant",
            )
        ]
    )
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    expectation = ScoringExpectation(
        objective="Capital of France?",
        conditions=[AnswerMatches(correct_answer="Paris", correct_answer_label="B")],
    )
    live = (await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=live.observation_ids))[0]
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]

    assert replay.get_value() == live.get_value()
    assert replay.scored_expectation == live.scored_expectation == expectation
    assert scorer._judgment_replay_identifier()["answer_condition_version"] == 2
    changed_answer = ScoringExpectation(
        objective=expectation.objective,
        conditions=[AnswerMatches(correct_answer="London", correct_answer_label="A")],
    )
    with pytest.raises(NonReplayableObservationError, match="expectation"):
        await scorer.score_observation_async(observation=observation, expectation=changed_answer)
    with patch.object(SelfAskQuestionAnswerScorer, "_ANSWER_CONDITION_VERSION", 1):
        old_contract = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
        assert old_contract.get_identifier() != scorer.get_identifier()
        with pytest.raises(NonReplayableObservationError):
            await old_contract.score_observation_async(observation=observation, expectation=expectation)
    mock_chat_target.send_prompt_async.assert_called_once()
