# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in shared execution and scoring against a caller-provided Docker daemon and image."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient
from pyrit.models import (
    Acquisition,
    Contains,
    ContentScorable,
    Equals,
    ProgramBehaves,
    ProgramCase,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    ProgramResult,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.models.code_execution import _ProgramInvocation
from pyrit.score import ProgramExecutionScorer, ProgramExecutionSource

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from pyrit.memory import SQLiteMemory

pytestmark = [pytest.mark.run_only_if_all_tests, pytest.mark.usefixtures("patch_central_database")]

_IMAGE = "python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
_PROFILE = "python-3.12"
_PROGRAM = "import sys\nn = int(sys.argv[1]) if len(sys.argv) > 1 else int(sys.stdin.read())\nprint(n * n)\n"


@pytest.fixture
async def docker_sandbox(tmp_path: Path) -> AsyncGenerator[DockerSandboxClient]:
    client = DockerSandboxClient(
        profiles={_PROFILE: DockerRuntimeProfile(image=_IMAGE, command=("python3", "-I"))},
        timeout_seconds=5,
        max_output_bytes=128,
    )
    client._temp_root = tmp_path
    for command in (("version", "--format", "{{.Server.Version}}"), ("image", "inspect", _IMAGE)):
        status, output = await client._control_async(*command)
        if status != 0:
            pytest.fail(
                f"Live execution requires a running Docker daemon and a pre-pulled {_IMAGE}. "
                f"Docker {' '.join(command)} failed: {output}"
            )
    with patch.object(client, "_run_arguments", wraps=client._run_arguments) as arguments:
        try:
            yield client
        finally:
            problems = []
            for call in arguments.call_args_list:
                name = call.kwargs["name"]
                status, output = await client._control_async(
                    "container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"
                )
                if status != 0:
                    problems.append(f"Could not verify container cleanup for {name}: {output}")
                elif output.strip():
                    problems.append(f"Container {name} remained after execution")
                    removed, detail = await client._control_async("rm", "--force", "--volumes", name)
                    if removed != 0:
                        problems.append(f"Could not remove test container {name}: {detail}")
            remaining = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
            for directory in remaining:
                problems.append(f"Program directory remained after execution: {directory}")
                if directory.name.startswith("pyrit-program-"):
                    await asyncio.to_thread(
                        client._remove_workspace,
                        workspace_dir=directory,
                        program_path=directory / "program" / "main.py",
                    )
            assert not problems, "\n".join(problems)


def _expectation(*, stdout: str = "ok") -> ScoringExpectation:
    return ScoringExpectation(
        conditions=(
            ProgramBehaves(
                runtime_profile_name=_PROFILE,
                cases=(ProgramCase(expected=ProgramResult(stdout=Equals(value=stdout))),),
            ),
        )
    )


async def test_real_client_returns_results_without_a_scorer_async(docker_sandbox: DockerSandboxClient) -> None:
    run = await docker_sandbox.execute_async(
        program="import sys\nprint(sys.argv[1] + ':' + sys.stdin.read())\n",
        runtime_profile_name=_PROFILE,
        invocations=(
            _ProgramInvocation(arguments=("first",), stdin="one"),
            _ProgramInvocation(arguments=("second",), stdin="two"),
        ),
    )
    assert [(event.invocation_index, event.exit_code, event.stdout) for event in run.events] == [
        (0, 0, "first:one\n"),
        (1, 0, "second:two\n"),
    ]
    assert run.reasons == run.warnings == ()


async def test_recorded_digest_matches_the_exact_executed_bytes_async(
    docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory
) -> None:
    program = (
        "import hashlib\nfrom pathlib import Path\nprint(hashlib.sha256(Path(__file__).read_bytes()).hexdigest())\n"
    )
    digest = hashlib.sha256(program.encode("utf-8")).hexdigest()
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    score = (
        await scorer.score_async(scorable=ContentScorable(value=program), expectation=_expectation(stdout=digest))
    )[0]
    assert score.get_value() is True
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.program_sha256 == digest
    assert observation.payload.events[0].stdout.strip() == digest


async def test_real_execution_persistence_and_replay_async(
    docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory
) -> None:
    condition = ProgramBehaves(
        runtime_profile_name=_PROFILE,
        cases=(
            ProgramCase(
                invocation=_ProgramInvocation(arguments=("3",)), expected=ProgramResult(stdout=Equals(value="9"))
            ),
            ProgramCase(
                invocation=_ProgramInvocation(stdin="12\n"), expected=ProgramResult(stdout=Equals(value="144"))
            ),
        ),
    )
    expectation = ScoringExpectation(objective="Square each input", conditions=(condition,))
    execution = ProgramExecutionScorable(
        program=ContentScorable(value=_PROGRAM), runtime_profile_name=_PROFILE, invocations=condition.invocations
    )
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    with patch.object(docker_sandbox, "execute_async", wraps=docker_sandbox.execute_async) as execute:
        first = (await scorer.score_async(scorable=execution, expectation=expectation))[0]
        second = (await scorer.score_async(scorable=execution, expectation=expectation))[0]
        assert execute.await_count == 2
        assert first.observation_ids != second.observation_ids
        stored = await sqlite_instance.get_scores_async(score_ids=[str(first.id), str(second.id)])
        assert len(stored) == 2
        for score in stored:
            assert score.get_value() is True
            assert score.status is ScoreStatus.COMPLETE
            assert score.scored_expectation == expectation
            assert score.scorable == execution
        observation = (await sqlite_instance.get_observations_async(observation_ids=first.observation_ids))[0]
        assert observation.acquisition is Acquisition.COMPLETE
        assert isinstance(observation.payload, ProgramExecutionObservationPayload)
        assert observation.payload.program_sha256 == hashlib.sha256(_PROGRAM.encode("utf-8")).hexdigest()
        assert [event.stdout for event in observation.payload.events] == ["9\n", "144\n"]
        for event, invocation in zip(observation.payload.events, condition.invocations, strict=True):
            assert event.runtime_profile_name == _PROFILE
            assert event.runtime_image_digest == _IMAGE.rsplit("@", 1)[1]
            assert event.resolved_command == ("python3", "-I", "/program/main.py", *invocation.arguments)
            assert event.exit_code == 0
            assert not event.timed_out and not event.output_truncated
        stricter = condition.model_copy(
            update={
                "cases": (
                    condition.cases[0],
                    ProgramCase(
                        invocation=condition.cases[1].invocation, expected=ProgramResult(stdout=Equals(value="145"))
                    ),
                )
            }
        )
        replayed = (
            await scorer.score_observation_async(
                observation=observation, expectation=ScoringExpectation(conditions=(stricter,))
            )
        )[0]
        assert replayed.get_value() is False
        assert replayed.observation_ids == first.observation_ids
        assert execute.await_count == 2
    saved_replay = (await sqlite_instance.get_scores_async(score_ids=[str(replayed.id)]))[0]
    assert saved_replay.get_value() is False
    assert saved_replay.observation_ids == first.observation_ids


@pytest.mark.parametrize(("program", "exit_code"), [("print('wrong')", 0), ("raise SystemExit(7)", 7), ("def (", 1)])
async def test_real_program_failure_is_false_async(
    docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory, program: str, exit_code: int
) -> None:
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    score = (await scorer.score_async(scorable=ContentScorable(value=program), expectation=_expectation()))[0]
    assert score.status is ScoreStatus.COMPLETE
    assert score.get_value() is False
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.COMPLETE
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events[0].exit_code == exit_code


async def test_real_timeout_is_false_async(docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory) -> None:
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    score = (
        await scorer.score_async(scorable=ContentScorable(value="while True:\n    pass\n"), expectation=_expectation())
    )[0]
    assert score.status is ScoreStatus.COMPLETE
    assert score.get_value() is False
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events[0].timed_out
    assert observation.payload.events[0].exit_code is None


async def test_real_output_is_bounded_async(docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory) -> None:
    expectation = ScoringExpectation(
        conditions=(
            ProgramBehaves(
                runtime_profile_name=_PROFILE,
                cases=(ProgramCase(expected=ProgramResult(stdout=Contains(value="xxx"))),),
            ),
        )
    )
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="import sys\nsys.stdout.write('x' * 8192)\nsys.stderr.write('e' * 8192)\n"),
            expectation=expectation,
        )
    )[0]
    assert score.get_value() is True
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    event = observation.payload.events[0]
    assert event.stdout == "x" * 128 and event.stdout_truncated
    assert event.stderr == "e" * 128 and event.stderr_truncated


