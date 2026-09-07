# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import ContentScorable, MessagePiece, MessageScorable
from pyrit.score import ManualScorer, ManualTrueFalseScorer


@pytest.mark.usefixtures("patch_central_database")
async def test_manual_scorer_persists_value_and_rationale(sqlite_instance: MemoryInterface) -> None:
    message = MessagePiece(
        role="assistant",
        original_value="response",
        conversation_id=str(uuid.uuid4()),
    ).to_message()
    sqlite_instance.add_message_to_memory(request=message)
    message_id = message.get_piece().id

    scores = await ManualScorer(value=0.75, rationale="Mostly satisfied").score_async(
        scorable=MessageScorable(message_piece_ids=(message_id,)),
    )

    assert len(scores) == 1
    assert scores[0].get_value() == 0.75
    assert scores[0].score_rationale == "Mostly satisfied"
    assert scores[0].score_metadata == {"success_threshold": 0.5}
    assert scores[0].message_piece_id == message_id
    assert sqlite_instance.get_prompt_scores(prompt_ids=[message_id])[0].id == scores[0].id


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_manual_scorer_rejects_value_outside_range(value: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        ManualScorer(value=value, rationale="")


async def test_manual_scorer_rejects_non_message_scorable() -> None:
    scorer = ManualScorer(value=0.5, rationale="")

    with pytest.raises(RuntimeError, match="ManualScorer requires a MessageScorable"):
        await scorer.score_async(scorable=ContentScorable(value="content"))


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("value", [True, False])
async def test_manual_true_false_scorer_persists_value(
    sqlite_instance: MemoryInterface,
    value: bool,
) -> None:
    message = MessagePiece(
        role="assistant",
        original_value="response",
        conversation_id=str(uuid.uuid4()),
    ).to_message()
    sqlite_instance.add_message_to_memory(request=message)
    message_id = message.get_piece().id

    scores = await ManualTrueFalseScorer(value=value, rationale="Human verdict").score_async(
        scorable=MessageScorable(message_piece_ids=(message_id,)),
    )

    assert len(scores) == 1
    assert scores[0].get_value() is value
    assert scores[0].score_type == "true_false"
    assert scores[0].score_rationale == "Human verdict"
    assert sqlite_instance.get_prompt_scores(prompt_ids=[message_id])[0].id == scores[0].id
