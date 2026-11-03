# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic Docker fault injection; real execution is covered by integration tests."""

from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS, wait_for_event_async

from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient, SandboxExecutionError, docker_client
from pyrit._code_execution.docker_client import _ClientOutput, _ContainerState, _InvocationResult
from pyrit.models.code_execution import _ProgramInvocation, _SandboxRun

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

_T = TypeVar("_T")

_IMAGE = "python@sha256:" + "c" * 64
_STARTED = "2026-10-21T00:00:00Z"


@pytest.fixture
def client(tmp_path: Path) -> DockerSandboxClient:
    instance = DockerSandboxClient(
        profiles={"python": DockerRuntimeProfile(image=_IMAGE, command=("python3", "-I"))},
        docker_executable=str(tmp_path / "no-docker"),
    )
    instance._temp_root = tmp_path
    return instance


def _process(*, returncode: int | None = 0, output: bytes = b"ok", eof: bool = True) -> MagicMock:
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode, process.stdin = returncode, None
    readers = [asyncio.StreamReader(), asyncio.StreamReader()]
    for reader, data in zip(readers, (output, b""), strict=True):
        reader.feed_data(data)
        if eof:
            reader.feed_eof()
    process.stdout, process.stderr = readers
    exited = asyncio.Event()
    if returncode is not None:
        exited.set()

    async def wait_async() -> int:
        await exited.wait()
        code = process.returncode
        assert isinstance(code, int)
        return code

    def kill() -> None:
        process.returncode = -9
        exited.set()
        for reader in readers:
            reader.feed_eof()

    process.wait = AsyncMock(side_effect=wait_async)
    process.kill.side_effect = kill
    process.communicate = AsyncMock(return_value=(b"", b""))
    return process


def _control(
    *,
    state: str = "exited",
    code: int = 0,
    oom: bool = False,
    removal_fails: bool = False,
    capabilities: str = "cgroupfs true true true true true",
    volumes: str = "null",
) -> AsyncMock:
    async def command_async(*arguments: str) -> tuple[int, str]:
        nonlocal state, code
        if arguments[0] == "info":
            return 0, capabilities
        if arguments[:2] == ("image", "inspect"):
            return 0, volumes
        if arguments[0] == "inspect":
            started = "0001-01-01T00:00:00Z" if state == "created" else _STARTED
            return 0, f"{state} {code} {str(oom).lower()} {started}"
        if arguments[0] == "kill":
            state, code = "exited", 137
            return 0, ""
        if arguments[:2] == ("container", "ls"):
            return 0, ""
        assert arguments[:3] == ("rm", "--force", "--volumes"), arguments
        return (1, "daemon unavailable") if removal_fails else (0, "")

    return AsyncMock(side_effect=command_async)


async def _run_async(client: DockerSandboxClient) -> _SandboxRun:
    return await client.execute_async(
        program="print('ok')", runtime_profile_name="python", invocations=(_ProgramInvocation(),)
    )


@pytest.mark.parametrize(
    "capabilities",
    [
        "none true true true true true",
        "cgroupfs false true true true true",
        "cgroupfs true false true true true",
        "systemd true true false true true",
        "systemd true true true false true",
        "systemd true true true true false",
        "",
        "unexpected response",
    ],
)
async def test_unsupported_resource_controls_prevent_execution_async(
    client: DockerSandboxClient, tmp_path: Path, capabilities: str
) -> None:
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as launch,
        patch.object(client, "_control_async", _control(capabilities=capabilities)),
        pytest.raises(SandboxExecutionError, match="must support cgroup"),
    ):
        await _run_async(client)
    launch.assert_not_awaited()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.parametrize("volumes", ['{"/data":{}}', '{"/tmp":{}}', "invalid metadata"])
async def test_image_volumes_prevent_execution_async(client: DockerSandboxClient, tmp_path: Path, volumes: str) -> None:
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as launch,
        patch.object(client, "_control_async", _control(volumes=volumes)),
        pytest.raises(SandboxExecutionError, match="must not declare volumes"),
    ):
        await _run_async(client)
    launch.assert_not_awaited()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_systemd_controls_and_empty_volume_map_allow_execution_async(client: DockerSandboxClient) -> None:
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()),
        patch.object(client, "_control_async", _control(capabilities="systemd true true true true true", volumes="{}")),
    ):
        assert (await _run_async(client)).events[0].exit_code == 0


