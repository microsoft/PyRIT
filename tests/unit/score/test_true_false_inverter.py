# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece, store_message

from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import MessagePiece
from pyrit.score import (
    MessageScorable,
    ScorerPromptValidator,
    SubStringScorer,
    TrueFalseInverterScorer,
)


@pytest.fixture
def image_message_piece() -> MessagePiece:
    return get_image_message_piece()


async def test_score_async_unsupported_data_type_returns_empty(
    patch_central_database, image_message_piece: MessagePiece
):
    sub_scorer = SubStringScorer(substring="test", categories=["new_category"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    request = image_message_piece.to_message()

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(request)))
    assert scores == []

    os.remove(image_message_piece.converted_value)


@pytest.mark.parametrize("sub_string", ["I can't answer", "Not present"])
async def test_substring_scorer_score(patch_central_database, sub_string: str):
    full_text = "blah I can't answer that too"

    sub_scorer = SubStringScorer(substring=sub_string, categories=["new_category"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    score = await scorer.score_text_async(full_text)

    assert len(score) == 1

    # score_value should be the opposite of substring scorer since results are inverted
    assert score[0].score_value != str(sub_string in full_text)
    assert score[0].score_type == "true_false"
    assert score[0].score_category == ["new_category"]
    assert score[0].message_piece_id is None


async def test_substring_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SubStringScorer(substring="string", categories=["new_category"])
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


async def test_inverter_propagates_silent_child(patch_central_database):
    """An inverter cannot invert a verdict that its child did not make."""
    sub_scorer = SubStringScorer(substring="test", categories=["new_category"])
    sub_scorer._validator = ScorerPromptValidator(supported_roles=["assistant"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)
    message = MessagePiece(role="user", original_value="test").to_message()

    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(message)))

    assert scores == []


def test_with_scorer_block_policy_reaches_wrapped_scorer(patch_central_database):
    """The inverter has no policy of its own, so it must hand the policy to its leaf."""
    sub_scorer = SubStringScorer(substring="test")
    sub_scorer.raise_if_scorer_blocks = True
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    scoped = scorer.with_scorer_block_policy(raise_if_scorer_blocks=False)

    assert scoped is not scorer
    assert scoped._scorer.raise_if_scorer_blocks is False
    assert sub_scorer.raise_if_scorer_blocks is True


def test_with_scorer_block_policy_returns_self_when_already_compliant(patch_central_database):
    """Returning self keeps shared instances from being copied for no reason."""
    sub_scorer = SubStringScorer(substring="test")
    sub_scorer.raise_if_scorer_blocks = True
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    assert scorer.with_scorer_block_policy(raise_if_scorer_blocks=True) is scorer
