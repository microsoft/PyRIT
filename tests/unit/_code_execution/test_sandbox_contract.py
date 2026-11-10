# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The contract-test kit passes for adapters that follow the rules and names the rule a broken one breaks."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from unit._code_execution.local_python_sandbox import LocalPythonSandbox
from unit.async_utils import TEST_WATCHDOG_SECONDS

from pyrit._code_execution import (
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    SandboxBusyError,
    SandboxCondition,
    SandboxContractError,
    SandboxProbes,
    SandboxRevisionChangedError,
    SandboxStateKind,
    check_sandbox_contract_async,
)
from pyrit._code_execution.sandbox_contract import _check_session_hooks_async
from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
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


class _DropsArguments(LocalPythonSandbox):
    async def _execute_async(self, *, arguments: tuple[str, ...], **kwargs: Any) -> ProcessResult:
        return await super()._execute_async(arguments=(), **kwargs)


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


@pytest.mark.parametrize("state_kind", [SandboxStateKind.ENVIRONMENT, SandboxStateKind.SESSION, SandboxStateKind.CALL])
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
        (_DropsArguments, "Arguments must reach the program"),
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


@pytest.mark.parametrize(
    "limits",
    [
        {"max_arguments_bytes": 1},
        {"max_arguments_bytes": 8},
        {"max_arguments_bytes": 28},
        {"max_output_bytes": 1},
        {"max_output_bytes": 8},
        {"max_session_seconds": 61.5, "default_timeout_seconds": 1.0},
    ],
)
async def test_contract_respects_small_declared_budgets_async(
    *, tmp_path: Path, limits: dict[str, int | float]
) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path, max_output_bytes=int(limits.get("max_output_bytes", 4_096)))
    sandbox._capabilities = sandbox.get_capabilities().model_copy(update=limits)
    try:
        await check_sandbox_contract_async(sandbox=sandbox, probes=SandboxProbes.python())
        assert sandbox.sessions == set()
    finally:
        await sandbox.close_async()


@pytest.mark.parametrize("argument_limit", [1, 8])
async def test_contract_still_detects_dropped_arguments_with_small_budgets_async(
    *, tmp_path: Path, argument_limit: int
) -> None:
    sandbox = _DropsArguments(root=tmp_path)
    sandbox._capabilities = sandbox.get_capabilities().model_copy(update={"max_arguments_bytes": argument_limit})
    try:
        with pytest.raises(SandboxContractError, match="Arguments must reach the program"):
            await check_sandbox_contract_async(sandbox=sandbox, probes=SandboxProbes.python())
    finally:
        await sandbox.close_async()


async def test_contract_counts_utf8_comment_prefix_bytes_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    try:
        await check_sandbox_contract_async(
            sandbox=sandbox, probes=replace(SandboxProbes.python(), comment_prefix="#\u00e9")
        )
    finally:
        await sandbox.close_async()


async def test_contract_diagnoses_a_probe_that_cannot_fit_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    sandbox._capabilities = sandbox.get_capabilities().model_copy(update={"max_input_bytes": 8})
    try:
        with pytest.raises(SandboxContractError, match="probe does not fit"):
            await check_sandbox_contract_async(sandbox=sandbox, probes=SandboxProbes.python())
    finally:
        await sandbox.close_async()


@pytest.mark.parametrize("failures", [1, 2])
async def test_contract_probe_retains_and_recovers_a_failed_first_close_async(failures: int) -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("close unavailable"), times=failures)
    try:
        with pytest.raises(SandboxContractError, match="Closing a session"):
            try:
                await _check_session_hooks_async(sandbox=fake, probes=SandboxProbes.python())
            finally:
                await fake.release_async()
        if failures == 1:
            assert fake.open_sessions == set() and fake._unconfirmed_session_ids == []
        else:
            assert fake.open_sessions == {"fake-session-1"}
            assert fake._unconfirmed_session_ids == ["fake-session-1"]
            assert (await fake.get_status_async()).condition is SandboxCondition.QUARANTINED
        await fake.release_async()
        assert fake.open_sessions == set() and fake._unconfirmed_session_ids == []
    finally:
        await fake.close_async()


async def test_contract_probe_first_close_finishes_after_caller_cancellation_async() -> None:
    fake = FakeSandbox()
    closing, finish = asyncio.Event(), asyncio.Event()
    close_session = fake._close_session_async

    async def close_async(*, session_id: str) -> None:
        closing.set()
        await finish.wait()
        await close_session(session_id=session_id)

    async def check_and_release_async() -> None:
        try:
            await _check_session_hooks_async(sandbox=fake, probes=SandboxProbes.python())
        finally:
            with suppress(SandboxBusyError):
                await fake.release_async()

    with patch.object(fake, "_close_session_async", side_effect=close_async):
        caller = asyncio.create_task(check_and_release_async())
        try:
            await asyncio.wait_for(closing.wait(), TEST_WATCHDOG_SECONDS)
            caller.cancel()
            finish.set()
            outcome = await _finish_probe_async(caller)
            assert isinstance(outcome, asyncio.CancelledError)
            await fake.release_async()
            assert fake.open_sessions == set()
        finally:
            finish.set()
            await _finish_probe_async(caller)
            await fake.close_async()


async def _finish_probe_async(task: asyncio.Task[None]) -> None | BaseException:
    return (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), TEST_WATCHDOG_SECONDS))[0]


async def test_contract_requires_the_probe_runtime_async(tmp_path: Path) -> None:
    with pytest.raises(SandboxContractError, match="does not offer"):
        await check_sandbox_contract_async(
            sandbox=LocalPythonSandbox(root=tmp_path), probes=SandboxProbes.python(runtime_profile_name="python-3.99")
        )
