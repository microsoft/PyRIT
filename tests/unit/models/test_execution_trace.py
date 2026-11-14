# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ExecutionStep,
    ExecutionStepKind,
    ExecutionTraceObservationPayload,
    Observation,
    TraceContent,
    TraceCoverage,
    TraceScorable,
)

TRACE = "1" * 32
OTHER_TRACE = "2" * 32
START = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = TraceScorable(trace_ids=(TRACE,))


def _content(value: str, *, retain: bool = True) -> TraceContent:
    return TraceContent.of(value, retain=retain)


def _step(
    index: int, *, span: str | None = None, trace: str = TRACE, ended: bool = True, **fields: object
) -> ExecutionStep:
    return ExecutionStep(
        index=index,
        trace_id=trace,
        span_id=span or f"{index + 1:016x}",
        start_time=START + timedelta(seconds=index),
        end_time=START + timedelta(seconds=index + 1) if ended else None,
        kind=fields.pop("kind", ExecutionStepKind.TOOL),
        name=fields.pop("name", "lookup"),
        **fields,
    )


def test_trace_content_keeps_the_complete_value() -> None:
    value = "x" * 50_000 + " charged_usd=900"
    content = _content(value)
    assert (content.text, content.length) == (value, len(value))
    assert content.sha256 == hashlib.sha256(value.encode()).hexdigest()
    withheld = _content(value, retain=False)
    assert (withheld.text, withheld.length, withheld.sha256) == (None, len(value), content.sha256)


@pytest.mark.parametrize("text", ["abc", "abcdeg"])
def test_trace_content_rejects_text_that_is_not_the_described_value(text: str) -> None:
    described = _content("abcdef")
    with pytest.raises(ValidationError, match="complete value"):
        TraceContent(text=text, length=described.length, sha256=described.sha256)


def test_payload_rejects_index_gaps_duplicates_and_out_of_scope_steps() -> None:
    with pytest.raises(ValidationError, match="indexes must match"):
        ExecutionTraceObservationPayload(scope=SCOPE, steps=(_step(1),))
    with pytest.raises(ValidationError, match="unique trace/span"):
        ExecutionTraceObservationPayload(scope=SCOPE, steps=(_step(0, span="a" * 16), _step(1, span="a" * 16)))
    with pytest.raises(ValidationError, match="declared trace scope"):
        ExecutionTraceObservationPayload(scope=SCOPE, steps=(_step(0, trace=OTHER_TRACE),))


def test_payload_rejects_unfinished_steps_under_complete_coverage() -> None:
    with pytest.raises(ValidationError, match="unfinished"):
        ExecutionTraceObservationPayload(
            scope=SCOPE, steps=(_step(0, ended=False),), coverage=TraceCoverage(complete=True)
        )


def test_payload_enforces_content_policy() -> None:
    with pytest.raises(ValidationError, match="retention policy"):
        ExecutionTraceObservationPayload(
            scope=SCOPE, steps=(_step(0, output_content=_content("secret")),), content_retained=False
        )
    digest_only = _content("secret", retain=False)
    with pytest.raises(ValidationError, match="retention policy"):
        ExecutionTraceObservationPayload(scope=SCOPE, steps=(_step(0, input_content=digest_only),))
    payload = ExecutionTraceObservationPayload(
        scope=SCOPE, steps=(_step(0, output_content=digest_only),), content_retained=False
    )
    assert payload.steps[0].output_content == digest_only


def test_payload_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ExecutionTraceObservationPayload(scope=SCOPE, schema_version=2)


@pytest.mark.parametrize(
    ("acquisition", "coverage", "steps", "error"),
    [
        (Acquisition.COMPLETE, TraceCoverage(complete=False), (), "must agree"),
        (Acquisition.PARTIAL, TraceCoverage(complete=True), (), "must agree"),
        (Acquisition.UNAVAILABLE, TraceCoverage(reasons=("gone",)), (_step(0),), "cannot contain steps"),
    ],
)
def test_observation_requires_acquisition_to_agree_with_payload(
    acquisition: Acquisition, coverage: TraceCoverage, steps: tuple[ExecutionStep, ...], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        Observation(
            source_identifier=ComponentIdentifier(class_name="Source", class_module=__name__),
            acquisition=acquisition,
            scorable=SCOPE,
            payload=ExecutionTraceObservationPayload(scope=SCOPE, steps=steps, coverage=coverage),
        )


def test_observation_requires_matching_trace_scope() -> None:
    with pytest.raises(ValidationError, match="TraceScorable matching"):
        Observation(
            source_identifier=ComponentIdentifier(class_name="Source", class_module=__name__),
            acquisition=Acquisition.PARTIAL,
            scorable=TraceScorable(trace_ids=(OTHER_TRACE,)),
            payload=ExecutionTraceObservationPayload(scope=SCOPE, coverage=TraceCoverage(reasons=("pending",))),
        )


def test_observation_round_trips_through_json() -> None:
    observation = Observation(
        source_identifier=ComponentIdentifier(class_name="Source", class_module=__name__),
        acquisition=Acquisition.COMPLETE,
        scorable=SCOPE,
        payload=ExecutionTraceObservationPayload(
            scope=SCOPE,
            steps=(_step(0, tool_name="lookup", input_content=_content('{"q": 1}'), output_content=_content("ok")),),
            coverage=TraceCoverage(complete=True),
        ),
    )
    assert Observation.model_validate_json(observation.model_dump_json()) == observation


def test_error_content_follows_the_content_policy() -> None:
    with pytest.raises(ValidationError, match="retention policy"):
        ExecutionTraceObservationPayload(
            scope=SCOPE, steps=(_step(0, error_content=_content("declined")),), content_retained=False
        )
