# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise DockerSandboxClient against a fake Docker command line client."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from pyrit.models import ProgramExecutionEvent, ProgramInvocation
from pyrit.score import DockerRuntimeProfile, DockerSandboxClient, SandboxClient, SandboxExecutionError

if TYPE_CHECKING:
    from pathlib import Path

_IMAGE = "python@sha256:" + "c" * 64

# The fake runs the "program" as a scenario keyword, and keeps container state in files so
# that inspect, kill and rm see what run did.
_FAKE_DOCKER = r"""
import json, os, signal, sys, time
from pathlib import Path

state = Path(os.environ["FAKE_DOCKER_STATE"])
args = sys.argv[1:]
command = args[0]

def path(name, suffix):
    return state / f"{name}.{suffix}"

if command == "run":
    name = args[args.index("--name") + 1]
    path(name, "argv").write_text(json.dumps(args))
    mount = args[args.index("--mount") + 1]
    source = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)["source"]
    image_index = next(i for i, a in enumerate(args) if "@sha256:" in a)
    inner = args[image_index + 1:]
    # Docker runs the image's entrypoint before the command, unless --entrypoint replaces it.
    entrypoint = os.environ.get("FAKE_IMAGE_ENTRYPOINT")
    if "--entrypoint" in args:
        entrypoint = args[args.index("--entrypoint") + 1]
    path(name, "effective").write_text(json.dumps(([entrypoint] if entrypoint else []) + inner))
    program_arg = next(a for a in inner if a.startswith("/program/"))
    program = (Path(source) / program_arg.rsplit("/", 1)[1]).read_text()
    user_args = inner[inner.index(program_arg) + 1:]
    flags = {"rm_fails": program == "RMFAIL", "slow_inspect": program == "SLOWINSPECT"}
    def save(**info):
        path(name, "state").write_text(json.dumps({**flags, **info}))
    def finish(code, oom=False, started=True):
        save(status="exited" if started else "created", code=code, oom=oom,
             started="2026-10-14T00:00:00Z" if started else "0001-01-01T00:00:00Z")
        sys.stdout.flush()
        sys.exit(code)
    path(name, "pid").write_text(str(os.getpid()))
    save(status="running", code=0, oom=False, started="2026-10-14T00:00:00Z")
    if program in ("CLOSEHANG", "CLOSEHANGRUNNING"):
        # The client closes its output streams but does not exit. With CLOSEHANG the
        # container has finished; with CLOSEHANGRUNNING it is still running.
        print("done")
        sys.stdout.flush()
        if program == "CLOSEHANG":
            save(status="exited", code=0, oom=False, started="2026-10-14T00:00:00Z")
        for fd in (1, 2):
            os.close(fd)
        (state / "hang.pid").write_text(str(os.getpid()))
        time.sleep(60)
        sys.exit(0)
    if program == "DETACHFAIL":
        # The attached client fails while the container keeps running.
        sys.stderr.write("error waiting for container: unexpected EOF")
        sys.exit(1)
    if program == "ECHO":
        data = sys.stdin.read()
        print(" ".join(user_args))
        sys.stderr.write(data)
        finish(0)
    elif program.startswith("EXIT "):
        finish(int(program.split()[1]))
    elif program == "SLEEP":
        time.sleep(60)
        finish(0)
    elif program == "OOM":
        finish(137, oom=True)
    elif program == "BIG":
        sys.stdout.write("a" * 200_000)
        finish(0)
    elif program == "NOSTART":
        sys.stderr.write("docker: Error response from daemon: No such image")
        finish(125, started=False)
    elif program == "FAILSECOND":
        marker = state / "ran-once"
        if marker.exists():
            sys.stderr.write("docker: daemon went away")
            finish(125, started=False)
        marker.write_text("x")
        finish(0)
    finish(0)
elif command == "inspect":
    name = args[-1]
    try:
        info = json.loads(path(name, "state").read_text())
    except FileNotFoundError:
        print("Error: No such object: " + name)
        sys.exit(1)
    if info.get("slow_inspect"):
        (state / "inspect.pid").write_text(str(os.getpid()))
        time.sleep(60)
    print(info["status"], info["code"], "true" if info["oom"] else "false", info["started"])
elif command == "kill":
    name = args[-1]
    pid = int(path(name, "pid").read_text())
    os.kill(pid, signal.SIGKILL)
    info = json.loads(path(name, "state").read_text())
    info["code"] = 137
    info["status"] = "exited"
    path(name, "state").write_text(json.dumps(info))
elif command == "rm":
    name = args[-1]
    try:
        info = json.loads(path(name, "state").read_text())
    except FileNotFoundError:
        info = {}
    if info.get("rm_fails"):
        print("Error response from daemon: daemon unavailable")
        sys.exit(1)
    with (state / "removed.log").open("a") as log:
        log.write(name + "\n")
    path(name, "state").unlink(missing_ok=True)
"""


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if sys.platform == "win32":
        pytest.skip("The fake Docker client is a POSIX script.")
    state = tmp_path / "state"
    state.mkdir()
    script = tmp_path / "docker"
    script.write_text(f"#!{sys.executable}\n{_FAKE_DOCKER}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    return script


def _client(docker: Path, **overrides: object) -> DockerSandboxClient:
    settings: dict[str, object] = {
        "profiles": {"python-3.12": DockerRuntimeProfile(image=_IMAGE, command=("python3", "-I"))},
        "docker_executable": str(docker),
        "timeout_seconds": 5.0,
    }
    settings.update(overrides)
    return DockerSandboxClient(**settings)  # type: ignore[arg-type]


async def _run(client: DockerSandboxClient, program: str, *invocations: ProgramInvocation):  # type: ignore[no-untyped-def]
    return await client.execute_async(
        program=program, runtime_profile="python-3.12", invocations=invocations or (ProgramInvocation(),)
    )


def _removed(docker: Path) -> list[str]:
    log = docker.parent / "state" / "removed.log"
    return log.read_text().split() if log.exists() else []


async def test_runs_each_invocation_with_its_arguments_and_input_async(fake_docker: Path) -> None:
    run = await _run(
        _client(fake_docker),
        "ECHO",
        ProgramInvocation(arguments=("a", "b c"), stdin="input one"),
        ProgramInvocation(arguments=("z",)),
    )

    assert [event.case_index for event in run.events] == [0, 1]
    first, second = run.events
    assert (first.stdout, first.stderr, first.exit_code) == ("a b c\n", "input one", 0)
    assert (second.stdout, second.stderr) == ("z\n", "")
    assert first.resolved_command == ("python3", "-I", "/program/main.py", "a", "b c")
    assert first.runtime_image_digest == "sha256:" + "c" * 64
    assert first.runtime_profile == "python-3.12"
    assert run.reasons == ()


async def test_containers_are_locked_down_and_removed_async(fake_docker: Path) -> None:
    await _run(_client(fake_docker, memory="128m", cpus=0.5, pids_limit=8), "ECHO")

    argv_files = list((fake_docker.parent / "state").glob("*.argv"))
    assert len(argv_files) == 1
    argv = json.loads(argv_files[0].read_text())
    for flag, value in [
        ("--network", "none"),
        ("--pull", "never"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--user", "65534:65534"),
        ("--memory", "128m"),
        ("--memory-swap", "128m"),
        ("--cpus", "0.5"),
        ("--pids-limit", "8"),
        ("--log-driver", "none"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    assert "--read-only" in argv
    assert "--privileged" not in argv
    assert "--log-opt" not in argv
    mount = argv[argv.index("--mount") + 1]
    assert mount.endswith(",target=/program,readonly")
    # The profile's first argument is the entrypoint, so the image's own entrypoint never runs.
    assert argv[argv.index("--entrypoint") + 1] == "python3"
    assert argv.index("--entrypoint") < argv.index(_IMAGE)
    assert argv[argv.index(_IMAGE) + 1 :] == ["-I", "/program/main.py"]
    assert _removed(fake_docker) == [argv_files[0].stem]


async def test_an_image_entrypoint_does_not_change_the_command_that_runs_async(
    fake_docker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_IMAGE_ENTRYPOINT", "python3")

    run = await _run(_client(fake_docker), "ECHO", ProgramInvocation(arguments=("x",)))

    effective = json.loads(next((fake_docker.parent / "state").glob("*.effective")).read_text())
    assert tuple(effective) == run.events[0].resolved_command == ("python3", "-I", "/program/main.py", "x")
    assert run.events[0].exit_code == 0


async def test_program_exit_codes_are_observed_not_errors_async(fake_docker: Path) -> None:
    run = await _run(_client(fake_docker), "EXIT 125")

    assert run.events[0].exit_code == 125
    assert not run.events[0].timed_out


async def test_timeout_kills_the_container_async(fake_docker: Path) -> None:
    run = await _run(_client(fake_docker, timeout_seconds=0.5), "SLEEP")

    event = run.events[0]
    assert event.timed_out
    assert event.exit_code is None
    assert not event.resource_limit_exceeded
    assert len(_removed(fake_docker)) == 1


def _alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text()), 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_client_that_closes_its_streams_but_never_exits_is_bounded_async(fake_docker: Path) -> None:
    # The container finished, so its exit is real evidence even though the client hung.
    run = await asyncio.wait_for(_run(_client(fake_docker, timeout_seconds=1.0), "CLOSEHANG"), timeout=10)

    event = run.events[0]
    assert (event.exit_code, event.stdout, event.timed_out) == (0, "done\n", False)
    assert not _alive(fake_docker.parent / "state" / "hang.pid")
    assert len(_removed(fake_docker)) == 1


async def test_a_hung_client_over_a_running_container_is_a_bounded_sandbox_error_async(fake_docker: Path) -> None:
    # The container never finished, so the result is a sandbox error, not a program timeout.
    with pytest.raises(SandboxExecutionError, match="still running"):
        await asyncio.wait_for(_run(_client(fake_docker, timeout_seconds=1.0), "CLOSEHANGRUNNING"), timeout=10)

    assert not _alive(fake_docker.parent / "state" / "hang.pid")
    assert len(_removed(fake_docker)) == 1


async def test_memory_kill_is_a_resource_limit_async(fake_docker: Path) -> None:
    event = (await _run(_client(fake_docker), "OOM")).events[0]

    assert event.resource_limit_exceeded
    assert event.exit_code is None


async def test_output_is_capped_per_stream_async(fake_docker: Path) -> None:
    event = (await _run(_client(fake_docker, max_output_bytes=1000), "BIG")).events[0]

    assert event.stdout == "a" * 1000
    assert event.stdout_truncated
    assert not event.stderr_truncated


async def test_a_container_that_never_starts_is_a_sandbox_error_async(fake_docker: Path) -> None:
    with pytest.raises(SandboxExecutionError, match="No such image"):
        await _run(_client(fake_docker), "NOSTART")
    assert len(_removed(fake_docker)) == 1


async def test_a_failure_after_some_runs_returns_what_ran_async(fake_docker: Path) -> None:
    run = await _run(_client(fake_docker), "FAILSECOND", ProgramInvocation(), ProgramInvocation())

    assert [event.case_index for event in run.events] == [0]
    assert "case 1" in run.reasons[0]


async def test_a_missing_docker_client_is_a_sandbox_error_async(tmp_path: Path) -> None:
    with pytest.raises(SandboxExecutionError, match="could not start"):
        await _run(_client(tmp_path / "no-such-docker"), "ECHO")


async def test_unknown_profile_is_rejected_async(fake_docker: Path) -> None:
    with pytest.raises(ValueError, match="not configured"):
        await _client(fake_docker).execute_async(
            program="ECHO", runtime_profile="node-20", invocations=(ProgramInvocation(),)
        )


def test_profile_image_must_be_pinned_by_digest() -> None:
    with pytest.raises(ValueError, match="pinned by digest"):
        DockerRuntimeProfile(image="python:3.12-slim", command=("python3",))
    with pytest.raises(ValueError, match="nonempty"):
        DockerRuntimeProfile(image=_IMAGE, command=())
    with pytest.raises(ValueError, match="plain file name"):
        DockerRuntimeProfile(image=_IMAGE, command=("python3",), program_filename="../main.py")


@pytest.mark.parametrize(
    "overrides",
    [
        {"profiles": {}},
        {"profiles": {"bad name": DockerRuntimeProfile(image=_IMAGE, command=("python3",))}},
        {"timeout_seconds": 0},
        {"pids_limit": 0},
        {"max_output_bytes": 0},
        {"memory": "lots"},
    ],
)
def test_client_rejects_invalid_settings(overrides: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _client(tmp_path / "docker", **overrides)


def test_client_satisfies_the_sandbox_protocol(tmp_path: Path) -> None:
    client = _client(tmp_path / "docker")
    assert isinstance(client, SandboxClient)
    assert client.get_runtime_profiles() == frozenset({"python-3.12"})
    params = client.get_identifier().params
    assert params["profiles"]["python-3.12"]["image"] == _IMAGE
    assert params["timeout_seconds"] == 5.0


async def test_a_client_failure_while_the_container_runs_is_a_sandbox_error_async(fake_docker: Path) -> None:
    with pytest.raises(SandboxExecutionError, match="still running"):
        await _run(_client(fake_docker), "DETACHFAIL")
    assert len(_removed(fake_docker)) == 1


async def test_a_failed_removal_is_reported_and_stops_further_cases_async(fake_docker: Path) -> None:
    run = await _run(_client(fake_docker), "RMFAIL", ProgramInvocation(), ProgramInvocation())

    assert [event.case_index for event in run.events] == [0]
    assert run.events[0].exit_code == 0
    assert "could not remove container pyrit-sandbox-" in run.warnings[0]
    assert "daemon unavailable" in run.warnings[0]
    assert run.reasons[0].startswith("stopped after case 0")


async def test_a_failed_removal_on_the_last_case_keeps_the_evidence_async(fake_docker: Path) -> None:
    run = await _run(_client(fake_docker), "RMFAIL")

    assert len(run.events) == 1
    assert run.reasons == ()
    assert len(run.warnings) == 1


async def test_cancellation_reaps_the_management_command_async(fake_docker: Path) -> None:
    pid_file = fake_docker.parent / "state" / "inspect.pid"
    task = asyncio.create_task(_run(_client(fake_docker), "SLOWINSPECT"))
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.05)
    inspect_pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(ProcessLookupError):
        os.kill(inspect_pid, 0)


async def test_the_program_directory_is_removed_after_making_it_writable_async(tmp_path: Path) -> None:
    client = _client(tmp_path / "docker")
    seen: dict[str, object] = {}
    real_rmtree = shutil.rmtree

    async def run_invocation(**kwargs: object):  # type: ignore[no-untyped-def]
        program_dir = kwargs["program_dir"]
        seen["dir"] = program_dir
        event = ProgramExecutionEvent(
            case_index=kwargs["case_index"],
            runtime_profile="python-3.12",
            runtime_image_digest="sha256:" + "c" * 64,
            resolved_command=("python3",),
            exit_code=0,
            duration_ms=1,
        )
        return event, None

    def recording_rmtree(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        program = next(iter(path.iterdir()))
        seen["writable"] = bool(program.stat().st_mode & stat.S_IWRITE)
        return real_rmtree(path, *args, **kwargs)

    with (
        patch.object(client, "_run_invocation_async", side_effect=run_invocation),
        patch("pyrit.score.observation.docker_sandbox_client.shutil.rmtree", side_effect=recording_rmtree),
    ):
        run = await _run(client, "print(1)")

    assert len(run.events) == 1
    assert seen["writable"] is True
    assert not seen["dir"].exists()  # type: ignore[union-attr]


async def _fake_run_invocation(**kwargs: object) -> tuple[ProgramExecutionEvent, None]:
    event = ProgramExecutionEvent(
        case_index=kwargs["case_index"],  # type: ignore[arg-type]
        runtime_profile="python-3.12",
        runtime_image_digest="sha256:" + "c" * 64,
        resolved_command=("python3",),
        exit_code=0,
        duration_ms=1,
    )
    return event, None


@pytest.mark.parametrize("blocked", ["write", "remove"])
async def test_file_work_runs_off_the_event_loop_async(blocked: str, tmp_path: Path) -> None:
    client = _client(tmp_path / "docker")
    release = threading.Event()
    threads: list[int] = []
    real_write_text = pathlib.Path.write_text
    real_rmtree = shutil.rmtree

    def wait_for_the_loop() -> None:
        # Only the running event loop releases this, so file work on the loop thread deadlocks here.
        threads.append(threading.get_ident())
        if not release.wait(2):
            raise AssertionError("the event loop was blocked while file work ran")

    def blocking_write_text(self: pathlib.Path, *args: object, **kwargs: object) -> int:
        if blocked == "write":
            wait_for_the_loop()
        return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def blocking_rmtree(path: pathlib.Path, *args: object, **kwargs: object) -> None:
        if blocked == "remove":
            wait_for_the_loop()
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    async def release_once_blocked() -> None:
        while not threads:
            await asyncio.sleep(0.005)
        release.set()

    with (
        patch.object(client, "_run_invocation_async", side_effect=_fake_run_invocation),
        patch.object(pathlib.Path, "write_text", blocking_write_text),
        patch("pyrit.score.observation.docker_sandbox_client.shutil.rmtree", side_effect=blocking_rmtree),
    ):
        releaser = asyncio.create_task(release_once_blocked())
        run = await _run(client, "print(1)")
        await asyncio.wait_for(releaser, timeout=1)

    assert len(run.events) == 1
    assert threads[0] != threading.get_ident()


async def test_cancelling_during_file_work_still_removes_the_program_async(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    client = _client(tmp_path / "docker")
    real_mkdir = pathlib.Path.mkdir
    entered = threading.Event()
    release = threading.Event()

    def gated_mkdir(self: pathlib.Path, *args: object, **kwargs: object) -> None:
        entered.set()
        release.wait(5)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch.object(client, "_run_invocation_async", side_effect=_fake_run_invocation),
        patch.object(pathlib.Path, "mkdir", gated_mkdir),
    ):
        task = asyncio.create_task(_run(client, "print(1)"))
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        task.cancel()
        asyncio.get_running_loop().call_later(0.2, release.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        # The cancellation completed only after the program directory had been made and removed.
        assert release.is_set()
        assert list(root.iterdir()) == []
