# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

from pyrit.models import (
    Score,
    ToolExecution,
    TraceCoverage,
    TraceQuery,
    TraceQueryResult,
    TraceScorable,
    TraceSpan,
    TraceSpanStatus,
    scorable_from_dict,
)

TRACE_ID = "1" * 32
SPAN_ID = "2" * 16
START_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _record_data() -> dict[str, object]:
    return {
        "trace_id": TRACE_ID,
        "span_id": SPAN_ID,
        "start_time": START_TIME,
    }


def _model_data(model_type: type[BaseModel]) -> dict[str, object]:
    data = _record_data()
    if model_type is ToolExecution:
        data["name"] = "lookup"
    return data


def test_trace_scorable_round_trips_through_score() -> None:
    scope = TraceScorable(trace_ids=(TRACE_ID,))
    score = Score(scorable=scope, score_type="true_false", score_value="true")

    assert scorable_from_dict(scope.model_dump(mode="json")) == scope
    assert Score.model_validate_json(score.model_dump_json()).scorable == scope
    assert scope.scorable_type == "trace"


@pytest.mark.parametrize("trace_id", ["0" * 32, "A" * 32, "g" * 32, "1" * 31, "1" * 33, "1" * 31 + "\n", 1])
def test_trace_scorable_rejects_invalid_trace_id(trace_id: object) -> None:
    with pytest.raises(ValidationError):
        TraceScorable.model_validate({"trace_ids": (trace_id,)})


@pytest.mark.parametrize("trace_ids", [(), (TRACE_ID, TRACE_ID)])
def test_trace_scorable_rejects_empty_or_duplicate_trace_ids(trace_ids: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="at least one trace|each trace once"):
        TraceScorable(trace_ids=trace_ids)


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
@pytest.mark.parametrize("field", ["trace_id", "span_id"])
@pytest.mark.parametrize("invalid_id", ["", "0" * 32, "0" * 16, "A" * 32, "A" * 16, "123", 123])
def test_trace_records_reject_invalid_identity(*, model_type: type[BaseModel], field: str, invalid_id: object) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**_model_data(model_type), field: invalid_id})


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
@pytest.mark.parametrize("parent_id", ["0" * 16, "A" * 16, "1" * 15, "1" * 17])
def test_execution_records_validate_parent_identity(*, model_type: type[BaseModel], parent_id: str) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**_model_data(model_type), "parent_span_id": parent_id})


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_trace_records_reject_naive_timestamps(*, model_type: type[BaseModel], field: str) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        model_type.model_validate({**_model_data(model_type), field: START_TIME.replace(tzinfo=None)})


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
def test_trace_records_validate_end_bounds_and_round_trip(model_type: type[BaseModel]) -> None:
    data = _model_data(model_type)
    with pytest.raises(ValidationError, match="end_time must"):
        model_type.model_validate({**data, "end_time": START_TIME - timedelta(microseconds=1)})

    for end in (None, START_TIME, START_TIME + timedelta(seconds=1)):
        record = model_type.model_validate({**data, "end_time": end})
        assert model_type.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
def test_trace_records_are_frozen_and_forbid_extra_fields(model_type: type[BaseModel]) -> None:
    data = _model_data(model_type)
    record = model_type.model_validate(data)
    with pytest.raises(ValidationError, match="frozen"):
        record.span_id = "3" * 16
    with pytest.raises(ValidationError, match="Extra inputs"):
        model_type.model_validate({**data, "credentials": "must not be retained"})


def test_trace_span_defaults_and_json_attributes() -> None:
    span = TraceSpan.model_validate(_record_data())
    other = TraceSpan.model_validate(_record_data())
    span.attributes["nested"] = {"values": ["lookup", 1, 1.2, True, None]}

    assert span.status is TraceSpanStatus.UNSET
    assert span.parent_span_id is None
    assert span.end_time is None
    assert span.sampled
    assert other.attributes == {}
    assert TraceSpan.model_validate_json(span.model_dump_json()) == span


