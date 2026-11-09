# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A fixed check runs in the sandbox and scores the judged response; the response's own text never runs."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from pyrit._code_execution import (
    FakeRun,
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    RuntimeProfile,
    SandboxCapabilities,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRegistry,
    SandboxStateKind,
)
from pyrit.models import (
    ConversationScorable,
    Equals,
    MessagePiece,
    MessageScorable,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramResult,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score import SandboxCheckScorer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyrit.memory import SQLiteMemory

pytestmark = pytest.mark.usefixtures("patch_central_database")

_CHECK = "import os\nprint('present' if os.path.exists('task.flag') else 'absent')\n"
_CONDITION = ProgramBehaves(
    runtime_profile_name="python", cases=(ProgramCase(expected=ProgramResult(stdout=Equals(value="present"))),)
)


class _Guest:
    """A fake sandbox whose check finds the flag only after the model's code created it."""

    def __init__(self) -> None:
        self.flag = False
        self.sandbox = FakeSandbox(handler=self._handle)

    def _handle(self, run: FakeRun) -> ProcessResult:
        return ProcessResult(exit_code=0, stdout="present\n" if self.flag else "absent\n")


@pytest.fixture
def registry() -> Iterator[SandboxRegistry]:
    SandboxRegistry.reset_registry_singleton()
    yield SandboxRegistry.get_registry_singleton()
    SandboxRegistry.reset_registry_singleton()


async def _response_async(memory: SQLiteMemory) -> MessageScorable:
    piece = MessagePiece(role="assistant", original_value="I created the task.", conversation_id=str(uuid.uuid4()))
    await memory.add_message_to_memory_async(request=piece.to_message())
    return MessageScorable(message_piece_ids=(piece.id,))


@pytest.mark.parametrize(("flag", "value"), [(True, "true"), (False, "false")])
async def test_the_check_decides_the_score_of_the_response_async(
    sqlite_instance: SQLiteMemory, flag: bool, value: str
) -> None:
    guest = _Guest()
    guest.flag = flag
    response = await _response_async(sqlite_instance)

    (score,) = await SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION).score_async(
        scorable=response, expectation=ScoringExpectation(objective="Persist with a scheduled task.")
    )

    assert (score.score_value, score.status) == (value, ScoreStatus.COMPLETE)
    assert score.scorable == response and score.message_piece_id == response.message_piece_ids[0]
    assert [run.program for run in guest.sandbox.runs] == [_CHECK]
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert isinstance(observation.scorable, ProgramExecutionScorable)
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.program_sha256 == hashlib.sha256(_CHECK.encode("utf-8")).hexdigest()
    assert observation.payload.events[0].stdout.strip() == ("present" if flag else "absent")
    (saved,) = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert saved.score_value == value and saved.observation_ids == score.observation_ids


async def test_a_broken_sandbox_leaves_the_score_undetermined_async(sqlite_instance: SQLiteMemory) -> None:
    guest = _Guest()
    guest.sandbox.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxExecutionError("host unreachable"))
    scorer = SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION)

    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))

    assert score.status is ScoreStatus.UNDETERMINED and score.score_value is None
    assert "could not be observed" in score.score_rationale


def test_identity_names_the_check_and_its_expected_result() -> None:
    sandbox = FakeSandbox()
    scorer = SandboxCheckScorer(sandbox=sandbox, check_program=_CHECK, condition=_CONDITION)
    params = scorer.get_identifier().params
    assert params["check_sha256"] == hashlib.sha256(_CHECK.encode("utf-8")).hexdigest()
    assert params["condition"]["runtime_profile_name"] == "python"
    other = ProgramBehaves(runtime_profile_name="python", cases=(ProgramCase(expected=ProgramResult(exit_code=1)),))
    other_scorer = SandboxCheckScorer(sandbox=sandbox, check_program=_CHECK, condition=other)
    assert other_scorer.get_identifier().hash != scorer.get_identifier().hash


