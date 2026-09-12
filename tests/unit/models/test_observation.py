# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    JudgmentObservationPayload,
    MessagePiece,
    MessageScorable,
    Observation,
    Score,
)
from pyrit.models.score.observation import _message_piece_digest, _response_piece_digest


def _identifier() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.models")


def _scorable() -> MessageScorable:
    return MessageScorable(message_piece_ids=(uuid.uuid4(),))


def test_judgment_payload_requires_managed_message_reference():
    with pytest.raises(ValidationError, match="at least one message piece"):
        JudgmentObservationPayload(
            scored_piece_id=uuid.uuid4(),
            message_piece_ids=(),
            message_piece_digests=(),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        )


def test_judgment_payload_rejects_duplicate_message_reference():
    piece_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="each message piece once"):
        JudgmentObservationPayload(
            scored_piece_id=piece_id,
            message_piece_ids=(piece_id, piece_id),
            message_piece_digests=("b" * 64, "b" * 64),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        )


@pytest.mark.parametrize("acquisition", [Acquisition.COMPLETE, Acquisition.ERROR])
def test_judgment_observation_accepts_supported_acquisition(acquisition: Acquisition):
    scorable = _scorable()
    observation = Observation(
        source_identifier=_identifier(),
        acquisition=acquisition,
        scorable=scorable,
        payload=JudgmentObservationPayload(
            scored_piece_id=scorable.message_piece_ids[0],
            message_piece_ids=scorable.message_piece_ids,
            message_piece_digests=("b" * 64,),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        ),
    )

    assert observation.acquisition is acquisition
    serialized = observation.model_dump(mode="json")
    assert serialized["payload"]["kind"] == "judgment"
    assert Observation.model_validate(serialized) == observation


def test_judgment_payload_requires_one_digest_per_message_piece():
    piece_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="one digest per message piece"):
        JudgmentObservationPayload(
            scored_piece_id=piece_id,
            message_piece_ids=(piece_id,),
            message_piece_digests=(),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        )


def test_message_observation_requires_scored_piece_in_anchor():
    anchor_piece_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="must belong"):
        Observation(
            source_identifier=_identifier(),
            acquisition=Acquisition.COMPLETE,
            scorable=MessageScorable(message_piece_ids=(anchor_piece_id,)),
            payload=JudgmentObservationPayload(
                scored_piece_id=uuid.uuid4(),
                message_piece_ids=(uuid.uuid4(),),
                message_piece_digests=("b" * 64,),
                scored_evidence_digest="c" * 64,
                expectation_fingerprint="a" * 64,
            ),
        )


def test_score_with_observation_requires_scorable():
    with pytest.raises(ValidationError, match="requires a scorable anchor"):
        Score(
            score_value="true",
            score_type="true_false",
            observation_ids=[uuid.uuid4()],
        )


def test_score_rejects_duplicate_observation_ids():
    observation_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="each observation once"):
        Score(
            score_value="true",
            score_type="true_false",
            scorable=_scorable(),
            observation_ids=[observation_id, observation_id],
        )


@pytest.mark.parametrize(
    ("field_name", "new_value"),
    [
        ("conversation_id", "other-conversation"),
        ("sequence", 2),
        ("timestamp", datetime(2026, 1, 2, tzinfo=UTC)),
        ("original_prompt_id", uuid.uuid4()),
        ("original_value_sha256", "a" * 64),
        ("converted_value_sha256", "b" * 64),
        ("converter_identifiers", [_identifier()]),
        ("not_in_memory", True),
    ],
)
def test_message_piece_digest_includes_template_visible_state(field_name: str, new_value: object):
    piece = MessagePiece(
        role="assistant",
        original_value="response",
        conversation_id="conversation",
        sequence=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    changed = piece.model_copy(update={field_name: new_value})

    assert _message_piece_digest(piece, include_id=False) != _message_piece_digest(changed, include_id=False)


def test_response_piece_digest_ignores_storage_timestamp_precision():
    piece = MessagePiece(
        role="assistant",
        original_value="response",
        timestamp=datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC),
    )
    rounded = piece.model_copy(update={"timestamp": datetime(2026, 1, 1, 0, 0, 0, 123333, tzinfo=UTC)})

    assert _response_piece_digest(piece, include_id=True) == _response_piece_digest(rounded, include_id=True)
