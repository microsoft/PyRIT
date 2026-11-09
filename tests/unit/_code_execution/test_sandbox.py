# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The rules every ``Sandbox`` follows, checked against ``FakeSandbox``."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS

from pyrit._code_execution import (
    FakeRun,
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    RuntimeProfile,
    Sandbox,
    SandboxBusyError,
    SandboxCapabilities,
    SandboxCondition,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRevisionChangedError,
    SandboxSessionLostError,
    SandboxStateKind,
    SandboxUnavailableError,
)
from pyrit.models.code_execution import _ProgramInvocation

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models.code_execution import _SandboxRun

_PYTHON = (RuntimeProfile(name="python"),)
_CALL_CAPABILITIES = SandboxCapabilities(runtime_profiles=_PYTHON, state_kind=SandboxStateKind.CALL)


def _echo(run: FakeRun) -> ProcessResult:
    return ProcessResult(exit_code=0, stdout=run.program)


async def _run_async(sandbox: Sandbox, program: str = "print(1)", **kwargs: Any) -> _SandboxRun:
    return await sandbox.execute_async(program=program, runtime_profile_name="python", **kwargs)


async def _wait_until_async(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(TEST_WATCHDOG_SECONDS):
        while not condition():
            await asyncio.sleep(0.001)


async def _finish_async(*tasks: asyncio.Task[Any]) -> list[Any]:
    return await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), TEST_WATCHDOG_SECONDS)


# ---- capabilities and validation (I6) ----------------------------------------------------------


@pytest.mark.parametrize(
    "settings",
    [
        {"runtime_profiles": (RuntimeProfile(name="python"), RuntimeProfile(name="python"))},
        {"default_timeout_seconds": 200.0},
        {"file_runtime_profile_name": "ruby"},
        {"state_kind": SandboxStateKind.CALL, "file_runtime_profile_name": "python"},
        {"state_kind": SandboxStateKind.CALL, "supports_reset": True},
        {"state_kind": SandboxStateKind.CALL, "max_session_seconds": 600.0},
    ],
)
def test_capabilities_reject_contradictions(settings: dict[str, Any]) -> None:
    values: dict[str, Any] = {"runtime_profiles": _PYTHON, "state_kind": SandboxStateKind.ENVIRONMENT, **settings}
    with pytest.raises(ValueError):
        SandboxCapabilities(**values)


def test_runtime_profiles_may_share_a_derived_tool_name() -> None:
    profiles = (RuntimeProfile(name="py.3"), RuntimeProfile(name="py-3"))
    assert SandboxCapabilities(runtime_profiles=profiles, state_kind=SandboxStateKind.CALL).runtime_profiles == profiles


async def test_arguments_alone_can_be_bounded_async() -> None:
    capabilities = _CALL_CAPABILITIES.model_copy(update={"max_arguments_bytes": 10})
    fake = FakeSandbox(capabilities=capabilities)
    with pytest.raises(SandboxInputError, match="standard input instead"):
        await _run_async(fake, invocations=(_ProgramInvocation(arguments=("x" * 11,)),))
    await _run_async(fake, invocations=(_ProgramInvocation(arguments=("x" * 10,), stdin="y" * 1_000),))


def test_adapter_must_implement_the_hooks_its_capabilities_need() -> None:
    class _NoSessions(Sandbox):
        def _build_identifier(self) -> Any:
            raise NotImplementedError

        def _get_capabilities(self) -> SandboxCapabilities:
            return FakeSandbox.DEFAULT_CAPABILITIES

        async def _execute_async(self, **kwargs: Any) -> ProcessResult:
            raise NotImplementedError

    with pytest.raises(TypeError, match="_open_session_async, _close_session_async, _reset_async"):
        _NoSessions().get_capabilities()


