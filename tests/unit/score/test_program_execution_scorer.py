# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Contains,
    ContentEntryScorable,
    ContentScorable,
    ContentWritten,
    Equals,
    MessagePiece,
    MessageScorable,
    Observation,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionEvent,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramInvocation,
    ProgramResult,
    Regex,
    ScoreStatus,
    ScoringExpectation,
    SurfaceScorable,
)
from pyrit.score import (
    NonReplayableObservationError,
    ProgramExecutionScorer,
    ProgramExecutionSource,
    SandboxClient,
    SandboxExecutionError,
    SandboxRun,
    TrueFalseInverterScorer,
)
from pyrit.score.true_false.program_execution_scorer import match_case, match_program_behaves

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory
    from pyrit.models import TextMatcher

pytestmark = pytest.mark.usefixtures("patch_central_database")

_PROFILE = "python-3.12"
_IMAGE_DIGEST = "sha256:" + "b" * 64


def _event(*, case_index: int = 0, **updates: object) -> ProgramExecutionEvent:
    values: dict[str, object] = {
        "case_index": case_index,
        "runtime_profile": _PROFILE,
        "runtime_image_digest": _IMAGE_DIGEST,
        "resolved_command": ("python3", "/program/main.py"),
        "exit_code": 0,
        "stdout": "",
        "duration_ms": 1,
    }
    values.update(updates)
    return ProgramExecutionEvent.model_validate(values)


class _FakeSandbox:
    """Answers each invocation from a script keyed by its first argument, and records calls."""

    def __init__(self, *, outputs: dict[str, dict[str, object]] | None = None, profiles: frozenset[str] | None = None):
        self.outputs = outputs or {}
        self.profiles = profiles or frozenset({_PROFILE})
        self.calls: list[tuple[str, str, tuple[ProgramInvocation, ...]]] = []
        self.error: SandboxExecutionError | None = None
        self.stop_after: int | None = None

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"fake": True})

    def get_runtime_profiles(self) -> frozenset[str]:
        return self.profiles

    async def execute_async(
        self, *, program: str, runtime_profile: str, invocations: tuple[ProgramInvocation, ...]
    ) -> SandboxRun:
        self.calls.append((program, runtime_profile, invocations))
        if self.error is not None:
            raise self.error
        ran = invocations if self.stop_after is None else invocations[: self.stop_after]
        events = tuple(
            _event(case_index=index, **self.outputs.get(invocation.arguments[0] if invocation.arguments else "", {}))
            for index, invocation in enumerate(ran)
        )
        reasons = ("the sandbox lost its worker",) if len(ran) < len(invocations) else ()
        return SandboxRun(events=events, reasons=reasons)


def _case(argument: str, *, exit_code: int | None = 0, stdout: TextMatcher | None = None) -> ProgramCase:
    return ProgramCase(
        invocation=ProgramInvocation(arguments=(argument,)),
        expected=ProgramResult(exit_code=exit_code, stdout=stdout),
    )


def _expectation(*cases: ProgramCase, profile: str = _PROFILE) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ProgramBehaves(runtime_profile=profile, cases=cases),))


_SQUARES = (_case("3", stdout=Equals(value="9")), _case("4", stdout=Equals(value="16")))


def _scorer(sandbox: _FakeSandbox, *, max_output_chars: int = 64_000) -> ProgramExecutionScorer:
    return ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=sandbox, max_output_chars=max_output_chars))


