# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime
from importlib import import_module

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentEntryScorable,
    ContentScorable,
    MessagePiece,
    MessageScorable,
    Observation,
    Score,
    ScorerTargetResponsePayload,
    TraceScorable,
)
from pyrit.models.score.observation import _content_scorable_digest, _message_piece_digest, _response_piece_digest


def _identifier() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.models")


def _scorable() -> MessageScorable:
    return MessageScorable(message_piece_ids=(uuid.uuid4(),))


@pytest.fixture
def response_observation() -> tuple[Observation, MessagePiece, MessagePiece]:
    scored = MessagePiece(role="assistant", original_value="input")
    response = MessagePiece(role="assistant", original_value="scorer response")
    observation = Observation(
        source_identifier=_identifier(),
        acquisition=Acquisition.COMPLETE,
        scorable=MessageScorable(message_piece_ids=(scored.id,)),
        payload=ScorerTargetResponsePayload(
            scored_piece_id=scored.id,
            message_piece_ids=(response.id,),
            message_piece_digests=(_response_piece_digest(response, include_id=True),),
            scored_evidence_digest=_message_piece_digest(scored, include_id=False),
            expectation_fingerprint="a" * 64,
        ),
    )
    return observation, scored, response


@pytest.mark.parametrize(
    ("anchor", "error"),
    [
        (TraceScorable(trace_ids=("1" * 32,)), "message or content evidence"),
        (ContentScorable(value="image.png", data_type="image_path"), "Media scorer target response"),
        (ContentEntryScorable(content_id=uuid.uuid4(), data_type="image_path"), "Media scorer target response"),
        (ContentScorable(value="changed input"), "modified scored evidence"),
    ],
)
def test_observation_rejects_incompatible_anchor(
    *, response_observation: tuple[Observation, MessagePiece, MessagePiece], anchor: object, error: str
) -> None:
    observation, _, _ = response_observation
    with pytest.raises(ValidationError, match=error):
        Observation.model_validate({**observation.model_dump(), "scorable": anchor})


def test_observation_validates_supplied_evidence(
    response_observation: tuple[Observation, MessagePiece, MessagePiece],
) -> None:
    observation, scored, response = response_observation
    assert observation.response_message_piece_ids == (response.id,)
    assert observation.scored_message_piece_id == scored.id
    assert observation.evidence_message_piece_ids == (response.id, scored.id)
    assert observation.scorable_content_id is None
    observation.validate_evidence(message_pieces={scored.id: scored, response.id: response})


@pytest.mark.parametrize(
    ("scored_evidence", "updates", "error"),
    [
        (True, None, "missing"),
        (True, {"converted_value": "changed"}, "modified scored evidence"),
        (True, {"converted_value_data_type": "image_path"}, "Media scorer target response"),
        (False, None, "missing or modified message pieces"),
        (False, {"converted_value": "changed"}, "modified message pieces"),
    ],
)
def test_observation_rejects_invalid_evidence(
    *,
    response_observation: tuple[Observation, MessagePiece, MessagePiece],
    scored_evidence: bool,
    updates: dict[str, object] | None,
    error: str,
) -> None:
    observation, scored, response = response_observation
    pieces = {scored.id: scored, response.id: response}
    piece = scored if scored_evidence else response
    if updates is None:
        del pieces[piece.id]
    else:
        pieces[piece.id] = piece.model_copy(update=updates)
    with pytest.raises(ValueError, match=error):
        observation.validate_evidence(message_pieces=pieces)


@pytest.mark.parametrize("evidence", ["valid", "missing", "changed_content", "changed_hash", "wrong_type"])
def test_content_observation_validates_supplied_evidence(
    *, response_observation: tuple[Observation, MessagePiece, MessagePiece], evidence: str
) -> None:
    observation, _, response = response_observation
    content = ContentScorable(value="stored input")
    digest = _content_scorable_digest(content)
    anchor = ContentEntryScorable(content_id=uuid.uuid4(), data_type=content.data_type)
    observation = Observation.model_validate(
        {
            **observation.model_dump(),
            "scorable": anchor,
            "payload": observation.payload.model_copy(update={"scored_evidence_digest": digest}),
        }
    )
    assert observation.scorable_content_id == anchor.content_id
    assert observation.scored_message_piece_id is None
    assert observation.evidence_message_piece_ids == (response.id,)
    stored_content = {
        "valid": (content, digest),
        "missing": None,
        "changed_content": (ContentScorable(value="changed"), digest),
        "changed_hash": (content, "b" * 64),
        "wrong_type": (ContentScorable(value="{}", data_type="error"), digest),
    }[evidence]
    if evidence == "valid":
        observation.validate_evidence(message_pieces={response.id: response}, stored_content=stored_content)
    else:
        with pytest.raises(ValueError, match="missing|modified"):
            observation.validate_evidence(message_pieces={response.id: response}, stored_content=stored_content)


@pytest.mark.parametrize("module", ["pyrit.models", "pyrit.models.score", "pyrit.models.score.observation"])
def test_scorer_target_response_payload_public_imports(module: str) -> None:
    assert import_module(module).ScorerTargetResponsePayload is ScorerTargetResponsePayload


def test_scorer_target_response_payload_requires_managed_message_reference() -> None:
    with pytest.raises(ValidationError, match="at least one message piece"):
        ScorerTargetResponsePayload(
            scored_piece_id=uuid.uuid4(),
            message_piece_ids=(),
            message_piece_digests=(),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        )


def test_scorer_target_response_payload_rejects_duplicate_message_reference() -> None:
    piece_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="each message piece once"):
        ScorerTargetResponsePayload(
            scored_piece_id=piece_id,
            message_piece_ids=(piece_id, piece_id),
            message_piece_digests=("b" * 64, "b" * 64),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        )


@pytest.mark.parametrize("acquisition", [Acquisition.COMPLETE, Acquisition.ERROR])
def test_scorer_target_response_observation_accepts_supported_acquisition(acquisition: Acquisition) -> None:
    scorable = _scorable()
    observation = Observation(
        source_identifier=_identifier(),
        acquisition=acquisition,
        scorable=scorable,
        payload=ScorerTargetResponsePayload(
            scored_piece_id=scorable.message_piece_ids[0],
            message_piece_ids=scorable.message_piece_ids,
            message_piece_digests=("b" * 64,),
            scored_evidence_digest="c" * 64,
            expectation_fingerprint="a" * 64,
        ),
    )

    assert observation.acquisition is acquisition
    serialized = observation.model_dump(mode="json")
    assert serialized["payload"]["kind"] == "scorer_target_response"
    restored = Observation.model_validate(serialized)
    assert restored == observation
    assert isinstance(restored.payload, ScorerTargetResponsePayload)
    serialized["payload"].pop("kind")
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        Observation.model_validate(serialized)
    serialized["payload"]["kind"] = "judgment"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Observation.model_validate(serialized)


def test_scorer_target_response_payload_requires_one_digest_per_message_piece() -> None:
    piece_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="one digest per message piece"):
        ScorerTargetResponsePayload(
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
            payload=ScorerTargetResponsePayload(
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