@pytest.mark.parametrize("value", [object(), START_TIME, {"nested": object()}])
def test_trace_span_rejects_non_json_attributes(value: object) -> None:
    with pytest.raises(ValidationError):
        TraceSpan.model_validate({**_record_data(), "attributes": {"invalid": value}})


@pytest.mark.parametrize("status", list(TraceSpanStatus))
def test_tool_execution_preserves_attempt_status(status: TraceSpanStatus) -> None:
    event = ToolExecution.model_validate(
        {
            **_record_data(),
            "name": "lookup",
            "parent_span_id": "3" * 16,
            "call_id": "call-1",
            "status": status,
            "end_time": START_TIME,
        }
    )
    assert ToolExecution.model_validate_json(event.model_dump_json()) == event
    assert event.status is status
    assert set(event.model_dump()) == {
        "name",
        "trace_id",
        "span_id",
        "parent_span_id",
        "call_id",
        "start_time",
        "end_time",
        "status",
    }


@pytest.mark.parametrize("model_type", [TraceSpan, ToolExecution])
def test_execution_rejects_unknown_status(model_type: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**_model_data(model_type), "status": "unknown"})


@pytest.mark.parametrize("name", ["", " ", 1])
def test_tool_execution_rejects_invalid_name(name: object) -> None:
    with pytest.raises(ValidationError):
        ToolExecution.model_validate({**_record_data(), "name": name})


def test_trace_query_result_defaults_to_unknown_coverage() -> None:
    result = TraceQueryResult()
    assert result.available
    assert result.spans == ()
    assert not result.coverage.complete
    assert result.coverage.reasons == ()


def test_unavailable_trace_result_round_trip() -> None:
    result = TraceQueryResult(available=False, coverage=TraceCoverage(reasons=("retention_expired",)))
    assert TraceQueryResult.model_validate_json(result.model_dump_json()) == result
    assert not result.available
    assert not result.coverage.complete
    assert result.spans == ()


@pytest.mark.parametrize("has_spans, complete", [(True, False), (False, True), (True, True)])
def test_unavailable_trace_result_rejects_evidence_or_complete_coverage(*, has_spans: bool, complete: bool) -> None:
    spans = (TraceSpan.model_validate(_record_data()),) if has_spans else ()
    with pytest.raises(ValidationError, match="Unavailable trace results"):
        TraceQueryResult(available=False, spans=spans, coverage=TraceCoverage(complete=complete))


@pytest.mark.parametrize("available", [0, 1, "false", None])
def test_trace_result_requires_boolean_availability(available: object) -> None:
    with pytest.raises(ValidationError):
        TraceQueryResult.model_validate({"available": available})


def test_trace_query_and_result_round_trip() -> None:
    query = TraceQuery(scope=TraceScorable(trace_ids=(TRACE_ID,)), limit=1)
    result = TraceQueryResult(
        spans=(TraceSpan.model_validate(_record_data()),),
        coverage=TraceCoverage(reasons=("capture still running",)),
    )
    assert TraceQuery.model_validate_json(query.model_dump_json()) == query
    assert TraceQueryResult.model_validate_json(result.model_dump_json()) == result


def test_trace_query_defaults_to_bounded_retrieval() -> None:
    assert TraceQuery(scope=TraceScorable(trace_ids=(TRACE_ID,))).limit == 10000


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, "1"])
def test_trace_query_requires_positive_integer_limit(limit: object) -> None:
    with pytest.raises(ValidationError):
        TraceQuery.model_validate({"scope": TraceScorable(trace_ids=(TRACE_ID,)), "limit": limit})


def test_trace_coverage_rejects_conflicting_or_empty_reasons() -> None:
    with pytest.raises(ValidationError, match="cannot have incompleteness reasons"):
        TraceCoverage(complete=True, reasons=("sampled",))
    with pytest.raises(ValidationError, match="nonempty"):
        TraceCoverage(reasons=(" ",))
