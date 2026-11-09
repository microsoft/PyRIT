# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A test-only sandbox that runs Python programs as local subprocesses. It isolates nothing."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from pyrit._code_execution import (
    ProcessResult,
    RuntimeProfile,
    Sandbox,
    SandboxCapabilities,
    SandboxRevisionChangedError,
    SandboxSessionLostError,
    SandboxStateKind,
    SandboxStatus,
)
from pyrit.models import ComponentIdentifier


class LocalPythonSandbox(Sandbox):
    """
    Run Python programs with the test interpreter, for tests of PyRIT's own programs only.

    An ``environment`` sandbox keeps one working directory across calls and sessions, and a reset empties
    it. A ``call`` sandbox runs each program in a new, empty directory. With ``isolated=False``, Python runs
    without ``-I`` and the program is saved in the working directory, as a careless adapter might do.
    """

    def __init__(
        self,
        *,
        root: Path,
        state_kind: SandboxStateKind = SandboxStateKind.ENVIRONMENT,
        max_output_bytes: int = 4_096,
        isolated: bool = True,
    ) -> None:
        super().__init__()
        self._root = root
        self._state_kind = state_kind
        self._max_output_bytes = max_output_bytes
        self._isolated = isolated
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (root / "programs").mkdir(exist_ok=True)
        self.sessions: set[str] = set()
        self.revision = "local-revision-1"

    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"root": str(self._root), "state_kind": self._state_kind.value})

    def _get_capabilities(self) -> SandboxCapabilities:
        environment = self._state_kind is SandboxStateKind.ENVIRONMENT
        return SandboxCapabilities(
            runtime_profiles=(RuntimeProfile(name="python", description="Python 3"),),
            state_kind=self._state_kind,
            description="a local test directory",
            supports_reset=environment,
            file_runtime_profile_name="python" if environment else None,
            max_output_bytes=self._max_output_bytes,
        )

    async def _execute_async(
        self,
        *,
        session_id: str | None,
        runtime_profile_name: str,
        program: str,
        arguments: tuple[str, ...],
        stdin: str | None,
        timeout_seconds: float,
    ) -> ProcessResult:
        if self._state_kind is SandboxStateKind.ENVIRONMENT and session_id not in self.sessions:
            raise SandboxSessionLostError(f"Session {session_id} is not open.")
        working_dir = self.workspace if session_id is not None else self._new_call_directory()
        program_dir = self._root / "programs" if self._isolated else working_dir
        program_path = program_dir / f"program_{uuid.uuid4().hex}.py"
        await asyncio.to_thread(program_path.write_text, program, encoding="utf-8")
        try:
            return await self._run_async(
                program_path=program_path,
                working_dir=working_dir,
                arguments=arguments,
                stdin=stdin,
                timeout_seconds=timeout_seconds,
            )
        finally:
            await asyncio.to_thread(program_path.unlink)
            if session_id is None:
                await asyncio.to_thread(shutil.rmtree, working_dir, True)

    async def _run_async(
        self,
        *,
        program_path: Path,
        working_dir: Path,
        arguments: tuple[str, ...],
        stdin: str | None,
        timeout_seconds: float,
    ) -> ProcessResult:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *(["-I"] if self._isolated else []),
            "-X",
            "utf8",
            str(program_path),
            *arguments,
            cwd=working_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate((stdin or "").encode("utf-8")), timeout=timeout_seconds
            )
        except TimeoutError:
            stdout, stderr = await self._stop_async(process)
            timed_out = True
        limit = self._max_output_bytes
        return ProcessResult(
            exit_code=None if timed_out else process.returncode,
            stdout=stdout[:limit].decode("utf-8", errors="replace"),
            stderr=stderr[:limit].decode("utf-8", errors="replace"),
            stdout_truncated=len(stdout) > limit,
            stderr_truncated=len(stderr) > limit,
            timed_out=timed_out,
        )

    async def _stop_async(self, process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        process.kill()
        return await process.communicate()

    def _new_call_directory(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self._root))

    async def _open_session_async(self, *, owner: str) -> str:
        session_id = uuid.uuid4().hex
        self.sessions.add(session_id)
        return session_id

    async def _close_session_async(self, *, session_id: str) -> None:
        self.sessions.discard(session_id)

    async def _get_status_async(self) -> SandboxStatus:
        return SandboxStatus(revision=self.revision, details={"workspace": str(self.workspace)})

    async def _reset_async(self, *, expected_revision: str | None) -> None:
        if expected_revision is not None and expected_revision != self.revision:
            raise SandboxRevisionChangedError(f"The revision is {self.revision}.")
        await asyncio.to_thread(shutil.rmtree, self.workspace)
        self.workspace.mkdir()
