# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end coverage that TAP and PAIR still run their first attack turn when a prepended
conversation is supplied. Prepended messages are persisted in the objective target
conversation, but they are history rather than attack turns: the first live turn must still
set the adversarial system prompt and honor ``next_message``.
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackScoringConfig,
    PAIRAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackOutcome, Message, MessagePiece, Score
from pyrit.score import FloatScaleThresholdScorer
from pyrit.score.float_scale.float_scale_scorer import MessageFloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class _AdversarialTarget(MockPromptTarget):
    """Adversarial chat that records its system prompts and replies with schema-valid JSON."""

    def __init__(self) -> None:
        super().__init__()
        self.system_prompts: list[str] = []

    async def set_system_prompt_async(self, *, system_prompt: str, conversation_id: str) -> None:
        self.system_prompts.append(system_prompt)
        await super().set_system_prompt_async(system_prompt=system_prompt, conversation_id=conversation_id)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        reply = json.dumps(
            {
                "next_message": f"attack prompt {len(self.prompt_sent)}",
                "rationale": "r",
                "last_response_summary": "s",
            }
        )
        return [
            MessagePiece(
                role="assistant", original_value=reply, conversation_id=message.message_pieces[0].conversation_id
            ).to_message()
        ]


class _FixedFloatScorer(MessageFloatScaleScorer):
    def __init__(self, *, value: str) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))
        self._value = value

    def _build_identifier(self):
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value=self._value,
                score_type="float_scale",
                score_category=["test"],
                score_rationale="rationale",
                score_metadata=None,
                message_piece_id=message_piece.id,
                score_value_description="description",
                scorer_class_identifier=self.get_identifier(),
            )
        ]


def _build_attack(attack_cls, *, objective_target, adversarial_chat, score_value: str):
    scorer = FloatScaleThresholdScorer(scorer=_FixedFloatScorer(value=score_value), threshold=0.7)
    kwargs = (
        {"branching_factor": 2, "on_topic_checking_enabled": False}
        if attack_cls is TreeOfAttacksWithPruningAttack
        else {}
    )
    return attack_cls(
        objective_target=objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        tree_width=2,
        tree_depth=3,
        **kwargs,
    )


@pytest.mark.parametrize("attack_cls", [TreeOfAttacksWithPruningAttack, PAIRAttack])
async def test_system_only_prepended_conversation_still_attacks(attack_cls, sqlite_instance):
    objective_target = MockPromptTarget()
    adversarial_chat = _AdversarialTarget()
    attack = _build_attack(
        attack_cls, objective_target=objective_target, adversarial_chat=adversarial_chat, score_value="0.9"
    )

    result = await attack.execute_async(
        objective="do X", prepended_conversation=[Message.from_system_prompt("You are a helpful assistant.")]
    )

    assert objective_target.prompt_sent, "no prompt reached the objective target"
    assert adversarial_chat.system_prompts, "the adversarial system prompt was never set"
    assert result.outcome == AttackOutcome.SUCCESS


@pytest.mark.parametrize("attack_cls", [TreeOfAttacksWithPruningAttack, PAIRAttack])
async def test_prepended_conversation_sets_adversarial_system_prompt_with_context(attack_cls, sqlite_instance):
    objective_target = MockPromptTarget()
    adversarial_chat = _AdversarialTarget()
    attack = _build_attack(
        attack_cls, objective_target=objective_target, adversarial_chat=adversarial_chat, score_value="0.1"
    )
    prepended = [
        Message.from_prompt(prompt="PRIOR-USER-TURN", role="user"),
        Message.from_prompt(prompt="PRIOR-ASSISTANT-TURN", role="assistant"),
    ]

    await attack.execute_async(objective="do X", prepended_conversation=prepended)

    assert adversarial_chat.system_prompts
    assert all("PRIOR-ASSISTANT-TURN" in system_prompt for system_prompt in adversarial_chat.system_prompts)


@pytest.mark.parametrize("sibling_sends_first", [False, True])
async def test_prepended_conversation_sends_next_message_first(
    *, sqlite_instance: SQLiteMemory, sibling_sends_first: bool
) -> None:
    objective_target = MockPromptTarget()
    adversarial_chat = _AdversarialTarget()
    attack = _build_attack(
        TreeOfAttacksWithPruningAttack,
        objective_target=objective_target,
        adversarial_chat=adversarial_chat,
        score_value="0.1",
    )
    prepended = [
        Message.from_prompt(prompt="PRIOR-USER-TURN", role="user"),
        Message.from_prompt(prompt="PRIOR-ASSISTANT-TURN", role="assistant"),
    ]

    requests: list[Message] = []
    sibling_sent = asyncio.Event()
    send_async = objective_target._send_prompt_to_target_async

    async def record_send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        if sibling_sends_first and message.get_value() == "MY-CUSTOM-FIRST-PROMPT":
            await asyncio.wait_for(sibling_sent.wait(), timeout=10)
        requests.append(message)
        responses = await send_async(normalized_conversation=normalized_conversation)
        if message.get_value() != "MY-CUSTOM-FIRST-PROMPT":
            sibling_sent.set()
        return responses

    with patch.object(objective_target, "_send_prompt_to_target_async", side_effect=record_send_async):
        await attack.execute_async(
            objective="do X",
            prepended_conversation=prepended,
            next_message=Message.from_prompt(prompt="MY-CUSTOM-FIRST-PROMPT", role="user"),
        )

    seeded_requests = [message for message in requests if message.get_value() == "MY-CUSTOM-FIRST-PROMPT"]
    assert len(seeded_requests) == 1
    seeded_conversation_id = seeded_requests[0].message_pieces[0].conversation_id
    conversation_requests = [
        message for message in requests if message.message_pieces[0].conversation_id == seeded_conversation_id
    ]
    assert conversation_requests[0].get_value() == "MY-CUSTOM-FIRST-PROMPT"
    if sibling_sends_first:
        assert objective_target.prompt_sent[0] != "MY-CUSTOM-FIRST-PROMPT"
