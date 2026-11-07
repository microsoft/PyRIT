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
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramResult,
    Score,
    ScoringExpectation,
    SurfaceScorable,
    scorable_from_dict,
)
from pyrit.models.code_execution import _ProgramExecutionEvent, _ProgramInvocation

_SOURCE = ComponentIdentifier(class_name="FakeSource", class_module="tests.fake")
_DIGEST = "a" * 64
_IMAGE_DIGEST = "sha256:" + "b" * 64


def _event(*, invocation_index: int = 0, profile: str = "python-3.12", **updates: object) -> _ProgramExecutionEvent:
    values: dict[str, object] = {
        "invocation_index": invocation_index,
        "runtime_profile_name": profile,
        "runtime_image_digest": _IMAGE_DIGEST,
        "resolved_command": ("python3", "/program/main.py"),
        "exit_code": 0,
        "stdout": "ok",
        "duration_ms": 5,
    }
    values.update(updates)
    return _ProgramExecutionEvent.model_validate(values)


def _execution(*, invocations: int = 2, profile: str = "python-3.12") -> ProgramExecutionScorable:
    return ProgramExecutionScorable(
        program=ContentScorable(value="print('ok')"),
        runtime_profile_name=profile,
        invocations=tuple(_ProgramInvocation(arguments=(str(index),)) for index in range(invocations)),
    )


def _observation(
    *,
    scorable: ProgramExecutionScorable,
    acquisition: Acquisition,
    events: tuple[_ProgramExecutionEvent, ...] = (),
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
        runtime_profile_name="python-3.12",
        cases=(
            ProgramCase(
                invocation=_ProgramInvocation(arguments=("3",), stdin="x"),
                expected=ProgramResult(exit_code=None, stdout=Equals(value="9")),
            ),
        ),
    )
    restored = Condition.model_validate(condition.model_dump(mode="json"))
    assert restored == condition
    assert ScoringExpectation(conditions=(condition,)).conditions == (condition,)


def test_program_behaves_invocations_drop_expected_results() -> None:
    condition = ProgramBehaves(
        runtime_profile_name="python-3.12",
        cases=(
            ProgramCase(invocation=_ProgramInvocation(arguments=("1",)), expected=ProgramResult(exit_code=3)),
            ProgramCase(invocation=_ProgramInvocation(stdin="in")),
        ),
    )
    assert condition.invocations == (_ProgramInvocation(arguments=("1",)), _ProgramInvocation(stdin="in"))


def test_program_case_defaults_expect_a_clean_exit_with_no_arguments() -> None:
    case = ProgramCase()
    assert case.invocation == _ProgramInvocation()
    assert case.expected == ProgramResult(exit_code=0)


def test_program_behaves_requires_a_case() -> None:
    with pytest.raises(ValidationError, match="at least one case"):
        ProgramBehaves(runtime_profile_name="python-3.12", cases=())


def test_condition_validates_its_runtime_profile_name() -> None:
    with pytest.raises(ValidationError):
        ProgramBehaves(runtime_profile_name="bad name", cases=(ProgramCase(),))


def test_execution_scorable_round_trips_as_a_stored_scorable() -> None:
    message_program = ProgramExecutionScorable(
        program=MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        runtime_profile_name="python-3.12",
        invocations=(_ProgramInvocation(),),
    )
    for scorable in (message_program, _execution()):
        assert scorable_from_dict(scorable.model_dump(mode="json")) == scorable


def test_execution_scorable_accepts_stored_text_content() -> None:
    stored = ProgramExecutionScorable(
        program=ContentEntryScorable(content_id=uuid.uuid4()),
        runtime_profile_name="python-3.12",
        invocations=(_ProgramInvocation(),),
    )
    assert scorable_from_dict(stored.model_dump(mode="json")) == stored
    with pytest.raises(ValidationError, match="text"):
        ProgramExecutionScorable(
            program=ContentEntryScorable(content_id=uuid.uuid4(), data_type="image_path"),
            runtime_profile_name="python-3.12",
            invocations=(_ProgramInvocation(),),
        )


def test_execution_scorable_requires_text_and_an_invocation() -> None:
    with pytest.raises(ValidationError, match="text"):
        ProgramExecutionScorable(
            program=ContentScorable(value="image.png", data_type="image_path"),
            runtime_profile_name="python-3.12",
            invocations=(_ProgramInvocation(),),
        )
    with pytest.raises(ValidationError, match="at least one invocation"):
        ProgramExecutionScorable(
            program=ContentScorable(value="print(1)"),
            runtime_profile_name="python-3.12",
            invocations=(),
        )


def test_execution_scorable_rejects_a_program_that_is_not_text_or_a_message() -> None:
    with pytest.raises(ValidationError):
        ProgramExecutionScorable.model_validate(
            {
                "program": SurfaceScorable(uri="/x").model_dump(),
                "runtime_profile_name": "python-3.12",
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
def test_payload_requires_events_once_in_invocation_order(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="invocation order"):
        ProgramExecutionObservationPayload(
            program_sha256=_DIGEST, events=tuple(_event(invocation_index=index) for index in indices)
        )


def test_payload_events_require_the_program_digest() -> None:
    with pytest.raises(ValidationError, match="digest"):
        ProgramExecutionObservationPayload(events=(_event(),))


def test_payload_rejects_a_coerced_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ProgramExecutionObservationPayload.model_validate({"schema_version": "1"})


def test_complete_observation_holds_one_event_per_invocation() -> None:
    scorable = _execution()
    observation = _observation(
        scorable=scorable,
        acquisition=Acquisition.COMPLETE,
        events=(_event(invocation_index=0), _event(invocation_index=1)),
    )
    assert Observation.model_validate(observation.model_dump(mode="json")) == observation
    with pytest.raises(ValidationError, match="Complete program acquisition"):
        _observation(scorable=scorable, acquisition=Acquisition.COMPLETE, events=(_event(invocation_index=0),))
    with pytest.raises(ValidationError, match="Complete program acquisition"):
        _observation(
            scorable=scorable,
            acquisition=Acquisition.COMPLETE,
            events=(_event(invocation_index=0), _event(invocation_index=1)),
            reasons=("gap",),
        )


def test_partial_observation_is_missing_an_invocation() -> None:
    scorable = _execution()
    _observation(
        scorable=scorable, acquisition=Acquisition.PARTIAL, events=(_event(invocation_index=0),), reasons=("x",)
    )
    with pytest.raises(ValidationError, match="Partial program acquisition"):
        _observation(
            scorable=scorable,
            acquisition=Acquisition.PARTIAL,
            events=(_event(invocation_index=0), _event(invocation_index=1)),
        )


@pytest.mark.parametrize("acquisition", [Acquisition.ERROR, Acquisition.UNAVAILABLE])
def test_failed_observation_holds_no_events(acquisition: Acquisition) -> None:
    scorable = _execution()
    _observation(scorable=scorable, acquisition=acquisition, reasons=("sandbox down",))
    with pytest.raises(ValidationError, match="cannot contain events"):
        _observation(scorable=scorable, acquisition=acquisition, events=(_event(invocation_index=0),))


def test_observation_events_must_belong_to_the_scorable() -> None:
    scorable = _execution(invocations=1)
    with pytest.raises(ValidationError, match="belong to the scorable"):
        _observation(scorable=scorable, acquisition=Acquisition.PARTIAL, events=(_event(invocation_index=1),))
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