@pytest.mark.parametrize(
    ("expected", "event", "result"),
    [
        (ProgramResult(), {}, True),
        (ProgramResult(), {"exit_code": 1}, False),
        (ProgramResult(exit_code=None), {"exit_code": 1}, True),
        (ProgramResult(exit_code=None), {"exit_code": None}, None),
        (ProgramResult(exit_code=None), {"exit_code": None, "timed_out": True}, False),
        (ProgramResult(exit_code=None), {"exit_code": None, "resource_limit_exceeded": True}, False),
        (ProgramResult(stdout=Equals(value="9")), {"stdout": "9\n"}, True),
        (ProgramResult(stdout=Equals(value="9")), {"stdout": "10"}, False),
        (ProgramResult(stdout=Equals(value="9")), {"stdout": "9", "stdout_truncated": True}, None),
        (ProgramResult(stdout=Contains(value="key")), {"stdout": "a key", "stdout_truncated": True}, True),
        (ProgramResult(stdout=Contains(value="key")), {"stdout": "a lock", "stdout_truncated": True}, None),
        (ProgramResult(stdout=Contains(value="key")), {"stdout": "a lock"}, False),
        (ProgramResult(stdout=Regex(value=r"^\d+$")), {"stdout": "42"}, True),
        (ProgramResult(stderr=Contains(value="Error")), {"stderr": "ValueError"}, True),
        (ProgramResult(stderr=Contains(value="Error")), {"stderr": ""}, False),
        (ProgramResult(stdout=Equals(value="9")), {"stdout": "9", "stderr_truncated": True}, True),
        (ProgramResult(exit_code=1, stdout=Equals(value="9")), {"stdout": "9"}, False),
        (
            ProgramResult(stdout=Equals(value="9"), stderr=Equals(value="x")),
            {"stdout": "8", "stderr_truncated": True},
            False,
        ),
    ],
)
def test_match_case(*, expected: ProgramResult, event: dict[str, object], result: bool | None) -> None:
    assert match_case(expected=expected, event=_event(**event)) is result


def test_match_program_behaves_needs_every_case() -> None:
    condition = ProgramBehaves(runtime_profile=_PROFILE, cases=_SQUARES)
    passing = (_event(case_index=0, stdout="9"), _event(case_index=1, stdout="16"))
    payload = ProgramExecutionObservationPayload(program_sha256="a" * 64, events=passing)
    assert match_program_behaves(condition=condition, payload=payload, acquisition=Acquisition.COMPLETE) is True

    one_ran = ProgramExecutionObservationPayload(program_sha256="a" * 64, events=passing[:1])
    assert match_program_behaves(condition=condition, payload=one_ran, acquisition=Acquisition.PARTIAL) is None

    failing_first = ProgramExecutionObservationPayload(
        program_sha256="a" * 64, events=(_event(case_index=0, stdout="8"),)
    )
    assert match_program_behaves(condition=condition, payload=failing_first, acquisition=Acquisition.PARTIAL) is False

    empty = ProgramExecutionObservationPayload(reasons=("down",))
    for acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        assert match_program_behaves(condition=condition, payload=empty, acquisition=acquisition) is None


async def test_scores_true_when_every_case_passes_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "16"}})
    program = ContentScorable(value="import sys\nprint(int(sys.argv[1]) ** 2)\n")

    score = (await _scorer(sandbox).score_async(scorable=program, expectation=_expectation(*_SQUARES)))[0]

    assert score.score_value == "true"
    assert score.status is ScoreStatus.COMPLETE
    assert "All 2 case(s)" in score.score_rationale
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.scorable, ProgramExecutionScorable)
    assert observation.acquisition is Acquisition.COMPLETE
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.program_sha256 == hashlib.sha256(program.value.encode()).hexdigest()


async def test_sandbox_receives_stimuli_but_not_expected_results_async() -> None:
    sandbox = _FakeSandbox()
    secret = "the-expected-answer"
    expectation = _expectation(
        ProgramCase(
            invocation=ProgramInvocation(arguments=("3",), stdin="in"),
            expected=ProgramResult(stdout=Equals(value=secret)),
        )
    )

    await _scorer(sandbox).score_async(scorable=ContentScorable(value="print(1)"), expectation=expectation)

    program, profile, invocations = sandbox.calls[0]
    assert (program, profile) == ("print(1)", _PROFILE)
    assert invocations == (ProgramInvocation(arguments=("3",), stdin="in"),)
    assert secret not in repr(sandbox.calls)


async def test_scores_false_when_one_case_fails_async() -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "15"}})

    score = (
        await _scorer(sandbox).score_async(
            scorable=ContentScorable(value="print(1)"), expectation=_expectation(*_SQUARES)
        )
    )[0]

    assert score.score_value == "false"
    assert "case 1 output did not match" in score.score_rationale


