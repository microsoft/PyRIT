# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private session lifecycle for OpenAI Realtime streaming conversations."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.models import Conversation, Message, MessagePiece
from pyrit.prompt_target.common.realtime_audio import (
    STREAMING_INTERRUPTED_KEY,
    RealtimeTargetResult,
    RealtimeTurnState,
    ServerVadConfig,
)
from pyrit.prompt_target.openai._openai_realtime_dispatcher import _OpenAIRealtimeDispatcher

try:
    from openai import BadRequestError as _OpenAIBadRequestError
except ImportError:  # pragma: no cover - openai is a hard dependency for this module
    _OpenAIBadRequestError = Exception  # type: ignore[misc, assignment, ty:invalid-assignment]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
    from pyrit.prompt_target.common.realtime_audio import CommittedEvent

    # Keep this type-only: openai_realtime_target imports this module at runtime, so a
    # runtime import here would reintroduce a circular dependency.
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget


logger = logging.getLogger(__name__)

#: Minimum amount of buffered audio (ms) the Realtime server accepts on an explicit
#: ``input_audio_buffer.commit``. Forcing a commit below this raises "buffer too small".
_MIN_COMMIT_MS = 100

#: Raised by the ``_require_*`` accessors when a wire helper runs before ``run_async``
#: has established the connection, dispatcher, and queue.
_SESSION_NOT_ACTIVE_MSG = "Streaming session is not active; its wire helpers must run within run_async()."


def _trim_snapshot_to_speech(
    *,
    raw_buffer: bytes,
    sample_rate_hz: int,
    audio_start_ms: int | None,
    prefix_padding_ms: int,
    sample_width_bytes: int = 2,
    channels: int = 1,
) -> bytes:
    """
    Trim leading pre-speech silence from a raw mic snapshot.

    Server VAD reports where speech began via ``audio_start_ms``. The session's
    local accumulator captures every chunk pushed since the last commit — including
    seconds of pre-speech silence — so without a trim the converted audio that
    gets swapped into the server's committed item would be much longer than
    what the server actually committed, causing the model to hear leading silence.

    Returns:
        The trimmed PCM. Returns ``raw_buffer`` unchanged when ``audio_start_ms`` is
        ``None`` or ``0``, or when the computed trim would leave nothing.

    Raises:
        ValueError: If ``audio_start_ms`` is negative.
    """
    if audio_start_ms is None:
        logger.warning(
            "audio_start_ms missing on commit; returning full buffer (converter audio may include leading silence)."
        )
        return raw_buffer
    if audio_start_ms == 0:
        return raw_buffer
    if audio_start_ms < 0:
        raise ValueError(f"audio_start_ms must be >= 0, got {audio_start_ms}")
    bytes_per_ms = sample_rate_hz * sample_width_bytes * channels // 1000
    start_ms = max(0, audio_start_ms - prefix_padding_ms)
    start_byte = start_ms * bytes_per_ms
    # Align to sample frame boundary so the trimmed buffer doesn't start mid-sample.
    frame_bytes = sample_width_bytes * channels
    start_byte -= start_byte % frame_bytes
    if start_byte >= len(raw_buffer):
        return raw_buffer
    return raw_buffer[start_byte:]


@dataclass(frozen=True)
class _SentinelDone:
    """Producer-side sentinel: all chunks drained and final turn callbacks have finished."""


@dataclass(frozen=True)
class _SentinelError:
    """Failure sentinel: bridges an exception raised in a background task to the consumer loop."""

    exc: BaseException


