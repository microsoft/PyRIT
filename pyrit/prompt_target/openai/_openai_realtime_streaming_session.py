# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private session lifecycle for OpenAI Realtime streaming conversations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.streaming.streaming_audio_target import (
    STREAMING_INTERRUPTED_KEY,
)
from pyrit.prompt_target.openai.openai_realtime_target import _OpenAIRealtimeDispatcher

try:
    from openai import BadRequestError as _OpenAIBadRequestError  # noqa: TC002
except ImportError:  # pragma: no cover - openai is a hard dependency for this module
    _OpenAIBadRequestError = Exception  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
    from pyrit.prompt_target.common.realtime_audio import CommittedEvent
    from pyrit.prompt_target.common.streaming import ServerVadConfig
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget


logger = logging.getLogger(__name__)


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

    Internal to :mod:`pyrit.prompt_target.openai`. Constructed and consumed only by
    :meth:`RealtimeTarget.open_streaming_session`; downstream code should depend on
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
        vad: ServerVadConfig | None = None,
        attack_identifier: ComponentIdentifier | None = None,
        persist_prepended_conversation: bool = True,
    ) -> None:
        self._target = target
        self._audio_chunks = audio_chunks
        self._prompt_normalizer = prompt_normalizer
        self._conversation_id = conversation_id or str(uuid.uuid4())
        self._request_converter_configurations = request_converter_configurations or []
        self._response_converter_configurations = response_converter_configurations or []
        self._prepended_conversation = prepended_conversation or []
        self._vad = vad
        self._attack_identifier = attack_identifier
        self._persist_prepended_conversation = persist_prepended_conversation

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

        # Set in ``_on_committed`` entry. Producer awaits this after issuing a
        # forced final commit so the resulting callback can be observed before
        # we signal end-of-stream and tear the dispatcher down.
        self._commit_observed = asyncio.Event()

        # Populated in ``run_async``; held on ``self`` so callbacks can address them.
        self._connection: Any = None
        self._dispatcher: _OpenAIRealtimeDispatcher | None = None
        self._queue: asyncio.Queue[Message | _SentinelDone | _SentinelError] | None = None

    async def run_async(self) -> AsyncIterator[Message]:
        """
        Drive the streaming conversation; yield one ``Message`` per VAD-committed user turn.

        Yields:
            Message: One assembled assistant ``Message`` per turn. The matching user
            ``Message`` for each turn is persisted to memory but not yielded.
        """
        target = self._target
        streaming = target.streaming

        self._connection = await streaming.connect_async(conversation_id=self._conversation_id)
        try:
            await streaming.send_streaming_session_config_async(
                connection=self._connection,
                conversation=self._prepended_conversation,
                vad=self._vad,
            )
            if self._persist_prepended_conversation:
                await self._prompt_normalizer.add_prepended_conversation_to_memory(
                    conversation_id=self._conversation_id,
                    should_convert=False,
                    prepended_conversation=self._prepended_conversation,
                )

            self._queue = asyncio.Queue()
            self._dispatcher = _OpenAIRealtimeDispatcher(
                connection=self._connection,
                on_user_audio_committed=self._on_committed,
            )
            await self._dispatcher.start()
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
                    await self._dispatcher.stop()
                except Exception as e:  # noqa: BLE001 - cleanup, surface via log
                    logger.warning(f"dispatcher.stop() raised during session teardown: {e}")
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
        assert self._connection is not None
        assert self._dispatcher is not None
        assert self._queue is not None

        connection = self._connection
        streaming = self._target.streaming
        try:
            async for chunk in self._audio_chunks:
                if not chunk:
                    continue
                async with self._pending_chunks_lock:
                    self._pending_chunks.extend(chunk)
                await streaming.push_audio_chunk_async(connection=connection, pcm_bytes=chunk)

            # Snapshot commit-event count before forcing a final commit so we can
            # detect whether the server accepted it (it produces a new committed
            # event) without racing with any concurrent natural commit.
            self._commit_observed.clear()
            force_commit_accepted = False
            try:
                await connection.input_audio_buffer.commit()
                force_commit_accepted = True
            except _OpenAIBadRequestError as e:
                # Empty buffer is a benign "nothing pending to commit" — happens whenever
                # server VAD already auto-committed the final phrase. Anything else from
                # this exception class still indicates a real API problem; log and continue.
                logger.debug(f"Forced final commit rejected (likely empty buffer): {e}")

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
            await self._dispatcher.drain_callbacks()
            await self._queue.put(_SentinelDone())
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - bridged to consumer via sentinel
            await self._queue.put(_SentinelError(e))

    def _on_dispatcher_failure(self, exc: BaseException) -> None:
        """Dispatch-loop crash bridge: unblock the consumer with a failure sentinel."""
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(_SentinelError(exc))
        except Exception as e:  # noqa: BLE001 - defensive; never let the bridge raise
            logger.warning(f"Failed to bridge dispatcher failure into session queue: {e}")

    async def _on_committed(self, event: CommittedEvent) -> None:
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
        assert self._queue is not None
        streaming = self._target.streaming
        sample_rate = streaming.SAMPLE_RATE_HZ

        async with self._pending_chunks_lock:
            raw_pcm = bytes(self._pending_chunks)
            self._pending_chunks.clear()

            bytes_per_ms = sample_rate * 2 // 1000  # PCM16 mono
            buffer_duration_ms = len(raw_pcm) // bytes_per_ms if bytes_per_ms else 0

            buffer_relative_audio_start_ms: int | None = None
            if event.audio_start_ms is not None:
                buffer_relative_audio_start_ms = event.audio_start_ms - self._buffer_start_session_ms

            # ``self._vad is None`` means "use target default", not "no VAD".
            effective_vad = self._vad if self._vad is not None else streaming.server_vad_config
            prefix_padding_ms = effective_vad.prefix_padding_ms if effective_vad is not None else 0

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
            await self._queue.put(message)
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - bridged to consumer via sentinel
            await self._queue.put(_SentinelError(e))

    async def _handle_committed_turn_async(self, *, event: CommittedEvent, raw_pcm: bytes) -> Message:
        """
        Convert raw user audio, request a response, then assemble and persist both messages.

        Returns:
            The assistant ``Message`` for this turn (the matching user ``Message`` is persisted only).
        """
        assert self._connection is not None
        assert self._dispatcher is not None

        target = self._target
        streaming = target.streaming
        sample_rate = streaming.SAMPLE_RATE_HZ

        if self._request_converter_configurations:
            converted_pcm = await self._prompt_normalizer.convert_audio_async(
                raw_pcm=raw_pcm,
                converter_configurations=self._request_converter_configurations,
                sample_rate_hz=sample_rate,
                num_channels=1,
                sample_width_bytes=2,
            )
            await target.swap_user_audio_async(
                connection=self._connection,
                committed_event=event,
                converted_pcm=converted_pcm,
            )
        else:
            converted_pcm = raw_pcm

        future = await target.request_response_async(
            connection=self._connection,
            dispatcher=self._dispatcher,
        )
        result = await future

        raw_user_path = await streaming.save_audio(raw_pcm, num_channels=1, sample_width=2, sample_rate=sample_rate)
        if converted_pcm is raw_pcm:
            converted_user_path = raw_user_path
        else:
            converted_user_path = await streaming.save_audio(
                converted_pcm, num_channels=1, sample_width=2, sample_rate=sample_rate
            )
        assistant_audio_path = await streaming.save_audio(
            result.audio_bytes, num_channels=1, sample_width=2, sample_rate=sample_rate
        )

        target_identifier = target.get_identifier()
        user_piece = MessagePiece(
            role="user",
            original_value=raw_user_path,
            original_value_data_type="audio_path",
            converted_value=converted_user_path,
            converted_value_data_type="audio_path",
            conversation_id=self._conversation_id,
            prompt_target_identifier=target_identifier,
            attack_identifier=self._attack_identifier,
        )
        for cfg in self._request_converter_configurations:
            user_piece.converter_identifiers.extend(converter.get_identifier() for converter in cfg.converters)
        user_message = Message(message_pieces=[user_piece])

        assistant_text_piece = MessagePiece(
            role="assistant",
            original_value=result.flatten_transcripts(),
            original_value_data_type="text",
            conversation_id=self._conversation_id,
            prompt_target_identifier=target_identifier,
            attack_identifier=self._attack_identifier,
        )
        assistant_audio_piece = MessagePiece(
            role="assistant",
            original_value=assistant_audio_path,
            original_value_data_type="audio_path",
            conversation_id=self._conversation_id,
            prompt_target_identifier=target_identifier,
            attack_identifier=self._attack_identifier,
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
