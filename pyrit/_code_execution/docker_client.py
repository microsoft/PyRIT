# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run programs in disposable, locked-down Docker containers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from pyrit._code_execution.client import SandboxExecutionError
from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.models import ComponentIdentifier
from pyrit.models.code_execution import _ProgramExecutionEvent, _SandboxRun

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from pyrit.models.code_execution import _ProgramInvocation, _RuntimeProfileName

logger = logging.getLogger(__name__)

_DIGEST_PATTERN = re.compile(r"@(sha256:[0-9a-f]{64})$")
_NEVER_STARTED = "0001-01-01T00:00:00Z"

_T = TypeVar("_T")


async def _finish_work_async(work: Awaitable[_T]) -> _T:
    """
    Drain owned file work or cleanup even if the caller is cancelled repeatedly.

    Returns:
        _T: The completed work's result.

    Raises:
        CancelledError: Once the work has ended, if the caller was cancelled.
    """
    task = asyncio.ensure_future(work)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except Exception:
            break
    try:
        result = task.result()
    except Exception as error:
        if cancellation is not None:
            raise cancellation from error
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def _close_stdin_async(stream: asyncio.StreamWriter) -> None:
    """
    Close input without letting cancellation poison the writer's close notification.

    Raises:
        CancelledError: After the buffered writes have been aborted and closure has finished.
    """
    stream.close()
    closing = asyncio.create_task(stream.wait_closed())
    try:
        await asyncio.shield(closing)
    except asyncio.CancelledError:
        stream.transport.abort()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await _finish_work_async(closing)
        raise
    except (BrokenPipeError, ConnectionResetError):
        pass


