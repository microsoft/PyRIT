# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyrit.prompt_target.common.realtime_audio import (
    RealtimeTargetResult,
    _RealtimeEventDispatcher,
    _RealtimeTurnState,
)


async def test_realtime_turn_state_defaults():
    """Newly constructed turn state must be empty: no audio, no transcripts, not responding, not interrupted."""
    state = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    assert state.is_responding is False
    assert state.interrupted is False
    assert bytes(state.delivered_audio) == b""
    assert state.delivered_transcripts == []
    assert state.current_item_id is None
    assert state.last_response_id is None


class _RecordingDispatcher(_RealtimeEventDispatcher):
    """Minimal concrete dispatcher for testing the generic base class behavior."""

    def __init__(self, *, connection: Any) -> None:
        super().__init__(connection=connection)
        self.routed_events: list[Any] = []
        self.cancel_calls: int = 0

    async def _route_event(self, *, event: Any, state: _RealtimeTurnState) -> None:
        self.routed_events.append(event)
        # End the turn on a sentinel event so tests can drain the loop.
        if getattr(event, "_finish", False):
            state.completion.set_result(RealtimeTargetResult())

    async def _cancel(self, *, state: _RealtimeTurnState) -> None:
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
    await dispatcher.start()
    first_task = dispatcher._task
    await dispatcher.start()
    assert dispatcher._task is first_task
    await dispatcher.stop()


async def test_dispatcher_stop_releases_task():
    """stop must cancel the task and clear the reference."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    await dispatcher.start()
    await dispatcher.stop()
    assert dispatcher._task is None


async def test_dispatcher_register_turn_rejects_concurrent_active_turn():
    """Registering a turn while another is active and unresolved must raise."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    first = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    second = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    dispatcher.register_turn(first)
    with pytest.raises(RuntimeError, match="already active"):
        dispatcher.register_turn(second)


async def test_dispatcher_register_turn_allows_replacement_after_completion():
    """Once the active turn's future is done, register_turn may bind a new turn."""
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([]))
    first = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    first.completion.set_result(RealtimeTargetResult())
    second = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    dispatcher.register_turn(first)
    dispatcher.register_turn(second)
    assert dispatcher._current_turn is second


async def test_dispatcher_loop_routes_events_to_active_turn():
    """The dispatch loop must forward events from the connection to _route_event."""
    finish = _sentinel_event(finish=True)
    other = _sentinel_event()
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([other, finish]))
    state = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    dispatcher.register_turn(state)

    await dispatcher.start()
    await asyncio.wait_for(state.completion, timeout=1.0)
    await dispatcher.stop()

    assert dispatcher.routed_events == [other, finish]


async def test_dispatcher_loop_skips_events_when_no_active_turn():
    """Events arriving with no current turn (or a completed one) are dropped quietly."""
    finish = _sentinel_event(finish=True)
    dispatcher = _RecordingDispatcher(connection=_ScriptedConnection([_sentinel_event(), finish]))

    # No register_turn called.
    await dispatcher.start()
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert dispatcher.routed_events == []


async def test_dispatcher_loop_sets_exception_on_router_failure():
    """A router exception must propagate to the active turn's completion future."""

    class _ExplodingDispatcher(_RecordingDispatcher):
        async def _route_event(self, *, event: Any, state: _RealtimeTurnState) -> None:
            raise ValueError("router boom")

    event = _sentinel_event()
    dispatcher = _ExplodingDispatcher(connection=_ScriptedConnection([event]))
    state = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())
    dispatcher.register_turn(state)

    await dispatcher.start()
    with pytest.raises(ValueError, match="router boom"):
        await asyncio.wait_for(state.completion, timeout=1.0)
    await dispatcher.stop()