@pytest.mark.parametrize(
    ("request_settings", "message"),
    [
        ({"runtime_profile_name": "ruby"}, "not offered"),
        ({"invocations": ()}, "At least one"),
        ({"program": "x" * 65_537}, "at most 65536"),
        ({"invocations": (_ProgramInvocation(stdin="x" * 65_530),)}, "at most 65536"),
        ({"invocations": (_ProgramInvocation(arguments=("x" * 65_530,)),)}, "at most 65536"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 121}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"program": "\ud800"}, "UTF-8"),
    ],
)
async def test_invalid_requests_fail_before_anything_runs_async(request_settings: dict[str, Any], message: str) -> None:
    fake = FakeSandbox()
    values: dict[str, Any] = {"program": "print(1)", "runtime_profile_name": "python", **request_settings}
    with pytest.raises(SandboxInputError, match=message):
        await fake.execute_async(**values)
    assert fake.runs == [] and fake.open_sessions == set()


async def test_timeout_must_fit_in_a_session_async() -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"max_session_seconds": 100.0})
    fake = FakeSandbox(capabilities=capabilities)
    with pytest.raises(SandboxInputError, match="session limit"):
        await _run_async(fake, timeout_seconds=50)
    assert (await _run_async(fake, timeout_seconds=40)).events[0].exit_code == 0


# ---- one shared session, taking turns (I2) -----------------------------------------------------


async def test_first_call_opens_the_session_and_later_calls_share_it_async() -> None:
    fake = FakeSandbox(handler=_echo)
    first = await _run_async(fake, program="one", invocations=(_ProgramInvocation(), _ProgramInvocation()))
    second = await _run_async(fake, program="two")
    assert [event.stdout for event in (*first.events, *second.events)] == ["one", "one", "two"]
    assert {run.session_id for run in fake.runs} == {"fake-session-1"}
    assert fake.open_sessions == {"fake-session-1"}


async def test_calls_take_turns_first_come_first_served_async() -> None:
    fake = FakeSandbox(handler=_echo)
    fake.gate = asyncio.Event()
    first = asyncio.create_task(_run_async(fake, program="first"))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    later = [asyncio.create_task(_run_async(fake, program=name)) for name in ("second", "third")]
    await asyncio.sleep(0.05)
    assert [run.program for run in fake.runs] == ["first"]
    fake.gate.set()
    await _finish_async(first, *later)
    assert [run.program for run in fake.runs] == ["first", "second", "third"]


async def test_a_call_that_waits_past_its_deadline_is_busy_and_never_runs_async() -> None:
    fake = FakeSandbox(handler=_echo, acquire_timeout_seconds=0.05)
    fake.gate = asyncio.Event()
    holder = asyncio.create_task(_run_async(fake, program="holder"))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    with pytest.raises(SandboxBusyError, match="nothing ran"):
        await _run_async(fake, program="late")
    fake.gate.set()
    await _finish_async(holder)
    assert [run.program for run in fake.runs] == ["holder"]


async def test_call_sandboxes_run_calls_concurrently_without_sessions_async() -> None:
    fake = FakeSandbox(capabilities=_CALL_CAPABILITIES, handler=_echo)
    fake.gate = asyncio.Event()
    calls = [asyncio.create_task(_run_async(fake, program=name)) for name in ("a", "b")]
    await _wait_until_async(lambda: len(fake.runs) == 2)
    fake.gate.set()
    await _finish_async(*calls)
    assert {run.session_id for run in fake.runs} == {None}
    assert fake.closed_sessions == []


# ---- retries and uncertain failures (I3, I4) ---------------------------------------------------


async def test_a_busy_open_is_retried_until_it_succeeds_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxBusyError("held"), times=2)
    with patch.object(FakeSandbox, "_BUSY_RETRY_INITIAL_SECONDS", 0.001):
        assert (await _run_async(fake)).events[0].exit_code == 0
    assert fake.open_sessions == {"fake-session-1"}


async def test_a_sandbox_that_stays_busy_fails_at_the_deadline_async() -> None:
    fake = FakeSandbox(acquire_timeout_seconds=0.05)
    fake.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxBusyError("held"), times=1_000)
    with patch.object(FakeSandbox, "_BUSY_RETRY_INITIAL_SECONDS", 0.01), pytest.raises(SandboxBusyError):
        await _run_async(fake)
    assert fake.runs == []


