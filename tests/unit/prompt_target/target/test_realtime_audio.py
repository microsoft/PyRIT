# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.prompt_target.common.realtime_audio import (
    CommittedEvent,
    RealtimeEventDispatcher,
    RealtimeTargetResult,
    RealtimeTurnState,
)


async def test_realtime_turn_state_defaults():
    """Newly constructed turn state must be empty: no audio, no transcripts, not responding, not interrupted."""
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    assert state.is_responding is False
    assert state.interrupted is False
    assert bytes(state.delivered_audio) == b""
    assert state.delivered_transcripts == []
    assert state.current_item_id is None
    assert state.last_response_id is None


def test_realtime_target_result_interrupted_defaults_false():
    """RealtimeTargetResult must default interrupted=False so atomic callers see no change."""
    result = RealtimeTargetResult()
    assert result.interrupted is False
    assert result.audio_bytes == b""
    assert result.transcripts == []


def test_realtime_target_result_carries_interrupted_when_set():
    """The interrupted flag round-trips through construction."""
    result = RealtimeTargetResult(audio_bytes=b"partial", transcripts=["hi"], interrupted=True)
    assert result.interrupted is True


class _RecordingDispatcher(RealtimeEventDispatcher):
    """Minimal concrete dispatcher for testing the generic base class behavior."""

    def __init__(self, *, connection: Any) -> None:
        super().__init__(connection=connection)
        self.routed_events: list[Any] = []
        self.cancel_calls: int = 0

    async def _route_event_async(self, *, event: Any, state: RealtimeTurnState | None) -> None:
        self.routed_events.append(event)
        # End the turn on a sentinel event so tests can drain the loop.
        if state is not None and getattr(event, "_finish", False):
            state.completion.set_result(RealtimeTargetResult())

    async def _cancel_async(self, *, state: RealtimeTurnState) -> None:
        self.cancel_calls += 1
        state.interrupted = True


class _ScriptedConnection:
    """Async-iterable connection that yields a fixed event list once registered."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aiter__(self):
        for event in self._events:
            yield event


def _sentinel_event(*, finish: bool = False) -> AsyncMock:
    event = AsyncMock()
    event._finish = finish
    return event


async def test_dispatcher_start_is_idempotent():
    """Calling start twice must not spawn two tasks."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    await dispatcher.start_async()
    first_task = dispatcher._task
    await dispatcher.start_async()
    assert dispatcher._task is first_task
    await dispatcher.stop_async()


async def test_dispatcher_stop_releases_task():
    """stop must cancel the task and clear the reference."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    await dispatcher.start_async()
    await dispatcher.stop_async()
    assert dispatcher._task is None


async def test_dispatcher_register_turn_rejects_concurrent_active_turn():
    """Registering a turn while another is active and unresolved must raise."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    first = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    second = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    dispatcher.register_turn(first)
    with pytest.raises(RuntimeError, match="already active"):
        dispatcher.register_turn(second)


async def test_dispatcher_register_turn_allows_replacement_after_completion():
    """Once the active turn's future is done, register_turn may bind a new turn."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    first = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    first.completion.set_result(RealtimeTargetResult())
    second = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    dispatcher.register_turn(first)
    dispatcher.register_turn(second)
    assert dispatcher._current_turn is second


async def test_dispatcher_loop_routes_events_to_active_turn():
    """The dispatch loop must forward events from the connection to _route_event_async."""
    finish = _sentinel_event(finish=True)
    other = _sentinel_event()
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([other, finish]))
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    dispatcher.register_turn(state)

    await dispatcher.start_async()
    await asyncio.wait_for(state.completion, timeout=1.0)
    await dispatcher.stop_async()

    assert dispatcher.routed_events == [other, finish]


async def test_dispatcher_loop_routes_events_with_no_turn_as_state_none():
    """When no turn is registered, events still reach _route_event_async so input callbacks can fire; state is None."""
    finish = _sentinel_event(finish=True)
    other = _sentinel_event()
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([other, finish]))

    # No register_turn called.
    await dispatcher.start_async()
    await asyncio.sleep(0.05)
    await dispatcher.stop_async()

    # Both events were routed but no turn was completed (state was None, sentinel branch skipped).
    assert dispatcher.routed_events == [other, finish]


async def test_dispatcher_loop_sets_exception_on_router_failure():
    """A router exception must propagate to the active turn's completion future."""

    class _ExplodingDispatcher(_RecordingDispatcher):
        async def _route_event_async(self, *, event: Any, state: RealtimeTurnState | None) -> None:
            raise ValueError("router boom")

    event = _sentinel_event()
    dispatcher = _ExplodingDispatcher(connection=_ScriptedConnection([event]))
    state = RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    dispatcher.register_turn(state)

    await dispatcher.start_async()
    with pytest.raises(ValueError, match="router boom"):
        await asyncio.wait_for(state.completion, timeout=1.0)
    await dispatcher.stop_async()


async def test_dispatcher_fires_committed_callback_as_background_task():
    """The on_user_audio_committed callback must be invoked and awaited via background tasks."""

    received: list[Any] = []
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def slow_callback(event):
        received.append(event)
        blocked.set()
        # Block until the test releases us; this proves the dispatch loop did not wait.
        await release.wait()

    class _CallbackDispatcher(RealtimeEventDispatcher):
        async def _route_event_async(self, *, event, state):
            # Synthesize a committed callback fire on every event for the test.
            self._fire_committed_callback(event)

        async def _cancel_async(self, *, state):  # pragma: no cover - not exercised here
            return

    fake_event_1 = MagicMock(spec=CommittedEvent)
    fake_event_2 = MagicMock(spec=CommittedEvent)
    dispatcher = _CallbackDispatcher(
        connection=_ScriptedConnection([fake_event_1, fake_event_2]),
        on_user_audio_committed=slow_callback,
    )

    await dispatcher.start_async()
    # Both events should reach the slow callback even though the first is "blocked" awaiting release.
    await asyncio.wait_for(blocked.wait(), timeout=1.0)
    # Give the loop a tick to process the second event despite the first callback still running.
    await asyncio.sleep(0.05)
    release.set()
    await dispatcher.stop_async()

    # Both events fired the callback; the loop did not serialize behind the slow first call.
    assert len(received) == 2


async def test_dispatcher_records_failure_on_iterator_crash():
    """When the connection iterator raises, the dispatcher's failure property captures the exception."""

    class _NoopDispatcher(RealtimeEventDispatcher):
        async def _route_event_async(self, *, event, state):  # pragma: no cover - never called
            return

        async def _cancel_async(self, *, state):  # pragma: no cover
            return

    class _ExplodingConnection:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("iterator died")

    dispatcher = _NoopDispatcher(connection=_ExplodingConnection())
    await dispatcher.start_async()
    for _ in range(50):
        if dispatcher.failure is not None:
            break
        await asyncio.sleep(0.01)
    await dispatcher.stop_async()

    assert isinstance(dispatcher.failure, RuntimeError) and str(dispatcher.failure) == "iterator died"
