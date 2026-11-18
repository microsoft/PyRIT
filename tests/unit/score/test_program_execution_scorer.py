# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient, SandboxExecutionError
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
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramResult,
    Regex,
    ScoreStatus,
    ScoringExpectation,
    SurfaceScorable,
)
from pyrit.models.code_execution import _ProgramExecutionEvent, _ProgramInvocation, _SandboxRun
from pyrit.score import (
    NonReplayableObservationError,
    ProgramExecutionScorer,
    ProgramExecutionSource,
    TrueFalseInverterScorer,
)
from pyrit.score.text_matching import match_text
from pyrit.score.true_false.program_execution_scorer import _evaluate_case, _evaluate_program

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory
    from pyrit.models import TextMatcher

pytestmark = pytest.mark.usefixtures("patch_central_database")

_PROFILE = "python-3.12"
_IMAGE_DIGEST = "sha256:" + "b" * 64


def _event(*, invocation_index: int = 0, **updates: object) -> _ProgramExecutionEvent:
    values: dict[str, object] = {
        "invocation_index": invocation_index,
        "runtime_profile_name": _PROFILE,
        "runtime_image_digest": _IMAGE_DIGEST,
        "resolved_command": ("python3", "/program/main.py"),
        "exit_code": 0,
        "stdout": "",
        "duration_ms": 1,
    }
    values.update(updates)
    return _ProgramExecutionEvent.model_validate(values)


class _FakeSandbox:
    """Answers each invocation from a script keyed by its first argument, and records calls."""

    def __init__(self, *, outputs: dict[str, dict[str, object]] | None = None, profiles: frozenset[str] | None = None):
        self.outputs = outputs or {}
        self.profiles = profiles or frozenset({_PROFILE})
        self.calls: list[tuple[str, str, tuple[_ProgramInvocation, ...]]] = []
        self.error: SandboxExecutionError | None = None
        self.stop_after: int | None = None

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"fake": True})

    def get_runtime_profile_names(self) -> frozenset[str]:
        return self.profiles

    async def execute_async(
        self, *, program: str, runtime_profile_name: str, invocations: tuple[_ProgramInvocation, ...]
    ) -> _SandboxRun:
        self.calls.append((program, runtime_profile_name, invocations))
        if self.error is not None:
            raise self.error
        ran = invocations if self.stop_after is None else invocations[: self.stop_after]
        events = tuple(
            _event(
                invocation_index=index, **self.outputs.get(invocation.arguments[0] if invocation.arguments else "", {})
            )
            for index, invocation in enumerate(ran)
        )
        reasons = ("the sandbox lost its worker",) if len(ran) < len(invocations) else ()
        return _SandboxRun(events=events, reasons=reasons)


def _case(argument: str, *, exit_code: int | None = 0, stdout: TextMatcher | None = None) -> ProgramCase:
    return ProgramCase(
        invocation=_ProgramInvocation(arguments=(argument,)),
        expected=ProgramResult(exit_code=exit_code, stdout=stdout),
    )


def _expectation(*cases: ProgramCase, profile: str = _PROFILE) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ProgramBehaves(runtime_profile_name=profile, cases=cases),))


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
def test_evaluate_case(*, expected: ProgramResult, event: dict[str, object], result: bool | None) -> None:
    assert _evaluate_case(expected=expected, event=_event(**event)).value is result


