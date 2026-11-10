# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A fixed check runs in the sandbox and scores the judged response; the response's own text never runs."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING, cast

import pytest

from pyrit._code_execution import (
    FakeRun,
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    RuntimeProfile,
    SandboxCapabilities,
    SandboxCondition,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRegistry,
    SandboxStateKind,
)
from pyrit.models import (
    Acquisition,
    ConversationScorable,
    Equals,
    MessagePiece,
    MessageScorable,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramResult,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.prompt_target import SandboxToolProvider
from pyrit.score import (
    SandboxCheckScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseFallbackScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)
from pyrit.score.observation import ProgramExecutionSource

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyrit.memory import SQLiteMemory
    from pyrit.models import ComponentIdentifier, Scorable, ScorableUnion

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


class _UndeterminedScorer(TrueFalseScorer):
    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return [
            Score(
                score_value=None,
                status=ScoreStatus.UNDETERMINED,
                score_type="true_false",
                score_rationale="No primary verdict.",
                scorer_class_identifier=self.get_identifier(),
                scorable=cast("ScorableUnion", scorable),
            )
        ]


def _wrapped_check(*, check: SandboxCheckScorer, wrapper: str) -> TrueFalseScorer:
    if wrapper == "inverter":
        return TrueFalseInverterScorer(scorer=check)
    if wrapper == "composite":
        return TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[check])
    if wrapper == "fallback-primary":
        return TrueFalseFallbackScorer(scorer=check, fallback_scorer=SubStringScorer(substring="created"))
    if wrapper == "fallback-secondary":
        return TrueFalseFallbackScorer(scorer=_UndeterminedScorer(), fallback_scorer=check)
    if wrapper == "nested":
        return TrueFalseInverterScorer(
            scorer=TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[check])
        )
    raise ValueError(f"Unknown wrapper: {wrapper}")


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


@pytest.mark.parametrize("wrapper", ["inverter", "composite", "fallback-primary", "fallback-secondary", "nested"])
@pytest.mark.parametrize("reset", [False, True], ids=["mutation", "reset"])
async def test_wrappers_never_reuse_a_stored_sandbox_verdict_async(
    *, sqlite_instance: SQLiteMemory, wrapper: str, reset: bool
) -> None:
    guest = _Guest()
    guest.flag = True
    check = SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION)
    scorer = _wrapped_check(check=check, wrapper=wrapper)
    response = await _response_async(sqlite_instance)
    (first,) = await scorer.score_async(scorable=response)
    if reset:
        await guest.sandbox.reset_async()
    guest.flag = False
    (persisted,) = await sqlite_instance.get_scores_async(score_ids=[str(first.id)])
    legacy = persisted.model_copy(deep=True)
    legacy._snapshot_participation = None

    for candidate in (first, persisted, legacy):
        assert await scorer.find_matching_scores_async(scores=[candidate], scorable=response) == []
    assert len(guest.sandbox.runs) == 1
    (second,) = await scorer.score_async(scorable=response)
    assert second.get_value() is not first.get_value()
    assert len(guest.sandbox.runs) == 2
    (saved_second,) = await sqlite_instance.get_scores_async(score_ids=[str(second.id)])
    assert saved_second.status is ScoreStatus.COMPLETE
    assert saved_second.get_value() is second.get_value()
    assert saved_second.score_category == second.score_category
    assert "Check program:" in saved_second.score_rationale
    assert saved_second.observation_ids == second.observation_ids
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=second.observation_ids)
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events[0].stdout.strip() == "absent"


async def test_an_unused_sandbox_fallback_keeps_the_primary_match_async(sqlite_instance: SQLiteMemory) -> None:
    guest = _Guest()
    check = SandboxCheckScorer(sandbox=guest.sandbox, check_program=_CHECK, condition=_CONDITION)
    scorer = TrueFalseFallbackScorer(scorer=SubStringScorer(substring="created"), fallback_scorer=check)
    response = await _response_async(sqlite_instance)
    (first,) = await scorer.score_async(scorable=response)
    (persisted,) = await sqlite_instance.get_scores_async(score_ids=[str(first.id)])
    guest.flag = True

    assert await scorer.find_matching_scores_async(scores=[persisted], scorable=response) == [persisted]
    assert guest.sandbox.runs == []


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


