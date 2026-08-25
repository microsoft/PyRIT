# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._server_launcher.ServerLauncher.
"""

import asyncio
import inspect
import json
import signal
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyrit.cli import _server_launcher
from pyrit.cli._server_launcher import ServerLauncher


@pytest.fixture(autouse=True)
def isolate_pid_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_server_launcher, "_PID_DIRECTORY", tmp_path / "run")


# ---------------------------------------------------------------------------
# address and PID helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", ["http://localhost:not-a-port", "http://localhost:99999"])
def test_parse_local_server_address_rejects_invalid_port(base_url):
    assert _server_launcher.parse_local_server_address(base_url=base_url) is None


def test_write_pid_record_logs_persist_and_cleanup_failures(caplog):
    with (
        patch.object(Path, "replace", side_effect=OSError("replace failed")),
        patch.object(Path, "unlink", side_effect=OSError("unlink failed")),
    ):
        _server_launcher._write_pid_record(host="localhost", port=8000, pid=1234)

    assert "Could not persist backend process state" in caplog.text
    assert "Could not remove temporary backend process state" in caplog.text


def test_remove_pid_record_logs_failure(caplog):
    with patch.object(Path, "unlink", side_effect=OSError("unlink failed")):
        _server_launcher._remove_pid_record(port=8000)

    assert "Could not remove backend process state" in caplog.text


def test_read_recorded_pid_discards_invalid_json():
    path = _server_launcher._pid_file_path(port=8000)
    path.parent.mkdir(parents=True)
    path.write_text("{invalid", encoding="utf-8")

    assert _server_launcher._read_recorded_pid(port=8000) is None
    assert not path.exists()


@pytest.mark.parametrize(
    "record",
    [
        {"host": "localhost", "port": 8000, "pid": True},
        {"host": "localhost", "port": 9000, "pid": 1234},
    ],
)
def test_read_recorded_pid_discards_invalid_fields(record):
    path = _server_launcher._pid_file_path(port=8000)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    assert _server_launcher._read_recorded_pid(port=8000) is None
    assert not path.exists()


def test_parse_netstat_listener_pids_ignores_malformed_rows():
    output = "not a listener row\nTCP invalid-address 0.0.0.0:0 LISTENING not-a-pid"

    assert _server_launcher._parse_netstat_listener_pids(output=output, port=8000) == set()


def test_windows_pid_lookup_rejects_ambiguous_listener():
    result = MagicMock(stdout="1234\n5678\n")
    with patch("subprocess.run", return_value=result) as run_mock:
        assert _server_launcher._find_pid_on_port_windows(port=8000) is None

    run_mock.assert_called_once()


def test_unix_pid_lookup_falls_back_to_ss_when_lsof_fails():
    ss_result = MagicMock(stdout='users:(("python",pid=5678,fd=3))')
    with patch("subprocess.run", side_effect=[OSError("lsof missing"), ss_result]):
        assert _server_launcher._find_pid_on_port_unix(port=8000) == 5678


def test_unix_pid_lookup_rejects_ambiguous_lsof_listener():
    result = MagicMock(stdout="1234\n5678\n")
    with patch("subprocess.run", return_value=result) as run_mock:
        assert _server_launcher._find_pid_on_port_unix(port=8000) is None

    run_mock.assert_called_once()


# ---------------------------------------------------------------------------
# probe_health_async
# ---------------------------------------------------------------------------


async def test_probe_health_returns_true_when_client_healthy():
    fake_client = MagicMock()
    fake_client.health_check_async = AsyncMock(return_value=True)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("pyrit.cli._server_launcher.PyRITApiClient", return_value=fake_client):
        result = await ServerLauncher.probe_health_async(base_url="http://localhost:8000")
    assert result is True
    fake_client.health_check_async.assert_awaited_once()


async def test_probe_health_returns_false_when_client_unhealthy():
    fake_client = MagicMock()
    fake_client.health_check_async = AsyncMock(return_value=False)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("pyrit.cli._server_launcher.PyRITApiClient", return_value=fake_client):
        result = await ServerLauncher.probe_health_async(base_url="http://localhost:8000")
    assert result is False


# ---------------------------------------------------------------------------
# launch planning and start_async
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform_name", "expected_creation_flags", "expected_start_new_session"),
    [
        ("posix", 0, True),
        ("nt", getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200), False),
    ],
)
def test_build_server_launch_plan_constructs_platform_settings(
    platform_name, expected_creation_flags, expected_start_new_session
):
    plan = _server_launcher._build_server_launch_plan(
        host="127.0.0.1",
        port=8765,
        config_file=Path("config.yaml"),
        log_level="DEBUG",
        startup_timeout=3.5,
        executable="python",
        platform_name=platform_name,
    )

    assert plan.base_url == "http://127.0.0.1:8765"
    assert plan.command == [
        "python",
        "-m",
        "pyrit.backend.pyrit_backend",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--config-file",
        "config.yaml",
        "--log-level",
        "DEBUG",
    ]
    assert plan.creation_flags == expected_creation_flags
    assert plan.start_new_session is expected_start_new_session
    assert plan.startup_timeout == 3.5


@pytest.mark.parametrize("startup_timeout", [True, 0, -1, float("inf"), float("nan"), "1"])
def test_build_server_launch_plan_rejects_invalid_timeout(startup_timeout):
    with pytest.raises(ValueError, match="finite number greater than 0"):
        _server_launcher._build_server_launch_plan(
            host="localhost",
            port=8000,
            config_file=None,
            log_level=None,
            startup_timeout=startup_timeout,
            executable="python",
            platform_name="posix",
        )


async def test_start_async_cancellation_during_spawn_terminates_spawned_process():
    started = threading.Event()
    release = threading.Event()
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 4321
    fake_proc.poll.return_value = None

    def delayed_spawn(**_kwargs: object) -> MagicMock:
        started.set()
        release.wait(timeout=5)
        return fake_proc

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(return_value=False)),
        patch.object(_server_launcher, "_spawn_backend_process", side_effect=delayed_spawn),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
    ):
        spawn_task = asyncio.create_task(launcher.start_async(host="localhost", port=8000, startup_timeout=60))
        assert await asyncio.to_thread(started.wait, 5)
        spawn_task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await spawn_task

    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


async def test_start_async_returns_url_when_already_healthy():
    launcher = ServerLauncher()
    with patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(return_value=True)):
        url = await launcher.start_async(host="localhost", port=8000)
    assert url == "http://localhost:8000"
    # Should not have created a subprocess.
    assert launcher.pid is None


async def test_start_async_spawns_subprocess_and_waits_for_health():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 4321
    fake_proc.poll.return_value = None
    # First health probe (already-running check) returns False, second returns True
    probe = AsyncMock(side_effect=[False, True])

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc) as popen_mock,
        patch.object(_server_launcher, "_find_pid_on_port", return_value=9876),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        url = await launcher.start_async(
            host="localhost",
            port=8001,
            config_file=Path("/tmp/foo.yaml"),
            log_level="INFO",
            startup_timeout=5,
        )
    assert url == "http://localhost:8001"
    assert launcher.pid == 9876
    # Verify command construction
    cmd = popen_mock.call_args.args[0]
    assert "pyrit.backend.pyrit_backend" in cmd
    assert "--config-file" in cmd
    assert "/tmp/foo.yaml" in cmd or "\\tmp\\foo.yaml" in cmd
    assert "--log-level" in cmd
    assert "INFO" in cmd
    record = json.loads(_server_launcher._pid_file_path(port=8001).read_text(encoding="utf-8"))
    assert record == {"host": "localhost", "port": 8001, "pid": 9876}


async def test_start_async_cleans_up_when_ready_message_cannot_be_printed():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 4321
    fake_proc.poll.return_value = None
    probe = AsyncMock(side_effect=[False, True])

    def fail_ready_message(message: object = "", *_args: object, **_kwargs: object) -> None:
        if isinstance(message, str) and message.startswith("Server ready"):
            raise BrokenPipeError("stdout closed")

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_find_pid_on_port", return_value=4321),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
        patch("builtins.print", side_effect=fail_ready_message),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(BrokenPipeError, match="stdout closed"):
            await launcher.start_async(host="localhost", port=8001, startup_timeout=5)

    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8001).exists()


def test_start_async_defaults_to_longer_startup_timeout():
    parameter = inspect.signature(ServerLauncher.start_async).parameters["startup_timeout"]
    assert parameter.default == 120.0


async def test_start_async_redirects_child_stdio_to_log_file():
    # A detached backend must not inherit the parent's stdout/stderr, otherwise a
    # caller capturing our output (piped shell, Jupyter `!`, CI) blocks forever.
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 4321
    fake_proc.poll.return_value = None
    probe = AsyncMock(side_effect=[False, True])

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc) as popen_mock,
        patch.object(_server_launcher, "_find_pid_on_port", return_value=4321),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        await launcher.start_async(host="localhost", port=8001, startup_timeout=5)

    kwargs = popen_mock.call_args.kwargs
    # stdout is redirected to a real file handle (not None/inherited)
    assert kwargs["stdout"] is not None
    assert kwargs["stderr"] is subprocess.STDOUT
    assert launcher._log_path is not None


async def test_start_async_raises_when_process_crashes_during_startup():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 42
    fake_proc.poll.return_value = 1  # exited
    probe = AsyncMock(return_value=False)

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="exited with code 1"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=3)


async def test_start_async_raises_when_timeout_exhausted():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None  # still running
    probe = AsyncMock(return_value=False)

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
    ):
        with pytest.raises(RuntimeError, match="did not become healthy"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)
    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


async def test_start_async_cancellation_cleans_up_spawned_process():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    probe_started = asyncio.Event()
    probe_calls = 0

    async def probe_health_async(*, base_url: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            return False
        probe_started.set()
        await asyncio.Event().wait()
        return False

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(side_effect=probe_health_async)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
    ):
        startup_task = asyncio.create_task(launcher.start_async(host="localhost", port=8000, startup_timeout=60))
        await probe_started.wait()
        startup_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup_task

    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


async def test_start_async_repeated_cancellation_waits_for_pid_write_before_cleanup():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    write_started = threading.Event()
    release_write = threading.Event()
    write_finished = threading.Event()

    def delayed_write(*, host: str, port: int, pid: int) -> None:
        write_started.set()
        release_write.wait(timeout=5)
        _server_launcher._pid_file_path(port=port).parent.mkdir(parents=True, exist_ok=True)
        _server_launcher._pid_file_path(port=port).write_text(
            json.dumps({"host": host, "port": port, "pid": pid}),
            encoding="utf-8",
        )
        write_finished.set()

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(return_value=False)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_write_pid_record", side_effect=delayed_write),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
    ):
        startup_task = asyncio.create_task(launcher.start_async(host="localhost", port=8000, startup_timeout=60))
        assert await asyncio.to_thread(write_started.wait, 5)
        startup_task.cancel()
        await asyncio.sleep(0)
        assert not startup_task.done()
        startup_task.cancel()
        await asyncio.sleep(0)
        assert not startup_task.done()
        release_write.set()
        with pytest.raises(asyncio.CancelledError):
            await startup_task

    assert write_finished.is_set()
    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


async def test_start_async_repeated_cancellation_during_cleanup_stops_process_once():
    launcher = ServerLauncher()
    fake_proc = MagicMock(spec=subprocess.Popen)
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    probe_started = asyncio.Event()
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    probe_calls = 0

    async def probe_health_async(*, base_url: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            return False
        probe_started.set()
        await asyncio.Event().wait()
        return False

    def delayed_terminate(*, process: subprocess.Popen[bytes]) -> bool:
        cleanup_started.set()
        release_cleanup.wait(timeout=5)
        return True

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(side_effect=probe_health_async)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", side_effect=delayed_terminate) as stop_tree_mock,
    ):
        startup_task = asyncio.create_task(launcher.start_async(host="localhost", port=8000, startup_timeout=60))
        await probe_started.wait()
        startup_task.cancel()
        assert await asyncio.to_thread(cleanup_started.wait, 5)
        startup_task.cancel()
        await asyncio.sleep(0)
        startup_task.cancel()
        await asyncio.sleep(0)
        assert not startup_task.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await startup_task

    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


async def test_start_async_repeated_cancellation_reports_failed_cleanup(caplog):
    launcher = ServerLauncher()
    fake_proc = MagicMock(spec=subprocess.Popen)
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    probe_started = asyncio.Event()
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    probe_calls = 0

    async def probe_health_async(*, base_url: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            return False
        probe_started.set()
        await asyncio.Event().wait()
        return False

    def delayed_failed_terminate(*, process: subprocess.Popen[bytes]) -> bool:
        cleanup_started.set()
        release_cleanup.wait(timeout=5)
        return False

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(side_effect=probe_health_async)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", side_effect=delayed_failed_terminate),
    ):
        startup_task = asyncio.create_task(launcher.start_async(host="localhost", port=8000, startup_timeout=60))
        await probe_started.wait()
        startup_task.cancel()
        assert await asyncio.to_thread(cleanup_started.wait, 5)
        startup_task.cancel()
        await asyncio.sleep(0)
        startup_task.cancel()
        await asyncio.sleep(0)
        assert not startup_task.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await startup_task

    assert "Failed to stop backend launcher process 99" in caplog.text
    assert launcher.pid == 99


async def test_startup_state_cleanup_without_process_marks_state_cleaned():
    state = _server_launcher._ServerStartupState(port=8000)

    assert await state.cleanup_async() is True
    assert state.phase is _server_launcher._StartupPhase.CLEANED_UP
    assert state.cleanup_succeeded is True


async def test_print_startup_diagnostics_is_idempotent():
    launcher = ServerLauncher()
    state = _server_launcher._ServerStartupState(port=8000)

    with (
        patch.object(launcher, "_read_log_tail", return_value="Loading datasets: 42%") as read_log_tail_mock,
        patch.object(launcher, "_print_log_tail", autospec=True) as print_log_tail_mock,
    ):
        await launcher._print_startup_diagnostics_async(startup_state=state)
        await launcher._print_startup_diagnostics_async(startup_state=state)

    read_log_tail_mock.assert_called_once_with()
    print_log_tail_mock.assert_called_once_with(tail="Loading datasets: 42%")
    assert state.diagnostic_tail == "Loading datasets: 42%"
    assert state.diagnostics_printed is True


async def test_wait_for_readiness_requires_recorded_process():
    launcher = ServerLauncher()
    plan = _server_launcher._build_server_launch_plan(
        host="localhost",
        port=8000,
        config_file=None,
        log_level=None,
        startup_timeout=1,
        executable="python",
        platform_name="posix",
    )

    with pytest.raises(RuntimeError, match="process was not recorded"):
        await launcher._wait_for_readiness_async(
            plan=plan,
            host="localhost",
            startup_state=_server_launcher._ServerStartupState(port=8000),
        )


async def test_start_async_timeout_retains_state_when_cleanup_fails(caplog):
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    probe = AsyncMock(return_value=False)

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="did not become healthy"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)

    assert launcher.pid == 99
    assert "Failed to stop backend launcher process 99" in caplog.text


async def test_start_async_timeout_caps_hanging_health_probe():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None
    probe_calls = 0

    async def probe_health_async(*, base_url: str) -> bool:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            return False
        await asyncio.Event().wait()
        return False

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(side_effect=probe_health_async)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock,
    ):
        with pytest.raises(RuntimeError, match="did not become healthy"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)

    stop_tree_mock.assert_called_once_with(process=fake_proc)


async def test_start_async_prints_log_path_on_success(capsys):
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 4321
    fake_proc.poll.return_value = None
    probe = AsyncMock(side_effect=[False, True])

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_find_pid_on_port", return_value=4321),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        await launcher.start_async(host="localhost", port=8001, startup_timeout=5)

    out = capsys.readouterr().out
    assert "Server ready (PID 4321)" in out
    assert "Logs:" in out
    assert launcher._log_path in out


async def test_start_async_echoes_log_tail_on_crash(capsys):
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 42
    fake_proc.poll.return_value = 1  # exited
    probe = AsyncMock(return_value=False)

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
        patch.object(ServerLauncher, "_read_log_tail", return_value="ERROR: port already in use"),
    ):
        with pytest.raises(RuntimeError, match="exited with code 1"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=3)

    err = capsys.readouterr().err
    assert "pyrit_backend log" in err
    assert "ERROR: port already in use" in err


async def test_start_async_echoes_log_tail_on_timeout(capsys):
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None  # still running
    probe = AsyncMock(return_value=False)

    with (
        patch.object(ServerLauncher, "probe_health_async", new=probe),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True),
        patch.object(ServerLauncher, "_read_log_tail", return_value="Traceback: boom"),
    ):
        with pytest.raises(RuntimeError, match="did not become healthy") as exc_info:
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)

    err = capsys.readouterr().err
    assert "Traceback: boom" in err
    assert "load_default_datasets" not in str(exc_info.value)


async def test_start_async_timeout_suggests_on_demand_fetching_during_dataset_preload() -> None:
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 99
    fake_proc.poll.return_value = None

    with (
        patch.object(ServerLauncher, "probe_health_async", new=AsyncMock(return_value=False)),
        patch("subprocess.Popen", return_value=fake_proc),
        patch.object(_server_launcher, "_terminate_process_tree", return_value=True),
        patch.object(
            ServerLauncher,
            "_read_log_tail",
            return_value="Loading datasets - this can take a few minutes: 42%",
        ),
    ):
        with pytest.raises(RuntimeError, match="did not become healthy") as exc_info:
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)

    message = str(exc_info.value)
    assert "Recent logs include dataset preload activity" in message
    assert "remove load_default_datasets" in message
    assert "on-demand fetching" in message
    assert "intentional preload" in message
    assert "larger server.startup_timeout" in message


# ---------------------------------------------------------------------------
# _read_log_tail
# ---------------------------------------------------------------------------


def test_read_log_tail_returns_last_lines(tmp_path):
    log_file = tmp_path / "pyrit_backend.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    launcher = ServerLauncher()
    launcher._log_path = str(log_file)

    tail = launcher._read_log_tail(max_lines=5)

    assert tail.splitlines() == ["line 45", "line 46", "line 47", "line 48", "line 49"]


def test_read_log_tail_returns_empty_when_no_log_path():
    launcher = ServerLauncher()
    assert launcher._read_log_tail() == ""


def test_read_log_tail_returns_empty_when_file_missing(tmp_path):
    launcher = ServerLauncher()
    launcher._log_path = str(tmp_path / "does_not_exist.log")
    assert launcher._read_log_tail() == ""


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_terminates_process():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None
    launcher._process = fake_proc
    launcher._listener_pid = 12345
    launcher._port = 8000

    with patch.object(_server_launcher, "_terminate_process_tree", return_value=True) as stop_tree_mock:
        assert launcher.stop() is True

    stop_tree_mock.assert_called_once_with(process=fake_proc)
    assert launcher.pid is None
    assert launcher._process is None


def test_stop_reports_termination_errors_and_retains_process():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None
    launcher._process = fake_proc
    launcher._listener_pid = 12345
    launcher._port = 8000

    with patch.object(_server_launcher, "_terminate_process_tree", return_value=False):
        assert launcher.stop() is False
    assert launcher._process is fake_proc
    assert launcher.pid == 12345


def test_stop_clears_state_when_process_already_exited():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 0
    launcher._process = fake_proc
    launcher._listener_pid = 12345
    launcher._port = 8000
    _server_launcher._write_pid_record(host="localhost", port=8000, pid=12345)

    assert launcher.stop() is True
    assert launcher._process is None
    assert launcher.pid is None
    assert not _server_launcher._pid_file_path(port=8000).exists()


def test_terminate_process_tree_kills_unresponsive_unix_group():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None
    fake_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="backend", timeout=10), 0]

    with (
        patch("os.name", "posix"),
        patch("os.killpg", create=True) as kill_group_mock,
        patch("signal.SIGKILL", 9, create=True),
    ):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is True

    assert kill_group_mock.call_args_list == [
        call(12345, signal.SIGTERM),
        call(12345, 9),
    ]


def test_terminate_process_tree_uses_windows_pid_tree():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    result = MagicMock(returncode=0)

    with patch("os.name", "nt"), patch("subprocess.run", return_value=result) as run_mock:
        assert _server_launcher._terminate_process_tree(process=fake_proc) is True

    assert run_mock.call_args.args[0] == ["taskkill", "/PID", "12345", "/T", "/F"]


def test_terminate_process_tree_reports_windows_taskkill_error():
    fake_proc = MagicMock()
    fake_proc.pid = 12345

    with patch("os.name", "nt"), patch("subprocess.run", side_effect=OSError("taskkill failed")):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is False


def test_terminate_process_tree_rejects_unsuccessful_windows_taskkill():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll.return_value = None

    with patch("os.name", "nt"), patch("subprocess.run", return_value=MagicMock(returncode=1)):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is False


def test_terminate_process_tree_handles_missing_unix_process():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.wait.return_value = 0

    with patch("os.name", "posix"), patch("os.killpg", side_effect=ProcessLookupError, create=True):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is True


def test_terminate_process_tree_reports_unix_signal_error():
    fake_proc = MagicMock()
    fake_proc.pid = 12345

    with patch("os.name", "posix"), patch("os.killpg", side_effect=OSError("signal failed"), create=True):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is False


def test_terminate_process_tree_reports_windows_wait_timeout():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="backend", timeout=10)

    with patch("os.name", "nt"), patch("subprocess.run", return_value=MagicMock(returncode=0)):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is False


def test_terminate_process_tree_reports_unix_kill_timeout():
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="backend", timeout=10)

    with (
        patch("os.name", "posix"),
        patch("os.killpg", create=True) as kill_group_mock,
        patch("signal.SIGKILL", 9, create=True),
    ):
        assert _server_launcher._terminate_process_tree(process=fake_proc) is False

    assert kill_group_mock.call_count == 2


def test_stop_is_noop_when_no_process():
    launcher = ServerLauncher()
    assert launcher.stop() is True
    assert launcher.pid is None


# ---------------------------------------------------------------------------
# persisted PID resolution
# ---------------------------------------------------------------------------


def test_stop_server_uses_valid_recorded_pid():
    _server_launcher._write_pid_record(host="localhost", port=8000, pid=1234)

    with (
        patch.object(_server_launcher, "_find_pid_on_port", return_value=1234),
        patch.object(_server_launcher, "_wait_for_process_exit", return_value=True),
        patch("os.kill") as kill_mock,
    ):
        assert _server_launcher.stop_server_on_port(port=8000) is True

    kill_mock.assert_called_once_with(1234, signal.SIGTERM)
    assert not _server_launcher._pid_file_path(port=8000).exists()


def test_stop_server_discards_stale_record_and_uses_listener_pid():
    _server_launcher._write_pid_record(host="localhost", port=8000, pid=1234)

    with (
        patch.object(_server_launcher, "_find_pid_on_port", return_value=5678),
        patch.object(_server_launcher, "_wait_for_process_exit", return_value=True),
        patch("os.kill") as kill_mock,
    ):
        assert _server_launcher.stop_server_on_port(port=8000) is True

    kill_mock.assert_called_once_with(5678, signal.SIGTERM)
    assert not _server_launcher._pid_file_path(port=8000).exists()


def test_stop_server_reports_signal_error():
    with (
        patch.object(_server_launcher, "_resolve_server_pid", return_value=1234),
        patch("os.kill", side_effect=OSError("signal failed")),
    ):
        assert _server_launcher.stop_server_on_port(port=8000) is False


def test_stop_server_reports_shutdown_timeout():
    with (
        patch.object(_server_launcher, "_resolve_server_pid", return_value=1234),
        patch.object(_server_launcher, "_wait_for_process_exit", return_value=False),
        patch("os.kill"),
    ):
        assert _server_launcher.stop_server_on_port(port=8000) is False


def test_windows_pid_lookup_prefers_get_net_tcp_connection():
    result = MagicMock(stdout="116284\n")
    with patch("sys.platform", "win32"), patch("subprocess.run", return_value=result) as run_mock:
        assert _server_launcher._find_pid_on_port(port=8765) == 116284

    assert run_mock.call_args.args[0][0] == "powershell.exe"


def test_windows_process_exists_uses_non_destructive_lookup():
    result = MagicMock(returncode=0)
    with (
        patch("sys.platform", "win32"),
        patch("subprocess.run", return_value=result) as run_mock,
        patch("os.kill") as kill_mock,
    ):
        assert _server_launcher._process_exists(pid=1234) is True

    kill_mock.assert_not_called()
    assert "Get-Process" in run_mock.call_args.args[0][-1]


def test_windows_process_exists_assumes_present_when_lookup_fails():
    with patch("sys.platform", "win32"), patch("subprocess.run", side_effect=OSError("lookup failed")):
        assert _server_launcher._process_exists(pid=1234) is True


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError(), False),
        (None, True),
    ],
)
def test_unix_process_exists_handles_signal_results(side_effect, expected):
    with patch("sys.platform", "linux"), patch("os.kill", side_effect=side_effect):
        assert _server_launcher._process_exists(pid=1234) is expected


def test_wait_for_process_exit_reports_timeout():
    with (
        patch.object(_server_launcher, "_process_exists", return_value=True),
        patch("time.monotonic", side_effect=[0.0, 1.0]),
    ):
        assert _server_launcher._wait_for_process_exit(pid=1234, port=8000, timeout=0.5) is False


def test_wait_for_process_exit_confirms_port_release():
    with (
        patch.object(_server_launcher, "_process_exists", side_effect=[True, False]),
        patch.object(_server_launcher, "_find_pid_on_port", return_value=None),
        patch("time.monotonic", side_effect=[0.0, 0.1]),
        patch("time.sleep") as sleep_mock,
    ):
        assert _server_launcher._wait_for_process_exit(pid=1234, port=8000, timeout=0.5) is True

    sleep_mock.assert_called_once_with(0.1)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process existence behavior")
def test_windows_process_exists_does_not_terminate_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _server_launcher._process_exists(pid=process.pid) is True
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
