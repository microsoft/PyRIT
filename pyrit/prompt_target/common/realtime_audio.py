# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared types for realtime audio prompt targets."""

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


#: Key under which streaming attacks stash the server-assigned item id of the most
#: recently committed user audio buffer (in ``MessagePiece.prompt_metadata``). Realtime
#: targets read this key in their streaming branch to identify which committed item
#: to delete when swapping in converter-transformed audio. Exposed as a public
#: constant so attacks can reference it without reaching into target internals.
REALTIME_COMMITTED_ITEM_ID_KEY = "realtime_committed_item_id"


@dataclass(frozen=True)
class ServerVadConfig:
    """Server-side voice activity detection (VAD) tuning for realtime audio targets."""

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
    """Result of a Realtime API turn: delivered audio, transcripts, and interruption status."""

    audio_bytes: bytes = b""
    transcripts: list[str] = field(default_factory=list)
    interrupted: bool = False

    def flatten_transcripts(self) -> str:
        """Return all transcript deltas concatenated into a single string."""
        return "".join(self.transcripts)


@dataclass
class RealtimeTurnState:
    """Mutable per-turn state assembled by the dispatcher from incoming events."""

    completion: asyncio.Future[RealtimeTargetResult]
    is_responding: bool = False
    delivered_audio: bytearray = field(default_factory=bytearray)
    delivered_transcripts: list[str] = field(default_factory=list)
    current_item_id: str | None = None
    last_response_id: str | None = None
    interrupted: bool = False


@dataclass(frozen=True)
class CommittedEvent:
    """Payload passed to ``on_user_audio_committed`` callbacks when server VAD commits."""

    item_id: str
    audio_start_ms: int | None = None