async def test_lock_wait_and_busy_retries_share_one_deadline_async() -> None:
    fake = FakeSandbox()
    with (
        patch.object(fake, "_acquire_lock_async", wraps=fake._acquire_lock_async) as acquire,
        patch.object(fake, "_open_session_with_retry_async", wraps=fake._open_session_with_retry_async) as open_session,
    ):
        await _run_async(fake)
    assert open_session.call_args.kwargs["deadline"] == acquire.call_args.kwargs["deadline"]


async def test_closing_stops_a_busy_open_from_retrying_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxBusyError("held"), times=1_000)
    with patch.object(FakeSandbox, "_BUSY_RETRY_INITIAL_SECONDS", 0.01):
        call = asyncio.create_task(_run_async(fake))
        await asyncio.sleep(0.05)
        await asyncio.wait_for(fake.close_async(), TEST_WATCHDOG_SECONDS)
        (outcome,) = await _finish_async(call)
    assert isinstance(outcome, SandboxUnavailableError) and "closed" in str(outcome)
    assert fake.runs == []


async def test_a_lost_session_is_reopened_and_the_call_retried_once_async() -> None:
    fake = FakeSandbox(handler=_echo)
    await _run_async(fake, program="before")
    fake.expire_sessions()
    assert (await _run_async(fake, program="after")).events[0].stdout == "after"
    assert [run.session_id for run in fake.runs] == ["fake-session-1", "fake-session-2"]


async def test_a_session_lost_twice_is_an_error_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=SandboxSessionLostError("gone"), times=2)
    with pytest.raises(SandboxSessionLostError):
        await _run_async(fake)
    assert fake.runs == []
    assert fake.open_sessions == {"fake-session-1", "fake-session-2"}


async def test_an_uncertain_failure_closes_the_session_and_is_never_retried_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=RuntimeError("connection reset"))
    with pytest.raises(SandboxExecutionError, match="uncertain") as raised:
        await _run_async(fake)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert fake.closed_sessions == ["fake-session-1"]
    await _run_async(fake)
    assert [run.session_id for run in fake.runs] == ["fake-session-2"]


@pytest.mark.parametrize("error", [SandboxInputError("too large"), SandboxBusyError("not now")])
async def test_failures_where_nothing_ran_keep_the_session_async(error: Exception) -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=error)
    with pytest.raises(type(error)):
        await _run_async(fake)
    assert fake.closed_sessions == []


async def test_an_adapter_that_does_not_answer_is_uncertain_async() -> None:
    async def hang_async(run: FakeRun) -> ProcessResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    fake = FakeSandbox(handler=hang_async)
    with (
        patch.object(FakeSandbox, "_EXECUTE_GRACE_SECONDS", 0.0),
        pytest.raises(SandboxExecutionError, match="did not answer in time"),
    ):
        await _run_async(fake, timeout_seconds=0.05)
    assert fake.closed_sessions == ["fake-session-1"]


@pytest.mark.parametrize(
    "result",
    [ProcessResult.model_construct(exit_code=0, timed_out=True, duration_ms=1), {"exit_code": 0}],
    ids=["unvalidated", "not-a-result"],
)
async def test_an_invalid_result_is_an_uncertain_failure_async(result: Any) -> None:
    fake = FakeSandbox(handler=lambda run: result)
    with pytest.raises(SandboxExecutionError, match="invalid result"):
        await _run_async(fake)
    assert fake.closed_sessions == ["fake-session-1"]


async def test_an_unconfirmed_close_quarantines_until_released_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=RuntimeError("lost the host"))
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("host unreachable"))
    with pytest.raises(SandboxExecutionError):
        await _run_async(fake)
    view = await fake.get_status_async()
    assert view.condition is SandboxCondition.QUARANTINED and "host unreachable" in (view.quarantine_reason or "")
    with pytest.raises(SandboxExecutionError, match="quarantined"):
        await _run_async(fake)
    await fake.release_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.READY
    assert fake.closed_sessions == ["fake-session-1"]
    assert (await _run_async(fake)).events[0].exit_code == 0