@pytest.mark.parametrize(
    ("outcome", "rationale"),
    [
        ({"exit_code": 1}, "case 0 exited with 1, expected 0"),
        ({"exit_code": None, "timed_out": True}, "case 0 timed out"),
        ({"exit_code": None, "resource_limit_exceeded": True}, "case 0 exceeded a resource limit"),
    ],
)
async def test_program_failure_is_a_false_verdict_not_undetermined_async(
    outcome: dict[str, object], rationale: str
) -> None:
    sandbox = _FakeSandbox(outputs={"1": outcome})

    score = (
        await _scorer(sandbox).score_async(scorable=ContentScorable(value="x"), expectation=_expectation(_case("1")))
    )[0]

    assert (score.score_value, score.status) == ("false", ScoreStatus.COMPLETE)
    assert rationale in score.score_rationale


async def test_sandbox_failure_is_undetermined_not_false_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox()
    sandbox.error = SandboxExecutionError("service unavailable")

    score = (
        await _scorer(sandbox).score_async(scorable=ContentScorable(value="x"), expectation=_expectation(_case("1")))
    )[0]

    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert "service unavailable" in score.score_rationale
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.ERROR


async def test_sandbox_stopping_early_leaves_passing_runs_undetermined_async() -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}})
    sandbox.stop_after = 1

    score = (
        await _scorer(sandbox).score_async(scorable=ContentScorable(value="x"), expectation=_expectation(*_SQUARES))
    )[0]

    assert score.status is ScoreStatus.UNDETERMINED
    assert "case 1 did not run" in score.score_rationale
    assert "lost its worker" in score.score_rationale


async def test_sandbox_stopping_early_still_fails_a_failed_case_async() -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "8"}})
    sandbox.stop_after = 1

    score = (
        await _scorer(sandbox).score_async(scorable=ContentScorable(value="x"), expectation=_expectation(*_SQUARES))
    )[0]

    assert score.score_value == "false"


async def test_output_beyond_the_limit_is_cut_and_marked_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"1": {"stdout": "x" * 50 + "key", "stderr": "e" * 60}})
    scorer = _scorer(sandbox, max_output_chars=10)

    equals = (
        await scorer.score_async(
            scorable=ContentScorable(value="x"),
            expectation=_expectation(_case("1", stdout=Equals(value="x" * 50 + "key"))),
        )
    )[0]
    contains = (
        await scorer.score_async(
            scorable=ContentScorable(value="x"), expectation=_expectation(_case("1", stdout=Contains(value="xxx")))
        )
    )[0]

    assert equals.status is ScoreStatus.UNDETERMINED
    assert contains.score_value == "true"
    observation = (await sqlite_instance.get_observations_async(observation_ids=equals.observation_ids))[0]
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    event = observation.payload.events[0]
    assert (event.stdout, event.stdout_truncated, len(event.stderr), event.stderr_truncated) == (
        "x" * 10,
        True,
        10,
        True,
    )


async def test_replay_judges_stored_output_without_running_again_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "16"}})
    scorer = _scorer(sandbox)
    score = (await scorer.score_async(scorable=ContentScorable(value="x"), expectation=_expectation(*_SQUARES)))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    same = (await scorer.score_observation_async(observation=observation, expectation=_expectation(*_SQUARES)))[0]
    stricter = (
        await scorer.score_observation_async(
            observation=observation,
            expectation=_expectation(_case("3", stdout=Equals(value="9")), _case("4", stdout=Equals(value="17"))),
        )
    )[0]

    assert same.score_value == "true"
    assert stricter.score_value == "false"
    assert same.observation_ids == [observation.id]
    assert len(sandbox.calls) == 1


async def test_replay_rejects_different_stimuli_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "16"}})
    scorer = _scorer(sandbox)
    score = (await scorer.score_async(scorable=ContentScorable(value="x"), expectation=_expectation(*_SQUARES)))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    with pytest.raises(NonReplayableObservationError, match="different invocations"):
        await scorer.score_observation_async(
            observation=observation, expectation=_expectation(_case("5", stdout=Equals(value="25")))
        )
    with pytest.raises(NonReplayableObservationError, match="different invocations"):
        await scorer.score_observation_async(
            observation=observation, expectation=_expectation(*_SQUARES, profile="python-3.13")
        )


