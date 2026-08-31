# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for what attack executors do when a target returns an error response.

Scoring policy is a scorer capability, so no executor filters a response before scoring.
An unreadable response reaches every scorer, and the message family reports an undetermined
score rather than staying silent.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import store_message

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackParameters,
    AttackScoringConfig,
    ConversationSession,
    CrescendoAttack,
    CrescendoAttackContext,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    SingleTurnAttackContext,
)
from pyrit.models import (
    AttackOutcome,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
    ScoreStatus,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import MessageTrueFalseScorer, ScorerPromptValidator, TrueFalseScorer

OBJECTIVE = "test objective"


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


@pytest.fixture
def mock_target():
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = _mock_target_id("MockTarget")
    return target


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_conversation_messages.return_value = []
    memory.add_message_to_memory = MagicMock()
    return memory


def create_error_response(conversation_id: str) -> Message:
    """
    Build a response that carries a transport error rather than the target's answer.

    Returns:
        Message: A single-piece assistant message flagged as an error.
    """
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="Transport error",
                conversation_id=conversation_id,
                response_error="processing",
                converted_value_data_type="error",
            )
        ]
    )


def _adversarial_config() -> AttackAdversarialConfig:
    adversarial_target = MagicMock(spec=PromptTarget)
    adversarial_target.send_prompt_async = AsyncMock()
    adversarial_target.get_identifier.return_value = _mock_target_id("AdversarialTarget")
    return AttackAdversarialConfig(target=adversarial_target)


def _build_prompt_sending(target, scorer):
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=False),
    )
    return attack, lambda response: attack._evaluate_response_async(response=response, objective=OBJECTIVE)


def _build_multi_prompt_sending(target, scorer):
    attack = MultiPromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=False),
    )
    return attack, lambda response: attack._evaluate_response_async(response=response, objective=OBJECTIVE)


def _build_crescendo(target, scorer):
    refusal_scorer = MagicMock(spec=TrueFalseScorer)
    refusal_scorer.score_async = AsyncMock(return_value=[])
    refusal_scorer.get_identifier.return_value = _mock_scorer_id("RefusalScorer")
    scoring_config = AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=False)
    scoring_config.refusal_scorer = refusal_scorer
    attack = CrescendoAttack(
        objective_target=target,
        attack_adversarial_config=_adversarial_config(),
        attack_scoring_config=scoring_config,
    )

    def invoke(response):
        context = CrescendoAttackContext(
            params=AttackParameters(objective=OBJECTIVE),
            session=ConversationSession(),
            last_response=response,
        )
        return attack._score_response_async(context=context)

    return attack, invoke


ATTACK_BUILDERS = [
    _build_prompt_sending,
    _build_multi_prompt_sending,
    _build_crescendo,
]


@pytest.mark.parametrize(
    "build_attack",
    ATTACK_BUILDERS,
    ids=["PromptSending", "MultiPromptSending", "Crescendo"],
)
@patch("pyrit.memory.CentralMemory.get_memory_instance")
async def test_attack_executor_does_not_filter_error_response(
    mock_memory_instance, mock_target, mock_memory, build_attack
):
    """
    Test that no executor decides scoring policy for the scorers it holds.

    An executor that dropped an error response before scoring would also drop a scorer whose
    evidence never came from that response, so each executor passes the response as it arrived.
    """
    mock_memory_instance.return_value = mock_memory

    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock(return_value=[])
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")

    attack, invoke_scoring = build_attack(mock_target, scorer)
    error_response = create_error_response(str(uuid.uuid4()))

    undetermined = _undetermined_score(error_response)
    with patch(
        "pyrit.score.message_scorer.MessageScorer.score_response_async",
        new=AsyncMock(return_value={"objective_scores": [undetermined], "auxiliary_scores": []}),
    ) as mock_score:
        await invoke_scoring(error_response)

    assert mock_score.await_count == 1, f"{type(attack).__name__} did not score the error response"
    call_kwargs = mock_score.await_args.kwargs
    for retired in ("skip_on_error_result", "role_filter"):
        assert retired not in call_kwargs, f"{type(attack).__name__} still passes the retired '{retired}' parameter"
    assert call_kwargs["response"] is error_response, f"{type(attack).__name__} did not pass the response as it arrived"


def _undetermined_score(response: Message) -> Score:
    """
    Build the score the message family reports when it cannot read the response.

    Returns:
        Score: An undetermined true/false score anchored on the response.
    """
    return Score(
        score_value=None,
        status=ScoreStatus.UNDETERMINED,
        score_value_description="Error response; no verdict was reachable.",
        score_type="true_false",
        score_category=None,
        score_metadata=None,
        score_rationale="Response had an error: processing; no verdict was reachable.",
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
        message_piece_id=response.message_pieces[0].id,
        objective=OBJECTIVE,
    )


class _FallbackTrueFalseScorer(MessageTrueFalseScorer):
    """A scorer that relies on message-family fallback for unreadable evidence."""

    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        raise AssertionError("Unreadable error evidence must not reach the leaf scorer.")


async def test_error_response_produces_undetermined_outcome(mock_target, patch_central_database):
    """
    Test that an error response ends a single-turn attack as undetermined, not as a failure.

    The scorer could not reach a verdict, so the attack reports what it could not determine
    instead of reporting that the objective was not met.
    """
    scorer = _FallbackTrueFalseScorer()
    attack = PromptSendingAttack(
        objective_target=mock_target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer, use_score_as_feedback=False),
        max_attempts_on_failure=0,
    )
    conversation_id = str(uuid.uuid4())
    error_response = store_message(create_error_response(conversation_id))

    score = await attack._evaluate_response_async(response=error_response, objective=OBJECTIVE)
    context = SingleTurnAttackContext(
        params=AttackParameters(objective=OBJECTIVE),
        conversation_id=conversation_id,
    )
    outcome, _ = attack._determine_attack_outcome(response=error_response, score=score, context=context)

    assert score is not None
    assert score.status == ScoreStatus.UNDETERMINED
    assert outcome == AttackOutcome.UNDETERMINED
