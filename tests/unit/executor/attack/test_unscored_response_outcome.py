# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A response the objective scorer cannot score is undetermined, not a defended attack."""

import json
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackScoringConfig,
    AttackStrategy,
    ChunkedRequestAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RedTeamingAttack,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackOutcome, AttackResult, Message, MessagePiece
from pyrit.score import SubStringScorer

ResponseKind = Literal["text", "blocked", "image"]
AttackKind = Literal["prompt_sending", "multi_prompt_sending", "red_teaming"]


class _ReplyTarget(MockPromptTarget):
    """Answer every prompt with one fixed kind of response."""

    def __init__(self, *, kind: ResponseKind, image_path: Path) -> None:
        super().__init__()
        self._kind = kind
        self._image_path = image_path

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
        if self._kind == "blocked":
            piece = MessagePiece(
                role="assistant",
                original_value='{"error": "content_filter"}',
                original_value_data_type="error",
                converted_value_data_type="error",
                response_error="blocked",
                conversation_id=conversation_id,
            )
        elif self._kind == "image":
            piece = MessagePiece(
                role="assistant",
                original_value=str(self._image_path),
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id=conversation_id,
            )
        else:
            piece = MessagePiece(role="assistant", original_value="a harmless answer", conversation_id=conversation_id)
        return [piece.to_message()]


class _AdversarialChat(MockPromptTarget):
    """Reply with the JSON the red-teaming adversarial prompt asks for."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        reply = json.dumps({"next_message": "attack prompt", "rationale": "r", "last_response_summary": "s"})
        conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
        return [MessagePiece(role="assistant", original_value=reply, conversation_id=conversation_id).to_message()]


def _attack(*, attack_kind: AttackKind, target: MockPromptTarget) -> AttackStrategy:
    # A text-only scorer: it scores text and blocked responses, and has nothing to say about an image.
    scoring = AttackScoringConfig(objective_scorer=SubStringScorer(substring="never present", categories=["test"]))
    if attack_kind == "prompt_sending":
        return PromptSendingAttack(objective_target=target, attack_scoring_config=scoring)
    if attack_kind == "multi_prompt_sending":
        return MultiPromptSendingAttack(objective_target=target, attack_scoring_config=scoring)
    return RedTeamingAttack(
        objective_target=target,
        attack_adversarial_config=AttackAdversarialConfig(target=_AdversarialChat()),
        attack_scoring_config=scoring,
        max_turns=1,
    )


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    path = tmp_path / "response.png"
    Image.new("RGB", (4, 4)).save(path)
    return path


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("attack_kind", ["prompt_sending", "multi_prompt_sending", "red_teaming"])
@pytest.mark.parametrize(
    ("response_kind", "expected"),
    [
        ("text", AttackOutcome.FAILURE),
        ("blocked", AttackOutcome.FAILURE),
        ("image", AttackOutcome.UNDETERMINED),
    ],
)
async def test_unscored_response_is_undetermined_async(
    *,
    attack_kind: AttackKind,
    response_kind: ResponseKind,
    expected: AttackOutcome,
    image_path: Path,
    sqlite_instance: SQLiteMemory,
) -> None:
    attack = _attack(attack_kind=attack_kind, target=_ReplyTarget(kind=response_kind, image_path=image_path))
    extra = (
        {"user_messages": [Message.from_prompt(prompt="hello", role="user")]}
        if attack_kind == "multi_prompt_sending"
        else {}
    )

    result = await attack.execute_async(objective="objective", **extra)

    assert isinstance(result, AttackResult)
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
    for outcome in (result, stored):
        assert outcome.outcome is expected
    if expected is AttackOutcome.UNDETERMINED:
        assert result.automated_score is None
        assert stored.outcome_reason == "Objective scorer returned no score"
    else:
        assert result.automated_score is not None
        assert result.automated_score.get_value() is False


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scored", [False, True])
async def test_chunked_request_without_an_objective_score_is_undetermined_async(
    *, scored: bool, sqlite_instance: SQLiteMemory
) -> None:
    scorer = SubStringScorer(substring="never present", categories=["test"])
    attack = ChunkedRequestAttack(
        objective_target=MockPromptTarget(),
        chunk_size=2,
        total_length=4,
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
    )

    if scored:
        result = await attack.execute_async(objective="objective")
    else:
        # The scorer runs and produces nothing for the combined text.
        with patch.object(scorer, "_score_piece_async", new_callable=AsyncMock, return_value=[]):
            result = await attack.execute_async(objective="objective")

    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
    assert stored.outcome is (AttackOutcome.FAILURE if scored else AttackOutcome.UNDETERMINED)
    if not scored:
        assert stored.outcome_reason == "Objective scorer returned no score"
