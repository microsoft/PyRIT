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
import subprocess
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)


class ServerLauncher:
    """
    Launch and manage a local ``pyrit_backend`` server.

    The subprocess is **detached** — it survives after the parent CLI exits.
    This is intentional: a running server on ``localhost:8000`` is reusable
    across multiple ``pyrit_scan`` / ``pyrit_shell`` sessions.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._pid: int | None = None

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
        from pyrit.cli.api_client import PyRITApiClient

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

        self._process = subprocess.Popen(
            cmd,
            creationflags=creation_flags,
            start_new_session=start_new_session,
        )
        self._pid = self._process.pid
        _logger.info("Backend PID: %d", self._pid)

        # Wait for health, checking if the process crashed
        for elapsed in range(startup_timeout):
            await asyncio.sleep(1)

            exit_code = self._process.poll()
            if exit_code is not None:
                raise RuntimeError(f"Server process exited with code {exit_code} during startup.")

            if await self.probe_health_async(base_url=base_url):
                print(f"Server ready (PID {self._pid})")
                return base_url

        raise RuntimeError(
            f"pyrit_backend did not become healthy within {startup_timeout}s. "
            f"Check the server logs or start it manually with: pyrit_backend"
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
