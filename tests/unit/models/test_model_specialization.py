# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pyrit.models import (
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
    Seed,
    SeedObjective,
    SeedPrompt,
)


class _TaggedPiece(MessagePiece):
    tag: str


class _TaggedScore(Score):
    tag: str


class _TaggedMessage(Message[_TaggedPiece]):
    pass


class _TaggedResult(AttackResult[_TaggedPiece, _TaggedScore]):
    pass


class _ImageSeed(Seed[Literal["image_path"]]):
    pass


def test_message_specialization_retains_piece_fields_through_validation() -> None:
    piece = _TaggedPiece(role="user", original_value="HELLO", tag="request")
    message = _TaggedMessage(message_pieces=[piece])

    restored = _TaggedMessage.model_validate(message.model_dump())

    assert restored.message_pieces[0].tag == "request"
    assert restored.get_piece().converted_value == "HELLO"
    assert Message(message_pieces=[MessagePiece(role="user", original_value="HELLO")]).get_value() == "HELLO"


def test_result_specialization_retains_response_and_score_fields() -> None:
    piece = _TaggedPiece(role="assistant", original_value="HELLO", tag="response", conversation_id=str(uuid4()))
    score = _TaggedScore(
        score_value="true",
        score_type="true_false",
        message_piece_id=piece.id,
        scorer_class_identifier=ComponentIdentifier(class_name="ExampleScorer", class_module="example"),
        tag="score",
    )
    result = _TaggedResult(
        conversation_id=piece.conversation_id,
        objective="Return a greeting",
        last_response=piece,
        automated_score=score,
    )

    restored = _TaggedResult.model_validate(result.model_dump())

    assert restored.last_response is not None
    assert restored.last_response.tag == "response"
    assert restored.automated_score is not None
    assert restored.automated_score.tag == "score"


def test_seed_specialization_validates_defaults_and_preserves_inference() -> None:
    assert Seed(value="HELLO").data_type == "text"
    assert SeedPrompt(value="HELLO", data_type=None).data_type == "text"
    assert _ImageSeed(value="image.png", data_type="image_path").data_type == "image_path"
    with pytest.raises(ValidationError, match="image_path"):
        _ImageSeed(value="image.png")
    with pytest.raises(ValidationError, match="text"):
        SeedObjective(value="Return a greeting", data_type="image_path")
