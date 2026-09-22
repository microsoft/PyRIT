# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    MessageScorable,
    Observation,
    ScorerTargetResponsePayload,
    ToolEventsObservationPayload,
    ToolExecution,
    TraceCoverage,
    TraceScorable,
    TraceSpanStatus,
)

TRACE_ID = "1" * 32
START_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = TraceScorable(trace_ids=(TRACE_ID,))


def _event(*, trace_id: str = TRACE_ID, end_time: datetime | None = START_TIME) -> ToolExecution:
    return ToolExecution(
        name="lookup",
        trace_id=trace_id,
        span_id="2" * 16,
        start_time=START_TIME,
        end_time=end_time,
        status=TraceSpanStatus.ERROR,
    )


def _observation(*, payload: ToolEventsObservationPayload, acquisition: Acquisition) -> Observation:
    return Observation(
        source_identifier=ComponentIdentifier(class_name="TraceSource", class_module="pyrit.score"),
        acquisition=acquisition,
        scorable=payload.scope,
        payload=payload,
    )


@pytest.mark.parametrize("acquisition", [Acquisition.COMPLETE, Acquisition.PARTIAL, Acquisition.ERROR])
def test_tool_observation_round_trip_preserves_anchor_and_scope(acquisition: Acquisition) -> None:
    scope = SCOPE
    payload = ToolEventsObservationPayload(
        scope=scope,
        events=(_event(),),
        coverage=TraceCoverage(complete=acquisition is Acquisition.COMPLETE),
    )
    observation = _observation(payload=payload, acquisition=acquisition)
    restored = Observation.model_validate_json(observation.model_dump_json())

    assert restored == observation
    assert isinstance(restored.payload, ToolEventsObservationPayload)
    assert restored.scorable == observation.scorable
    assert restored.payload.scope == scope
    assert restored.payload.scope == restored.scorable
    assert restored.payload.events[0].status is TraceSpanStatus.ERROR
    assert set(restored.payload.model_dump()) == {
        "kind",
        "schema_version",
        "scope",
        "events",
        "coverage",
        "arguments_retained",
    }
    assert restored.payload.arguments_retained is False
    assert restored.evidence_message_piece_ids == ()
    assert restored.scored_message_piece_id is None
    assert restored.scorable_content_id is None
    restored.validate_evidence(message_pieces={})


def test_tool_payload_serializes_no_argument_capture_by_default() -> None:
    payload = ToolEventsObservationPayload(scope=SCOPE)
    assert payload.arguments_retained is False
    assert payload.model_dump(mode="json")["arguments_retained"] is False
    assert ToolEventsObservationPayload.model_validate_json(payload.model_dump_json()) == payload
    assert ToolEventsObservationPayload(scope=SCOPE, arguments_retained=False) == payload


@pytest.mark.parametrize("arguments_retained", [True, 0, 0.0, "false", None])
def test_tool_payload_rejects_argument_capture_and_coercion(arguments_retained: object) -> None:
    with pytest.raises(ValidationError, match="arguments_retained must be false"):
        ToolEventsObservationPayload.model_validate({"scope": SCOPE, "arguments_retained": arguments_retained})


def test_unavailable_observation_preserves_requested_scope() -> None:
    observation = _observation(payload=ToolEventsObservationPayload(scope=SCOPE), acquisition=Acquisition.UNAVAILABLE)
    assert Observation.model_validate_json(observation.model_dump_json()) == observation
    assert observation.payload.scope == SCOPE


def test_complete_empty_observation_is_explicit() -> None:
    payload = ToolEventsObservationPayload(
        scope=TraceScorable(trace_ids=(TRACE_ID,)),
        coverage=TraceCoverage(complete=True),
    )
    observation = _observation(payload=payload, acquisition=Acquisition.COMPLETE)
    assert observation.payload.events == ()
    assert observation.payload.coverage.complete


@pytest.mark.parametrize(
    ("acquisition", "complete"),
    [
        (Acquisition.COMPLETE, False),
        (Acquisition.PARTIAL, True),
        (Acquisition.UNAVAILABLE, True),
        (Acquisition.ERROR, True),
    ],
)
def test_tool_observation_rejects_status_coverage_conflicts(*, acquisition: Acquisition, complete: bool) -> None:
    payload = ToolEventsObservationPayload(
        scope=TraceScorable(trace_ids=(TRACE_ID,)), coverage=TraceCoverage(complete=complete)
    )
    with pytest.raises(ValidationError, match="completeness must agree"):
        _observation(payload=payload, acquisition=acquisition)


