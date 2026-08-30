# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Manage a local ``pyrit_backend`` subprocess.

Provides helpers to probe whether a server is already running, start a
detached backend process, and (optionally) stop it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from urllib.parse import urlparse

from pyrit.cli._config_reader import DEFAULT_SERVER_STARTUP_TIMEOUT
from pyrit.cli.api_client import PyRITApiClient

_logger = logging.getLogger(__name__)
_HEALTH_PROBE_TIMEOUT = 5.0
_PROCESS_STOP_TIMEOUT = 10.0
_STARTUP_POLL_INTERVAL = 0.5
_PID_DIRECTORY = Path.home() / ".pyrit" / "run"


@dataclass(frozen=True)
class _ServerLaunchPlan:
    """Validated command and platform settings for one backend launch."""

    base_url: str
    command: list[str]
    creation_flags: int
    start_new_session: bool
    startup_timeout: float


def _build_server_launch_plan(
    *,
    host: str,
    port: int,
    config_file: Path | None,
    log_level: str | None,
    startup_timeout: float,
    executable: str,
    platform_name: str,
) -> _ServerLaunchPlan:
    """
    Validate launch parameters and build platform-specific process settings.

    Args:
        host: Backend bind address.
        port: Backend bind port.
        config_file: Optional backend configuration path.
        log_level: Optional backend log level.
        startup_timeout: Seconds to wait for readiness.
        executable: Python executable used to launch the backend module.
        platform_name: Operating-system family, such as ``posix`` or ``nt``.

    Returns:
        _ServerLaunchPlan: Validated command and process settings.

    Raises:
        ValueError: If ``startup_timeout`` is not finite and greater than zero.
    """
    if (
        isinstance(startup_timeout, bool)
        or not isinstance(startup_timeout, int | float)
        or not math.isfinite(startup_timeout)
        or startup_timeout <= 0
    ):
        raise ValueError("startup_timeout must be a finite number greater than 0.")

    command = [
        executable,
        "-m",
        "pyrit.backend.pyrit_backend",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if config_file is not None:
        command.extend(["--config-file", str(config_file)])
    if log_level is not None:
        command.extend(["--log-level", log_level])

    is_windows = platform_name == "nt"
    return _ServerLaunchPlan(
        base_url=f"http://{host}:{port}",
        command=command,
        creation_flags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) if is_windows else 0,
        start_new_session=not is_windows,
        startup_timeout=startup_timeout,
    )


class _StartupPhase(Enum):
    """Lifecycle phases for a backend process owned by one startup attempt."""

    CREATED = auto()
    PROCESS_STARTED = auto()
    PID_PERSISTED = auto()
    CHECKING_HEALTH = auto()
    READY = auto()
    TIMED_OUT = auto()
    FAILED = auto()
    CLEANED_UP = auto()


@dataclass
class _ServerStartupState:
    """Explicit state and cleanup ownership for one backend startup attempt."""

    port: int
    phase: _StartupPhase = _StartupPhase.CREATED
    process: subprocess.Popen[bytes] | None = None
    pid_record_written: bool = False
    health_check_count: int = 0
    healthy: bool = False
    timed_out: bool = False
    diagnostics_printed: bool = False
    cleanup_attempted: bool = False
    cleanup_succeeded: bool | None = None
    diagnostic_tail: str = ""
    _cleanup_task: asyncio.Task[bool] | None = field(default=None, repr=False)

    def record_process(self, *, process: subprocess.Popen[bytes]) -> None:
        """Record ownership of the spawned process."""
        self.process = process
        self.phase = _StartupPhase.PROCESS_STARTED

    def record_pid_write(self) -> None:
        """Record that the startup attempt persisted a PID record."""
        self.pid_record_written = True
        if self.phase is _StartupPhase.PROCESS_STARTED:
            self.phase = _StartupPhase.PID_PERSISTED

    def record_health_check(self, *, healthy: bool) -> None:
        """Record one bounded readiness probe result."""
        self.health_check_count += 1
        self.healthy = healthy
        self.phase = _StartupPhase.CHECKING_HEALTH

    def record_ready(self) -> None:
        """Record that the backend completed startup."""
        self.phase = _StartupPhase.READY

    def record_timeout(self) -> None:
        """Record that the readiness deadline elapsed."""
        self.timed_out = True
        self.phase = _StartupPhase.TIMED_OUT

    def record_failure(self) -> None:
        """Record that the backend exited before becoming ready."""
        self.phase = _StartupPhase.FAILED

    async def cleanup_async(self) -> bool:
        """
        Stop the owned process exactly once, even under repeated cancellation.

        Returns:
            bool: ``True`` when the process stopped, otherwise ``False``.

        Raises:
            asyncio.CancelledError: After in-flight cleanup completes if the caller was cancelled.
        """
        if self.process is None:
            self.cleanup_succeeded = True
            self.phase = _StartupPhase.CLEANED_UP
            return True

        if self._cleanup_task is None:
            self.cleanup_attempted = True
            self._cleanup_task = asyncio.create_task(
                asyncio.to_thread(
                    _cleanup_owned_process,
                    process=self.process,
                    port=self.port,
                    remove_pid_record=self.pid_record_written,
                )
            )

        cancellation: asyncio.CancelledError | None = None
        while not self._cleanup_task.done():
            try:
                await asyncio.shield(self._cleanup_task)
            except asyncio.CancelledError as exc:
                cancellation = exc

        self.cleanup_succeeded = self._cleanup_task.result()
        if self.cleanup_succeeded:
            self.phase = _StartupPhase.CLEANED_UP
        if cancellation is not None:
            raise cancellation
        return self.cleanup_succeeded