class _OpenAIRealtimeStreamingSession:
    """
    Per-conversation lifecycle owner for one OpenAI Realtime streaming exchange.

    Internal to ``pyrit.prompt_target.openai``. Constructed and consumed only by
    ``RealtimeTarget.open_streaming_session``; downstream code should depend on
    the ``AsyncIterator[Message]`` contract, never on this class directly.
    """

    def __init__(
        self,
        *,
        target: RealtimeTarget,
        audio_chunks: AsyncIterator[bytes],
        prompt_normalizer: PromptNormalizer,
        conversation_id: str | None = None,
        request_converter_configurations: list[PromptConverterConfiguration] | None = None,
        response_converter_configurations: list[PromptConverterConfiguration] | None = None,
        prepended_conversation: list[Message] | None = None,
        server_vad: bool | ServerVadConfig = True,
        persist_prepended_conversation: bool = True,
    ) -> None:
        self._target = target
        self._audio_chunks = audio_chunks
        self._prompt_normalizer = prompt_normalizer
        self._conversation_id = conversation_id or str(uuid.uuid4())
        self._request_converter_configurations = request_converter_configurations or []
        self._response_converter_configurations = response_converter_configurations or []
        self._prepended_conversation = prepended_conversation or []
        self._persist_prepended_conversation = persist_prepended_conversation

        # Normalize server_vad once at construction so config send and commit-time trim
        # both see the same value. ``True`` uses default tuning; pass a ``ServerVadConfig``
        # for custom tuning, ``False`` to disable (sending streaming config then raises).
        if isinstance(server_vad, ServerVadConfig):
            self._effective_vad: ServerVadConfig | None = server_vad
        elif server_vad:
            self._effective_vad = ServerVadConfig()
        else:
            self._effective_vad = None

        # Tee raw user audio so we can persist it per VAD-committed turn; the dispatcher
        # only surfaces ``CommittedEvent`` with an item id, not the bytes themselves.
        self._pending_chunks = bytearray()
        self._pending_chunks_lock = asyncio.Lock()

        # Session-time (ms) at which the current buffer started accumulating. Used to
        # convert the server's session-relative ``audio_start_ms`` into a buffer-relative
        # offset for trimming. Advanced under ``_pending_chunks_lock`` so back-to-back
        # commits cannot interleave with the snapshot/trim.
        self._buffer_start_session_ms: int = 0

        # Serializes per-turn convert/swap/respond/persist work so two server-VAD
        # commits firing back-to-back cannot interleave.
        self._turn_lock = asyncio.Lock()

        # Set in ``_on_committed_async`` entry. Producer awaits this after issuing a
        # forced final commit so the resulting callback can be observed before
        # we signal end-of-stream and tear the dispatcher down.
        self._commit_observed = asyncio.Event()

        # Populated in ``run_async``; held on ``self`` so callbacks can address them.
        self._connection: Any = None
        self._dispatcher: _OpenAIRealtimeDispatcher | None = None
        self._queue: asyncio.Queue[Message | _SentinelDone | _SentinelError] | None = None

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise RuntimeError(_SESSION_NOT_ACTIVE_MSG)
        return self._connection

    def _require_dispatcher(self) -> _OpenAIRealtimeDispatcher:
        if self._dispatcher is None:
            raise RuntimeError(_SESSION_NOT_ACTIVE_MSG)
        return self._dispatcher

    def _require_queue(self) -> asyncio.Queue[Message | _SentinelDone | _SentinelError]:
        if self._queue is None:
            raise RuntimeError(_SESSION_NOT_ACTIVE_MSG)
        return self._queue

    async def run_async(self) -> AsyncIterator[Message]:
        """
        Drive the streaming conversation; yield one ``Message`` per VAD-committed user turn.

        Yields:
            Message: One assembled assistant ``Message`` per turn. The matching user
            ``Message`` for each turn is persisted to memory but not yielded.
        """
        self._connection = await self._target._connect_async(conversation_id=self._conversation_id)
        try:
            await self._send_streaming_session_config_async()
            if self._persist_prepended_conversation:
                await self._prompt_normalizer.add_prepended_conversation_to_memory(
                    conversation_id=self._conversation_id,
                    should_convert=False,
                    prepended_conversation=self._prepended_conversation,
                    target_identifier=self._target.get_identifier(),
                )

            self._queue = asyncio.Queue()
            self._dispatcher = _OpenAIRealtimeDispatcher(
                connection=self._connection,
                on_user_audio_committed=self._on_committed_async,
            )
            await self._dispatcher.start_async()
            self._dispatcher.add_failure_callback(self._on_dispatcher_failure)

            producer = asyncio.create_task(self._drain_chunks_async())
            try:
                while True:
                    item = await self._queue.get()
                    if isinstance(item, _SentinelDone):
                        break
                    if isinstance(item, _SentinelError):
                        raise item.exc
                    yield item
            finally:
                if not producer.done():
                    producer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await producer
                try:
                    await self._dispatcher.stop_async()
                except Exception as e:  # noqa: BLE001 - cleanup, surface via log
                    logger.warning(f"dispatcher.stop_async() raised during session teardown: {e}")
        finally:
            try:
                await self._connection.close()
            except Exception as e:  # noqa: BLE001 - cleanup, surface via log
                logger.warning(f"connection.close() raised during session teardown: {e}")

    async def _drain_chunks_async(self) -> None:
        """
        Forward caller chunks to the connection; on exhaustion, force commit and drain callbacks.

        Raises:
            asyncio.CancelledError: Propagated when the consuming task is cancelled.
        """
        connection = self._require_connection()
        dispatcher = self._require_dispatcher()
        queue = self._require_queue()
        try:
            async for chunk in self._audio_chunks:
                if not chunk:
                    continue
                async with self._pending_chunks_lock:
                    self._pending_chunks.extend(chunk)
                await self._push_audio_chunk_async(chunk)

            # Force a final commit only when enough uncommitted audio remains locally.
            # When server VAD already committed the final phrase (e.g. the source ended
            # with trailing silence), the buffer is empty and committing would be rejected
            # — synchronously as a BadRequestError, or asynchronously as an
            # ``input_audio_buffer_commit_empty`` error event that the dispatcher now treats
            # as benign. Skipping sub-minimum buffers avoids both, plus the 5s observe wait.
            bytes_per_ms = self._target.SAMPLE_RATE_HZ * 2 // 1000  # PCM16 mono
            async with self._pending_chunks_lock:
                pending_ms = len(self._pending_chunks) // bytes_per_ms if bytes_per_ms else 0

            self._commit_observed.clear()
            force_commit_accepted = False
            if pending_ms >= _MIN_COMMIT_MS:
                try:
                    await connection.input_audio_buffer.commit()
                    force_commit_accepted = True
                except _OpenAIBadRequestError as e:
                    # Server VAD may have committed between the size check and this call;
                    # the empty-buffer rejection is benign. Other BadRequestErrors still
                    # indicate a real API problem, so log and continue.
                    logger.debug(f"Forced final commit rejected (likely empty buffer): {e}")
            else:
                logger.debug(
                    f"Skipping forced final commit; {pending_ms}ms pending is below the {_MIN_COMMIT_MS}ms minimum."
                )

            if force_commit_accepted:
                try:
                    await asyncio.wait_for(self._commit_observed.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Forced final commit was accepted but no committed event observed within 5s; "
                        "the final user turn may have been dropped by the server."
                    )

            # Let any commit-triggered callbacks (the one we just forced plus any
            # natural ones still mid-work) run to completion before signalling done.
            await dispatcher.drain_callbacks_async()
            await queue.put(_SentinelDone())
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - bridged to consumer via sentinel
            await queue.put(_SentinelError(e))

    def _on_dispatcher_failure(self, exc: BaseException) -> None:
        """Dispatch-loop crash bridge: unblock the consumer with a failure sentinel."""
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(_SentinelError(exc))
        except Exception as e:  # noqa: BLE001 - defensive; never let the bridge raise
            logger.warning(f"Failed to bridge dispatcher failure into session queue: {e}")

    async def _on_committed_async(self, event: CommittedEvent) -> None:
        """
        Dispatcher-side callback: snapshot raw audio + trim now, then run the turn under the lock.

        Snapshot, trim, and ``_buffer_start_session_ms`` advance all happen under
        ``_pending_chunks_lock`` so back-to-back commits (the dispatcher schedules
        callbacks as background tasks) cannot interleave and corrupt the trim or
        the offset bookkeeping. The slow convert/swap/respond work then runs
        outside this lock, gated by ``_turn_lock``.

        Raises:
            asyncio.CancelledError: Propagated when the dispatcher task is cancelled.
        """
        queue = self._require_queue()
        sample_rate = self._target.SAMPLE_RATE_HZ

        async with self._pending_chunks_lock:
            raw_pcm = bytes(self._pending_chunks)
            self._pending_chunks.clear()

            bytes_per_ms = sample_rate * 2 // 1000  # PCM16 mono
            buffer_duration_ms = len(raw_pcm) // bytes_per_ms if bytes_per_ms else 0

            buffer_relative_audio_start_ms: int | None = None
            if event.audio_start_ms is not None:
                # The server's session clock and the locally-summed buffer offset can drift
                # slightly out of alignment, so this subtraction may go marginally negative.
                # Clamp to 0 ("trim nothing") rather than letting it reach the helper, which
                # treats a negative offset as a caller bug and raises.
                buffer_relative_audio_start_ms = max(0, event.audio_start_ms - self._buffer_start_session_ms)

            prefix_padding_ms = self._effective_vad.prefix_padding_ms if self._effective_vad is not None else 0

            trimmed_pcm = _trim_snapshot_to_speech(
                raw_buffer=raw_pcm,
                sample_rate_hz=sample_rate,
                audio_start_ms=buffer_relative_audio_start_ms,
                prefix_padding_ms=prefix_padding_ms,
            )
            self._buffer_start_session_ms += buffer_duration_ms

        # Signal the producer that a committed event was observed so a forced final
        # commit can verify the server actually processed it.
        self._commit_observed.set()
        try:
            async with self._turn_lock:
                message = await self._handle_committed_turn_async(event=event, raw_pcm=trimmed_pcm)
            await queue.put(message)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - bridged to consumer via sentinel
            await queue.put(_SentinelError(e))

    async def _handle_committed_turn_async(self, *, event: CommittedEvent, raw_pcm: bytes) -> Message:
        """
        Convert raw user audio, request a response, then assemble and persist both messages.

        Returns:
            The assistant ``Message`` for this turn (the matching user ``Message`` is persisted only).
        """
        self._require_connection()
        self._require_dispatcher()

        target = self._target
        sample_rate = target.SAMPLE_RATE_HZ

        if self._request_converter_configurations:
            converted_pcm = await self._prompt_normalizer.convert_audio_async(
                raw_pcm=raw_pcm,
                converter_configurations=self._request_converter_configurations,
                sample_rate_hz=sample_rate,
                num_channels=1,
                sample_width_bytes=2,
            )
            await self._swap_user_audio_async(committed_event=event, converted_pcm=converted_pcm)
        else:
            converted_pcm = raw_pcm

        future = await self._request_response_async()
        result = await future

        raw_user_path = await target.save_audio_async(raw_pcm, num_channels=1, sample_width=2, sample_rate=sample_rate)
        if converted_pcm is raw_pcm:
            converted_user_path = raw_user_path
        else:
            converted_user_path = await target.save_audio_async(
                converted_pcm, num_channels=1, sample_width=2, sample_rate=sample_rate
            )
        assistant_audio_path = await target.save_audio_async(
            result.audio_bytes, num_channels=1, sample_width=2, sample_rate=sample_rate
        )

        target_identifier = target.get_identifier()
        target._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=self._conversation_id, target_identifier=target_identifier)
        )
        user_piece = MessagePiece(
            role="user",
            original_value=raw_user_path,
            original_value_data_type="audio_path",
            converted_value=converted_user_path,
            converted_value_data_type="audio_path",
            conversation_id=self._conversation_id,
        )
        for cfg in self._request_converter_configurations:
            user_piece.converter_identifiers.extend(converter.get_identifier() for converter in cfg.converters)
        user_message = Message(message_pieces=[user_piece])

        assistant_text_piece = MessagePiece(
            role="assistant",
            original_value=result.flatten_transcripts(),
            original_value_data_type="text",
            conversation_id=self._conversation_id,
        )
        assistant_audio_piece = MessagePiece(
            role="assistant",
            original_value=assistant_audio_path,
            original_value_data_type="audio_path",
            conversation_id=self._conversation_id,
        )
        if result.interrupted:
            assistant_text_piece.prompt_metadata[STREAMING_INTERRUPTED_KEY] = True
            assistant_audio_piece.prompt_metadata[STREAMING_INTERRUPTED_KEY] = True
        assistant_message = Message(message_pieces=[assistant_text_piece, assistant_audio_piece])

        if self._response_converter_configurations:
            await self._prompt_normalizer.convert_values(
                converter_configurations=self._response_converter_configurations,
                message=assistant_message,
            )

        await self._prompt_normalizer.hash_and_persist_message_async(message=user_message)
        await self._prompt_normalizer.hash_and_persist_message_async(message=assistant_message)
        return assistant_message

    # ---- Wire helpers -------------------------------------------------------
    # Private methods owning the session's websocket-level concerns: per-turn
    # convert-swap, response triggering, and the streaming session config /
    # audio chunk push that the producer uses. Kept here so ``RealtimeTarget``
    # stays atomic-only.

    async def _send_streaming_session_config_async(self) -> None:
        """
        Configure the realtime session for streaming use: server VAD with manual response creation.

        Emits the same session config as the atomic path except ``turn_detection.create_response``
        is forced to False so the streaming attack can swap the raw user audio item for converted
        audio before triggering ``response.create``.

        Raises:
            ValueError: If server VAD is disabled for this session.
        """
        connection = self._require_connection()
        if self._effective_vad is None:
            raise ValueError(
                "_send_streaming_session_config_async requires server VAD; "
                "pass server_vad=True or server_vad=ServerVadConfig(...) when opening the session."
            )
        system_prompt = self._target._get_system_prompt_from_conversation(conversation=self._prepended_conversation)
        config = self._target._set_system_prompt_and_config_vars(
            system_prompt=system_prompt, server_vad=self._effective_vad
        )
        turn_detection = config.get("audio", {}).get("input", {}).get("turn_detection")
        if turn_detection is not None:
            turn_detection["create_response"] = False
        await connection.session.update(session=config)

    async def _push_audio_chunk_async(self, pcm_bytes: bytes) -> None:
        """
        Append a single PCM16 mono @ 24 kHz audio chunk to the server's input buffer.

        Server VAD, when enabled on the session, decides when to commit and fire
        response logic. Empty buffers are accepted as no-ops.
        """
        if not pcm_bytes:
            return
        connection = self._require_connection()
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await connection.input_audio_buffer.append(audio=audio_b64)

    async def _insert_user_audio_async(self, pcm_bytes: bytes) -> None:
        """Insert a user message containing PCM16 mono @ 24 kHz audio into the conversation."""
        connection = self._require_connection()
        audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
        await connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_audio", "audio": audio_b64}],
            }
        )

    async def _delete_conversation_item_async(self, item_id: str) -> None:
        """Delete a conversation item by id (e.g. the server's raw user audio item)."""
        connection = self._require_connection()
        await connection.conversation.item.delete(item_id=item_id)

    async def _swap_user_audio_async(self, *, committed_event: CommittedEvent, converted_pcm: bytes) -> None:
        """
        Replace the server's just-committed user audio with converted PCM.

        Inserts ``converted_pcm`` as a new user item then best-effort deletes the
        original item identified by ``committed_event``. Insert precedes delete so
        the converted audio is already in place if delete fails or races.
        """
        await self._insert_user_audio_async(converted_pcm)
        try:
            await self._delete_conversation_item_async(committed_event.item_id)
        except Exception as e:
            logger.warning(f"conversation.item.delete failed for {committed_event.item_id}: {e}")

    async def _request_response_async(self) -> asyncio.Future[RealtimeTargetResult]:
        """
        Trigger ``response.create`` and return a future that resolves when the turn ends.

        Constructs a fresh ``RealtimeTurnState``, binds it to the dispatcher as the
        active turn, then sends ``response.create``. The dispatcher resolves the
        returned future via ``response.done`` (with ``interrupted=False``) or via
        the barge-in cancel path (with ``interrupted=True``).

        Returns:
            A future resolving to the ``RealtimeTargetResult`` for this turn.

        Raises:
            RuntimeError: If another turn is already pending on the dispatcher.
        """
        connection = self._require_connection()
        dispatcher = self._require_dispatcher()
        state = RealtimeTurnState(completion=asyncio.get_running_loop().create_future())
        dispatcher.register_turn(state)
        await connection.response.create()
        return state.completion