@pytest.mark.parametrize("kind", [SandboxStateKind.ENVIRONMENT, SandboxStateKind.SESSION])
async def test_named_stateful_checks_share_tool_state_and_preserve_fresh_source_boundary_async(
    *, registry: SandboxRegistry, sqlite_instance: SQLiteMemory, kind: SandboxStateKind
) -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"state_kind": kind})

    def handle(run: FakeRun) -> ProcessResult:
        if run.program == "set the flag":
            sandbox.state["flag"] = True
            return ProcessResult(exit_code=0)
        assert run.program == _CHECK
        return ProcessResult(exit_code=0, stdout="present" if sandbox.state.get("flag") else "absent")

    sandbox = FakeSandbox(capabilities=capabilities, handler=handle)
    registry.register(sandbox, name="shared-check")
    with pytest.raises(ValueError, match="fresh invocation environments"):
        ProgramExecutionSource(sandbox=sandbox)
    scorer = SandboxCheckScorer(sandbox="shared-check", check_program=_CHECK, condition=_CONDITION)
    response = await _response_async(sqlite_instance)
    (before,) = await scorer.score_async(scorable=response)
    tools = await SandboxToolProvider(sandbox="shared-check").get_tools_async()
    runtime = next(tool for tool in tools if tool.name == "run_python")
    arguments = {"program": "set the flag"}
    runtime.validate_arguments(arguments=arguments)
    await runtime.execute_async(arguments=arguments)
    (after,) = await scorer.score_async(scorable=response)

    assert before.get_value() is False and after.get_value() is True
    assert len({run.session_id for run in sandbox.runs}) == 1
    assert sandbox.closed_sessions == [] and sandbox.reset_count == 0
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=after.observation_ids)
    assert observation.source_identifier.class_name == "_SandboxCheckSource"
    await sandbox.reset_async()
    assert await scorer.find_matching_scores_async(scores=[after], scorable=response) == []
    (fresh,) = await scorer.score_async(scorable=response)
    assert fresh.get_value() is False
    run_count = len(sandbox.runs)
    (replayed,) = await scorer.score_observation_async(observation=observation)
    assert replayed.get_value() is True and len(sandbox.runs) == run_count


@pytest.mark.parametrize("failure", ["state-loss", "uncertain-run"])
async def test_a_session_check_never_recovers_lost_or_uncertain_state_async(
    *, sqlite_instance: SQLiteMemory, failure: str
) -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"state_kind": SandboxStateKind.SESSION})
    sandbox = FakeSandbox(capabilities=capabilities, handler=lambda run: ProcessResult(exit_code=0, stdout="present"))
    scorer = SandboxCheckScorer(sandbox=sandbox, check_program=_CHECK, condition=_CONDITION)
    response = await _response_async(sqlite_instance)
    (first,) = await scorer.score_async(scorable=response)
    assert first.get_value() is True
    if failure == "state-loss":
        sandbox.expire_sessions()
    else:
        sandbox.fail_next(hook=FakeSandboxHook.EXECUTE, error=SandboxExecutionError("outcome unknown"))

    (unknown,) = await scorer.score_async(scorable=response)
    assert unknown.status is ScoreStatus.UNDETERMINED and unknown.score_value is None
    assert "could not be observed" in unknown.score_rationale
    (saved,) = await sqlite_instance.get_scores_async(score_ids=[str(unknown.id)])
    assert saved.status is ScoreStatus.UNDETERMINED and saved.score_value is None
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=saved.observation_ids)
    assert observation.acquisition is Acquisition.ERROR
    assert isinstance(observation.payload, ProgramExecutionObservationPayload) and observation.payload.events == ()
    assert (await sandbox.get_status_async()).condition is SandboxCondition.QUARANTINED
    await sandbox.release_async()
    assert (await sandbox.get_status_async()).condition is SandboxCondition.QUARANTINED
    (still_unknown,) = await scorer.score_async(scorable=response)
    assert still_unknown.status is ScoreStatus.UNDETERMINED
    assert len(sandbox.runs) == 1 and sandbox.reset_count == 0 and sandbox.open_sessions == set()
    await sandbox.close_async()


@pytest.mark.parametrize("kind", [SandboxStateKind.ENVIRONMENT, SandboxStateKind.SESSION])
async def test_stateful_check_retains_bounded_run_evidence_async(
    *, sqlite_instance: SQLiteMemory, kind: SandboxStateKind
) -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"state_kind": kind})
    sandbox = FakeSandbox(
        capabilities=capabilities,
        handler=lambda run: ProcessResult(exit_code=0, stdout="present", execution_id="check-42"),
    )
    scorer = SandboxCheckScorer(sandbox=sandbox, check_program=_CHECK, condition=_CONDITION, max_output_chars=3)
    (score,) = await scorer.score_async(scorable=await _response_async(sqlite_instance))
    assert score.status is ScoreStatus.UNDETERMINED and "cut" in score.score_rationale
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events[0].stdout == "pre" and observation.payload.events[0].stdout_truncated
    assert observation.metadata["sandbox_execution_ids"] == "check-42"


def test_stateful_check_rejects_an_invalid_retention_limit() -> None:
    sandbox = FakeSandbox()
    with pytest.raises(ValueError, match="max_output_chars must be positive"):
        SandboxCheckScorer(sandbox=sandbox, check_program=_CHECK, condition=_CONDITION, max_output_chars=0)
    assert sandbox.runs == [] and sandbox.open_sessions == set()