@pytest.mark.parametrize(("command", "message"), [("info", "resource support"), ("image", "runtime image")])
async def test_preflight_failures_prevent_execution_async(
    client: DockerSandboxClient, command: str, message: str
) -> None:
    healthy = _control()

    async def control_async(*arguments: str) -> tuple[int, str]:
        if arguments[0] == command:
            return 1, "unavailable"
        response: tuple[int, str] = await healthy(*arguments)
        return response

    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as launch,
        patch.object(client, "_control_async", side_effect=control_async),
        pytest.raises(SandboxExecutionError, match=f"could not inspect.*{message}"),
    ):
        await _run_async(client)
    launch.assert_not_awaited()


def test_run_arguments_enforce_runtime_and_isolation(client: DockerSandboxClient, tmp_path: Path) -> None:
    command = ("python3", "-I", "/program/main.py", "argument")
    argv = client._run_arguments(
        name="test-container", profile=client._profiles["python"], program_dir=tmp_path, resolved_command=command
    )
    for flag, value in {
        "--pull": "never",
        "--log-driver": "none",
        "--network": "none",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges",
        "--user": "65534:65534",
        "--memory": "256m",
        "--memory-swap": "256m",
        "--cpus": "1.0",
        "--pids-limit": "64",
    }.items():
        assert argv[argv.index(flag) + 1] == value
    assert "--read-only" in argv and "--privileged" not in argv
    assert argv[argv.index("--entrypoint") + 1] == command[0]
    assert argv.index("--entrypoint") < argv.index(_IMAGE)
    assert argv[argv.index(_IMAGE) + 1 :] == list(command[1:])
    assert argv[argv.index("--mount") + 1].endswith(",target=/program,readonly")


@pytest.mark.parametrize(
    ("state", "code", "oom", "client_code", "error"),
    [
        ("exited", 0, False, 0, None),
        ("exited", 125, False, 125, None),
        ("exited", 137, True, 137, None),
        ("running", 0, False, 1, "still running"),
        ("created", 125, False, 125, "did not start"),
        ("exited", 0, False, 1, "output capture may be incomplete"),
    ],
)
async def test_container_and_client_outcomes_async(
    client: DockerSandboxClient,
    tmp_path: Path,
    state: str,
    code: int,
    oom: bool,
    client_code: int,
    error: str | None,
) -> None:
    process = _process(returncode=client_code)
    control = _control(state=state, code=code, oom=oom)
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process),
        patch.object(client, "_control_async", control),
        patch.object(client, "_run_arguments", wraps=client._run_arguments) as arguments,
    ):
        if error is not None:
            with pytest.raises(SandboxExecutionError, match=error):
                await _run_async(client)
        else:
            event = (await _run_async(client)).events[0]
            assert event.exit_code == (None if oom else code)
            assert event.resource_limit_exceeded is oom
            assert event.runtime_image_digest == "sha256:" + "c" * 64
    assert control.call_args_list[-1].args[:3] == ("rm", "--force", "--volumes")
    mounted_dir = arguments.call_args.kwargs["program_dir"]
    assert mounted_dir.name == "program"
    assert mounted_dir.parent.parent == tmp_path
    process.communicate.assert_awaited_once()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_startup_consumes_shared_deadline_before_capture_async(client: DockerSandboxClient) -> None:
    process = _process()
    scope_entered = asyncio.Event()

    async def start_async(*, argv: list[str]) -> MagicMock:
        del argv
        assert scope_entered.is_set()
        return process

    output = _ClientOutput(stdout=b"", stdout_truncated=False, stderr=b"", stderr_truncated=False)
    container = _ContainerState(status="exited", exit_code=0, oom_killed=False)
    wait = AsyncMock(return_value=_InvocationResult(output=output, container=container))

    class _NoopTimeout:
        async def __aenter__(self) -> None:
            scope_entered.set()

        async def __aexit__(self, *args: object) -> bool:
            return False

    with (
        patch("pyrit._code_execution.docker_client.asyncio.timeout_at", return_value=_NoopTimeout()) as timeout_at,
        patch.object(client, "_start_client_async", side_effect=start_async),
        patch.object(client, "_wait_for_invocation_async", wait),
        patch.object(client, "_control_async", _control()),
    ):
        assert (await _run_async(client)).events[0].exit_code == 0

    deadline = timeout_at.call_args.args[0]
    assert wait.call_args.kwargs["deadline"] == deadline