# ---------------------------------------------------------------------------
# Port-based process termination
# ---------------------------------------------------------------------------


def parse_local_server_address(*, base_url: str) -> tuple[str, int] | None:
    """
    Parse a plain loopback HTTP URL into its host and port.

    Args:
        base_url: Server root URL.

    Returns:
        tuple[str, int] | None: The loopback host and port, or ``None`` for a non-local or malformed URL.
    """
    parsed_url = urlparse(base_url)
    if (
        parsed_url.scheme != "http"
        or parsed_url.hostname not in {"localhost", "127.0.0.1"}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in {"", "/"}
        or parsed_url.query
        or parsed_url.fragment
    ):
        return None
    try:
        port = parsed_url.port or 80
    except ValueError:
        return None
    return parsed_url.hostname, port


def _get_pid_directory() -> Path:
    return _PID_DIRECTORY


def _pid_file_path(*, port: int) -> Path:
    return _get_pid_directory() / f"server-{port}.json"


def _write_pid_record(*, host: str, port: int, pid: int) -> None:
    path = _pid_file_path(port=port)
    temporary_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps({"host": host, "port": port, "pid": pid}),
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as exc:
        _logger.warning("Could not persist backend process state to %s: %s", path, exc)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            _logger.warning("Could not remove temporary backend process state from %s", temporary_path)


def _remove_pid_record(*, port: int) -> None:
    path = _pid_file_path(port=port)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("Could not remove backend process state from %s: %s", path, exc)


def _read_recorded_pid(*, port: int) -> int | None:
    path = _pid_file_path(port=port)
    try:
        raw_record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        _logger.warning("Discarding invalid backend process state from %s: %s", path, exc)
        _remove_pid_record(port=port)
        return None

    pid = raw_record.get("pid") if isinstance(raw_record, dict) else None
    recorded_port = raw_record.get("port") if isinstance(raw_record, dict) else None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or recorded_port != port:
        _logger.warning("Discarding invalid backend process state from %s", path)
        _remove_pid_record(port=port)
        return None
    return pid


def _parse_netstat_listener_pids(*, output: str, port: int) -> set[int]:
    pids: set[int] = set()
    for line in output.splitlines():
        tokens = line.split()
        if len(tokens) < 5 or tokens[0].upper() != "TCP" or tokens[3].upper() != "LISTENING":
            continue
        try:
            local_port = int(tokens[1].rsplit(":", maxsplit=1)[1])
            pid = int(tokens[-1])
        except (IndexError, ValueError):
            continue
        if local_port == port:
            pids.add(pid)
    return pids


def _find_pid_on_port_windows(*, port: int) -> int | None:
    """
    Find the PID listening on *port* on Windows.

    Args:
        port: TCP port to look up.

    Returns:
        int | None: The PID, or ``None`` if no listener was found.
    """
    powershell_command = (
        f"Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty OwningProcess -Unique"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell_command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}
        if len(pids) == 1:
            return next(iter(pids))
        if len(pids) > 1:
            return None
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pids = _parse_netstat_listener_pids(output=result.stdout, port=port)
    return next(iter(pids)) if len(pids) == 1 else None


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
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None:
        pids = {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}
        if len(pids) == 1:
            return next(iter(pids))
        if len(pids) > 1:
            return None

    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pids = {int(pid) for pid in re.findall(r"pid=(\d+)", result.stdout)}
    return next(iter(pids)) if len(pids) == 1 else None


