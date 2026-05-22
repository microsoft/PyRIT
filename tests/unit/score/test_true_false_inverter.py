# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece

from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece, Score
from pyrit.score import SubStringScorer, TrueFalseInverterScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


@pytest.fixture
def image_message_piece() -> MessagePiece:
    return get_image_message_piece()


async def test_inverter_fallback_for_blocked_message(patch_central_database):
    """Wrapping a generic text-only TrueFalseScorer must yield Score(False) on blocked
    input — the inverter's own fallback ("attack did not succeed"), NOT Score(True)
    produced by inverting the inner scorer's Score(False) fallback (which would
    incorrectly mean "attack succeeded")."""
    sub_scorer = SubStringScorer(substring="anything", categories=["test"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    blocked_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        response_error="blocked",
    )
    request = Message(message_pieces=[blocked_piece])

    scores = await scorer.score_async(request)

    assert len(scores) == 1
    # The inverter's own fallback must fire — NOT the inverted inner fallback.
    assert scores[0].get_value() is False


async def test_score_async_unsupported_data_type_falls_back_to_false(
    patch_central_database, image_message_piece: MessagePiece
):
    """With the corrected semantics, an unsupported data type causes the inner scorer
    to return no scores, and the inverter falls back to False ("attack did not
    succeed") rather than inverting an inner fallback to True."""
    sub_scorer = SubStringScorer(substring="test", categories=["new_category"])
    scorer = TrueFalseInverterScorer(scorer=sub_scorer)

    request = image_message_piece.to_message()

    scores = await scorer.score_async(request)
    assert len(scores) == 1
    # Inverter's own fallback ("attack did not succeed") — NOT inverted inner fallback.
    assert scores[0].get_value() is False

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


async def test_inverter_passes_through_real_scores(patch_central_database):
    """Real True from inner becomes False from inverter, and vice versa."""
    # Inner returns True (substring present) → inverter returns False
    present_scorer = SubStringScorer(substring="hello", categories=["c"])
    inverter_present = TrueFalseInverterScorer(scorer=present_scorer)
    score_present = await inverter_present.score_text_async("hello world")
    assert len(score_present) == 1
    assert score_present[0].get_value() is False

    # Inner returns False (substring absent) → inverter returns True
    absent_scorer = SubStringScorer(substring="goodbye", categories=["c"])
    inverter_absent = TrueFalseInverterScorer(scorer=absent_scorer)
    score_absent = await inverter_absent.score_text_async("hello world")
    assert len(score_absent) == 1
    assert score_absent[0].get_value() is True


async def test_inverter_with_refusal_like_scorer_blocked(patch_central_database):
    """A scorer that, like SelfAskRefusalScorer, returns Score(True) for blocked input
    from its own _score_async (not via fallback) must still be correctly inverted to
    Score(False) by the inverter."""

    class RefusalLikeScorer(TrueFalseScorer):
        """Mimics SelfAskRefusalScorer's blocked-content handling: accepts the error
        data type and returns True via its own _score_async."""

        def __init__(self) -> None:
            super().__init__(
                validator=ScorerPromptValidator(supported_data_types=["text", "error"]),
            )

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_async(  # type: ignore[override]
            self, message: Message, *, objective: Optional[str] = None
        ) -> list[Score]:
            piece = message.message_pieces[0]
            if piece.response_error == "blocked":
                return [
                    Score(
                        score_value="True",
                        score_value_description="Refusal detected",
                        score_metadata=None,
                        score_type="true_false",
                        score_category=["refusal"],
                        score_rationale="Content was filtered, constituting a refusal.",
                        scorer_class_identifier=self.get_identifier(),
                        message_piece_id=piece.id or uuid.uuid4(),
                        objective=objective,
                    )
                ]
            return []

        async def _score_piece_async(
            self, message_piece: MessagePiece, *, objective: Optional[str] = None
        ) -> list[Score]:
            return []

    inner = RefusalLikeScorer()
    inverter = TrueFalseInverterScorer(scorer=inner)

    blocked_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        response_error="blocked",
    )
    request = Message(message_pieces=[blocked_piece])

    scores = await inverter.score_async(request)
    assert len(scores) == 1
    # Inner returned real True for blocked refusal — inverter inverts to False.
    assert scores[0].get_value() is False


async def test_substring_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SubStringScorer(substring="string", categories=["new_category"])
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()
