# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Condition,
    ContentEntryScorable,
    ContentScorable,
    Equals,
    MessageScorable,
    Observation,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionEvent,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramInvocation,
    ProgramResult,
    Score,
    ScoringExpectation,
    SurfaceScorable,
    scorable_from_dict,
)

_SOURCE = ComponentIdentifier(class_name="FakeSource", class_module="tests.fake")
_DIGEST = "a" * 64
_IMAGE_DIGEST = "sha256:" + "b" * 64


def _event(*, case_index: int = 0, profile: str = "python-3.12", **updates: object) -> ProgramExecutionEvent:
    values: dict[str, object] = {
        "case_index": case_index,
        "runtime_profile": profile,
        "runtime_image_digest": _IMAGE_DIGEST,
        "resolved_command": ("python3", "/program/main.py"),
        "exit_code": 0,
        "stdout": "ok",
        "duration_ms": 5,
    }
    values.update(updates)
    return ProgramExecutionEvent.model_validate(values)


def _execution(*, invocations: int = 2, profile: str = "python-3.12") -> ProgramExecutionScorable:
    return ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="print('ok')"),
        runtime_profile=profile,
        invocations=tuple(ProgramInvocation(arguments=(str(index),)) for index in range(invocations)),
    )


def _observation(
    *,
    scorable: ProgramExecutionScorable,
    acquisition: Acquisition,
    events: tuple[ProgramExecutionEvent, ...] = (),
    reasons: tuple[str, ...] = (),
) -> Observation:
    return Observation(
        source_identifier=_SOURCE,
        acquisition=acquisition,
        scorable=scorable,
        payload=ProgramExecutionObservationPayload(
            program_sha256=_DIGEST if events else None, events=events, reasons=reasons
        ),
    )


def test_program_behaves_round_trips_through_condition_registry() -> None:
    condition = ProgramBehaves(
        runtime_profile="python-3.12",
        cases=(
            ProgramCase(
                invocation=ProgramInvocation(arguments=("3",), stdin="x"),
                expected=ProgramResult(exit_code=None, stdout=Equals(value="9")),
            ),
        ),
    )
    restored = Condition.model_validate(condition.model_dump(mode="json"))
    assert restored == condition
    assert ScoringExpectation(conditions=(condition,)).conditions == (condition,)


def test_program_behaves_invocations_drop_expected_results() -> None:
    condition = ProgramBehaves(
        runtime_profile="python-3.12",
        cases=(
            ProgramCase(invocation=ProgramInvocation(arguments=("1",)), expected=ProgramResult(exit_code=3)),
            ProgramCase(invocation=ProgramInvocation(stdin="in")),
        ),
    )
    assert condition.invocations == (ProgramInvocation(arguments=("1",)), ProgramInvocation(stdin="in"))


def test_program_case_defaults_expect_a_clean_exit_with_no_arguments() -> None:
    case = ProgramCase()
    assert case.invocation == ProgramInvocation()
    assert case.expected == ProgramResult(exit_code=0)


def test_program_behaves_requires_a_case() -> None:
    with pytest.raises(ValidationError, match="at least one case"):
        ProgramBehaves(runtime_profile="python-3.12", cases=())


@pytest.mark.parametrize("profile", ["", "-leading-dash", "has space", "image:tag", "a/b", "x" * 65])
def test_runtime_profile_must_be_a_plain_name(profile: str) -> None:
    with pytest.raises(ValidationError):
        ProgramBehaves(runtime_profile=profile, cases=(ProgramCase(),))


def test_invocation_arguments_reject_nul() -> None:
    with pytest.raises(ValidationError):
        ProgramInvocation(arguments=("a\x00b",))


def test_timed_out_event_cannot_report_an_exit_code() -> None:
    with pytest.raises(ValidationError, match="timed-out"):
        _event(timed_out=True, exit_code=0)
    assert _event(timed_out=True, exit_code=None).output_truncated is False
    assert _event(stderr_truncated=True).output_truncated is True


def test_execution_scorable_round_trips_as_a_stored_scorable() -> None:
    message_program = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        runtime_profile="python-3.12",
        invocations=(ProgramInvocation(),),
    )
    for scorable in (message_program, _execution()):
        assert scorable_from_dict(scorable.model_dump(mode="json")) == scorable


def test_execution_scorable_accepts_stored_text_content() -> None:
    stored = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentEntryScorable(content_id=uuid.uuid4()),
        runtime_profile="python-3.12",
        invocations=(ProgramInvocation(),),
    )
    assert scorable_from_dict(stored.model_dump(mode="json")) == stored
    with pytest.raises(ValidationError, match="text"):
        ProgramExecutionScorable(
            execution_id=uuid.uuid4(),
            program=ContentEntryScorable(content_id=uuid.uuid4(), data_type="image_path"),
            runtime_profile="python-3.12",
            invocations=(ProgramInvocation(),),
        )