def test_evaluate_program_needs_every_case() -> None:
    condition = ProgramBehaves(runtime_profile_name=_PROFILE, cases=_SQUARES)
    passing = (_event(invocation_index=0, stdout="9"), _event(invocation_index=1, stdout="16"))
    payload = ProgramExecutionObservationPayload(program_sha256="a" * 64, events=passing)
    assert _evaluate_program(condition=condition, payload=payload, acquisition=Acquisition.COMPLETE).value is True

    one_ran = ProgramExecutionObservationPayload(program_sha256="a" * 64, events=passing[:1])
    assert _evaluate_program(condition=condition, payload=one_ran, acquisition=Acquisition.PARTIAL).value is None

    failing_first = ProgramExecutionObservationPayload(
        program_sha256="a" * 64, events=(_event(invocation_index=0, stdout="8"),)
    )
    assert _evaluate_program(condition=condition, payload=failing_first, acquisition=Acquisition.PARTIAL).value is False

    empty = ProgramExecutionObservationPayload(reasons=("down",))
    for acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        assert _evaluate_program(condition=condition, payload=empty, acquisition=acquisition).value is None


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
            invocation=_ProgramInvocation(arguments=("3",), stdin="in"),
            expected=ProgramResult(stdout=Equals(value=secret)),
        )
    )

    await _scorer(sandbox).score_async(scorable=ContentScorable(value="print(1)"), expectation=expectation)

    program, profile, invocations = sandbox.calls[0]
    assert (program, profile) == ("print(1)", _PROFILE)
    assert invocations == (_ProgramInvocation(arguments=("3",), stdin="in"),)
    assert secret not in repr(sandbox.calls)


async def test_scoring_and_replay_do_not_require_runtime_provenance_async(sqlite_instance: SQLiteMemory) -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9", "runtime_image_digest": None, "resolved_command": None}})
    scorer = _scorer(sandbox)
    expectation = _expectation(_case("3", stdout=Equals(value="9")))
    score = (await scorer.score_async(scorable=ContentScorable(value="print(9)"), expectation=expectation))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    assert score.get_value() is True
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events[0].runtime_image_digest is None
    assert observation.payload.events[0].resolved_command is None
    replayed = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replayed.get_value() is True
    assert replayed.observation_ids == score.observation_ids
    assert len(sandbox.calls) == 1
    saved = (await sqlite_instance.get_scores_async(score_ids=[str(replayed.id)]))[0]
    assert saved.get_value() is True
    assert saved.scored_expectation == expectation


async def test_cases_are_matched_once_for_verdict_and_rationale_async() -> None:
    sandbox = _FakeSandbox(outputs={"3": {"stdout": "9"}, "4": {"stdout": "wrong"}})
    with patch("pyrit.score.true_false.program_execution_scorer.match_text", wraps=match_text) as matching:
        score = (
            await _scorer(sandbox).score_async(
                scorable=ContentScorable(value="print(1)"), expectation=_expectation(*_SQUARES)
            )
        )[0]
    assert score.get_value() is False
    assert "case 1 output did not match" in score.score_rationale
    assert matching.call_count == 2