async def test_missing_image_is_undetermined_async(
    docker_sandbox: DockerSandboxClient, sqlite_instance: SQLiteMemory
) -> None:
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    with patch.object(
        docker_sandbox,
        "_profiles",
        {_PROFILE: DockerRuntimeProfile(image="python@sha256:" + "0" * 64, command=("python3", "-I"))},
    ):
        score = (await scorer.score_async(scorable=ContentScorable(value="print('ok')"), expectation=_expectation()))[0]
    assert score.status is ScoreStatus.UNDETERMINED
    assert score.score_value is None
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.ERROR
    assert isinstance(observation.payload, ProgramExecutionObservationPayload)
    assert observation.payload.events == ()
    assert observation.payload.reasons


async def test_cancelling_real_execution_cleans_up_async(docker_sandbox: DockerSandboxClient) -> None:
    scorer = ProgramExecutionScorer(source=ProgramExecutionSource(sandbox=docker_sandbox))
    with (
        patch.object(docker_sandbox, "_timeout_seconds", 30),
        patch.object(docker_sandbox, "_run_arguments", wraps=docker_sandbox._run_arguments) as arguments,
    ):
        task = asyncio.create_task(
            scorer.score_async(scorable=ContentScorable(value="while True:\n    pass\n"), expectation=_expectation())
        )

        async def wait_until_running_async() -> None:
            while not task.done():
                if arguments.call_args is not None:
                    name = arguments.call_args.kwargs["name"]
                    status, output = await docker_sandbox._control_async(
                        "inspect", "--format", "{{.State.Running}}", name
                    )
                    if status == 0 and output.strip() == "true":
                        return
                await asyncio.sleep(0.01)
            pytest.fail(f"Execution ended before cancellation: {task.result()}")

        try:
            await asyncio.wait_for(wait_until_running_async(), timeout=15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=15)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
