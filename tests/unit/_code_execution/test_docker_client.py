# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic Docker fault injection; real execution is covered by integration tests."""

from __future__ import annotations

import asyncio
import os
import stat
import threading
from contextlib import nullcontext
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit._code_execution import DockerRuntimeProfile, DockerSandboxClient, SandboxExecutionError
from pyrit.models.code_execution import _ProgramInvocation, _SandboxRun

if TYPE_CHECKING:
    from pathlib import Path

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
    client: DockerSandboxClient, eof: bool, state: str, error: str | None
) -> None:
    process, control = _process(returncode=None, eof=eof), _control(state=state)
    with (
        patch.object(client, "_timeout_seconds", 0.05),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process),
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
    commands = [call.args[0] for call in control.call_args_list]
    assert ("kill" in commands) is (state == "running" and not eof)
    assert commands[-1] == "rm"
    process.kill.assert_called_once()


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