async def test_deadline_expiry_during_startup_prevents_capture_async(
    client: DockerSandboxClient, tmp_path: Path
) -> None:
    process, control = _process(returncode=None), _control()

    async def start_async(*, argv: list[str]) -> MagicMock:
        await asyncio.sleep(0)
        return process

    def timeout_at(deadline: float) -> asyncio.Timeout:
        return asyncio.timeout_at(0)

    proxy = SimpleNamespace(**vars(asyncio))
    proxy.timeout_at = timeout_at
    with (
        patch.object(docker_client, "asyncio", proxy),
        patch.object(client, "_start_client_async", side_effect=start_async),
        patch.object(client, "_wait_for_invocation_async", new_callable=AsyncMock) as wait,
        patch.object(client, "_control_async", control),
        pytest.raises(SandboxExecutionError, match="did not finish starting before its deadline"),
    ):
        await _run_async(client)
    wait.assert_not_awaited()
    process.kill.assert_called_once()
    process.communicate.assert_awaited_once()
    assert control.call_args.args[:3] == ("rm", "--force", "--volumes")
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_capture_and_client_wait_use_remaining_shared_deadline_async(client: DockerSandboxClient) -> None:
    process = _process(returncode=None)
    budgets: list[float | None] = []

    async def stopped_async() -> int:
        process.returncode = 0
        return 0

    async def wait_for_async(future: Awaitable[_T], *, timeout: float | None) -> _T:
        budgets.append(timeout)
        return await asyncio.wait_for(future, timeout=timeout)

    process.wait = AsyncMock(side_effect=stopped_async)
    proxy = SimpleNamespace(**vars(asyncio))
    proxy.wait_for = wait_for_async
    clock = SimpleNamespace(**vars(time))
    clock.monotonic = MagicMock(side_effect=(101.0, 104.0))
    with (
        patch.object(docker_client, "asyncio", proxy),
        patch.object(docker_client, "time", clock),
        patch.object(client, "_control_async", _control()),
    ):
        result = await client._wait_for_invocation_async(process=process, name="test", stdin=None, deadline=110.0)
    assert budgets == [9.0, 6.0]
    assert result.container.exit_code == 0 and not result.timed_out
    assert result.output.stdout == b"ok"


@pytest.mark.parametrize(("before_capture_yields", "after_eof_yields"), [(0, 0), (1, 0), (0, 3), (3, 3)])
@pytest.mark.parametrize(
    ("eof", "state", "error"),
    [
        (False, "exited", "a program timeout could not be established"),
        (True, "running", "still running"),
        (True, "exited", None),
        (False, "running", None),
    ],
)
async def test_deadline_distinguishes_stalled_capture_client_and_program_async(
    client: DockerSandboxClient,
    tmp_path: Path,
    eof: bool,
    state: str,
    error: str | None,
    before_capture_yields: int,
    after_eof_yields: int,
) -> None:
    process, control = _process(returncode=None, eof=eof), _control(state=state)
    collecting, captured = asyncio.Event(), asyncio.Event()
    phases: list[str] = []
    capture_tasks: list[asyncio.Task[object]] = []
    collect = client._collect_output_async

    async def collect_async(*, process: asyncio.subprocess.Process, stdin: str | None) -> _ClientOutput:
        task = asyncio.current_task()
        assert task is not None
        capture_tasks.append(task)
        for _ in range(before_capture_yields):
            await asyncio.sleep(0)
        collecting.set()
        output = await collect(process=process, stdin=stdin)
        for _ in range(after_eof_yields):
            await asyncio.sleep(0)
        captured.set()
        return output

    async def wait_for_async(future: Awaitable[_T], *, timeout: float | None) -> _T:
        if not phases:
            if eof:
                output = await asyncio.wait_for(future, timeout=TEST_WATCHDOG_SECONDS)
                assert captured.is_set()
                phases.append("capture-complete")
                return output
            assert isinstance(future, asyncio.Future)
            await wait_for_event_async(event=collecting, tasks=[future])
            assert not future.done() and not captured.is_set()
            phases.append("capture-timeout")
            return await asyncio.wait_for(future, timeout=0)
        if eof:
            assert phases == ["capture-complete"] and captured.is_set()
            phases.append("client-timeout")
            return await asyncio.wait_for(future, timeout=0)
        assert phases == ["capture-timeout"]
        phases.append("capture-drain")
        return await asyncio.wait_for(future, timeout=timeout)

    # Change only this module's waits; collection, classification, and cleanup remain real.
    proxy = SimpleNamespace(**vars(asyncio))
    proxy.wait_for = wait_for_async
    with (
        patch.object(docker_client, "asyncio", proxy),
        patch.object(client, "_collect_output_async", side_effect=collect_async),
        patch.object(proxy, "create_subprocess_exec", new_callable=AsyncMock, return_value=process),
        patch.object(client, "_control_async", control),
    ):
        if error is not None:
            with pytest.raises(SandboxExecutionError, match=error):
                await _run_async(client)
        else:
            event = (await _run_async(client)).events[0]
            assert event.timed_out is (not eof)
            assert event.exit_code == (0 if eof else None)
            assert event.stdout_truncated and event.stderr_truncated
            assert event.stdout == "ok" and event.stderr == ""
    expected_phases = ["capture-complete", "client-timeout"] if eof else ["capture-timeout"]
    if not eof and state == "running":
        expected_phases.append("capture-drain")
    assert phases == expected_phases
    assert len(capture_tasks) == 1 and capture_tasks[0].done()
    commands = [call.args[0] for call in control.call_args_list]
    assert ("kill" in commands) is (state == "running" and not eof)
    assert commands.count("kill") == int(state == "running" and not eof)
    assert commands.count("rm") == 1
    assert commands[-1] == "rm"
    process.kill.assert_called_once()
    process.communicate.assert_awaited_once()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.parametrize("count", [1, 2])