@pytest.mark.parametrize(
    ("failure", "rationale"),
    [
        ("lost-output", "output capture may be incomplete"),
        ("resource-limits", "must support cgroup"),
        ("image-volumes", "must not declare volumes"),
    ],
)
async def test_unsafe_docker_execution_cannot_produce_a_passing_score_async(
    sqlite_instance: SQLiteMemory, failure: str, rationale: str
) -> None:
    client = DockerSandboxClient(
        profiles={_PROFILE: DockerRuntimeProfile(image="python@" + _IMAGE_DIGEST, command=("python3", "-I"))}
    )
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = 1
    process.stdin = None
    for name, data in (("stdout", b"ok"), ("stderr", b"attachment failed")):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        setattr(process, name, reader)
    process.communicate = AsyncMock(return_value=(b"", b""))

    async def control_async(*arguments: str) -> tuple[int, str]:
        if arguments[0] == "info":
            driver = "none" if failure == "resource-limits" else "cgroupfs"
            return 0, f"{driver} true true true true true"
        if arguments[:2] == ("image", "inspect"):
            return 0, '{"/data":{}}' if failure == "image-volumes" else "null"
        if arguments[0] == "inspect":
            return 0, "exited 0 false 2026-10-21T00:00:00Z"
        assert arguments[:3] == ("rm", "--force", "--volumes")
        return 0, ""

    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=client))
    expectation = _expectation(_case("3", stdout=Equals(value="ok")))
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as launch,
        patch.object(client, "_control_async", side_effect=control_async),
    ):
        score = (await scorer.score_async(scorable=ContentScorable(value="print('okextra')"), expectation=expectation))[
            0
        ]
    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert rationale in score.score_rationale
    assert launch.await_count == (1 if failure == "lost-output" else 0)
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.ERROR
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events == ()


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


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("concurrent", [False, True])
async def test_scoring_calls_store_independent_executions_async(
    sqlite_instance: SQLiteMemory, explicit: bool, concurrent: bool
) -> None:
    sandbox = _FakeSandbox()
    scorer = _scorer(sandbox)
    program = ContentScorable(value="x")

    execution = ProgramExecutionScorable(
        program=program,
        runtime_profile_name=_PROFILE,
        invocations=(_ProgramInvocation(arguments=("1",)),),
    )

    scorable = execution if explicit else program
    expectation = _expectation(_case("1"))
    if concurrent:
        first_scores, second_scores = await asyncio.gather(
            scorer.score_async(scorable=scorable, expectation=expectation),
            scorer.score_async(scorable=scorable, expectation=expectation),
        )
    else:
        first_scores = await scorer.score_async(scorable=scorable, expectation=expectation)
        second_scores = await scorer.score_async(scorable=scorable, expectation=expectation)
    first, second = first_scores[0], second_scores[0]

    assert len(sandbox.calls) == 2
    assert first.observation_ids != second.observation_ids
    stored = await sqlite_instance.get_scores_async(score_ids=[str(first.id), str(second.id)])
    assert len(stored) == 2
    assert all(score.get_value() is True for score in stored)


async def test_a_run_with_no_events_is_an_acquisition_error_async() -> None:
    sandbox = _FakeSandbox()
    sandbox.stop_after = 0
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = ProgramExecutionScorable(
        program=ContentScorable(value="x"),
        runtime_profile_name=_PROFILE,
        invocations=(_ProgramInvocation(),),
    )

    failed = await source.acquire_async(scorable=execution)
    assert failed.acquisition is Acquisition.ERROR
    assert isinstance(failed.payload, ProgramExecutionObservationPayload)
    assert failed.payload.reasons == ("the sandbox lost its worker",)


async def test_explicit_execution_must_match_the_condition_async() -> None:
    execution = ProgramExecutionScorable(
        program=ContentScorable(value="x"),
        runtime_profile_name=_PROFILE,
        invocations=(_ProgramInvocation(arguments=("2",)),),
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
        async def execute_async(self, *, program, runtime_profile_name, invocations):  # type: ignore[no-untyped-def]
            return _SandboxRun(events=(_event(invocation_index=5),))

    execution = ProgramExecutionScorable(
        program=ContentScorable(value="x"),
        runtime_profile_name=_PROFILE,
        invocations=(_ProgramInvocation(),),
    )
    with pytest.raises(ValueError, match="not given"):
        await ProgramExecutionSource(sandbox=_Rogue()).acquire_async(scorable=execution)


async def test_sandbox_warnings_are_kept_on_the_observation_async(sqlite_instance: SQLiteMemory) -> None:
    class _LeakySandbox(_FakeSandbox):
        async def execute_async(self, *, program, runtime_profile_name, invocations):  # type: ignore[no-untyped-def]
            return _SandboxRun(
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


async def test_each_acquisition_returns_new_evidence_without_memory_async() -> None:
    sandbox = _FakeSandbox()
    source = ProgramExecutionSource(sandbox=sandbox)
    execution = ProgramExecutionScorable(
        program=ContentScorable(value="x"), runtime_profile_name=_PROFILE, invocations=(_ProgramInvocation(),)
    )
    with patch("pyrit.score.observation.program_execution_source.CentralMemory.get_memory_instance") as memory:
        memory.side_effect = AssertionError("Loose program acquisition must not read memory")
        first = await source.acquire_async(scorable=execution)
        second = await source.acquire_async(scorable=execution)
    assert first.id != second.id
    assert first.scorable == second.scorable == execution
    assert len(sandbox.calls) == 2