async def test_each_scoring_call_is_a_new_execution_async() -> None:
    sandbox = _FakeSandbox()
    scorer = _scorer(sandbox)
    program = ContentScorable(value="x")

    first = (await scorer.score_async(scorable=program, expectation=_expectation(_case("1"))))[0]
    second = (await scorer.score_async(scorable=program, expectation=_expectation(_case("1"))))[0]

    assert len(sandbox.calls) == 2
    assert first.observation_ids != second.observation_ids


async def test_explicit_execution_reuses_its_observed_run_async() -> None:
    sandbox = _FakeSandbox()
    scorer = _scorer(sandbox)
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(arguments=("1",)),),
    )

    first = (await scorer.score_async(scorable=execution, expectation=_expectation(_case("1"))))[0]
    second = (await scorer.score_async(scorable=execution, expectation=_expectation(_case("1", exit_code=None))))[0]

    assert len(sandbox.calls) == 1
    assert first.observation_ids == second.observation_ids
    assert first.scorable == execution


async def test_sandbox_errors_are_not_reused_async() -> None:
    sandbox = _FakeSandbox()
    sandbox.error = SandboxExecutionError("down")
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(),),
    )

    assert (await source.acquire_async(scorable=execution)).acquisition is Acquisition.ERROR
    sandbox.error = None
    assert (await source.acquire_async(scorable=execution)).acquisition is Acquisition.COMPLETE
    assert len(sandbox.calls) == 2


async def test_a_run_with_no_events_is_an_error_and_not_reused_async() -> None:
    sandbox = _FakeSandbox()
    sandbox.stop_after = 0
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(),),
    )

    failed = await source.acquire_async(scorable=execution)
    sandbox.stop_after = None
    retried = await source.acquire_async(scorable=execution)

    assert failed.acquisition is Acquisition.ERROR
    assert isinstance(failed.payload, ProgramExecutionObservationPayload)
    assert failed.payload.reasons == ("the sandbox lost its worker",)
    assert retried.acquisition is Acquisition.COMPLETE


async def test_explicit_execution_must_match_the_condition_async() -> None:
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(arguments=("2",)),),
    )

    with pytest.raises(RuntimeError, match="runtime profile and invocations"):
        await _scorer(_FakeSandbox()).score_async(scorable=execution, expectation=_expectation(_case("1")))


async def test_runtime_profile_must_be_offered_by_the_sandbox_async() -> None:
    sandbox = _FakeSandbox()

    with pytest.raises(RuntimeError, match="not offered by the sandbox"):
        await _scorer(sandbox).score_async(
            scorable=ContentScorable(value="x"), expectation=_expectation(_case("1"), profile="node-20")
        )
    assert sandbox.calls == []


async def test_scores_a_stored_message_verbatim_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox()
    piece = MessagePiece(role="assistant", original_value="print('hi')", conversation_id=str(uuid.uuid4()))
    await sqlite_instance.add_message_to_memory_async(request=piece.to_message())
    scorable = MessageScorable(message_piece_ids=(piece.id,))

    score = (await _scorer(sandbox).score_async(scorable=scorable, expectation=_expectation(_case("1"))))[0]

    assert score.score_value == "true"
    assert sandbox.calls[0][0] == "print('hi')"
    assert score.scorable == scorable
    assert score.message_piece_id == piece.id
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.scorable, ProgramExecutionScorable)
    assert observation.scorable.program == scorable


async def test_a_blocked_response_has_no_program_to_run_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox()
    piece = MessagePiece(
        role="assistant",
        original_value="content filtered",
        conversation_id=str(uuid.uuid4()),
        response_error="blocked",
    )
    await sqlite_instance.add_message_to_memory_async(request=piece.to_message())

    score = (
        await _scorer(sandbox).score_async(
            scorable=MessageScorable(message_piece_ids=(piece.id,)), expectation=_expectation(_case("1"))
        )
    )[0]

    assert score.status is ScoreStatus.UNDETERMINED
    assert "blocked response" in score.score_rationale
    assert sandbox.calls == []


