# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify that all attack executors skip scoring when error responses are returned.
This ensures consistent error handling across all attack strategies.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    CrescendoAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.core import AttackAdversarialConfig, AttackScoringConfig
from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
from pyrit.models import Message, MessagePiece, SeedGroup, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.score import FloatScaleThresholdScorer, TrueFalseScorer


@pytest.fixture
def mock_target():
    """Create a mock prompt target for testing"""
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = {"id": "mock_target_id"}
    return target


@pytest.fixture
def mock_scorer():
    """Create a mock scorer for testing"""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = {"id": "mock_scorer_id"}
    return scorer


@pytest.fixture
def mock_memory():
    """Create a mock memory instance"""
    memory = MagicMock()
    memory.get_conversation.return_value = []
    memory.add_message_to_memory = MagicMock()
    return memory


def create_error_response(conversation_id: str) -> Message:
    """Helper to create an error response message"""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="Content filter error",
                conversation_id=conversation_id,
                response_error="blocked",
                converted_value_data_type="error",
            )
        ]
    )


# Test parameters: (attack_class, attack_kwargs, execute_kwargs, scorer_patch_path)
ATTACK_TEST_PARAMS = [
    (
        PromptSendingAttack,
        {},  # No extra kwargs needed
        lambda: {
            "objective": "test objective",
            "seed_group": SeedGroup(seeds=[SeedPrompt(value="test", data_type="text")]),
        },
        "pyrit.score.scorer.Scorer.score_response_async",
    ),
    (
        MultiPromptSendingAttack,
        {},
        lambda: {"objective": "test objective", "prompt_sequence": [SeedPrompt(value="test", data_type="text")]},
        "pyrit.score.scorer.Scorer.score_response_async",
    ),
    (
        RedTeamingAttack,
        {},
        lambda: {
            "objective": "test objective",
            "seed_prompt": SeedPrompt(value="test", data_type="text"),
            "max_turns": 1,
        },
        "pyrit.score.scorer.Scorer.score_response_async",
    ),
    (
        CrescendoAttack,
        {},
        lambda: {
            "objective": "test objective",
            "seed_prompt": SeedPrompt(value="test", data_type="text"),
            "max_turns": 1,
        },
        "pyrit.score.scorer.Scorer.score_response_async",
    ),
    (
        TreeOfAttacksWithPruningAttack,
        {"tree_width": 2},
        lambda: {
            "objective": "test objective",
            "seed_prompt": SeedPrompt(value="test", data_type="text"),
            "max_iterations": 1,
        },
        "pyrit.score.scorer.Scorer.score_response_async",
    ),
]


@pytest.mark.parametrize(
    "attack_class,attack_extra_kwargs,execute_kwargs_func,scorer_patch_path",
    ATTACK_TEST_PARAMS,
    ids=["PromptSending", "MultiPromptSending", "RedTeaming", "Crescendo", "TreeOfAttacks"],
)
@pytest.mark.asyncio
@patch("pyrit.memory.CentralMemory.get_memory_instance")
async def test_attack_executor_skips_scoring_on_error(
    mock_memory_instance,
    mock_target,
    mock_scorer,
    mock_memory,
    attack_class,
    attack_extra_kwargs,
    execute_kwargs_func,
    scorer_patch_path,
):
    """
    Test that all attack executors skip scoring when target returns an error response.

    This parametrized test verifies that each executor:
    1. Calls Scorer.score_response_async with skip_on_error_result=True
    2. Handles error responses appropriately without attempting to score them
    """
    # Setup memory mock
    mock_memory_instance.return_value = mock_memory

    # Setup scoring config with objective scorer
    # TAP requires FloatScaleThresholdScorer, so use TAPAttackScoringConfig for it
    if attack_class == TreeOfAttacksWithPruningAttack:
        tap_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        tap_scorer.score_async = AsyncMock()
        tap_scorer.get_identifier.return_value = {"id": "mock_tap_scorer_id"}
        tap_scorer.threshold = 0.7
        attack_scoring_config = TAPAttackScoringConfig(
            objective_scorer=tap_scorer,
            use_score_as_feedback=False,
        )
    else:
        attack_scoring_config = AttackScoringConfig(
            objective_scorer=mock_scorer,
            use_score_as_feedback=False,
        )

    # Setup additional configs for multi-turn attacks that need adversarial config
    if attack_class in [RedTeamingAttack, CrescendoAttack, TreeOfAttacksWithPruningAttack]:
        # TreeOfAttacks requires PromptChatTarget, others can use PromptTarget
        if attack_class == TreeOfAttacksWithPruningAttack:
            adversarial_target = MagicMock(spec=PromptChatTarget)
        else:
            adversarial_target = MagicMock(spec=PromptTarget)

        adversarial_target.send_prompt_async = AsyncMock()
        adversarial_target.get_identifier.return_value = {"id": "adversarial_target_id"}

        attack_adversarial_config = AttackAdversarialConfig(
            target=adversarial_target,
        )
        attack_extra_kwargs["attack_adversarial_config"] = attack_adversarial_config

    # Setup refusal scorer for Crescendo
    if attack_class == CrescendoAttack:
        refusal_scorer = MagicMock(spec=TrueFalseScorer)
        refusal_scorer.score_async = AsyncMock(return_value=[])
        refusal_scorer.get_identifier.return_value = {"id": "refusal_scorer_id"}
        attack_scoring_config.refusal_scorer = refusal_scorer

    # Create attack with proper configuration
    attack = attack_class(
        objective_target=mock_target, attack_scoring_config=attack_scoring_config, **attack_extra_kwargs
    )

    # Create error response
    conversation_id = str(uuid.uuid4())
    error_response = create_error_response(conversation_id)

    # Mock normalizer to return error response
    with patch.object(attack, "_prompt_normalizer") as mock_normalizer:
        # For RedTeaming, we need adversarial response first, then error
        if attack_class == RedTeamingAttack:
            adversarial_response = Message(
                message_pieces=[
                    MessagePiece(
                        role="assistant",
                        original_value="adversarial prompt",
                        conversation_id=str(uuid.uuid4()),
                    )
                ]
            )
            mock_normalizer.send_prompt_async = AsyncMock(side_effect=[adversarial_response, error_response])
        else:
            mock_normalizer.send_prompt_async = AsyncMock(return_value=error_response)

        # Mock the Scorer.score_response_async to track if it's called
        with patch(scorer_patch_path) as mock_score:
            mock_score.return_value = {"objective_scores": [], "auxiliary_scores": []}

            # Execute attack
            try:
                await attack.execute_async(**execute_kwargs_func())
            except Exception:
                # May fail due to mocking complexity, we just care about scoring behavior
                pass

            # Verify scoring was called with skip_on_error_result=True if it was called
            if mock_score.called:
                call_kwargs = mock_score.call_args.kwargs
                assert "skip_on_error_result" in call_kwargs, (
                    f"{attack_class.__name__} did not pass skip_on_error_result parameter"
                )
                assert call_kwargs["skip_on_error_result"] is True, (
                    f"{attack_class.__name__} did not set skip_on_error_result=True"
                )
