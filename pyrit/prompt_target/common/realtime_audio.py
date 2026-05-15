# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared types for realtime audio prompt targets."""

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerVadConfig:
    """
    Server-side voice activity detection (VAD) tuning for realtime audio targets.

    Attributes:
        threshold: VAD activation threshold (0.0 to 1.0). Defaults to 0.4.
        prefix_padding_ms: Milliseconds of pre-roll audio retained before detected speech.
            Defaults to 200.
        silence_duration_ms: Milliseconds of silence required to detect end-of-turn.
            Defaults to 1500.
    """

    threshold: float = 0.4
    prefix_padding_ms: int = 200
    silence_duration_ms: int = 1500

    def __post_init__(self) -> None:
        """
        Validate VAD tuning values.

        Raises:
            ValueError: If any field is outside its valid range.
        """
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {self.threshold}")
        if self.prefix_padding_ms < 0:
            raise ValueError(f"prefix_padding_ms must be non-negative, got {self.prefix_padding_ms}")
        if self.silence_duration_ms < 0:
            raise ValueError(f"silence_duration_ms must be non-negative, got {self.silence_duration_ms}")


@dataclass
class RealtimeTargetResult:
    """
    Result of a Realtime API turn, containing the audio and transcripts actually delivered.

    Attributes:
        audio_bytes: Raw PCM16 audio returned by the assistant. May be partial if the
            turn was interrupted.
        transcripts: Transcript deltas captured during the turn.
    """

    audio_bytes: bytes = b""
    transcripts: list[str] = field(default_factory=list)

    def flatten_transcripts(self) -> str:
        """Return all transcript deltas concatenated into a single string."""
        return "".join(self.transcripts)


@dataclass
class _RealtimeTurnState:
    """
    Mutable per-turn state assembled by the dispatcher and read by the cancel path.

    The dispatcher routes incoming events into this object during a turn; the
    completion future is resolved by the dispatcher with a ``RealtimeTargetResult``
    snapshotted from these fields once the turn ends normally or via interruption.

    Attributes:
        completion: Future resolved with the assembled result when the turn ends.
        is_responding: True between ``response.created`` and ``response.done`` for
            the active response.
        delivered_audio: Assistant audio bytes accumulated from ``response.audio.delta``.
            Uses ``bytearray`` so deltas append in place rather than reallocating.
        delivered_transcripts: Transcript deltas accumulated from ``response.audio_transcript.delta``.
        current_item_id: Item id of the assistant response currently being streamed.
            None until ``response.output_item.added`` fires.
        last_response_id: Response id of the in-flight response. None until
            ``response.created`` fires.
        interrupted: Set True when the cancel/truncate path runs.
    """

    completion: asyncio.Future[RealtimeTargetResult]
    is_responding: bool = False
    delivered_audio: bytearray = field(default_factory=bytearray)
    delivered_transcripts: list[str] = field(default_factory=list)
    current_item_id: str | None = None
    last_response_id: str | None = None
    interrupted: bool = False


class _RealtimeEventDispatcher(ABC):
    """
    Owns a realtime connection's event stream and routes events to the active turn.

    One long-lived async task per websocket connection. The dispatcher is the only
    code that consumes the connection's async iterator; turn-aware senders register
    a ``_RealtimeTurnState`` and ``await state.completion`` while the dispatcher
    mutates the state in response to incoming events.

    Provider-specific event names and cancel wire calls are isolated to the
    abstract methods so each realtime provider (OpenAI, Gemini Live, etc.) supplies
    only its routing and cancel logic.
    """

    def __init__(self, *, connection: Any) -> None:
        """
        Args:
            connection: An open realtime connection exposing an async iterator
                of server events. The dispatcher owns reading from it.
        """
        self._connection = connection
        self._current_turn: _RealtimeTurnState | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background dispatch task. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        """Cancel the background dispatch task and release the reference."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def register_turn(self, state: _RealtimeTurnState) -> None:
        """
        Bind a new turn as the active turn.

        Args:
            state (_RealtimeTurnState): The turn whose completion future will be
                resolved when this turn ends.

        Raises:
            RuntimeError: If another turn is already active on this dispatcher.
        """
        if self._current_turn is not None and not self._current_turn.completion.done():
            raise RuntimeError("Another turn is already active on this dispatcher")
        self._current_turn = state

    async def _dispatch_loop(self) -> None:
        """
        Consume events from the connection and route each to the active turn.

        Raises:
            asyncio.CancelledError: Propagated when ``stop()`` cancels the task.
        """
        try:
            async for event in self._connection:
                turn = self._current_turn
                if turn is None or turn.completion.done():
                    continue
                try:
                    await self._route_event(event=event, state=turn)
                except Exception as e:
                    logger.exception(f"Realtime event router raised: {e}")
                    if not turn.completion.done():
                        turn.completion.set_exception(e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Realtime dispatch loop crashed: {e}")
            turn = self._current_turn
            if turn is not None and not turn.completion.done():
                turn.completion.set_exception(e)

    @abstractmethod
    async def _route_event(self, *, event: Any, state: _RealtimeTurnState) -> None:
        """
        Update ``state`` based on a single provider-specific event.

        Concrete implementations:
        - Set ``state.is_responding`` / ``state.last_response_id`` / ``state.current_item_id``
          as the relevant lifecycle events arrive.
        - Append delivered audio and transcript deltas to ``state``.
        - On normal completion, resolve ``state.completion`` with a
          ``RealtimeTargetResult`` snapshotted from ``state``.
        - On server-side speech-started while ``state.is_responding``, call
          ``self._cancel(state=state)`` then resolve ``state.completion`` with
          ``interrupted=True`` and the partial audio.
        - On error events, resolve ``state.completion`` via ``set_exception``.

        Args:
            event: A single provider-specific event from the connection iterator.
            state (_RealtimeTurnState): The currently-active turn.
        """

    @abstractmethod
    async def _cancel(self, *, state: _RealtimeTurnState) -> None:
        """
        Send provider-specific cancel and truncate events for the in-flight response.

        Must set ``state.interrupted = True`` even on wire-call failure so callers
        can tell the turn was cut short. Must not resolve ``state.completion``;
        that is the dispatcher's responsibility.

        Args:
            state (_RealtimeTurnState): The turn whose response should be cancelled.
        """