async def test_release_keeps_the_quarantine_while_the_close_still_fails_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("host unreachable"), times=2)
    await fake.release_async()
    await fake.release_async()
    view = await fake.get_status_async()
    assert view.condition is SandboxCondition.QUARANTINED and fake.open_sessions == {"fake-session-1"}
    await fake.release_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.READY
    assert fake.open_sessions == set()


async def test_a_quarantine_during_renewal_opens_no_second_session_async() -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"max_session_seconds": 200.0})
    fake = FakeSandbox(capabilities=capabilities)
    await _run_async(fake, timeout_seconds=30)
    fake._session_opened_at -= 120
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("host unreachable"))
    with pytest.raises(SandboxUnavailableError, match="quarantined"):
        await _run_async(fake, timeout_seconds=30)
    assert fake.open_sessions == {"fake-session-1"} and len(fake.runs) == 1


# ---- cancellation (I1) -------------------------------------------------------------------------


async def test_a_caller_that_gives_up_leaves_the_call_holding_the_sandbox_async() -> None:
    fake = FakeSandbox(handler=_echo)
    fake.gate = asyncio.Event()
    caller = asyncio.create_task(_run_async(fake, program="slow"))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert (await fake.get_status_async()).busy
    waiting = asyncio.create_task(_run_async(fake, program="next"))
    await asyncio.sleep(0.05)
    assert len(fake.runs) == 1
    fake.gate.set()
    (run,) = await _finish_async(waiting)
    assert run.events[0].stdout == "next"
    assert fake.runs[0].session_id == fake.runs[1].session_id


async def test_a_cancelled_open_still_records_its_session_async() -> None:
    fake = FakeSandbox()
    opening, release = asyncio.Event(), asyncio.Event()
    open_session = fake._open_session_async

    async def slow_open_async(*, owner: str) -> str:
        opening.set()
        await release.wait()
        return await open_session(owner=owner)

    with patch.object(fake, "_open_session_async", side_effect=slow_open_async):
        caller = asyncio.create_task(_run_async(fake))
        await asyncio.wait_for(opening.wait(), TEST_WATCHDOG_SECONDS)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await _wait_until_async(lambda: not fake._lock.locked())
    assert fake.open_sessions == {"fake-session-1"}
    await fake.close_async()
    assert fake.open_sessions == set()


async def test_a_caller_cancelled_while_waiting_never_runs_async() -> None:
    fake = FakeSandbox(handler=_echo)
    fake.gate = asyncio.Event()
    holder = asyncio.create_task(_run_async(fake, program="holder"))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    waiting = asyncio.create_task(_run_async(fake, program="abandoned"))
    await asyncio.sleep(0.01)
    waiting.cancel()
    fake.gate.set()
    await _finish_async(holder, waiting)
    assert waiting.cancelled()
    assert [run.program for run in fake.runs] == ["holder"]


@pytest.mark.parametrize("operation", ["release", "close"])
async def test_closing_the_session_finishes_when_the_caller_gives_up_async(operation: str) -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    closing, release = asyncio.Event(), asyncio.Event()
    close_session = fake._close_session_async

    async def slow_close_async(*, session_id: str) -> None:
        closing.set()
        await release.wait()
        await close_session(session_id=session_id)

    with patch.object(fake, "_close_session_async", side_effect=slow_close_async):
        caller = asyncio.create_task(fake.release_async() if operation == "release" else fake.close_async())
        await asyncio.wait_for(closing.wait(), TEST_WATCHDOG_SECONDS)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert (await fake.get_status_async()).busy
        release.set()
        await _wait_until_async(lambda: not fake._lock.locked())
    assert fake.open_sessions == set() and fake.closed_sessions == ["fake-session-1"]