class RealtimeEventDispatcher(ABC):
    """
    Owns a realtime connection's event stream and routes events to the active turn.

    Provider-specific event routing and cancel logic are isolated to the abstract methods.
    """

    def __init__(
        self,
        *,
        connection: Any,
        on_user_audio_committed: Callable[[CommittedEvent], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """
        Args:
            connection: An open realtime connection exposing an async iterator
                of server events. The dispatcher owns reading from it.
            on_user_audio_committed: Optional callback fired when the server
                commits a user audio buffer (e.g. server VAD finalizing a turn).
                Invoked as a background task so converter work in the callback
                does not block the dispatch loop. Default None disables it.
        """
        self._connection = connection
        self._on_user_audio_committed = on_user_audio_committed
        self._current_turn: RealtimeTurnState | None = None
        self._task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._failure: BaseException | None = None
        # Optional bridge slot for providers whose protocol reports audio_start_ms on
        # ``speech_started`` but omits it from ``input_audio_buffer.committed``. Such
        # subclasses capture it here when speech_started fires and read it back on commit.
        # Providers that report audio_start_ms directly on commit can ignore this slot.
        self._pending_speech_start_ms: int | None = None

    @property
    def failure(self) -> BaseException | None:
        """
        The exception that killed the dispatch loop, or None if it is still healthy.

        Set when the outer event iterator raises. Callers (e.g. ``BargeInAttack``)
        poll this between operations to detect a dead connection without needing a
        callback. Once set, ``stop()`` should be called and the attack torn down.
        """
        return self._failure

    async def start(self) -> None:
        """Start the background dispatch task. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        """
        Cancel the background dispatch task and release the reference.

        In-flight callback tasks are cancelled and awaited (with exception
        suppression) so they don't deadlock waiting on the turn future that the
        now-dead dispatch loop would have resolved.
        """
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._callback_tasks:
            pending = list(self._callback_tasks)
            self._callback_tasks.clear()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def register_turn(self, state: RealtimeTurnState) -> None:
        """
        Bind a new turn as the active turn.

        Args:
            state (RealtimeTurnState): The turn whose completion future will be
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

        The router is called for every event with the current turn (which may
        be None during the gap between turns). Concrete routers are expected to
        handle ``state is None`` for input-side events that need no turn state
        and return early on output-side events when no turn is registered.

        Raises:
            asyncio.CancelledError: Propagated when ``stop()`` cancels the task.
        """
        try:
            async for event in self._connection:
                turn = self._current_turn
                if turn is not None and turn.completion.done():
                    turn = None
                try:
                    await self._route_event(event=event, state=turn)
                except Exception as e:
                    logger.exception(f"Realtime event router raised: {e}")
                    if turn is not None and not turn.completion.done():
                        turn.completion.set_exception(e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Realtime dispatch loop crashed: {e}")
            self._failure = e
            turn = self._current_turn
            if turn is not None and not turn.completion.done():
                turn.completion.set_exception(e)

    def _fire_committed_callback(self, event: CommittedEvent) -> None:
        """
        Schedule the ``on_user_audio_committed`` callback as a background task.

        Tracks the resulting task so ``stop()`` can wait for it to finish.
        """
        if self._on_user_audio_committed is None:
            return
        task = asyncio.create_task(self._on_user_audio_committed(event))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    @abstractmethod
    async def _route_event(self, *, event: Any, state: RealtimeTurnState | None) -> None:
        """
        Route a single provider-specific event.

        Concrete implementations:
        - When the event is output-side (response lifecycle, audio/transcript
          deltas, etc.) and ``state`` is non-None, mutate ``state`` and resolve
          ``state.completion`` at end-of-turn or on interruption.
        - When ``state`` is None (no active turn) or
          ``state.completion.done()``, output-side events should be dropped.
        - When the event is input-side (e.g. ``input_audio_buffer.committed``),
          fire any subscribed callback via ``self._fire_committed_callback(...)``.
          These callbacks may run regardless of ``state``.
        - On error events, resolve ``state.completion`` via ``set_exception``
          when a turn is active.

        Args:
            event: A single provider-specific event from the connection iterator.
            state (RealtimeTurnState | None): The currently-active turn, or None
                if no turn is registered (e.g. between turns in a streaming
                session).
        """

    @abstractmethod
    async def _cancel(self, *, state: RealtimeTurnState) -> None:
        """
        Send provider-specific cancel and truncate events for the in-flight response.

        Must set ``state.interrupted = True`` even on wire-call failure so callers
        can tell the turn was cut short. Must not resolve ``state.completion``;
        that is the dispatcher's responsibility.

        Args:
            state (RealtimeTurnState): The turn whose response should be cancelled.
        """


class StreamingHandle(ABC):
    """
    Provider-agnostic streaming surface owned by targets that declare ``STREAMING_BARGE_IN``.

    A streaming attack (e.g. ``BargeInAttack``) interacts with the target's realtime
    transport via ``target.streaming``, not via methods on the target itself. This keeps
    the streaming surface out of ``PromptTarget`` while giving the attack a single typed
    contract to program against. Concrete realtime providers (OpenAI, Azure, etc.) provide
    a concrete ``StreamingHandle`` subclass and assign it to ``self.streaming`` in their
    target's ``__init__``.
    """

    #: PCM sample rate in Hz negotiated by the provider's realtime protocol.
    SAMPLE_RATE_HZ: ClassVar[int]

    @property
    @abstractmethod
    def server_vad_config(self) -> "ServerVadConfig | None":
        """Server VAD configuration in effect, or ``None`` if server VAD is disabled."""

    @abstractmethod
    async def connect_async(self, conversation_id: str) -> Any:
        """Open the streaming connection for ``conversation_id`` and return the connection handle."""

    @abstractmethod
    async def subscribe_events_async(
        self,
        *,
        connection: Any,
        conversation_id: str,
        on_user_audio_committed: Callable[[CommittedEvent], Coroutine[Any, Any, None]] | None = None,
    ) -> "RealtimeEventDispatcher":
        """Spawn a background reader that routes server events and returns the dispatcher."""

    @abstractmethod
    async def send_streaming_session_config_async(
        self, *, connection: Any, conversation: "list[Any] | None" = None
    ) -> None:
        """Send the initial streaming session configuration over the wire."""

    @abstractmethod
    async def push_audio_chunk_async(self, *, connection: Any, pcm_bytes: bytes) -> None:
        """Push a PCM16 audio chunk into the server's input buffer."""

    @abstractmethod
    async def save_audio(
        self,
        audio_bytes: bytes,
        num_channels: int = 1,
        sample_width: int = 2,
        sample_rate: int = 16000,
        output_filename: str | None = None,
    ) -> str:
        """Persist a PCM buffer to disk and return the file path."""

    @abstractmethod
    async def cleanup_conversation(self, conversation_id: str) -> None:
        """Tear down any per-conversation state held by the target."""


@runtime_checkable
class SupportsStreamingBargeIn(Protocol):
    """
    Structural marker for targets that expose a streaming barge-in surface.

    Used by ``BargeInAttack`` to validate at construction time that its objective
    target wires a ``StreamingHandle`` on ``self.streaming``. Decoupled from
    ``PromptTarget`` so the base class stays free of streaming-specific attributes.

    Note: ``runtime_checkable`` Protocol ``isinstance`` checks attribute *presence*,
    not value — a target that explicitly sets ``streaming = None`` would still pass
    the check and fail at first method call instead of construction time.
    """

    streaming: StreamingHandle
