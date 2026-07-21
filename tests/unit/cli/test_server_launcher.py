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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyrit.cli import _server_launcher
from pyrit.cli._server_launcher import ServerLauncher


@pytest.fixture(autouse=True)
def isolate_pid_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_server_launcher, "_PID_DIRECTORY", tmp_path / "run")


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
# start_async
# ---------------------------------------------------------------------------


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
    assert launcher.pid == 4321
    # Verify command construction
    cmd = popen_mock.call_args.args[0]
    assert "pyrit.backend.pyrit_backend" in cmd
    assert "--config-file" in cmd
    assert "/tmp/foo.yaml" in cmd or "\\tmp\\foo.yaml" in cmd
    assert "--log-level" in cmd
    assert "INFO" in cmd
    record = json.loads(_server_launcher._pid_file_path(port=8001).read_text(encoding="utf-8"))
    assert record == {"host": "localhost", "port": 8001, "pid": 9876}


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
        with pytest.raises(RuntimeError, match="did not become healthy"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=0.01)

    err = capsys.readouterr().err
    assert "Traceback: boom" in err


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
    launcher._pid = 12345
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
    launcher._pid = 12345
    launcher._port = 8000

    with patch.object(_server_launcher, "_terminate_process_tree", return_value=False):
        assert launcher.stop() is False
    assert launcher._process is fake_proc
    assert launcher.pid == 12345


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific process existence behavior")
def test_windows_process_exists_does_not_terminate_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _server_launcher._process_exists(pid=process.pid) is True
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