async def test_a_call_sandbox_passes_cancellation_to_the_adapter_async() -> None:
    cancelled = asyncio.Event()

    async def slow_async(run: FakeRun) -> ProcessResult:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    fake = FakeSandbox(capabilities=_CALL_CAPABILITIES, handler=slow_async)
    caller = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cancelled.is_set()


# ---- renewal and idle close (I5, I7) -----------------------------------------------------------


async def test_a_session_is_renewed_before_a_run_could_outlast_it_async() -> None:
    capabilities = FakeSandbox.DEFAULT_CAPABILITIES.model_copy(update={"max_session_seconds": 200.0})
    fake = FakeSandbox(capabilities=capabilities)
    await _run_async(fake, timeout_seconds=30)
    await _run_async(fake, timeout_seconds=30)
    assert fake.closed_sessions == []
    fake._session_opened_at -= 120
    await _run_async(fake, timeout_seconds=30)
    assert fake.closed_sessions == ["fake-session-1"]
    assert fake.runs[-1].session_id == "fake-session-2"


async def test_session_age_counts_from_when_the_open_was_sent_async() -> None:
    fake = FakeSandbox()
    sent_at: list[float] = []
    open_session = fake._open_session_async

    async def slow_open_async(*, owner: str) -> str:
        sent_at.append(time.monotonic())
        await asyncio.sleep(0.05)
        return await open_session(owner=owner)

    with patch.object(fake, "_open_session_async", side_effect=slow_open_async):
        await _run_async(fake)
    assert fake._session_opened_at is not None and fake._session_opened_at <= sent_at[0]


async def test_an_idle_session_closes_by_itself_async() -> None:
    fake = FakeSandbox(idle_timeout_seconds=0.05)
    await _run_async(fake)
    await _wait_until_async(lambda: fake.closed_sessions == ["fake-session-1"])
    assert not (await fake.get_status_async()).session_open


async def test_an_idle_timer_that_fires_during_a_call_waits_for_the_next_idle_period_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 2)
    fake._on_idle_timer(fake._uses)
    await asyncio.sleep(0)
    assert fake.closed_sessions == []
    fake.gate.set()
    await _finish_async(running)
    assert fake._idle_handle is not None
    fake._on_idle_timer(fake._uses)
    await _wait_until_async(lambda: fake.closed_sessions == ["fake-session-1"])


async def test_idle_close_spares_a_session_used_since_its_timer_was_set_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    stale = fake._uses
    await _run_async(fake)
    await fake._close_if_idle_async(uses=stale)
    assert fake.closed_sessions == []
    await fake._close_if_idle_async(uses=fake._uses)
    assert fake.closed_sessions == ["fake-session-1"]


# ---- reset (I8) --------------------------------------------------------------------------------


async def test_reset_closes_the_session_restores_the_baseline_and_records_it_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    fake.state["artifact"] = True
    await fake.reset_async(expected_revision="fake-revision-1")
    view = await fake.get_status_async()
    assert fake.state == {} and fake.reset_count == 1 and fake.closed_sessions == ["fake-session-1"]
    assert view.last_reset is not None and view.last_reset.succeeded and not view.resetting


async def test_reset_refuses_a_changed_revision_async() -> None:
    fake = FakeSandbox()
    fake.revision = "fake-revision-2"
    with pytest.raises(SandboxRevisionChangedError):
        await fake.reset_async(expected_revision="fake-revision-1")
    view = await fake.get_status_async()
    assert fake.reset_count == 0 and view.last_reset is not None and view.last_reset.succeeded is False
    assert view.condition is SandboxCondition.READY


async def test_a_reset_that_fails_quarantines_until_one_succeeds_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.RESET, error=RuntimeError("checkpoint restore failed"))
    with pytest.raises(SandboxExecutionError, match="checkpoint restore failed"):
        await fake.reset_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.QUARANTINED
    with pytest.raises(SandboxUnavailableError):
        await _run_async(fake)
    with pytest.raises(SandboxRevisionChangedError):
        await fake.reset_async(expected_revision="fake-revision-0")
    assert (await fake.get_status_async()).condition is SandboxCondition.QUARANTINED
    await fake.reset_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.READY