@dataclass(frozen=True)
class DockerRuntimeProfile:
    """
    Configure a trusted, digest-pinned image and its program interpreter.

    The command is followed by the program's path and the invocation's arguments.
    """

    image: str
    command: tuple[str, ...]
    program_filename: str = "main.py"

    def __post_init__(self) -> None:
        """
        Validate the image pin and the program file name.

        Raises:
            ValueError: If the image is not pinned by digest, the command is empty, or the
                file name is not a plain name.
        """
        if _DIGEST_PATTERN.search(self.image) is None:
            raise ValueError(f"Runtime image {self.image!r} must be pinned by digest (name@sha256:<64 hex>).")
        if not self.command or any(not part for part in self.command):
            raise ValueError("A runtime profile command must be a nonempty argument list.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.program_filename):
            raise ValueError(f"Program file name {self.program_filename!r} must be a plain file name.")

    @property
    def image_digest(self) -> str:
        """The ``sha256:`` digest the image is pinned to; ``__post_init__`` guarantees its presence."""
        return self.image.rsplit("@", 1)[1]


@dataclass(frozen=True)
class _ContainerState:
    status: str
    exit_code: int | None
    oom_killed: bool


@dataclass(frozen=True)
class _ClientOutput:
    stdout: bytes
    stdout_truncated: bool
    stderr: bytes
    stderr_truncated: bool


@dataclass(frozen=True)
class _InvocationResult:
    output: _ClientOutput
    container: _ContainerState
    timed_out: bool = False


class DockerSandboxClient:
    """
    Run each invocation in a disposable container on a caller-provided Docker daemon.

    Profiles require pre-pulled, pinned images with no declared volumes. The daemon must support
    cgroup memory, swap, CPU, and process limits; unsupported backends are rejected before execution.
    Containers have no network or privileges, run as an unprivileged user, and have memory, CPU,
    and process limits. The root and program are read-only; ``/tmp`` is a bounded tmpfs mount.
    The profile replaces the image's entrypoint.
    Daemon logging is disabled, and each stream retains at most ``max_output_bytes`` bytes.
    The program mount sits inside a private host directory that is never mounted.

    One deadline covers startup, output capture, and the attached client's exit. The container's
    state distinguishes program termination from client failure. Cleanup removes each container
    before another invocation starts.

    This is not a hardened multi-tenant sandbox. Use a host dedicated to evaluation.
    """

    _READ_CHUNK_BYTES = 1 << 16
    _CONTROL_TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        *,
        profiles: Mapping[_RuntimeProfileName, DockerRuntimeProfile],
        docker_executable: str = "docker",
        timeout_seconds: float = 10.0,
        memory: str = "256m",
        cpus: float = 1.0,
        pids_limit: int = 64,
        max_output_bytes: int = 64_000,
    ) -> None:
        """
        Initialize with the allowlisted runtime profiles and per-container limits.

        Args:
            profiles (Mapping[_RuntimeProfileName, DockerRuntimeProfile]): Names and their trusted
                runtime configurations.
            docker_executable (str): The Docker command line client to call.
            timeout_seconds (float): Wall-clock limit for each invocation.
            memory (str): Memory limit for each container, in Docker's notation, such as ``256m``.
            cpus (float): CPU limit for each container.
            pids_limit (int): Most processes a container may hold.
            max_output_bytes (int): Most bytes of each stream kept per invocation.

        Raises:
            ValueError: If no profile is given, a profile name is invalid, or a limit is not positive.
        """
        if not profiles:
            raise ValueError("DockerSandboxClient requires at least one runtime profile.")
        invalid = [name for name in profiles if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name)]
        if invalid:
            raise ValueError(f"Invalid runtime profile names: {invalid}.")
        if timeout_seconds <= 0 or cpus <= 0 or pids_limit < 1 or max_output_bytes < 1:
            raise ValueError("timeout_seconds, cpus, pids_limit and max_output_bytes must be positive.")
        if not re.fullmatch(r"[1-9][0-9]*[bkmg]?", memory):
            raise ValueError(f"Memory limit {memory!r} must look like 256m or 1g.")
        self._profiles = dict(profiles)
        self._docker = docker_executable
        self._timeout_seconds = timeout_seconds
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._max_output_bytes = max_output_bytes
        self._temp_root = Path(tempfile.gettempdir())

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the profiles and limits.

        Returns:
            ComponentIdentifier: The nonsecret client configuration.
        """
        return ComponentIdentifier.of(
            self,
            params={
                "client_version": 3,
                "profiles": {
                    name: {
                        "image": profile.image,
                        "command": list(profile.command),
                        "program_filename": profile.program_filename,
                    }
                    for name, profile in sorted(self._profiles.items())
                },
                "timeout_seconds": self._timeout_seconds,
                "memory": self._memory,
                "cpus": self._cpus,
                "pids_limit": self._pids_limit,
                "max_output_bytes": self._max_output_bytes,
            },
        )

    def get_runtime_profile_names(self) -> frozenset[_RuntimeProfileName]:
        """
        Return the configured runtime profile names.

        Returns:
            frozenset[_RuntimeProfileName]: Names that select the configured runtimes.
        """
        return frozenset(self._profiles)

    async def execute_async(
        self, *, program: str, runtime_profile_name: _RuntimeProfileName, invocations: tuple[_ProgramInvocation, ...]
    ) -> _SandboxRun:
        """
        Run each invocation in a fresh container, in order.

        Args:
            program (str): The program text, written to a read-only file in the container.
            runtime_profile_name (_RuntimeProfileName): A key from ``profiles``, also returned by
                ``get_runtime_profile_names``. The key selects a trusted image and command.
            invocations (tuple[_ProgramInvocation, ...]): The arguments and input of each run.

        Returns:
            _SandboxRun: One event per invocation that ran. If the sandbox fails after some
            invocations ran, the run holds those events and the reason it stopped.

        Raises:
            ValueError: If the runtime profile is not configured or there are no invocations.
            SandboxExecutionError: If the backend cannot enforce the limits, the image declares
                volumes, or no invocation could be run.
        """
        profile = self._profiles.get(runtime_profile_name)
        if profile is None:
            raise ValueError(f"Runtime profile name {runtime_profile_name!r} is not configured.")
        if not invocations:
            raise ValueError("At least one program invocation is required.")
        await self._validate_backend_async(profile=profile)
        workspace_dir = self._temp_root / f"pyrit-program-{uuid.uuid4().hex}"
        program_dir = workspace_dir / "program"
        program_path = program_dir / profile.program_filename
        try:
            await _finish_work_async(
                asyncio.to_thread(
                    self._write_program, workspace_dir=workspace_dir, program_path=program_path, program=program
                )
            )
            events: list[_ProgramExecutionEvent] = []
            for index, invocation in enumerate(invocations):
                try:
                    event, cleanup_warning = await self._run_invocation_async(
                        invocation_index=index,
                        runtime_profile_name=runtime_profile_name,
                        profile=profile,
                        program_dir=program_dir,
                        invocation=invocation,
                    )
                except SandboxExecutionError as error:
                    if not events:
                        raise
                    return _SandboxRun(events=tuple(events), reasons=(f"sandbox error at invocation {index}: {error}",))
                events.append(event)
                if cleanup_warning is not None:
                    # The environment may still be running, so no further invocation starts beside it.
                    remaining = len(invocations) - len(events)
                    reasons = (f"stopped after invocation {index}: {cleanup_warning}",) if remaining else ()
                    return _SandboxRun(events=tuple(events), reasons=reasons, warnings=(cleanup_warning,))
            return _SandboxRun(events=tuple(events))
        finally:
            await _finish_work_async(
                asyncio.to_thread(self._remove_workspace, workspace_dir=workspace_dir, program_path=program_path)
            )

    async def _validate_backend_async(self, *, profile: DockerRuntimeProfile) -> None:
        """
        Reject unsupported resource controls and image-declared writable volumes.

        Raises:
            SandboxExecutionError: If the backend or image cannot meet the isolation requirements.
        """
        status, output = await self._control_async(
            "info",
            "--format",
            "{{.CgroupDriver}} {{.MemoryLimit}} {{.SwapLimit}} {{.CPUCfsPeriod}} {{.CPUCfsQuota}} {{.PidsLimit}}",
        )
        if status != 0:
            raise SandboxExecutionError(f"could not inspect Docker resource support: {output.strip()[:300]}")
        fields = output.split()
        if len(fields) != 6 or fields[0] not in ("cgroupfs", "systemd") or fields[1:] != ["true"] * 5:
            raise SandboxExecutionError(
                "Docker must support cgroup memory, swap, CPU, and process limits; "
                f"check controller delegation on rootless hosts (reported: {output.strip()[:300]})."
            )
        status, output = await self._control_async(
            "image", "inspect", "--format", "{{json .Config.Volumes}}", profile.image
        )
        if status != 0:
            raise SandboxExecutionError(f"could not inspect the runtime image: {output.strip()[:300]}")
        if output.strip() not in ("null", "{}"):
            raise SandboxExecutionError(
                "Runtime images must not declare volumes, which can bypass the bounded tmpfs storage policy."
            )

    @staticmethod
    def _write_program(*, workspace_dir: Path, program_path: Path, program: str) -> None:
        """Keep the host workspace private while making its inner program mount readable."""
        workspace_dir.mkdir(mode=0o700)
        program_path.parent.mkdir(mode=0o700)
        program_path.write_bytes(program.encode("utf-8"))
        program_path.chmod(0o444)
        program_path.parent.chmod(0o755)

    @staticmethod
    def _remove_workspace(*, workspace_dir: Path, program_path: Path) -> None:
        """Restore file write permission for Windows and remove the entire private workspace."""
        with contextlib.suppress(OSError):
            program_path.chmod(stat.S_IREAD | stat.S_IWRITE)
        shutil.rmtree(workspace_dir, ignore_errors=True)
        if workspace_dir.exists():
            logger.warning("Could not remove the program workspace %s", workspace_dir)

    async def _run_invocation_async(
        self,
        *,
        invocation_index: int,
        runtime_profile_name: _RuntimeProfileName,
        profile: DockerRuntimeProfile,
        program_dir: Path,
        invocation: _ProgramInvocation,
    ) -> tuple[_ProgramExecutionEvent, str | None]:
        """
        Run one invocation in a new container and remove the container afterwards.

        Returns:
            tuple[_ProgramExecutionEvent, str | None]: What the invocation did, and a warning when
            the container could not be removed.

        Raises:
            SandboxExecutionError: If Docker cannot start or inspect the container, or the
                container had not finished when the attached client exited.
        """
        name = f"pyrit-sandbox-{uuid.uuid4().hex}"
        resolved_command = (*profile.command, f"/program/{profile.program_filename}", *invocation.arguments)
        argv = self._run_arguments(
            name=name, profile=profile, program_dir=program_dir, resolved_command=resolved_command
        )
        started = time.monotonic()
        deadline = started + self._timeout_seconds
        startup = asyncio.create_task(self._start_client_async(argv=argv))
        cleanup_warning: str | None = None
        try:
            try:
                process = await asyncio.wait_for(asyncio.shield(startup), timeout=self._timeout_seconds)
            except TimeoutError as error:
                raise SandboxExecutionError("the Docker client did not finish starting before its deadline") from error
            result = await self._wait_for_invocation_async(
                process=process, name=name, stdin=invocation.stdin, deadline=deadline
            )
            duration_ms = int((time.monotonic() - started) * 1000)
        finally:
            cleanup_warning = await _finish_work_async(self._cleanup_invocation_async(startup=startup, name=name))
        output, container = result.output, result.container
        event = _ProgramExecutionEvent(
            invocation_index=invocation_index,
            runtime_profile_name=runtime_profile_name,
            runtime_image_digest=profile.image_digest,
            resolved_command=resolved_command,
            exit_code=None if (result.timed_out or container.oom_killed) else container.exit_code,
            stdout=output.stdout.decode("utf-8", errors="replace"),
            stderr=output.stderr.decode("utf-8", errors="replace"),
            stdout_truncated=output.stdout_truncated,
            stderr_truncated=output.stderr_truncated,
            timed_out=result.timed_out,
            resource_limit_exceeded=container.oom_killed and not result.timed_out,
            duration_ms=duration_ms,
        )
        return event, cleanup_warning

    async def _start_client_async(self, *, argv: list[str]) -> asyncio.subprocess.Process:
        """
        Start the attached Docker client for an invocation that already owns cleanup.

        Returns:
            asyncio.subprocess.Process: The started client.

        Raises:
            SandboxExecutionError: If the client cannot be started.
        """
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise SandboxExecutionError(f"could not start {self._docker!r}: {error}") from error

    async def _cleanup_invocation_async(
        self, *, startup: asyncio.Task[asyncio.subprocess.Process], name: str
    ) -> str | None:
        """
        Wait for startup before reaping the client and removing its container.

        Returns:
            str | None: A warning if cleanup could not be confirmed.
        """
        process: asyncio.subprocess.Process | None = None
        try:
            process = await startup
        except SandboxExecutionError:
            status, output = await self._control_async(
                "container", "ls", "--all", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"
            )
            if status != 0:
                warning = f"could not verify cleanup of container {name}: {output.strip()[:300]}"
                logger.warning("Sandbox cleanup failed: %s", warning)
                return warning
            if not output.strip():
                return None
        return await self._remove_container_async(process=process, name=name)

    async def _remove_container_async(self, *, process: asyncio.subprocess.Process | None, name: str) -> str | None:
        """
        Reap the attached client and remove its container.

        Returns:
            str | None: A warning if removal failed, otherwise None.
        """
        warnings = []
        if process is not None:
            try:
                self._kill_client(process)
                await process.communicate()
            except OSError as error:
                warnings.append(f"could not reap the Docker client for container {name}: {error}")
        status, output = await self._control_async("rm", "--force", "--volumes", name)
        if status != 0:
            detail = output.strip()[:300] or "unknown Docker failure"
            warnings.append(f"could not remove container {name}: {detail}")
        if not warnings:
            return None
        warning = "; ".join(warnings)
        logger.warning("Sandbox cleanup failed: %s", warning)
        return warning

    def _run_arguments(
        self, *, name: str, profile: DockerRuntimeProfile, program_dir: Path, resolved_command: tuple[str, ...]
    ) -> list[str]:
        """
        Build the ``docker run`` command line for one invocation.

        Returns:
            list[str]: The arguments, starting with the Docker client.
        """
        return [
            self._docker,
            "run",
            "--name",
            name,
            "--interactive",
            "--pull",
            "never",
            "--log-driver",
            "none",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--memory",
            self._memory,
            "--memory-swap",
            self._memory,
            "--cpus",
            str(self._cpus),
            "--pids-limit",
            str(self._pids_limit),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m",
            "--workdir",
            "/tmp",
            "--mount",
            f"type=bind,source={os.fspath(program_dir)},target=/program,readonly",
            "--entrypoint",
            resolved_command[0],
            profile.image,
            *resolved_command[1:],
        ]

    async def _wait_for_invocation_async(
        self, *, process: asyncio.subprocess.Process, name: str, stdin: str | None, deadline: float
    ) -> _InvocationResult:
        """
        Apply one deadline and distinguish a client failure from the program's termination.

        Returns:
            _InvocationResult: The captured output and the container's termination facts.

        Raises:
            SandboxExecutionError: If capture failed or the client and container ended inconsistently.
        """
        capture = asyncio.create_task(self._collect_output_async(process=process, stdin=stdin))
        try:
            try:
                output = await asyncio.wait_for(asyncio.shield(capture), timeout=max(deadline - time.monotonic(), 0.0))
            except TimeoutError:
                return await self._finish_timed_out_capture_async(process=process, name=name, capture=capture)
            client_stopped = False
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=max(deadline - time.monotonic(), 0.0))
                except TimeoutError:
                    self._kill_client(process)
                    await process.wait()
                    client_stopped = True
                    output = replace(output, stdout_truncated=True, stderr_truncated=True)
                    logger.warning("Docker client for %s stalled after EOF; output completeness is unknown", name)
            container = await self._inspect_async(name=name, cli_stderr=output.stderr)
            if container.exit_code is None:
                raise SandboxExecutionError(
                    f"the Docker client exited while the container was still {container.status}, "
                    "so the program did not finish"
                )
            if not client_stopped and process.returncode != container.exit_code:
                raise SandboxExecutionError(
                    f"the Docker client exited with {process.returncode}, but the container exited with "
                    f"{container.exit_code}; output capture may be incomplete"
                )
            return _InvocationResult(output=output, container=container)
        finally:
            if not capture.done():
                capture.cancel()
            await _finish_work_async(asyncio.gather(capture, return_exceptions=True))

    async def _finish_timed_out_capture_async(
        self,
        *,
        process: asyncio.subprocess.Process,
        name: str,
        capture: asyncio.Task[_ClientOutput],
    ) -> _InvocationResult:
        """
        Enforce the program deadline only when the container is still running.

        Returns:
            _InvocationResult: A timed-out invocation with its retained output.

        Raises:
            SandboxExecutionError: If the deadline expired because capture stalled after execution.
        """
        container = await self._inspect_async(name=name, cli_stderr=b"")
        if container.status != "running":
            raise SandboxExecutionError(
                f"output capture did not finish before the deadline, but the container was {container.status}; "
                "a program timeout could not be established"
            )
        status, detail = await self._control_async("kill", name)
        if status != 0:
            logger.warning("Could not kill timed-out container %s: %s", name, detail.strip()[:300])
        interrupted = not capture.done()
        self._kill_client(process)
        try:
            output = await asyncio.wait_for(asyncio.shield(capture), timeout=self._CONTROL_TIMEOUT_SECONDS)
        except TimeoutError as error:
            raise SandboxExecutionError("output capture did not finish after stopping the Docker client") from error
        if interrupted:
            output = replace(output, stdout_truncated=True, stderr_truncated=True)
        await process.wait()
        container = await self._inspect_async(name=name, cli_stderr=output.stderr)
        return _InvocationResult(output=output, container=container, timed_out=True)

    async def _collect_output_async(self, *, process: asyncio.subprocess.Process, stdin: str | None) -> _ClientOutput:
        """
        Feed standard input and capture bounded output without managing the container.

        Returns:
            _ClientOutput: The retained streams and their truncation markers.

        Raises:
            SandboxExecutionError: If the client streams cannot be read.
        """
        feeder = asyncio.create_task(self._feed_stdin_async(stream=process.stdin, data=stdin))
        try:
            (stdout, stdout_truncated), (stderr, stderr_truncated) = await gather_with_cleanup_async(
                (self._read_capped_async(process.stdout), self._read_capped_async(process.stderr))
            )
            await feeder
            return _ClientOutput(
                stdout=stdout,
                stdout_truncated=stdout_truncated,
                stderr=stderr,
                stderr_truncated=stderr_truncated,
            )
        except OSError as error:
            raise SandboxExecutionError(f"could not capture Docker client output: {error}") from error
        finally:
            if not feeder.done():
                feeder.cancel()
            await _finish_work_async(asyncio.gather(feeder, return_exceptions=True))

    async def _inspect_async(self, *, name: str, cli_stderr: bytes) -> _ContainerState:
        """
        Read the container's state without inferring anything from the attached client.

        Returns:
            _ContainerState: The state, with an exit code only for a finished container.

        Raises:
            SandboxExecutionError: If the container does not exist, never started, or has invalid state.
        """
        returncode, output = await self._control_async(
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}} {{.State.StartedAt}}",
            name,
        )
        fields = output.split()
        if returncode != 0 or len(fields) != 4 or fields[3] == _NEVER_STARTED:
            lines = [line.strip() for line in cli_stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
            detail = lines[0][:500] if lines else output.strip()[:500]
            raise SandboxExecutionError(f"the container did not start: {detail or 'unknown Docker failure'}")
        try:
            exit_code = int(fields[1])
        except ValueError as error:
            raise SandboxExecutionError(f"the container reported an invalid exit code: {fields[1]!r}") from error
        if fields[2] not in ("true", "false"):
            raise SandboxExecutionError(f"the container reported an invalid memory-kill state: {fields[2]!r}")
        return _ContainerState(
            status=fields[0], exit_code=exit_code if fields[0] == "exited" else None, oom_killed=fields[2] == "true"
        )

    async def _control_async(self, *arguments: str) -> tuple[int, str]:
        """
        Run a short Docker management command, bounded in time.

        Returns:
            tuple[int, str]: The exit status and combined output; status -1 when it failed to run.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self._docker,
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as error:
            logger.warning("Docker %s failed to start: %s", arguments[0], error)
            return -1, str(error)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=self._CONTROL_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Docker %s timed out", arguments[0])
            return -1, "timed out"
        finally:
            # Runs on timeout and on cancellation alike, so no management command outlives its caller.
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await _finish_work_async(process.communicate())
        return process.returncode if process.returncode is not None else -1, output.decode("utf-8", errors="replace")

    async def _read_capped_async(self, stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        """
        Read a stream to its end, keeping at most the configured number of bytes.

        Returns:
            tuple[bytes, bool]: The retained bytes and whether more were discarded.
        """
        if stream is None:
            return b"", False
        kept = bytearray()
        truncated = False
        while chunk := await stream.read(self._READ_CHUNK_BYTES):
            room = self._max_output_bytes - len(kept)
            if len(chunk) > room:
                truncated = True
            kept.extend(chunk[: max(room, 0)])
        return bytes(kept), truncated

    @staticmethod
    async def _feed_stdin_async(*, stream: asyncio.StreamWriter | None, data: str | None) -> None:
        """
        Write the invocation's input, then close standard input so the program sees its end.

        Raises:
            CancelledError: After input has been aborted and its writer closed.
        """
        if stream is None:
            return
        try:
            if data:
                stream.write(data.encode("utf-8"))
                await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except asyncio.CancelledError:
            stream.transport.abort()
            raise
        finally:
            await _close_stdin_async(stream)

    @staticmethod
    def _kill_client(process: asyncio.subprocess.Process) -> None:
        """Stop only this invocation's client, leaving container removal to its owner."""
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
