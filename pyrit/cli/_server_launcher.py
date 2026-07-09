# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Manage a local ``pyrit_backend`` subprocess.

Provides helpers to probe whether a server is already running, start a
detached backend process, and (optionally) stop it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

from pyrit.cli.api_client import PyRITApiClient

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port-based process termination
# ---------------------------------------------------------------------------


def _find_pid_on_port_windows(*, port: int) -> int | None:
    """
    Find the PID listening on *port* on Windows via ``netstat``.

    Args:
        port: TCP port to look up.

    Returns:
        int | None: The PID, or ``None`` if no listener was found.
    """
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        tokens = line.split()
        if len(tokens) < 5:
            continue
        # Match the local-address column exactly so port 80 doesn't match
        # :8000 / :8080 / etc. Handles both IPv4 ("0.0.0.0:8000") and
        # IPv6 ("[::]:8000") local-address formats.
        local_addr = tokens[1]
        if local_addr.rsplit(":", 1)[-1] != str(port):
            continue
        try:
            return int(tokens[-1])
        except (ValueError, IndexError):
            continue
    return None


def _find_pid_on_port_unix(*, port: int) -> int | None:
    """
    Find the first PID listening on *port* on Unix via ``lsof``.

    Args:
        port: TCP port to look up.

    Returns:
        int | None: The PID, or ``None`` if no listener was found.
    """
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for pid_str in result.stdout.strip().splitlines():
        try:
            return int(pid_str)
        except ValueError:
            continue
    return None


def stop_server_on_port(*, port: int) -> bool:
    """
    Find and terminate the process listening on *port*.

    Args:
        port: TCP port to look up.

    Returns:
        bool: ``True`` if a process was found and signalled, ``False`` otherwise.
    """
    if sys.platform == "win32":
        pid = _find_pid_on_port_windows(port=port)
    else:
        pid = _find_pid_on_port_unix(port=port)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


class ServerLauncher:
    """
    Launch and manage a local ``pyrit_backend`` server.

    The subprocess is **detached** — it survives after the parent CLI exits.
    This is intentional: a running server on ``localhost:8000`` is reusable
    across multiple ``pyrit_scan`` / ``pyrit_shell`` sessions.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._pid: int | None = None
        self._log_path: str | None = None

    # ------------------------------------------------------------------
    # Health probe
    # ------------------------------------------------------------------

    @staticmethod
    async def probe_health_async(*, base_url: str) -> bool:
        """
        Check whether a server at *base_url* is healthy.

        Args:
            base_url: Server root URL (e.g. ``http://localhost:8000``).

        Returns:
            bool: ``True`` if ``GET /api/health`` returned 200.
        """
        async with PyRITApiClient(base_url=base_url) as client:
            return await client.health_check_async()

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start_async(
        self,
        *,
        host: str = "localhost",
        port: int = 8000,
        config_file: Path | None = None,
        log_level: str | None = None,
        startup_timeout: int = 30,
    ) -> str:
        """
        Start ``pyrit_backend`` as a detached subprocess and wait until healthy.

        Args:
            host: Bind address forwarded to ``pyrit_backend --host``.
            port: Bind port forwarded to ``pyrit_backend --port``.
            config_file: Optional config forwarded via ``--config-file``.
            log_level: Optional log level forwarded via ``--log-level``.
            startup_timeout: Seconds to wait for the server to become healthy.

        Returns:
            str: The ``base_url`` of the running server.

        Raises:
            RuntimeError: If the server did not become healthy within the timeout.
        """
        base_url = f"http://{host}:{port}"

        # Already running?
        if await self.probe_health_async(base_url=base_url):
            _logger.info("Server already running at %s", base_url)
            return base_url

        cmd: list[str] = [
            sys.executable,
            "-m",
            "pyrit.backend.pyrit_backend",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if config_file is not None:
            cmd.extend(["--config-file", str(config_file)])
        if log_level is not None:
            cmd.extend(["--log-level", log_level])

        _logger.info("Launching pyrit_backend: %s", " ".join(cmd))

        creation_flags = 0
        start_new_session = False
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True

        print(f"Starting server at {base_url}...")
        sys.stdout.flush()

        # The backend is detached and outlives this process, so it must not inherit
        # our stdout/stderr. A caller that captures our output (a piped shell, a
        # Jupyter ``!`` cell, or CI) would otherwise block forever waiting for the
        # inherited handle to close. Send the child's output to a log file so
        # startup diagnostics are still available.
        self._log_path = os.path.join(tempfile.gettempdir(), "pyrit_backend.log")
        with open(self._log_path, "w", encoding="utf-8") as log_handle:
            self._process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                start_new_session=start_new_session,
            )
        self._pid = self._process.pid
        _logger.info("Backend PID: %d (logs: %s)", self._pid, self._log_path)

        # Wait for health, checking if the process crashed
        for _elapsed in range(startup_timeout):
            await asyncio.sleep(1)

            exit_code = self._process.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"Server process exited with code {exit_code} during startup. See logs: {self._log_path}"
                )

            if await self.probe_health_async(base_url=base_url):
                print(f"Server ready (PID {self._pid})")
                return base_url

        raise RuntimeError(
            f"pyrit_backend did not become healthy within {startup_timeout}s. "
            f"Check the server logs ({self._log_path}) or start it manually with: pyrit_backend"
        )

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Terminate the owned subprocess (if any)."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
                _logger.info("Stopped server (PID %d)", self._pid)
            except Exception:
                _logger.warning("Failed to stop server (PID %s)", self._pid, exc_info=True)
            finally:
                self._process = None
                self._pid = None

    @property
    def pid(self) -> int | None:
        """PID of the owned backend process, or ``None``."""
        return self._pid
