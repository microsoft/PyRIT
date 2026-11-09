# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from unit.async_utils import wait_for_completion_async, wait_for_event_async

from pyrit._code_execution import FakeSandbox, FakeSandboxHook, SandboxExecutionError, SandboxRegistry, resolve_sandbox
from pyrit.setup.initialization import reset_setup_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def registry() -> Iterator[SandboxRegistry]:
    SandboxRegistry.reset_registry_singleton()
    yield SandboxRegistry.get_registry_singleton()
    SandboxRegistry.reset_registry_singleton()


def test_register_and_find_by_name(registry: SandboxRegistry) -> None:
    sandbox = FakeSandbox(name="linux-1")
    registry.register(sandbox, name="linux-1")
    assert registry.get("linux-1") is sandbox
    assert registry.get_names() == ["linux-1"]
    assert resolve_sandbox("linux-1") is sandbox
    assert resolve_sandbox(sandbox) is sandbox
    assert registry.unregister("linux-1") is sandbox
    assert registry.get("linux-1") is None


@pytest.mark.parametrize("name", ["", "has space", "../escape", "-leading", "x" * 129])
def test_register_rejects_invalid_names(registry: SandboxRegistry, name: str) -> None:
    with pytest.raises(ValueError, match="Sandbox name"):
        registry.register(FakeSandbox(), name=name)


def test_register_rejects_a_taken_name(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(name="one"), name="sandbox")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeSandbox(name="two"), name="sandbox")


def test_register_rejects_a_second_object_for_the_same_sandbox(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(name="windows"), name="windows-1")
    with pytest.raises(ValueError, match="already registered as 'windows-1'"):
        registry.register(FakeSandbox(name="windows"), name="windows-2")


def test_resolve_names_what_is_registered(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(), name="present")
    with pytest.raises(ValueError, match=r"No sandbox is registered as 'absent'; registered: \['present'\]"):
        resolve_sandbox("absent")


async def test_close_all_closes_every_session(registry: SandboxRegistry) -> None:
    sandboxes = [FakeSandbox(name=name) for name in ("a", "b")]
    for sandbox in sandboxes:
        registry.register(sandbox, name=sandbox.get_identifier().params["name"])
        await sandbox.execute_async(program="print(1)", runtime_profile_name="python")
    await registry.close_all_async()
    assert all(sandbox.open_sessions == set() for sandbox in sandboxes)


async def test_close_all_retains_unconfirmed_cleanup_and_retries_async(registry: SandboxRegistry) -> None:
    sandboxes = [FakeSandbox(name=name) for name in ("failed", "healthy")]
    for sandbox in sandboxes:
        registry.register(sandbox, name=sandbox.get_identifier().params["name"])
        await sandbox.execute_async(program="pass", runtime_profile_name="python")
    failed, healthy = sandboxes
    failed.fail_next(hook=FakeSandboxHook.CLOSE, error=SandboxExecutionError("close not confirmed"))

    try:
        with pytest.raises(SandboxExecutionError, match="unconfirmed for failed"):
            await registry.close_all_async()

        assert registry.get("failed") is failed
        assert failed.open_sessions
        assert (await failed.get_status_async()).quarantine_reason
        assert not healthy.open_sessions
        assert (await healthy.get_status_async()).closed

        await registry.close_all_async()
        assert not failed.open_sessions
        assert (await failed.get_status_async()).quarantine_reason is None
    finally:
        await registry.close_all_async()


async def test_close_all_refuses_replacement_while_a_call_remains_busy_async(registry: SandboxRegistry) -> None:
    sandbox = FakeSandbox()
    sandbox.gate = asyncio.Event()
    registry.register(sandbox, name="busy")
    running = asyncio.create_task(sandbox.execute_async(program="pass", runtime_profile_name="python"))
    try:
        await wait_for_event_async(event=sandbox.run_started, tasks=[running])
        with (
            patch.object(sandbox, "_wait_for_lock_async", new_callable=AsyncMock, return_value=False),
            pytest.raises(SandboxExecutionError, match="unconfirmed for busy"),
        ):
            await registry.close_all_async()

        assert registry.get("busy") is sandbox
        assert (await sandbox.get_status_async()).busy
        assert sandbox.open_sessions
    finally:
        sandbox.gate.set()
        await wait_for_completion_async(future=running)
        await registry.close_all_async()
    assert not sandbox.open_sessions


async def test_close_all_fences_registration_while_awaiting_cleanup_async(registry: SandboxRegistry) -> None:
    sandbox = FakeSandbox()
    registry.register(sandbox, name="existing")
    await sandbox.execute_async(program="pass", runtime_profile_name="python")
    entered, finish = asyncio.Event(), asyncio.Event()
    original_close = sandbox._close_session_async

    async def close_session_async(*, session_id: str) -> None:
        entered.set()
        await finish.wait()
        await original_close(session_id=session_id)

    with patch.object(sandbox, "_close_session_async", side_effect=close_session_async):
        closing = asyncio.create_task(registry.close_all_async())
        try:
            await wait_for_event_async(event=entered, tasks=[closing])
            with pytest.raises(ValueError, match="registry is closing"):
                registry.register(FakeSandbox(name="later"), name="later")
        finally:
            finish.set()
            await wait_for_completion_async(future=closing)

    assert registry.get_names() == ["existing"]
    assert not sandbox.open_sessions
    with pytest.raises(ValueError, match="registry is closing"):
        registry.register(FakeSandbox(name="after"), name="after")


async def test_cancelled_close_all_preserves_cleanup_for_retry_async(registry: SandboxRegistry) -> None:
    sandbox = FakeSandbox()
    registry.register(sandbox, name="existing")
    await sandbox.execute_async(program="pass", runtime_profile_name="python")
    entered, finish = asyncio.Event(), asyncio.Event()
    original_close = sandbox._close_session_async

    async def close_session_async(*, session_id: str) -> None:
        entered.set()
        await finish.wait()
        await original_close(session_id=session_id)

    with patch.object(sandbox, "_close_session_async", side_effect=close_session_async):
        closing = asyncio.create_task(registry.close_all_async())
        try:
            await wait_for_event_async(event=entered, tasks=[closing])
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert registry.get("existing") is sandbox
            assert sandbox.open_sessions
            with pytest.raises(ValueError, match="registry is closing"):
                registry.register(FakeSandbox(name="later"), name="later")
        finally:
            finish.set()
            await registry.close_all_async()
    assert not sandbox.open_sessions


async def test_close_all_empty_registry_is_idempotent_async(registry: SandboxRegistry) -> None:
    await registry.close_all_async()
    await registry.close_all_async()
    assert registry.get_names() == []


def test_reapplying_configuration_resets_the_registry(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(), name="before")
    reset_setup_registries()
    assert SandboxRegistry.get_registry_singleton().get_names() == []