async def test_a_reset_that_does_not_finish_in_time_quarantines_async() -> None:
    fake = FakeSandbox()

    async def hang_async(*, expected_revision: str | None) -> None:
        await asyncio.Event().wait()

    with (
        patch.object(FakeSandbox, "_RESET_TIMEOUT_SECONDS", 0.05),
        patch.object(fake, "_reset_async", side_effect=hang_async),
        pytest.raises(SandboxExecutionError, match="did not finish"),
    ):
        await fake.reset_async()
    view = await fake.get_status_async()
    assert view.condition is SandboxCondition.QUARANTINED and not view.resetting and not view.busy


async def test_reset_waits_for_the_running_call_and_shows_progress_async() -> None:
    fake = FakeSandbox()
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    reset = asyncio.create_task(fake.reset_async())
    await asyncio.sleep(0.01)
    assert (await fake.get_status_async()).resetting and fake.reset_count == 0
    with pytest.raises(SandboxBusyError, match="already running"):
        await fake.reset_async()
    fake.gate.set()
    await _finish_async(running, reset)
    assert fake.reset_count == 1


async def test_reset_runs_to_the_end_when_its_caller_gives_up_async() -> None:
    fake = FakeSandbox()
    resetting, release = asyncio.Event(), asyncio.Event()
    reset = fake._reset_async

    async def slow_reset_async(*, expected_revision: str | None) -> None:
        resetting.set()
        await release.wait()
        await reset(expected_revision=expected_revision)

    with patch.object(fake, "_reset_async", side_effect=slow_reset_async):
        caller = asyncio.create_task(fake.reset_async())
        await asyncio.wait_for(resetting.wait(), TEST_WATCHDOG_SECONDS)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await _wait_until_async(lambda: fake.reset_count == 1)
    await _wait_until_async(lambda: not fake._lock.locked())
    assert not (await fake.get_status_async()).resetting


async def test_reset_clears_a_quarantine_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=RuntimeError("lost"))
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("unreachable"))
    with pytest.raises(SandboxExecutionError):
        await _run_async(fake)
    await fake.reset_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.READY


async def test_reset_is_refused_when_unsupported_async() -> None:
    fake = FakeSandbox(capabilities=_CALL_CAPABILITIES)
    with pytest.raises(SandboxInputError, match="does not support reset"):
        await fake.reset_async()


# ---- closing (I9) ------------------------------------------------------------------------------


async def test_close_waits_for_the_running_call_and_is_final_async() -> None:
    fake = FakeSandbox(handler=_echo)
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake, program="running"))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    queued = asyncio.create_task(_run_async(fake, program="queued"))
    await asyncio.sleep(0.01)
    closing = asyncio.create_task(fake.close_async())
    await asyncio.sleep(0.01)
    with pytest.raises(SandboxUnavailableError, match="closed"):
        await _run_async(fake, program="late")
    fake.gate.set()
    results = await _finish_async(running, queued, closing)
    assert results[0].events[0].stdout == "running"
    assert isinstance(results[1], SandboxUnavailableError)
    assert [run.program for run in fake.runs] == ["running"]
    assert fake.closed_sessions == ["fake-session-1"]
    assert (await fake.get_status_async()).closed
    with pytest.raises(SandboxUnavailableError, match="closed"):
        await _run_async(fake, program="after")
    with pytest.raises(SandboxUnavailableError, match="closed"):
        await fake.reset_async()
    await fake.release_async()
    assert fake.reset_count == 0 and fake.open_sessions == set()


async def test_close_never_raises_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    fake.fail_next(hook=FakeSandboxHook.CLOSE, error=RuntimeError("unreachable"))
    await fake.close_async()
    await fake.close_async()
    assert (await fake.get_status_async()).condition is SandboxCondition.QUARANTINED


