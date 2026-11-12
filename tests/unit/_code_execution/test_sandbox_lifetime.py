# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""State lifetimes, explicit recovery, and per-invocation acquisition budgets."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS

from pyrit._code_execution import (
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    SandboxBusyError,
    SandboxCondition,
    SandboxExecutionError,
    SandboxRevisionChangedError,
    SandboxStateKind,
    SandboxStateLostError,
    SandboxUnavailableError,
)
from pyrit.models.code_execution import _ProgramInvocation


def _session_sandbox(
    *,
    max_session_seconds: float | None = None,
    acquire_timeout_seconds: float = FakeSandbox.DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> FakeSandbox:
    return FakeSandbox(
        capabilities=FakeSandbox.DEFAULT_CAPABILITIES.model_copy(
            update={"state_kind": SandboxStateKind.SESSION, "max_session_seconds": max_session_seconds}
        ),
        acquire_timeout_seconds=acquire_timeout_seconds,
    )


async def _run_async(sandbox: FakeSandbox, *, timeout_seconds: float | None = None) -> None:
    await sandbox.execute_async(program="print(1)", runtime_profile_name="python", timeout_seconds=timeout_seconds)


async def test_session_state_survives_calls_but_not_explicit_release_async() -> None:
    sandbox = _session_sandbox()
    try:
        await _run_async(sandbox)
        sandbox.state["work"] = "retained"
        await _run_async(sandbox)
        assert sandbox.state["work"] == "retained"
        assert sandbox._session_count == 1
        assert sandbox._idle_handle is None
        await sandbox.release_async()
        assert sandbox.state == {} and sandbox.open_sessions == set()
        await _run_async(sandbox)
        assert sandbox._session_count == 2 and sandbox.state == {}
    finally:
        await sandbox.close_async()


async def test_session_lifetime_refusal_preserves_state_without_renewal_async() -> None:
    now = [1_000.0]
    sandbox = _session_sandbox(max_session_seconds=200)
    with patch("pyrit._code_execution.sandbox.time", SimpleNamespace(monotonic=lambda: now[0])):
        try:
            await _run_async(sandbox, timeout_seconds=30)
            sandbox.state["work"] = "retained"
            now[0] += 120
            with pytest.raises(SandboxUnavailableError, match="state"):
                await _run_async(sandbox, timeout_seconds=30)
            assert sandbox.state["work"] == "retained"
            assert sandbox._session_count == 1 and len(sandbox.runs) == 1
            assert sandbox.closed_sessions == []
            await _run_async(sandbox, timeout_seconds=10)
            assert sandbox._session_count == 1 and len(sandbox.runs) == 2
        finally:
            await sandbox.close_async()


async def test_requested_final_close_drains_session_when_owned_work_finishes_async() -> None:
    sandbox = _session_sandbox(acquire_timeout_seconds=0.01)
    sandbox.gate = asyncio.Event()
    closed = asyncio.Event()
    close_session = sandbox._close_session_async

    async def close_async(*, session_id: str) -> None:
        await close_session(session_id=session_id)
        closed.set()

    with patch.object(sandbox, "_close_session_async", side_effect=close_async):
        running = asyncio.create_task(_run_async(sandbox))
        try:
            await asyncio.wait_for(sandbox.run_started.wait(), TEST_WATCHDOG_SECONDS)
            await sandbox.close_async()
            assert sandbox._closed and sandbox._lock.locked()
            assert sandbox.open_sessions == {"fake-session-1"}
            sandbox.gate.set()
            await asyncio.wait_for(running, TEST_WATCHDOG_SECONDS)
            await asyncio.wait_for(closed.wait(), TEST_WATCHDOG_SECONDS)
            assert sandbox.open_sessions == set() and sandbox._session_id is None
        finally:
            sandbox.gate.set()
            await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), TEST_WATCHDOG_SECONDS)
            await sandbox.close_async()


async def test_lost_session_state_is_not_silently_replaced_async() -> None:
    sandbox = _session_sandbox()
    try:
        await _run_async(sandbox)
        sandbox.state["work"] = "before-loss"
        sandbox.expire_sessions()
        with pytest.raises(SandboxStateLostError, match="state"):
            await _run_async(sandbox)
        assert len(sandbox.runs) == 1 and sandbox._session_count == 1
        assert (await sandbox.get_status_async()).condition is SandboxCondition.QUARANTINED
        await sandbox.release_async()
        with pytest.raises(SandboxUnavailableError, match="quarantined"):
            await _run_async(sandbox)
        await sandbox.recover_async()
        await _run_async(sandbox)
        assert sandbox._session_count == 2 and sandbox.state == {}
    finally:
        await sandbox.close_async()


