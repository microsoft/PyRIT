# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece, store_message

from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import ComponentIdentifier, MessagePiece, Scorable, Score, ScoringExpectation
from pyrit.score import (
    MessageScorable,
    MessageTrueFalseScorer,
    ScorerPromptValidator,
    SubStringScorer,
    TrueFalseInverterScorer,
    TrueFalseScorer,
)


@pytest.fixture
def image_message_piece() -> MessagePiece:
    return get_image_message_piece()


async def test_score_async_unsupported_data_type_inverts_false_to_true(
    patch_central_database, image_message_piece: MessagePiece
):
    sub_scorer = SubStringScorer(substring="test", categories=["new_category"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    request = image_message_piece.to_message()

    # With raise_on_no_valid_pieces=False (default), the inner scorer returns False,
    # and the inverter inverts it to True
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(request)))
    assert len(scores) == 1
    # Inverter inverts False -> True
    assert scores[0].get_value() is True

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


async def test_inverter_score_message_async_uses_message_capable_child(patch_central_database):
    class AlwaysTrueScorer(MessageTrueFalseScorer):
        def __init__(self):
            super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            return [Score(score_value="true", score_type="true_false", message_piece_id=message_piece.id)]

    scorer = TrueFalseInverterScorer(scorer=AlwaysTrueScorer())

    scores = await scorer.score_message_async(
        message=MessagePiece(role="assistant", original_value="response").to_message()
    )

    assert len(scores) == 1
    assert scores[0].get_value() is False


async def test_inverter_message_api_rejects_non_message_child(patch_central_database):
    class ContentOnlyScorer(TrueFalseScorer):
        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_scorable_async(
            self,
            *,
            scorable: Scorable,
            expectation: ScoringExpectation | None,
        ) -> list[Score]:
            return [Score(score_value="true", score_type="true_false", scorable=scorable)]

    scorer = TrueFalseInverterScorer(scorer=ContentOnlyScorer())

    with pytest.raises(RuntimeError, match="not message-capable"):
        await scorer.score_message_async(message=MessagePiece(role="assistant", original_value="test").to_message())