async def test_cleanup_failure_keeps_evidence_and_stops_further_invocations_async(
    client: DockerSandboxClient, count: int
) -> None:
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()) as launch,
        patch.object(client, "_control_async", _control(removal_fails=True)),
    ):
        run = await client.execute_async(
            program="print('ok')", runtime_profile_name="python", invocations=(_ProgramInvocation(),) * count
        )
    assert len(run.events) == 1 and run.events[0].exit_code == 0
    assert "could not remove container" in run.warnings[0]
    assert bool(run.reasons) is (count > 1)
    launch.assert_awaited_once()


async def test_failure_after_a_completed_invocation_returns_partial_results_async(client: DockerSandboxClient) -> None:
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=[_process(), OSError("offline")]),
        patch.object(client, "_control_async", _control()),
    ):
        run = await client.execute_async(
            program="print('ok')", runtime_profile_name="python", invocations=(_ProgramInvocation(),) * 2
        )
    assert [event.invocation_index for event in run.events] == [0]
    assert "invocation 1" in run.reasons[0] and "offline" in run.reasons[0]


@pytest.mark.parametrize("failed", [False, True])
async def test_startup_is_owned_until_cleanup_async(client: DockerSandboxClient, tmp_path: Path, failed: bool) -> None:
    process, entered, release = _process(returncode=None), asyncio.Event(), asyncio.Event()
    created: list[str] = []
    healthy = _control()

    async def start_async(*args: str, **kwargs: object) -> MagicMock:
        created.append(args[args.index("--name") + 1])
        entered.set()
        await release.wait()
        if failed:
            raise OSError("stdio connection failed after container creation")
        return process

    async def control_async(*arguments: str) -> tuple[int, str]:
        if arguments[0] in ("info", "image"):
            response: tuple[int, str] = await healthy(*arguments)
            return response
        if arguments[:2] == ("container", "ls"):
            assert f"name=^/{created[0]}$" in arguments
            return 0, created[0]
        assert arguments == ("rm", "--force", "--volumes", created[0])
        return 0, ""

    with (
        patch("asyncio.create_subprocess_exec", side_effect=start_async),
        patch.object(client, "_control_async", side_effect=control_async) as control,
    ):
        task = asyncio.create_task(_run_async(client))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if not failed:
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0.01)
                assert not task.done()
                assert all(call.args[0] in ("info", "image") for call in control.call_args_list)
            release.set()
            with pytest.raises(SandboxExecutionError if failed else asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert control.call_args.args == ("rm", "--force", "--volumes", created[0])
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []
    if not failed:
        process.kill.assert_called_once()
        process.communicate.assert_awaited_once()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_program_bytes_and_readonly_file_cleanup(client: DockerSandboxClient, tmp_path: Path, newline: str) -> None:
    workspace = tmp_path / "workspace"
    path = workspace / "program" / "main.py"
    program = "print('\u00e9')\nprint(42)\n".replace("\n", newline)
    try:
        client._write_program(workspace_dir=workspace, program_path=path, program=program)
        assert path.read_bytes() == program.encode("utf-8")
        if os.name == "posix":
            assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
            assert stat.S_IMODE(path.stat().st_mode) == 0o444
    finally:
        client._remove_workspace(workspace_dir=workspace, program_path=path)
    assert not workspace.exists()


async def test_partial_workspace_is_removed_after_write_failure_async(
    client: DockerSandboxClient, tmp_path: Path
) -> None:
    with (
        patch.object(client, "_control_async", _control()),
        patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as launch,
        pytest.raises(OSError, match="disk full"),
    ):
        await _run_async(client)
    launch.assert_not_awaited()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.parametrize("stage", ["drain", "close"])
async def test_input_cancellation_aborts_buffered_writes_async(stage: str) -> None:
    stream = MagicMock(spec=asyncio.StreamWriter)
    stream.transport = MagicMock(spec=asyncio.WriteTransport)
    blocked, released = asyncio.Event(), asyncio.Event()

    async def drain_async() -> None:
        if stage == "drain":
            blocked.set()
            await asyncio.Event().wait()

    async def close_async() -> None:
        if stage == "close":
            blocked.set()
        await released.wait()

    stream.transport.abort.side_effect = released.set
    stream.drain, stream.wait_closed = AsyncMock(side_effect=drain_async), AsyncMock(side_effect=close_async)
    task = asyncio.create_task(DockerSandboxClient._feed_stdin_async(stream=stream, data="input"))
    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stream.transport.abort.assert_called_once()
    stream.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("phase", ["write", "remove"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_file_work_does_not_block_or_outlive_the_call_async(
    client: DockerSandboxClient, tmp_path: Path, phase: str, cancel: bool
) -> None:
    entered, release = threading.Event(), threading.Event()
    operation = "_write_program" if phase == "write" else "_remove_workspace"
    original = getattr(client, operation)
    loop_thread = threading.get_ident()

    def gated(**kwargs: object) -> None:
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(5), "file work blocked the event loop"
        original(**kwargs)

    with (
        patch.object(client, operation, side_effect=gated),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()),
        patch.object(client, "_control_async", _control()),
    ):
        task = asyncio.create_task(_run_async(client))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            if cancel:
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0.01)
                assert not task.done()
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            else:
                assert (await asyncio.wait_for(task, timeout=5)).events[0].exit_code == 0
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.parametrize("phase", ["container", "management"])
async def test_repeated_cancellation_drains_process_cleanup_async(client: DockerSandboxClient, phase: str) -> None:
    process = _process(returncode=None, eof=False)
    running, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def communicate_async() -> tuple[bytes, bytes]:
        nonlocal calls
        calls += 1
        if phase == "management" and calls == 1:
            running.set()
            await asyncio.Event().wait()
        cleaning.set()
        await release.wait()
        return b"", b""

    async def collect_async(**kwargs: object) -> None:
        running.set()
        await asyncio.Event().wait()

    process.communicate = AsyncMock(side_effect=communicate_async)
    control = _control()
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process),
        patch.object(client, "_collect_output_async", side_effect=collect_async),
        patch.object(client, "_control_async", control) if phase == "container" else nullcontext(),
    ):
        task = asyncio.create_task(
            _run_async(client) if phase == "container" else client._control_async("inspect", "test")
        )
        try:
            await asyncio.wait_for(running.wait(), timeout=5)
            task.cancel()
            await asyncio.wait_for(cleaning.wait(), timeout=5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    process.kill.assert_called_once()
    assert process.communicate.await_count == (2 if phase == "management" else 1)
    if phase == "container":
        removals = [call for call in control.call_args_list if call.args[0] == "rm"]
        assert len(removals) == 1
        assert removals[0].args[:3] == ("rm", "--force", "--volumes")


@pytest.mark.parametrize("case", ["empty", "unknown", "missing-client"])
async def test_invalid_requests_fail_before_execution_async(client: DockerSandboxClient, case: str) -> None:
    with pytest.raises(SandboxExecutionError if case == "missing-client" else ValueError):
        await client.execute_async(
            program="print(1)",
            runtime_profile_name="unknown" if case == "unknown" else "python",
            invocations=() if case == "empty" else (_ProgramInvocation(),),
        )


@pytest.mark.parametrize(
    "settings", [{"timeout_seconds": 0}, {"pids_limit": 0}, {"max_output_bytes": 0}, {"memory": "bad"}]
)
def test_invalid_limits(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DockerSandboxClient(profiles={"python": DockerRuntimeProfile(image=_IMAGE, command=("python3",))}, **settings)  # type: ignore[arg-type]


def test_profiles_are_explicit_trusted_configuration() -> None:
    for settings in (
        {"image": "python:latest", "command": ("python3",)},
        {"image": _IMAGE, "command": ()},
        {"image": _IMAGE, "command": ("python3",), "program_filename": "../main.py"},
    ):
        with pytest.raises(ValueError):
            DockerRuntimeProfile(**settings)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DockerSandboxClient(profiles={})
    with pytest.raises(ValueError):
        DockerSandboxClient(profiles={"bad name": DockerRuntimeProfile(image=_IMAGE, command=("python3",))})


_RUNTIMES = '{"io.containerd.runc.v2":{"path":"runc"},"runc":{"path":"runc"},"runsc":{"path":"/usr/bin/runsc"}}'


def _runtime_client(tmp_path: Path, *, runtime: str | None = "runsc") -> DockerSandboxClient:
    instance = DockerSandboxClient(
        profiles={"python": DockerRuntimeProfile(image=_IMAGE, command=("python3", "-I"), runtime=runtime)},
        docker_executable=str(tmp_path / "no-docker"),
    )
    instance._temp_root = tmp_path
    return instance


def _control_with_runtimes(*, status: int = 0, runtimes: str = _RUNTIMES) -> AsyncMock:
    healthy = _control()

    async def control_async(*arguments: str) -> tuple[int, str]:
        if arguments == ("info", "--format", "{{json .Runtimes}}"):
            return status, runtimes
        response: tuple[int, str] = await healthy(*arguments)
        return response

    return AsyncMock(side_effect=control_async)


async def test_profile_runtime_runs_the_container_under_that_runtime_async(tmp_path: Path) -> None:
    client = _runtime_client(tmp_path)
    control = _control_with_runtimes()
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()) as launch,
        patch.object(client, "_control_async", control),
    ):
        assert (await _run_async(client)).events[0].exit_code == 0
    argv = list(launch.await_args.args)
    assert argv[argv.index("--runtime") + 1] == "runsc"
    assert argv.index("--runtime") < argv.index(_IMAGE)
    assert control.call_args_list[0].args[0] == "info"
    assert ("info", "--format", "{{json .Runtimes}}") in [call.args for call in control.call_args_list]