def _find_pid_on_port(*, port: int) -> int | None:
    if sys.platform == "win32":
        return _find_pid_on_port_windows(port=port)
    return _find_pid_on_port_unix(port=port)


def _resolve_server_pid(*, port: int) -> int | None:
    listener_pid = _find_pid_on_port(port=port)
    recorded_pid = _read_recorded_pid(port=port)
    if recorded_pid is not None:
        if recorded_pid == listener_pid:
            return recorded_pid
        _remove_pid_record(port=port)
    return listener_pid


def _process_exists(*, pid: int) -> bool:
    if sys.platform == "win32":
        powershell_command = (
            f"$process = Get-Process -Id {pid} -ErrorAction SilentlyContinue; if ($null -eq $process) {{ exit 1 }}"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell_command],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return result.returncode == 0

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_process_exit(*, pid: int, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _process_exists(pid=pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return _find_pid_on_port(port=port) is None


def _terminate_process_tree(*, process: subprocess.Popen[bytes]) -> bool:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=_PROCESS_STOP_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0 and process.poll() is None:
            return False
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            return False

    try:
        process.wait(timeout=_PROCESS_STOP_TIMEOUT)
        return True
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            return False
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=_PROCESS_STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True


def _cleanup_owned_process(
    *,
    process: subprocess.Popen[bytes],
    port: int | None,
    remove_pid_record: bool,
) -> bool:
    """
    Stop an owned backend process and discard its PID record after it exits.

    Args:
        process: Backend process owned by the launcher.
        port: Backend bind port, when known.
        remove_pid_record: Whether this startup attempt wrote the PID record.

    Returns:
        bool: ``True`` when no owned process remains, otherwise ``False``.
    """
    if process.poll() is None and not _terminate_process_tree(process=process):
        return False
    if remove_pid_record and port is not None:
        _remove_pid_record(port=port)
    return True


def stop_server_on_port(*, port: int, shutdown_timeout: float = _PROCESS_STOP_TIMEOUT) -> bool:
    """
    Find and terminate the process listening on *port*.

    Args:
        port: TCP port to look up.
        shutdown_timeout: Seconds to wait for the process to exit.

    Returns:
        bool: ``True`` if the process exits and releases the port, ``False`` otherwise.
    """
    pid = _resolve_server_pid(port=port)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        _logger.warning("Could not signal backend process %d on port %d: %s", pid, port, exc)
        return False
    if not _wait_for_process_exit(pid=pid, port=port, timeout=shutdown_timeout):
        _logger.warning("Backend process %d did not stop within %.1f seconds", pid, shutdown_timeout)
        return False
    _remove_pid_record(port=port)
    return True


def _spawn_backend_process(
    *,
    command: list[str],
    log_path: str,
    creation_flags: int,
    start_new_session: bool,
) -> subprocess.Popen[bytes]:
    """
    Spawn the detached backend while redirecting its output to a log file.

    Args:
        command: Backend command and arguments.
        log_path: File path for backend output.
        creation_flags: Platform-specific subprocess creation flags.
        start_new_session: Whether to detach into a new process session.

    Returns:
        subprocess.Popen[bytes]: The spawned launcher process.
    """
    with open(log_path, "w", encoding="utf-8") as log_handle:
        return subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            start_new_session=start_new_session,
        )


async def _spawn_backend_process_async(
    *,
    command: list[str],
    log_path: str,
    creation_flags: int,
    start_new_session: bool,
    startup_state: _ServerStartupState,
) -> subprocess.Popen[bytes]:
    """
    Spawn the detached backend without leaking it if startup is cancelled.

    Args:
        command: Backend command and arguments.
        log_path: File path for backend output.
        creation_flags: Platform-specific subprocess creation flags.
        start_new_session: Whether to detach into a new process session.
        startup_state: State that assumes process ownership before cancellation propagates.

    Returns:
        subprocess.Popen[bytes]: The spawned launcher process.

    Raises:
        asyncio.CancelledError: If startup is cancelled after the process is spawned.
    """
    spawn_task = asyncio.create_task(
        asyncio.to_thread(
            _spawn_backend_process,
            command=command,
            log_path=log_path,
            creation_flags=creation_flags,
            start_new_session=start_new_session,
        )
    )
    cancellation: asyncio.CancelledError | None = None
    while not spawn_task.done():
        try:
            await asyncio.shield(spawn_task)
        except asyncio.CancelledError as exc:
            cancellation = exc

    process = spawn_task.result()
    startup_state.record_process(process=process)
    if cancellation is not None:
        raise cancellation
    return process


async def _write_pid_record_async(*, host: str, port: int, pid: int, startup_state: _ServerStartupState) -> None:
    """
    Persist process state before allowing cancellation to unwind startup.

    Raises:
        asyncio.CancelledError: After the in-flight record write completes.
    """
    write_task = asyncio.create_task(
        asyncio.to_thread(
            _write_pid_record,
            host=host,
            port=port,
            pid=pid,
        )
    )
    cancellation: asyncio.CancelledError | None = None
    while not write_task.done():
        try:
            await asyncio.shield(write_task)
        except asyncio.CancelledError as exc:
            cancellation = exc

    write_task.result()
    startup_state.record_pid_write()
    if cancellation is not None:
        raise cancellation


class ServerLauncher:
    """
    Launch and manage a local ``pyrit_backend`` server.

    The subprocess is **detached** — it survives after the parent CLI exits.
    This is intentional: a running server on ``localhost:8000`` is reusable
    across multiple ``pyrit_scan`` / ``pyrit_shell`` sessions.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._listener_pid: int | None = None
        self._port: int | None = None
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

    async def _probe_health_with_timeout_async(self, *, base_url: str, timeout: float) -> bool:
        """
        Probe backend health while bounding an individual request.

        Returns:
            bool: ``True`` when the backend reports healthy, otherwise ``False``.
        """
        try:
            return await asyncio.wait_for(
                self.probe_health_async(base_url=base_url),
                timeout=min(_HEALTH_PROBE_TIMEOUT, timeout),
            )
        except asyncio.TimeoutError:
            return False

    async def _print_startup_diagnostics_async(self, *, startup_state: _ServerStartupState) -> None:
        """Print the backend log tail once for a failed startup attempt."""
        if startup_state.diagnostics_printed:
            return
        startup_state.diagnostic_tail = await asyncio.to_thread(self._read_log_tail)
        await asyncio.to_thread(self._print_log_tail, tail=startup_state.diagnostic_tail)
        startup_state.diagnostics_printed = True

    async def _wait_for_readiness_async(
        self,
        *,
        plan: _ServerLaunchPlan,
        host: str,
        startup_state: _ServerStartupState,
    ) -> None:
        """
        Advance a launched process until it becomes ready, exits, or times out.

        Raises:
            RuntimeError: If no process was recorded or the process exits before readiness.
        """
        process = startup_state.process
        if process is None:
            raise RuntimeError("Backend process was not recorded after launch.")

        deadline = time.monotonic() + plan.startup_timeout
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                startup_state.record_failure()
                await self._print_startup_diagnostics_async(startup_state=startup_state)
                raise RuntimeError(
                    f"Server process exited with code {exit_code} during startup. See logs: {self._log_path}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            healthy = await self._probe_health_with_timeout_async(base_url=plan.base_url, timeout=remaining)
            startup_state.record_health_check(healthy=healthy)
            if healthy:
                listener_pid = await asyncio.to_thread(_find_pid_on_port, port=startup_state.port)
                if listener_pid is not None:
                    self._listener_pid = listener_pid
                    await _write_pid_record_async(
                        host=host,
                        port=startup_state.port,
                        pid=listener_pid,
                        startup_state=startup_state,
                    )
                print(f"Server ready (PID {self._listener_pid}). Logs: {self._log_path}")
                startup_state.record_ready()
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(_STARTUP_POLL_INTERVAL, remaining))

        startup_state.record_timeout()
        await self._print_startup_diagnostics_async(startup_state=startup_state)

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
        startup_timeout: float = DEFAULT_SERVER_STARTUP_TIMEOUT,
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
            ValueError: If ``startup_timeout`` is not finite and greater than zero.
        """
        plan = _build_server_launch_plan(
            host=host,
            port=port,
            config_file=config_file,
            log_level=log_level,
            startup_timeout=startup_timeout,
            executable=sys.executable,
            platform_name=os.name,
        )

        # Already running?
        already_running = await self._probe_health_with_timeout_async(
            base_url=plan.base_url,
            timeout=plan.startup_timeout,
        )
        if already_running:
            _logger.info("Server already running at %s", plan.base_url)
            return plan.base_url

        _logger.info("Launching pyrit_backend: %s", " ".join(plan.command))

        print(f"Starting server at {plan.base_url}...")
        sys.stdout.flush()

        # The backend is detached and outlives this process, so it must not inherit
        # our stdout/stderr. A caller that captures our output (a piped shell, a
        # Jupyter ``!`` cell, or CI) would otherwise block forever waiting for the
        # inherited handle to close. Send the child's output to a log file so
        # startup diagnostics are still available.
        self._log_path = os.path.join(tempfile.gettempdir(), "pyrit_backend.log")
        startup_state = _ServerStartupState(port=port)
        try:
            process = await _spawn_backend_process_async(
                command=plan.command,
                log_path=self._log_path,
                creation_flags=plan.creation_flags,
                start_new_session=plan.start_new_session,
                startup_state=startup_state,
            )
            self._process = process
            self._listener_pid = process.pid
            self._port = port
            await _write_pid_record_async(
                host=host,
                port=port,
                pid=process.pid,
                startup_state=startup_state,
            )
            _logger.info("Backend launcher PID: %d (logs: %s)", process.pid, self._log_path)
            await self._wait_for_readiness_async(plan=plan, host=host, startup_state=startup_state)
        finally:
            if startup_state.phase is not _StartupPhase.READY and startup_state.process is not None:
                if self._process is None:
                    self._process = startup_state.process
                    self._listener_pid = startup_state.process.pid
                    self._port = port if startup_state.pid_record_written else None
                try:
                    await startup_state.cleanup_async()
                finally:
                    if startup_state.cleanup_succeeded:
                        self._clear_process_state()
                    elif startup_state.cleanup_succeeded is False:
                        _logger.warning("Failed to stop backend launcher process %d", startup_state.process.pid)

        if startup_state.timed_out:
            cleanup_message = (
                "" if startup_state.cleanup_succeeded else " The spawned backend process could not be stopped."
            )
            preload_message = ""
            if "loading datasets" in startup_state.diagnostic_tail.casefold():
                preload_message = (
                    " Recent logs include dataset preload activity, which can extend startup. Check the complete log "
                    "to see what the backend was doing when the timeout occurred. For normal on-demand fetching, "
                    "remove load_default_datasets. For intentional preload, retry with a larger server.startup_timeout."
                )
            raise RuntimeError(
                f"pyrit_backend did not become healthy within {plan.startup_timeout}s. "
                f"Check the server logs ({self._log_path}) or start it manually with: pyrit_backend."
                f"{preload_message}{cleanup_message}"
            )
        return plan.base_url

    def _clear_process_state(self) -> None:
        """Clear process state after the owned backend exits."""
        self._process = None
        self._listener_pid = None
        self._port = None

    def _read_log_tail(self, *, max_lines: int = 20) -> str:
        """
        Read the last ``max_lines`` lines of the backend log file.

        Returns:
            str: The tail of the log, or an empty string when the log is
            unavailable or empty.
        """
        if not self._log_path:
            return ""
        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError:
            return ""
        return "".join(lines[-max_lines:]).rstrip()

    def _print_log_tail(self, *, tail: str | None = None) -> None:
        """Echo the tail of the backend log to stderr, if any is available."""
        tail = self._read_log_tail() if tail is None else tail
        if tail:
            print(f"\n--- pyrit_backend log ({self._log_path}) ---", file=sys.stderr)
            print(tail, file=sys.stderr)
            print("--- end of log ---", file=sys.stderr)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    def stop(self) -> bool:
        """
        Terminate the owned subprocess, if any.

        Returns:
            bool: ``True`` when no owned process remains, ``False`` when termination fails.
        """
        if self._process is None:
            return True

        process = self._process
        process_was_running = process.poll() is None
        if not _cleanup_owned_process(
            process=process,
            port=self._port,
            remove_pid_record=self._port is not None,
        ):
            _logger.warning(
                "Failed to stop server (listener PID %s, launcher PID %d)",
                self._listener_pid,
                process.pid,
            )
            return False

        if process_was_running:
            _logger.info(
                "Stopped server (listener PID %s, launcher PID %d)",
                self._listener_pid,
                process.pid,
            )
        self._clear_process_state()
        return True

    @property
    def pid(self) -> int | None:
        """Resolved listener PID, falling back to the launcher PID during startup."""
        return self._listener_pid
