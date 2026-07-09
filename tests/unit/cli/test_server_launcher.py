# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._server_launcher.ServerLauncher.
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli._server_launcher import ServerLauncher

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
        patch("asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="did not become healthy"):
            await launcher.start_async(host="localhost", port=8000, startup_timeout=2)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_terminates_process():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    launcher._process = fake_proc
    launcher._pid = 12345

    launcher.stop()

    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once_with(timeout=5)
    assert launcher.pid is None
    assert launcher._process is None


def test_stop_swallows_termination_errors():
    launcher = ServerLauncher()
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.terminate.side_effect = OSError("permission denied")
    launcher._process = fake_proc
    launcher._pid = 12345

    # Should not raise.
    launcher.stop()
    assert launcher._process is None
    assert launcher.pid is None


def test_stop_is_noop_when_no_process():
    launcher = ServerLauncher()
    launcher.stop()  # Should not raise.
    assert launcher.pid is None