@pytest.mark.parametrize(
    "anchor", [MessageScorable(message_piece_ids=(uuid.uuid4(),)), TraceScorable(trace_ids=("3" * 32,))]
)
def test_tool_observation_requires_matching_trace_anchor(anchor: object) -> None:
    observation = _observation(payload=ToolEventsObservationPayload(scope=SCOPE), acquisition=Acquisition.PARTIAL)
    with pytest.raises(ValidationError, match="TraceScorable matching"):
        Observation.model_validate({**observation.model_dump(), "scorable": anchor})


def test_unavailable_observation_rejects_events() -> None:
    payload = ToolEventsObservationPayload(scope=TraceScorable(trace_ids=(TRACE_ID,)), events=(_event(),))
    with pytest.raises(ValidationError, match="cannot contain events"):
        _observation(payload=payload, acquisition=Acquisition.UNAVAILABLE)


def test_tool_payload_requires_scope_for_events_and_complete_coverage() -> None:
    with pytest.raises(ValidationError, match="scope"):
        ToolEventsObservationPayload(events=(_event(),))
    with pytest.raises(ValidationError, match="scope"):
        ToolEventsObservationPayload(coverage=TraceCoverage(complete=True))


def test_tool_payload_rejects_events_outside_scope() -> None:
    with pytest.raises(ValidationError, match="belong to the declared trace scope"):
        ToolEventsObservationPayload(scope=TraceScorable(trace_ids=(TRACE_ID,)), events=(_event(trace_id="3" * 32),))


def test_tool_payload_rejects_duplicate_event_identity_not_duplicate_names() -> None:
    scope = TraceScorable(trace_ids=(TRACE_ID, "3" * 32))
    with pytest.raises(ValidationError, match="unique trace/span identities"):
        ToolEventsObservationPayload(scope=scope, events=(_event(), _event()))

    payload = ToolEventsObservationPayload(scope=scope, events=(_event(), _event(trace_id="3" * 32)))
    assert len(payload.events) == 2


def test_complete_tool_payload_rejects_unfinished_events() -> None:
    with pytest.raises(ValidationError, match="unfinished tool events"):
        ToolEventsObservationPayload(
            scope=TraceScorable(trace_ids=(TRACE_ID,)),
            coverage=TraceCoverage(complete=True),
            events=(_event(end_time=None),),
        )


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_tool_payload_rejects_unsupported_or_coerced_version(version: object) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ToolEventsObservationPayload.model_validate({"scope": SCOPE, "schema_version": version})


@pytest.mark.parametrize("field", ["attributes", "arguments", "results", "credentials"])
def test_tool_payload_rejects_raw_evidence_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ToolEventsObservationPayload.model_validate({"scope": SCOPE, field: "not retained"})


def test_tool_snapshot_is_immutable_and_detached_from_input_list() -> None:
    events = [_event()]
    payload = ToolEventsObservationPayload.model_validate(
        {"scope": TraceScorable(trace_ids=(TRACE_ID,)), "events": events}
    )
    events.clear()
    assert len(payload.events) == 1
    with pytest.raises(ValidationError, match="frozen"):
        payload.events = ()
    with pytest.raises(ValidationError, match="frozen"):
        payload.events[0].name = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        payload.coverage.complete = True


def test_scorer_target_response_round_trip_and_acquisition() -> None:
    piece_id = uuid.uuid4()
    observation = Observation(
        source_identifier=ComponentIdentifier(class_name="Judge", class_module="pyrit.score"),
        acquisition=Acquisition.COMPLETE,
        scorable=MessageScorable(message_piece_ids=(piece_id,)),
        payload=ScorerTargetResponsePayload(
            scored_piece_id=piece_id,
            message_piece_ids=(piece_id,),
            message_piece_digests=("a" * 64,),
            scored_evidence_digest="b" * 64,
            expectation_fingerprint="c" * 64,
        ),
    )
    serialized = observation.model_dump(mode="json")
    assert serialized["payload"]["kind"] == "scorer_target_response"
    assert "coverage" not in serialized["payload"]
    assert Observation.model_validate(serialized).model_dump(mode="json") == serialized

    for acquisition in (Acquisition.PARTIAL, Acquisition.UNAVAILABLE):
        with pytest.raises(ValidationError, match="Scorer target response observations require"):
            Observation.model_validate({**serialized, "acquisition": acquisition})