@pytest.mark.parametrize(
    ("status", "runtimes", "message"),
    [
        (0, '{"runc":{"path":"runc"}}', "does not offer the 'runsc' runtime"),
        (0, "{}", "does not offer the 'runsc' runtime"),
        (0, "not json", "unreadable runtimes"),
        (0, '["runsc"]', "unreadable runtimes"),
        (1, "permission denied", "could not inspect Docker runtimes"),
    ],
)
async def test_a_runtime_the_daemon_does_not_offer_prevents_execution_async(
    tmp_path: Path, status: int, runtimes: str, message: str
) -> None:
    client = _runtime_client(tmp_path)
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()) as launch,
        patch.object(client, "_control_async", _control_with_runtimes(status=status, runtimes=runtimes)),
        pytest.raises(SandboxExecutionError, match=message),
    ):
        await _run_async(client)
    launch.assert_not_awaited()
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


async def test_default_runtime_keeps_the_command_line_and_identifier_async(client: DockerSandboxClient) -> None:
    control = _control()
    with (
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_process()) as launch,
        patch.object(client, "_control_async", control),
    ):
        await _run_async(client)
    assert "--runtime" not in launch.await_args.args
    assert ("info", "--format", "{{json .Runtimes}}") not in [call.args for call in control.call_args_list]
    assert set(client.get_identifier().params["profiles"]["python"]) == {"image", "command", "program_filename"}


def test_identifier_records_the_profile_runtime(client: DockerSandboxClient, tmp_path: Path) -> None:
    hardened = _runtime_client(tmp_path).get_identifier()
    assert hardened.params["profiles"]["python"]["runtime"] == "runsc"
    assert hardened != client.get_identifier()


@pytest.mark.parametrize("runtime", ["", "--privileged", "run sc", "../runsc"])
def test_runtime_must_be_a_plain_name(runtime: str) -> None:
    with pytest.raises(ValueError, match="plain runtime name"):
        DockerRuntimeProfile(image=_IMAGE, command=("python3",), runtime=runtime)