async def test_close_gives_up_waiting_for_a_call_that_holds_the_sandbox_too_long_async() -> None:
    fake = FakeSandbox(acquire_timeout_seconds=0.05)
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    await asyncio.wait_for(fake.close_async(), TEST_WATCHDOG_SECONDS)
    assert fake.closed_sessions == []
    fake.gate.set()
    await _finish_async(running)
    assert (await fake.get_status_async()).closed


async def test_release_is_refused_while_a_call_runs_async() -> None:
    fake = FakeSandbox()
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    with pytest.raises(SandboxBusyError):
        await fake.release_async()
    fake.gate.set()
    await _finish_async(running)
    await fake.release_async()
    assert fake.closed_sessions == ["fake-session-1"]


# ---- status (I10) ------------------------------------------------------------------------------


async def test_status_combines_the_sandbox_report_with_session_state_async() -> None:
    fake = FakeSandbox()
    idle = await fake.get_status_async()
    assert idle.status_is_current and not idle.session_open and idle.status.revision == "fake-revision-1"
    await _run_async(fake)
    view = await fake.get_status_async()
    assert view.session_open and view.session_age_seconds is not None and view.idle_seconds is not None
    assert view.status.details == {"fake_sandbox": "fake"}


async def test_status_reuses_the_last_report_while_a_call_runs_async() -> None:
    fake = FakeSandbox()
    await fake.get_status_async()
    fake.gate = asyncio.Event()
    running = asyncio.create_task(_run_async(fake))
    await _wait_until_async(lambda: len(fake.runs) == 1)
    fake.revision = "fake-revision-2"
    view = await fake.get_status_async()
    assert view.busy and not view.status_is_current and view.status.revision == "fake-revision-1"
    fake.gate.set()
    await _finish_async(running)


async def test_a_status_failure_reports_unavailable_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.STATUS, error=RuntimeError("host unreachable"))
    view = await fake.get_status_async()
    assert view.condition is SandboxCondition.UNAVAILABLE and "host unreachable" in (view.status.reason or "")


# ---- evidence and identity (I11, I12) ----------------------------------------------------------


async def test_runs_keep_warnings_execution_ids_and_durations_async() -> None:
    fake = FakeSandbox(handler=lambda run: ProcessResult(exit_code=0, execution_id="run-7", warnings=("slow disk",)))
    run = await _run_async(fake)
    assert run.execution_ids == ("run-7",) and run.warnings == ("slow disk",)
    assert run.events[0].duration_ms >= 0


async def test_a_failure_after_some_runs_returns_them_with_the_reason_async() -> None:
    fake = FakeSandbox(handler=_echo)
    original = fake._execute_async
    calls = 0

    async def second_fails_async(**kwargs: Any) -> ProcessResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("worker died")
        return await original(**kwargs)

    with patch.object(fake, "_execute_async", side_effect=second_fails_async):
        run = await _run_async(fake, invocations=(_ProgramInvocation(),) * 3)
    assert len(run.events) == 1 and "sandbox error at invocation 1" in run.reasons[0]


async def test_unconfirmed_cleanup_stops_further_runs_async() -> None:
    fake = FakeSandbox(
        handler=lambda run: ProcessResult(exit_code=0, warnings=("could not remove",), cleanup_confirmed=False)
    )
    run = await _run_async(fake, invocations=(_ProgramInvocation(),) * 2)
    assert len(run.events) == 1 and len(fake.runs) == 1
    assert run.warnings == ("could not remove",) and "stopped after invocation 0" in run.reasons[0]
    with pytest.raises(SandboxUnavailableError, match="may still be running"):
        await _run_async(fake)
    await fake.release_async()
    assert fake.closed_sessions == ["fake-session-1"]


async def test_identifier_holds_static_configuration_only_async() -> None:
    fake = FakeSandbox()
    await _run_async(fake)
    assert fake.open_sessions
    assert fake._build_identifier().hash == FakeSandbox()._build_identifier().hash
