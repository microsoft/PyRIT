# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The contract-test kit passes for adapters that follow the rules and names the rule a broken one breaks."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

import pytest
from unit._code_execution.local_python_sandbox import LocalPythonSandbox

from pyrit._code_execution import (
    ProcessResult,
    SandboxContractError,
    SandboxProbes,
    SandboxRevisionChangedError,
    SandboxStateKind,
    check_sandbox_contract_async,
)
from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path


class _LostSessionIsGenericError(LocalPythonSandbox):
    async def _execute_async(self, *, session_id: str | None, **kwargs: Any) -> ProcessResult:
        if session_id not in self.sessions:
            raise RuntimeError("no such session")
        return await super()._execute_async(session_id=session_id, **kwargs)


class _ClosingTwiceFails(LocalPythonSandbox):
    async def _close_session_async(self, *, session_id: str) -> None:
        if session_id not in self.sessions:
            raise RuntimeError("already closed")
        await super()._close_session_async(session_id=session_id)


class _CutsOutputSilently(LocalPythonSandbox):
    async def _execute_async(self, **kwargs: Any) -> ProcessResult:
        result = await super()._execute_async(**kwargs)
        return result.model_copy(update={"stdout": result.stdout[:1_000], "stdout_truncated": False})


class _ForgetsFilesOnClose(LocalPythonSandbox):
    async def _close_session_async(self, *, session_id: str) -> None:
        await super()._close_session_async(session_id=session_id)
        shutil.rmtree(self.workspace)
        self.workspace.mkdir()


class _ResetIgnoresRevision(LocalPythonSandbox):
    async def _reset_async(self, *, expected_revision: str | None) -> None:
        await super()._reset_async(expected_revision=None)


class _IdentifierHoldsSessions(LocalPythonSandbox):
    def _build_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"sessions": sorted(self.sessions)})


class _WipesBeforeRefusingReset(LocalPythonSandbox):
    async def _reset_async(self, *, expected_revision: str | None) -> None:
        await super()._reset_async(expected_revision=None)
        if expected_revision is not None and expected_revision != self.revision:
            raise SandboxRevisionChangedError("The revision changed.")


class _LeavesTimedOutRunsRunning(LocalPythonSandbox):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.left_running: list[asyncio.subprocess.Process] = []

    async def _stop_async(self, process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        self.left_running.append(process)
        return b"", b""


@pytest.mark.parametrize("state_kind", [SandboxStateKind.ENVIRONMENT, SandboxStateKind.CALL])
async def test_contract_passes_for_a_sandbox_that_follows_the_rules_async(
    tmp_path: Path, state_kind: SandboxStateKind
) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path, state_kind=state_kind)
    await check_sandbox_contract_async(sandbox=sandbox, probes=SandboxProbes.python(), timeout_probe_seconds=1.0)
    assert not sandbox.sessions


@pytest.mark.parametrize(
    ("sandbox_type", "rule"),
    [
        (_LostSessionIsGenericError, "must raise SandboxSessionLostError"),
        (_ClosingTwiceFails, "Closing a closed session must succeed"),
        (_ForgetsFilesOnClose, "must survive closing its session"),
        (_ResetIgnoresRevision, "must be refused with SandboxRevisionChangedError"),
        (_WipesBeforeRefusingReset, "A refused reset must leave the sandbox unchanged"),
        (_IdentifierHoldsSessions, "static configuration only"),
        (_CutsOutputSilently, "must come back whole"),
    ],
)
async def test_contract_names_the_rule_a_broken_adapter_breaks_async(
    tmp_path: Path, sandbox_type: type[LocalPythonSandbox], rule: str
) -> None:
    sandbox = sandbox_type(root=tmp_path)
    with pytest.raises(SandboxContractError, match=rule):
        await check_sandbox_contract_async(sandbox=sandbox, probes=SandboxProbes.python(), timeout_probe_seconds=1.0)
    assert not (await sandbox.get_status_async()).session_open


async def test_contract_catches_a_timeout_that_leaves_the_program_running_async(tmp_path: Path) -> None:
    sandbox = _LeavesTimedOutRunsRunning(root=tmp_path)
    try:
        with pytest.raises(SandboxContractError, match="must be stopped, not left running"):
            await check_sandbox_contract_async(
                sandbox=sandbox, probes=SandboxProbes.python(), timeout_probe_seconds=1.0
            )
    finally:
        for process in sandbox.left_running:
            if process.returncode is None:
                process.kill()
            await process.wait()


async def test_contract_requires_the_probe_runtime_async(tmp_path: Path) -> None:
    with pytest.raises(SandboxContractError, match="does not offer"):
        await check_sandbox_contract_async(
            sandbox=LocalPythonSandbox(root=tmp_path), probes=SandboxProbes.python(runtime_profile_name="python-3.99")
        )