async def test_uncertain_session_run_requires_explicit_recovery_async() -> None:
    sandbox = _session_sandbox()
    try:
        await _run_async(sandbox)
        sandbox.fail_next(hook=FakeSandboxHook.EXECUTE, error=RuntimeError("connection lost"))
        with pytest.raises(SandboxExecutionError, match="uncertain"):
            await _run_async(sandbox)
        await sandbox.release_async()
        with pytest.raises(SandboxUnavailableError, match="quarantined"):
            await _run_async(sandbox)
        await sandbox.reset_async(expected_revision=sandbox.revision)
        await _run_async(sandbox)
    finally:
        await sandbox.close_async()


async def test_refused_session_reset_does_not_discard_its_state_async() -> None:
    sandbox = _session_sandbox()
    try:
        await _run_async(sandbox)
        sandbox.state["work"] = "retained"
        with pytest.raises(SandboxRevisionChangedError):
            await sandbox.reset_async(expected_revision="wrong")
        assert sandbox.state["work"] == "retained"
        assert sandbox.open_sessions == {"fake-session-1"}
        assert sandbox.closed_sessions == []
        await sandbox.reset_async(expected_revision=sandbox.revision)
        assert sandbox.state == {} and sandbox.open_sessions == set()
    finally:
        await sandbox.close_async()


@pytest.mark.parametrize("recovery", ["operator", "reset"])
async def test_release_cannot_clear_unconfirmed_process_cleanup_async(recovery: str) -> None:
    sandbox = FakeSandbox(handler=lambda run: ProcessResult(exit_code=0, cleanup_confirmed=False))
    try:
        await _run_async(sandbox)
        await sandbox.release_async()
        assert sandbox.open_sessions == set()
        assert (await sandbox.get_status_async()).condition is SandboxCondition.QUARANTINED
        with pytest.raises(SandboxUnavailableError, match="quarantined"):
            await _run_async(sandbox)
        if recovery == "operator":
            await sandbox.recover_async()
        else:
            await sandbox.reset_async(expected_revision=sandbox.revision)
        assert (await sandbox.get_status_async()).condition is SandboxCondition.READY
    finally:
        await sandbox.close_async()


async def test_release_cannot_clear_an_uncertain_reset_async() -> None:
    sandbox = FakeSandbox()
    try:
        sandbox.fail_next(hook=FakeSandboxHook.RESET, error=RuntimeError("reset outcome unknown"))
        with pytest.raises(SandboxExecutionError, match="reset outcome unknown"):
            await sandbox.reset_async()
        await sandbox.release_async()
        assert (await sandbox.get_status_async()).condition is SandboxCondition.QUARANTINED
        await sandbox.recover_async()
        assert (await sandbox.get_status_async()).condition is SandboxCondition.READY
    finally:
        await sandbox.close_async()


async def test_operator_recovery_cannot_bypass_an_unconfirmed_close_async() -> None:
    sandbox = FakeSandbox()
    try:
        await _run_async(sandbox)
        sandbox.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("still unavailable"), times=2)
        await sandbox.release_async()
        with pytest.raises(SandboxUnavailableError, match="quarantined"):
            await sandbox.recover_async()
        assert sandbox._unconfirmed_session_ids == ["fake-session-1"]
        await sandbox.recover_async()
        assert sandbox.open_sessions == set()
        assert (await sandbox.get_status_async()).condition is SandboxCondition.READY
    finally:
        await sandbox.close_async()


async def test_operator_recovery_does_not_run_beside_owned_work_async() -> None:
    sandbox = FakeSandbox()
    sandbox.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(sandbox))
    try:
        await asyncio.wait_for(sandbox.run_started.wait(), TEST_WATCHDOG_SECONDS)
        with pytest.raises(SandboxBusyError):
            await sandbox.recover_async()
    finally:
        sandbox.gate.set()
        await asyncio.wait_for(running, TEST_WATCHDOG_SECONDS)
        await sandbox.close_async()


@pytest.mark.parametrize("lost_session", [False, True])
async def test_later_invocation_acquisition_excludes_completed_execution_time_async(lost_session: bool) -> None:
    now = [1_000.0]
    sandbox = FakeSandbox(
        capabilities=FakeSandbox.DEFAULT_CAPABILITIES.model_copy(
            update={"max_session_seconds": None if lost_session else 600}
        ),
        acquire_timeout_seconds=300,
    )

    def handler(run: object) -> ProcessResult:
        now[0] += 100
        if lost_session and len(sandbox.runs) == 5:
            sandbox.expire_sessions()
        return ProcessResult(exit_code=0)

    sandbox._handler = handler
    with patch("pyrit._code_execution.sandbox.time", SimpleNamespace(monotonic=lambda: now[0])):
        try:
            run = await sandbox.execute_async(
                program="print(1)",
                runtime_profile_name="python",
                invocations=(_ProgramInvocation(),) * 6,
                timeout_seconds=100,
            )
            assert len(run.events) == 6 and run.reasons == ()
            assert sandbox._session_count == 2
        finally:
            await sandbox.close_async()