async def test_a_message_reference_must_name_one_piece_async(sqlite_instance: SQLiteMemory) -> None:
    conversation_id = str(uuid.uuid4())
    first = MessagePiece(role="assistant", original_value="a", conversation_id=conversation_id)
    second = MessagePiece(role="assistant", original_value="b", conversation_id=conversation_id)
    await sqlite_instance.add_message_to_memory_async(request=first.to_message())

    with pytest.raises(RuntimeError, match="exactly one message piece"):
        await _scorer(_FakeSandbox()).score_async(
            scorable=MessageScorable(message_piece_ids=(first.id, second.id)), expectation=_expectation(_case("1"))
        )


async def test_rejects_other_scorables_async() -> None:
    with pytest.raises(RuntimeError, match="requires a MessageScorable"):
        await _scorer(_FakeSandbox()).score_async(
            scorable=SurfaceScorable(uri="/out.txt"), expectation=_expectation(_case("1"))
        )


async def test_requires_the_program_behaves_condition_async() -> None:
    with pytest.raises(ValueError, match="requires one ProgramBehaves condition"):
        await _scorer(_FakeSandbox()).score_async(
            scorable=ContentScorable(value="x"),
            expectation=ScoringExpectation(conditions=(ContentWritten(uri="/out.txt"),)),
        )


async def test_inverter_keeps_undetermined_async() -> None:
    sandbox = _FakeSandbox()
    sandbox.error = SandboxExecutionError("down")
    passing = _FakeSandbox()

    undetermined = (
        await TrueFalseInverterScorer(scorer=_scorer(sandbox)).score_async(
            scorable=ContentScorable(value="x"), expectation=_expectation(_case("1"))
        )
    )[0]
    inverted = (
        await TrueFalseInverterScorer(scorer=_scorer(passing)).score_async(
            scorable=ContentScorable(value="x"), expectation=_expectation(_case("1"))
        )
    )[0]

    assert undetermined.status is ScoreStatus.UNDETERMINED
    assert inverted.get_value() is False


def test_fake_sandbox_satisfies_the_protocol() -> None:
    assert isinstance(_FakeSandbox(), SandboxClient)


def test_source_rejects_a_nonpositive_output_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        ProgramExecutionSource(sandbox=_FakeSandbox(), max_output_chars=0)


def test_identifier_names_the_source_and_sandbox() -> None:
    identifier = _scorer(_FakeSandbox()).get_identifier()
    source = identifier.children["source"]
    assert isinstance(source, ComponentIdentifier)
    assert source.class_name == "ProgramExecutionSource"
    assert isinstance(source.children["sandbox"], ComponentIdentifier)


async def test_source_rejects_events_for_unknown_invocations_async() -> None:
    class _Rogue(_FakeSandbox):
        async def execute_async(self, *, program, runtime_profile, invocations):  # type: ignore[no-untyped-def]
            return SandboxRun(events=(_event(case_index=5),))

    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(),),
    )
    with pytest.raises(ValueError, match="not given"):
        await ProgramExecutionSource(sandbox=_Rogue()).acquire_async(scorable=execution)


def test_observation_round_trip_keeps_the_program_reference() -> None:
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(),),
    )
    observation = Observation(
        source_identifier=ComponentIdentifier(class_name="S", class_module="m"),
        acquisition=Acquisition.COMPLETE,
        scorable=execution,
        payload=ProgramExecutionObservationPayload(program_sha256="a" * 64, events=(_event(),)),
    )
    assert Observation.model_validate(observation.model_dump(mode="json")) == observation


