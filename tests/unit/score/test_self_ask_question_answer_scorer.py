# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import store_message

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
from pyrit.score import MessageScorable, NonReplayableObservationError, Scorer
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
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()):
        with patch(
            "pyrit.score.true_false.self_ask_question_answer_scorer._run_llm_scoring_async",
            new=AsyncMock(return_value=unvalidated),
        ):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(store_message(message)),
                expectation=ScoringExpectation(objective="2+2=?\nanswer: 4"),
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
        conditions=[AnswerMatches(correct_answer="Paris", correct_answer_index="B")],
    )
    assert scorer.required_conditions() == frozenset()
    assert scorer.matched_conditions() == frozenset({AnswerMatches, MatchesObjective})
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


@pytest.mark.parametrize("expectation", [None, ScoringExpectation()])
async def test_llm_question_answer_requires_answer_or_objective_async(
    mock_chat_target: MagicMock, expectation: ScoringExpectation | None
) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    with pytest.raises(ValueError, match="requires AnswerMatches or an objective"):
        await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)


def test_llm_question_answer_rejects_duplicate_answers(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    answer = AnswerMatches(correct_answer="Paris", correct_answer_index="B")
    with pytest.raises(ValueError, match="2 AnswerMatches"):
        Scorer.validate_expectation_for_scorers(
            scorers=[scorer], expectation=ScoringExpectation(conditions=[answer, answer])
        )


async def test_llm_question_answer_rejects_simultaneous_alternatives_async(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    expectation = ScoringExpectation(
        objective="Answer in German.",
        conditions=(MatchesObjective(), AnswerMatches(correct_answer="Paris")),
    )
    with pytest.raises(ValueError, match="not both"):
        Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=expectation)
    with pytest.raises(ValueError, match="not both"):
        await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation)


@pytest.mark.parametrize("canonical_input", [False, True])
async def test_inferred_objective_is_validated_after_resolution_async(
    sqlite_instance: MemoryInterface, mock_chat_target: MagicMock, canonical_input: bool
) -> None:
    question = "Capital of France? The correct answer is Paris."
    request = MessagePiece(role="user", original_value=question, conversation_id="inferred-qa", sequence=0).to_message()
    sqlite_instance.add_message_to_memory(request=request)
    response = MessagePiece(
        role="assistant",
        original_value="Paris",
        conversation_id=request.get_piece().conversation_id,
        sequence=1,
    ).to_message()
    sqlite_instance.add_message_to_memory(request=response)
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    mock_chat_target.send_prompt_async = AsyncMock(
        return_value=[
            Message.from_prompt(
                prompt='{"score_value":"true","description":"correct","rationale":"Paris matches","metadata":""}',
                role="assistant",
            )
        ]
    )
    with pytest.warns(DeprecationWarning):
        if canonical_input:
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(response), infer_objective_from_request=True
            )
        else:
            scores = await scorer.score_async(response, infer_objective_from_request=True)
    mock_chat_target.send_prompt_async.assert_awaited_once()
    assert question in mock_chat_target.send_prompt_async.call_args.kwargs["message"].get_value()
    assert scores[0].get_value() is True
    assert scores[0].scored_expectation == ScoringExpectation(objective=question)
    [stored] = sqlite_instance.get_scores(score_ids=[scores[0].id])
    assert stored.scored_expectation == scores[0].scored_expectation


async def test_missing_inferred_objective_still_fails_async(mock_chat_target: MagicMock) -> None:
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="requires AnswerMatches or an objective"):
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
        conditions=[AnswerMatches(correct_answer="Paris", correct_answer_index="B")],
    )
    live = (await scorer.score_async(scorable=ContentScorable(value="Paris"), expectation=expectation))[0]
    observation = sqlite_instance.get_observations(observation_ids=live.observation_ids)[0]
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]

    assert replay.get_value() == live.get_value()
    assert replay.scored_expectation == live.scored_expectation == expectation
    assert scorer._judgment_replay_identifier()["answer_condition_version"] == 1
    changed_answer = ScoringExpectation(
        objective=expectation.objective,
        conditions=[AnswerMatches(correct_answer="London", correct_answer_index="A")],
    )
    with pytest.raises(NonReplayableObservationError, match="expectation"):
        await scorer.score_observation_async(observation=observation, expectation=changed_answer)
    mock_chat_target.send_prompt_async.assert_called_once()
