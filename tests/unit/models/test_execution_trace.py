# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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
    TraceCoverage,
    TraceScorable,
)

TRACE = "1" * 32
OTHER_TRACE = "2" * 32
START = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = TraceScorable(trace_ids=(TRACE,))
SOURCE = ComponentIdentifier(class_name="Source", class_module=__name__)


def _step(
    index: int, *, span: str | None = None, trace: str = TRACE, ended: bool = True, **fields: object
) -> ExecutionStep:
    return ExecutionStep(
        index=index,
        trace_id=trace,
        span_id=span or f"{index + 1:016x}",
        start_time=START + timedelta(seconds=index),
        end_time=START + timedelta(seconds=index + 1) if ended else None,
        kind=ExecutionStepKind.TOOL,
        name="lookup",
        **fields,
    )


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"steps": (_step(1),)}, "indexes must match"),
        ({"steps": (_step(0, span="a" * 16), _step(1, span="a" * 16))}, "unique trace/span"),
        ({"steps": (_step(0, trace=OTHER_TRACE),)}, "declared trace scope"),
        ({"steps": (_step(0, ended=False),), "coverage": TraceCoverage(complete=True)}, "unfinished"),
        ({"schema_version": 2}, "schema_version"),
    ],
)
def test_payload_rejects_inconsistent_snapshots(fields: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ExecutionTraceObservationPayload(scope=SCOPE, **fields)


@pytest.mark.parametrize(
    ("acquisition", "scorable", "coverage", "steps", "error"),
    [
        (Acquisition.COMPLETE, SCOPE, TraceCoverage(complete=False), (), "must agree"),
        (Acquisition.PARTIAL, SCOPE, TraceCoverage(complete=True), (), "must agree"),
        (Acquisition.UNAVAILABLE, SCOPE, TraceCoverage(reasons=("gone",)), (_step(0),), "cannot contain steps"),
        (
            Acquisition.PARTIAL,
            TraceScorable(trace_ids=(OTHER_TRACE,)),
            TraceCoverage(reasons=("pending",)),
            (),
            "TraceScorable matching",
        ),
    ],
)
def test_observation_requires_acquisition_and_scope_to_agree_with_payload(
    acquisition: Acquisition,
    scorable: TraceScorable,
    coverage: TraceCoverage,
    steps: tuple[ExecutionStep, ...],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        Observation(
            source_identifier=SOURCE,
            acquisition=acquisition,
            scorable=scorable,
            payload=ExecutionTraceObservationPayload(scope=SCOPE, steps=steps, coverage=coverage),
        )


def test_observation_round_trips_through_json() -> None:
    observation = Observation(
        source_identifier=SOURCE,
        acquisition=Acquisition.COMPLETE,
        scorable=SCOPE,
        payload=ExecutionTraceObservationPayload(
            scope=SCOPE,
            steps=(_step(0, tool_name="lookup", input_content='{"q": 1}', output_content="ok", error_content="e"),),
            coverage=TraceCoverage(complete=True),
        ),
    )
    assert Observation.model_validate_json(observation.model_dump_json()) == observation