class _SlowSandbox(_FakeSandbox):
    """Holds each run open until released, so concurrent callers overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def execute_async(self, *, program, runtime_profile, invocations):  # type: ignore[no-untyped-def]
        self.calls.append((program, runtime_profile, invocations))
        await self.release.wait()
        return SandboxRun(events=tuple(_event(case_index=index) for index in range(len(invocations))))


def _attempt() -> ProgramExecutionScorable:
    return ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(),),
    )


async def test_concurrent_callers_share_one_run_of_an_attempt_async() -> None:
    sandbox = _SlowSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = _attempt()

    pending = asyncio.gather(source.acquire_async(scorable=execution), source.acquire_async(scorable=execution))
    await asyncio.sleep(0)
    sandbox.release.set()
    first, second = await pending

    assert len(sandbox.calls) == 1
    assert first.id == second.id


async def test_one_cancelled_caller_does_not_cancel_the_shared_run_async() -> None:
    sandbox = _SlowSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = _attempt()

    cancelled = asyncio.create_task(source.acquire_async(scorable=execution))
    kept = asyncio.create_task(source.acquire_async(scorable=execution))
    await asyncio.sleep(0)
    cancelled.cancel()
    await asyncio.sleep(0)
    sandbox.release.set()

    observation = await kept
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert observation.acquisition is Acquisition.COMPLETE
    assert len(sandbox.calls) == 1


async def test_a_run_nobody_waits_for_is_cancelled_async() -> None:
    sandbox = _SlowSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = _attempt()

    task = asyncio.create_task(source.acquire_async(scorable=execution))
    await asyncio.sleep(0)
    flight = source._in_flight[execution]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert flight.task.cancelled()
    assert execution not in source._in_flight


class _SlowCleanupSandbox(_SlowSandbox):
    """Holds its cleanup open after cancellation until released."""

    def __init__(self) -> None:
        super().__init__()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleaned_up = False

    async def execute_async(self, *, program, runtime_profile, invocations):  # type: ignore[no-untyped-def]
        try:
            return await super().execute_async(
                program=program, runtime_profile=runtime_profile, invocations=invocations
            )
        except asyncio.CancelledError:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.cleaned_up = True
            raise


async def test_cancelling_the_last_caller_waits_for_sandbox_cleanup_async() -> None:
    sandbox = _SlowCleanupSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = _attempt()

    task = asyncio.create_task(source.acquire_async(scorable=execution))
    # The source checks memory before it runs the program, so wait for the run itself to start.
    for _ in range(500):
        if sandbox.calls:
            break
        await asyncio.sleep(0.01)
    assert sandbox.calls
    task.cancel()
    await asyncio.wait_for(sandbox.cleanup_started.wait(), timeout=5)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done(), "cancellation finished before the sandbox cleaned up"

    sandbox.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sandbox.cleaned_up
    assert execution not in source._in_flight


async def test_concurrent_scoring_of_one_attempt_stores_its_observation_once_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    sandbox = _SlowSandbox()
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=sandbox))
    execution = _attempt()
    expectation = _expectation(ProgramCase(invocation=ProgramInvocation()))
    # Hold both callers at the write until each has done every read, so neither sees the
    # other's observation already stored.
    arrived, both_arrived = 0, asyncio.Event()
    write = sqlite_instance.add_scores_to_memory_async

    async def write_together(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)
        return await write(**kwargs)

    sqlite_instance.add_scores_to_memory_async = write_together  # type: ignore[method-assign]

    pending = asyncio.gather(
        scorer.score_async(scorable=execution, expectation=expectation),
        scorer.score_async(scorable=execution, expectation=expectation),
    )
    await asyncio.sleep(0)
    sandbox.release.set()
    first, second = await pending

    assert len(sandbox.calls) == 1
    assert first[0].score_value == second[0].score_value == "true"
    assert first[0].observation_ids == second[0].observation_ids
    stored = await sqlite_instance.get_scores_async(score_ids=[first[0].id, second[0].id])
    assert len(stored) == 2
    assert len(await sqlite_instance.get_observations_async(observation_ids=first[0].observation_ids)) == 1


async def test_sandbox_warnings_are_kept_on_the_observation_async(sqlite_instance: SQLiteMemory) -> None:
    class _LeakySandbox(_FakeSandbox):
        async def execute_async(self, *, program, runtime_profile, invocations):  # type: ignore[no-untyped-def]
            return SandboxRun(
                events=(_event(),), warnings=("could not remove container pyrit-sandbox-1: daemon unavailable",)
            )

    score = (
        await _scorer(_LeakySandbox()).score_async(
            scorable=ContentScorable(value="x"), expectation=_expectation(_case("1"))
        )
    )[0]

    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert score.score_value == "true"
    assert observation.acquisition is Acquisition.COMPLETE
    assert "daemon unavailable" in observation.metadata["sandbox_warnings"]


async def test_scoring_a_saved_score_anchor_runs_the_stored_program_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "16"}})
    scorer = _scorer(sandbox)
    program = ContentScorable(value="import sys\nprint(int(sys.argv[1]) ** 2)\n")
    first = (await scorer.score_async(scorable=program, expectation=_expectation(*_SQUARES)))[0]
    saved = (await sqlite_instance.get_scores_async(score_ids=[first.id]))[0]
    assert isinstance(saved.scorable, ContentEntryScorable)

    again = (await scorer.score_async(scorable=saved.scorable, expectation=_expectation(*_SQUARES)))[0]

    assert again.score_value == "true"
    assert [call[0] for call in sandbox.calls] == [program.value, program.value]
    observation = (await sqlite_instance.get_observations_async(observation_ids=again.observation_ids))[0]
    assert isinstance(observation.scorable, ProgramExecutionScorable)
    assert observation.scorable.program == saved.scorable


class _ChangingSandbox(_FakeSandbox):
    """Prints something different on every run, so a second run of one attempt would show."""

    async def execute_async(self, *, program, runtime_profile, invocations):  # type: ignore[no-untyped-def]
        self.calls.append((program, runtime_profile, invocations))
        stdout = "9" if len(self.calls) == 1 else f"run {len(self.calls)}"
        return SandboxRun(events=tuple(_event(case_index=index, stdout=stdout) for index in range(len(invocations))))


async def test_a_stored_attempt_is_reused_after_the_source_evicts_it_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _ChangingSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    scorer = ProgramExecutionScorer(source=source)
    expectation = _expectation(_case("3", stdout=Equals(value="9")))
    execution = ProgramExecutionScorable(
        execution_id=uuid.uuid4(),
        program=ContentScorable(value="x"),
        runtime_profile=_PROFILE,
        invocations=(ProgramInvocation(arguments=("3",)),),
    )

    first = (await scorer.score_async(scorable=execution, expectation=expectation))[0]
    for _ in range(ProgramExecutionSource._MAX_REUSED_OBSERVATIONS):
        await source.acquire_async(scorable=_attempt())
    assert execution not in source._observed
    again = (await scorer.score_async(scorable=execution, expectation=expectation))[0]

    runs_of_execution = [call for call in sandbox.calls if call[2] == execution.invocations]
    assert len(runs_of_execution) == 1
    assert (first.score_value, again.score_value) == ("true", "true")
    assert first.observation_ids == again.observation_ids
    stored = await sqlite_instance.get_scores_async(score_ids=[first.id, again.id])
    assert len(stored) == 2
    assert len(await sqlite_instance.get_observations_async(observation_ids=first.observation_ids)) == 1


async def test_errors_are_retried_as_new_observations_not_the_attempts_evidence_async() -> None:
    sandbox = _FakeSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = _attempt()
    sandbox.error = SandboxExecutionError("no daemon")

    failed = await source.acquire_async(scorable=execution)
    sandbox.error = None
    ran = await source.acquire_async(scorable=execution)

    assert failed.acquisition is Acquisition.ERROR
    assert ran.acquisition is Acquisition.COMPLETE
    assert failed.id != ran.id
    assert ran.id == ProgramExecutionSource(sandbox=sandbox)._attempt_observation_id(execution)


async def test_two_sources_cannot_store_two_runs_of_one_attempt_async(sqlite_instance: SQLiteMemory) -> None:
    first_sandbox, second_sandbox = _SlowSandbox(), _SlowSandbox()
    scorers = [_scorer(first_sandbox), _scorer(second_sandbox)]
    execution = _attempt()
    expectation = _expectation(ProgramCase(invocation=ProgramInvocation(), expected=ProgramResult()))

    pending = asyncio.gather(
        *(scorer.score_async(scorable=execution, expectation=expectation) for scorer in scorers),
        return_exceptions=True,
    )
    for _ in range(500):
        if first_sandbox.calls and second_sandbox.calls:
            break
        await asyncio.sleep(0.01)
    first_sandbox.release.set()
    second_sandbox.release.set()
    results = await pending

    # Each source ran the attempt, so the later store is refused rather than keeping a second run.
    errors = [result for result in results if isinstance(result, BaseException)]
    stored = [result for result in results if not isinstance(result, BaseException)]
    assert len(errors) == 1
    assert "already exist" in str(errors[0])
    assert len(stored) == 1
    assert len(await sqlite_instance.get_observations_async(observation_ids=stored[0][0].observation_ids)) == 1