async def test_a_registered_sandbox_can_be_named_async(
    registry: SandboxRegistry, sqlite_instance: SQLiteMemory
) -> None:
    guest = _Guest()
    registry.register(guest.sandbox, name="windows-1")
    scorer = SandboxCheckScorer(sandbox="windows-1", check_program=_CHECK, condition=_CONDITION)
    await scorer.score_async(scorable=await _response_async(sqlite_instance))
    assert [run.program for run in guest.sandbox.runs] == [_CHECK]


@pytest.mark.parametrize(
    ("check_program", "runtime", "message"),
    [("  ", "python", "must not be empty"), (_CHECK, "powershell", "does not offer runtime 'powershell'")],
)
def test_invalid_checks_are_rejected(check_program: str, runtime: str, message: str) -> None:
    condition = _CONDITION.model_copy(update={"runtime_profile_name": runtime})
    with pytest.raises(ValueError, match=message):
        SandboxCheckScorer(sandbox=FakeSandbox(), check_program=check_program, condition=condition)


def test_a_sandbox_that_keeps_nothing_is_rejected() -> None:
    capabilities = SandboxCapabilities(
        runtime_profiles=(RuntimeProfile(name="python"),), state_kind=SandboxStateKind.CALL
    )
    with pytest.raises(ValueError, match="state persists"):
        SandboxCheckScorer(sandbox=FakeSandbox(capabilities=capabilities), check_program=_CHECK, condition=_CONDITION)


async def test_a_stored_verdict_is_never_reused_async(sqlite_instance: SQLiteMemory) -> None:
    guest = _Guest()
    scorer = SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION)
    response = await _response_async(sqlite_instance)
    first = await scorer.score_async(scorable=response)
    assert await scorer.find_matching_scores_async(scores=first, scorable=response) == []


@pytest.mark.parametrize("limit", ["timed_out", "resource_limit_exceeded"])
async def test_a_check_that_does_not_finish_is_undetermined_async(sqlite_instance: SQLiteMemory, limit: str) -> None:
    fake = FakeSandbox(handler=lambda run: ProcessResult(exit_code=None, **{limit: True}))
    scorer = SandboxCheckScorer(sandbox=fake, check_program=_CHECK, condition=_CONDITION)
    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))
    assert score.status is ScoreStatus.UNDETERMINED and "did not finish" in score.score_rationale


async def test_a_check_the_sandbox_rejects_is_undetermined_async(sqlite_instance: SQLiteMemory) -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=SandboxInputError("request too large for the host"))
    scorer = SandboxCheckScorer(sandbox=fake, check_program=_CHECK, condition=_CONDITION)
    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))
    assert score.status is ScoreStatus.UNDETERMINED and "request too large" in score.score_rationale


async def test_a_stored_check_can_be_judged_again_async(sqlite_instance: SQLiteMemory) -> None:
    guest = _Guest()
    guest.flag = True
    scorer = SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION)
    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    (replayed,) = await scorer.score_observation_async(observation=observation)
    assert replayed.get_value() is True and len(guest.sandbox.runs) == 1


async def test_the_sandboxs_run_ids_are_kept_with_the_evidence_async(sqlite_instance: SQLiteMemory) -> None:
    fake = FakeSandbox(handler=lambda run: ProcessResult(exit_code=0, stdout="present", execution_id="run-42"))
    scorer = SandboxCheckScorer(sandbox=fake, check_program=_CHECK, condition=_CONDITION)
    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert observation.metadata["sandbox_execution_ids"] == "run-42"


async def test_conditions_in_the_expectation_are_not_consumed_async() -> None:
    scorer = SandboxCheckScorer(sandbox=FakeSandbox(), check_program=_CHECK, condition=_CONDITION)
    with pytest.raises(ValueError):
        await scorer.score_async(
            scorable=MessageScorable(message_piece_ids=(uuid.uuid4(),)),
            expectation=ScoringExpectation(conditions=(_CONDITION,)),
        )


async def test_only_responses_are_scored_async() -> None:
    scorer = SandboxCheckScorer(sandbox=FakeSandbox(), check_program=_CHECK, condition=_CONDITION)
    with pytest.raises(RuntimeError, match="scores a MessageScorable") as raised:
        await scorer.score_async(scorable=ConversationScorable(conversation_id=str(uuid.uuid4())))
    assert isinstance(raised.value.__cause__, TypeError)