def test_execution_scorable_requires_text_and_an_invocation() -> None:
    with pytest.raises(ValidationError, match="text"):
        ProgramExecutionScorable(
            execution_id=uuid.uuid4(),
            program=ContentScorable(value="image.png", data_type="image_path"),
            runtime_profile="python-3.12",
            invocations=(ProgramInvocation(),),
        )
    with pytest.raises(ValidationError, match="at least one invocation"):
        ProgramExecutionScorable(
            execution_id=uuid.uuid4(),
            program=ContentScorable(value="print(1)"),
            runtime_profile="python-3.12",
            invocations=(),
        )


def test_execution_scorable_rejects_a_program_that_is_not_text_or_a_message() -> None:
    with pytest.raises(ValidationError):
        ProgramExecutionScorable.model_validate(
            {
                "execution_id": str(uuid.uuid4()),
                "program": SurfaceScorable(uri="/x").model_dump(),
                "runtime_profile": "python-3.12",
                "invocations": [{}],
            }
        )


def test_score_round_trips_an_execution_anchor() -> None:
    scorable = _execution()
    score = Score(
        score_value="true",
        score_type="true_false",
        score_rationale="",
        score_value_description="",
        scorable=scorable,
        scorer_class_identifier=_SOURCE,
    )
    assert Score.model_validate(score.model_dump(mode="json")).scorable == scorable


@pytest.mark.parametrize(
    "indices",
    [(1, 0), (0, 0)],
)
def test_payload_requires_events_once_in_case_order(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="case order"):
        ProgramExecutionObservationPayload(
            program_sha256=_DIGEST, events=tuple(_event(case_index=index) for index in indices)
        )


def test_payload_events_require_the_program_digest() -> None:
    with pytest.raises(ValidationError, match="digest"):
        ProgramExecutionObservationPayload(events=(_event(),))


def test_payload_rejects_a_coerced_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ProgramExecutionObservationPayload.model_validate({"schema_version": "1"})


def test_payload_finds_events_by_case() -> None:
    payload = ProgramExecutionObservationPayload(program_sha256=_DIGEST, events=(_event(case_index=1),))
    assert payload.event_for(1) is payload.events[0]
    assert payload.event_for(0) is None


def test_complete_observation_holds_one_event_per_invocation() -> None:
    scorable = _execution()
    observation = _observation(
        scorable=scorable, acquisition=Acquisition.COMPLETE, events=(_event(case_index=0), _event(case_index=1))
    )
    assert Observation.model_validate(observation.model_dump(mode="json")) == observation
    with pytest.raises(ValidationError, match="Complete program acquisition"):
        _observation(scorable=scorable, acquisition=Acquisition.COMPLETE, events=(_event(case_index=0),))
    with pytest.raises(ValidationError, match="Complete program acquisition"):
        _observation(
            scorable=scorable,
            acquisition=Acquisition.COMPLETE,
            events=(_event(case_index=0), _event(case_index=1)),
            reasons=("gap",),
        )


def test_partial_observation_is_missing_an_invocation() -> None:
    scorable = _execution()
    _observation(scorable=scorable, acquisition=Acquisition.PARTIAL, events=(_event(case_index=0),), reasons=("x",))
    with pytest.raises(ValidationError, match="Partial program acquisition"):
        _observation(
            scorable=scorable, acquisition=Acquisition.PARTIAL, events=(_event(case_index=0), _event(case_index=1))
        )


@pytest.mark.parametrize("acquisition", [Acquisition.ERROR, Acquisition.UNAVAILABLE])
def test_failed_observation_holds_no_events(acquisition: Acquisition) -> None:
    scorable = _execution()
    _observation(scorable=scorable, acquisition=acquisition, reasons=("sandbox down",))
    with pytest.raises(ValidationError, match="cannot contain events"):
        _observation(scorable=scorable, acquisition=acquisition, events=(_event(case_index=0),))


def test_observation_events_must_belong_to_the_scorable() -> None:
    scorable = _execution(invocations=1)
    with pytest.raises(ValidationError, match="belong to the scorable"):
        _observation(scorable=scorable, acquisition=Acquisition.PARTIAL, events=(_event(case_index=1),))
    with pytest.raises(ValidationError, match="runtime profile"):
        _observation(scorable=scorable, acquisition=Acquisition.COMPLETE, events=(_event(profile="node-20"),))


def test_program_payload_requires_an_execution_anchor() -> None:
    with pytest.raises(ValidationError, match="ProgramExecutionScorable"):
        Observation(
            source_identifier=_SOURCE,
            acquisition=Acquisition.ERROR,
            scorable=ContentScorable(value="print(1)"),
            payload=ProgramExecutionObservationPayload(reasons=("x",)),
        )
