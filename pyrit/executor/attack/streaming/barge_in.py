# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Streaming barge-in attack over realtime audio targets."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.component.conversation_manager import ConversationManager
from pyrit.executor.attack.core.attack_config import AttackConverterConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target.common.realtime_audio import REALTIME_COMMITTED_ITEM_ID_KEY, SupportsStreamingBargeIn
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_target import PromptTarget
    from pyrit.prompt_target.common.realtime_audio import (
        CommittedEvent,
    )

logger = logging.getLogger(__name__)


@dataclass
class BargeInAttackContext(AttackContext[AttackParamsT]):
    """
    Context for a streaming barge-in attack with an audio chunk source.

    ``prepended_conversation`` (inherited from ``AttackContext``) is persisted to memory
    on setup, but only the leading system message is propagated to the live realtime
    session as session instructions. User / assistant turns from the prepended history
    are not (yet) pushed through ``conversation.item.create``, so the model conditions
    only on the system prompt plus live audio chunks. See follow-up issue for full
    realtime-session injection.
    """

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_chunks: AsyncIterator[bytes] | None = None


@dataclass
class _BargeInRunState:
    """Mutable per-session state shared between ``_perform_async`` and ``on_committed``."""

    raw_buffer: bytearray = field(default_factory=bytearray)
    turn_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    # Session-time (in ms) at which the current buffer started accumulating. Used to
    # convert the server's session-relative ``audio_start_ms`` into a buffer-relative
    # offset for trimming. 0 at session start; advances by ``audio_end_ms`` of each
    # commit, but since the server omits ``audio_end_ms`` we approximate it as
    # ``audio_start_ms + buffer_speech_duration``. In practice we just track the most
    # recent commit's reported start so the next turn's trim is relative to it.
    buffer_start_session_ms: int = 0


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

    Server VAD reports where speech began via ``audio_start_ms``. The local
    accumulator captures every chunk pushed since the last commit — including
    seconds of pre-speech silence — so without a trim the converted audio that
    gets swapped into the server's committed item would be much longer than
    what the server actually committed, causing the model to hear leading silence.

    Args:
        raw_buffer: PCM16 mono audio for the current buffer (all bytes pushed since the last commit).
        sample_rate_hz: PCM sample rate in Hz.
        audio_start_ms: Server's ``audio_start_ms`` offset, or None when unknown.
        prefix_padding_ms: Bytes to keep before ``audio_start_ms`` so we don't chop the speech onset
            (typically matches server VAD's ``prefix_padding_ms``).
        sample_width_bytes: Bytes per sample (2 for PCM16).
        channels: Audio channels (1 for mono).

    Returns:
        The trimmed buffer; returns ``raw_buffer`` unchanged when ``audio_start_ms``
        is None or 0, or when the computed trim would leave nothing.

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


class BargeInAttack(AttackStrategy["BargeInAttackContext[Any]", AttackResult]):
    """
    Streaming attack that drives a Realtime API session with server VAD + barge-in.

    The attack pushes user audio chunks through the target, lets server VAD detect
    turn boundaries, manually fires ``response.create`` after each commit, and
    observes assistant turns (including interrupted ones) via per-turn futures
    returned by the target's ``request_response_async``.
    """

    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
        required=frozenset({CapabilityName.STREAMING_BARGE_IN}),
    )

    #: Default maximum time to wait after the chunk source exhausts for any in-flight
    #: VAD-committed turn to finish (commit → convert → response.create → response.done
    #: → persist). Acts as a safety cap; the attack returns as soon as the last turn
    #: actually completes. Overridable per-instance via ``max_post_stream_wait_seconds``.
    DEFAULT_MAX_POST_STREAM_WAIT_SECONDS: ClassVar[float] = 60.0

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: AttackConverterConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        max_post_stream_wait_seconds: float = DEFAULT_MAX_POST_STREAM_WAIT_SECONDS,
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
    ) -> None:
        """
        Initialize the streaming barge-in attack.

        Args:
            objective_target: Target to attack. Must support ``STREAMING_BARGE_IN`` capability.
            attack_converter_config: Converters applied to each committed user turn.
            prompt_normalizer: Normalizer used to apply converters and persist messages.
                Defaults to a fresh ``PromptNormalizer``.
            max_post_stream_wait_seconds: Safety cap on the wait between the chunk source
                exhausting and the last in-flight turn finishing. Defaults to 60 seconds.
                Bump if a long realtime response is being cancelled at teardown.
            params_type: Attack parameter dataclass type.

        Raises:
            TypeError: If ``objective_target`` does not satisfy ``SupportsStreamingBargeIn``
                (i.e. it declared ``STREAMING_BARGE_IN`` but did not wire a ``streaming``
                attribute pointing at a ``StreamingHandle``).
        """
        super().__init__(
            objective_target=objective_target,
            context_type=BargeInAttackContext,
            params_type=params_type,
            logger=logger,
        )
        if not isinstance(objective_target, SupportsStreamingBargeIn):
            raise TypeError(
                f"{type(objective_target).__name__} does not satisfy SupportsStreamingBargeIn "
                f"(missing `streaming` attribute). Targets that declare STREAMING_BARGE_IN must "
                f"set `self.streaming` to a StreamingHandle instance in `__init__`."
            )
        self._streaming = objective_target.streaming
        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_manager = ConversationManager(
            attack_identifier=self.get_identifier(),
            prompt_normalizer=self._prompt_normalizer,
        )
        self._max_post_stream_wait_seconds = max_post_stream_wait_seconds

    def _validate_context(self, *, context: BargeInAttackContext[Any]) -> None:
        """
        Validate the context before executing.

        Args:
            context: The streaming attack context.

        Raises:
            ValueError: If the context is missing required fields.
        """
        if not context.objective or context.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty in the context")
        if context.audio_chunks is None:
            raise ValueError("BargeInAttackContext.audio_chunks must be set to an async iterator of PCM bytes")

    async def _setup_async(self, *, context: BargeInAttackContext[Any]) -> None:
        """
        Set up the attack: ensure a conversation id and initialize prepended conversation.

        Merges memory labels and persists ``context.prepended_conversation`` to memory via
        ``ConversationManager`` so streaming attacks share the same memory contract as
        non-streaming attacks. Note: prepended messages are recorded in memory but are NOT
        pushed into the live realtime session beyond the system prompt — the model only
        conditions on the system message and live audio chunks. Pushing prepended user /
        assistant turns into the websocket session via ``conversation.item.create`` is
        tracked as a follow-up.
        """
        if not context.conversation_id:
            context.conversation_id = str(uuid.uuid4())
        await self._conversation_manager.initialize_context_async(
            context=context,
            target=self._objective_target,
            conversation_id=context.conversation_id,
            request_converters=self._request_converters,
        )

    async def _teardown_async(self, *, context: BargeInAttackContext[Any]) -> None:
        """No-op teardown — connection / dispatcher are closed inside ``_perform_async``."""
        return

    async def _perform_async(self, *, context: BargeInAttackContext[Any]) -> AttackResult:
        """
        Run the streaming session: connect, subscribe, push chunks, await final turn, tear down.

        Args:
            context: Streaming attack context with ``audio_chunks`` source.

        Returns:
            An ``AttackResult`` capturing the last assistant turn (if any) and the
            number of completed turns.

        Raises:
            ValueError: If ``context.audio_chunks`` is ``None``.
        """
        if context.audio_chunks is None:
            raise ValueError("BargeInAttackContext.audio_chunks must be set before executing the attack.")

        connection = await self._streaming.connect_async(conversation_id=context.conversation_id)
        state = _BargeInRunState()
        last_response: Message | None = None
        executed_turns = 0

        async def on_committed(event: CommittedEvent) -> None:
            nonlocal last_response, executed_turns
            current_task = asyncio.current_task()
            if current_task is not None:
                state.turn_tasks.append(current_task)
            try:
                response = await self._handle_committed_turn_async(
                    event=event,
                    context=context,
                    state=state,
                )
                last_response = response
                executed_turns += 1
            except Exception:
                logger.exception("BargeInAttack turn failed in convert-on-commit handler.")

        await self._streaming.subscribe_events_async(
            connection=connection,
            conversation_id=context.conversation_id,
            on_user_audio_committed=on_committed,
        )

        try:
            await self._streaming.send_streaming_session_config_async(
                connection=connection, conversation=context.prepended_conversation
            )

            async for chunk in context.audio_chunks:
                if chunk:
                    state.raw_buffer.extend(chunk)
                await self._streaming.push_audio_chunk_async(connection=connection, pcm_bytes=chunk)

            # Wait for any in-flight committed-turn tasks to finish, capped by a safety timeout.
            # The chunk source must end with enough trailing silence for server VAD's silence
            # threshold to fire commit — otherwise the last turn never enters the pipeline.
            await self._wait_for_pending_turns_async(state.turn_tasks)
        finally:
            await self._streaming.cleanup_conversation(context.conversation_id)

        return self._build_result(
            last_response=last_response,
            executed_turns=executed_turns,
            context=context,
        )

    async def _handle_committed_turn_async(
        self,
        *,
        event: CommittedEvent,
        context: BargeInAttackContext[Any],
        state: _BargeInRunState,
    ) -> Message:
        """
        Run one convert-and-respond turn for a VAD-committed user audio buffer.

        Snapshots the locally-accumulated raw PCM, persists it as a durable WAV,
        wraps it in a Message with the server's committed item id stashed in
        ``prompt_metadata`` so the target's streaming branch can swap raw audio
        for converter-transformed audio, then drives ``send_prompt_async``.

        Returns:
            The assistant Message returned by ``send_prompt_async`` for this turn.
        """
        snapshot, original_buffer_duration_ms = self._snapshot_and_trim(event=event, state=state)
        # Centralize state mutations so the helpers stay pure and testable.
        state.raw_buffer.clear()
        state.buffer_start_session_ms += original_buffer_duration_ms

        message = await self._build_message_for_turn(
            snapshot=snapshot,
            item_id=event.item_id,
            conversation_id=context.conversation_id,
        )
        return await self._send_via_normalizer(message=message, conversation_id=context.conversation_id)

    def _snapshot_and_trim(
        self,
        *,
        event: CommittedEvent,
        state: _BargeInRunState,
    ) -> tuple[bytes, int]:
        """
        Return a trimmed PCM snapshot for the current buffer plus its original (pre-trim) duration.

        Converts the server's session-relative ``audio_start_ms`` into a buffer-relative offset
        and trims leading pre-speech silence. Without this, the converted audio that gets
        swapped into the server's committed item would be several seconds longer than what
        server VAD actually committed, and the model would hear the leading silence (often
        dominant) when converters are active.

        The original duration (pre-trim) is returned so the caller can advance session-time
        bookkeeping — the server saw every byte we pushed, not just the trimmed snapshot.

        Returns:
            ``(snapshot, original_buffer_duration_ms)``. The caller is responsible for
            clearing ``state.raw_buffer`` and advancing ``state.buffer_start_session_ms``.
        """
        snapshot = bytes(state.raw_buffer)

        bytes_per_ms = self._streaming.SAMPLE_RATE_HZ * 2 // 1000  # PCM16 mono
        original_buffer_duration_ms = len(snapshot) // bytes_per_ms if bytes_per_ms else 0

        buffer_relative_audio_start_ms: int | None = None
        if event.audio_start_ms is not None:
            buffer_relative_audio_start_ms = event.audio_start_ms - state.buffer_start_session_ms

        server_vad = self._streaming.server_vad_config
        prefix_padding_ms = server_vad.prefix_padding_ms if server_vad is not None else 0
        snapshot = _trim_snapshot_to_speech(
            raw_buffer=snapshot,
            sample_rate_hz=self._streaming.SAMPLE_RATE_HZ,
            audio_start_ms=buffer_relative_audio_start_ms,
            prefix_padding_ms=prefix_padding_ms,
        )
        return snapshot, original_buffer_duration_ms

    async def _build_message_for_turn(
        self,
        *,
        snapshot: bytes,
        item_id: str,
        conversation_id: str,
    ) -> Message:
        """
        Persist the snapshot to disk and wrap it in an audio_path-shaped Message.

        ``send_prompt_async`` requires a file-backed Message, so the caller persists
        the PCM bytes to a durable WAV first. The server's committed ``item_id`` is
        stashed in ``prompt_metadata`` so the target's streaming branch can identify
        which committed item to swap for converter-transformed audio.

        Returns:
            The constructed Message containing one ``audio_path`` MessagePiece.
        """
        snapshot_path = await self._streaming.save_audio(
            snapshot,
            num_channels=1,
            sample_width=2,
            sample_rate=self._streaming.SAMPLE_RATE_HZ,
        )
        piece = MessagePiece(
            role="user",
            original_value=snapshot_path,
            original_value_data_type="audio_path",
            converted_value=snapshot_path,
            converted_value_data_type="audio_path",
            conversation_id=conversation_id,
            prompt_metadata={REALTIME_COMMITTED_ITEM_ID_KEY: item_id},
        )
        return Message(message_pieces=[piece])

    async def _send_via_normalizer(self, *, message: Message, conversation_id: str) -> Message:
        """
        Send a built turn-Message through the normalizer with this attack's converters.

        Returns:
            The assistant Message returned by ``PromptNormalizer.send_prompt_async``.
        """
        return await self._prompt_normalizer.send_prompt_async(
            message=message,
            target=self._objective_target,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            conversation_id=conversation_id,
            attack_identifier=self.get_identifier(),
        )

    def _build_result(
        self,
        *,
        last_response: Message | None,
        executed_turns: int,
        context: BargeInAttackContext[Any],
    ) -> AttackResult:
        """
        Assemble the final ``AttackResult`` from accumulated turn outcomes.

        Returns:
            ``AttackResult`` with the last assistant message, executed turn count,
            and outcome reason.
        """
        if executed_turns == 0:
            outcome_reason: str | None = "No assistant turns completed (server VAD did not commit any user audio)"
        else:
            outcome_reason = f"{executed_turns} assistant turn(s) completed; no scorer configured"

        return AttackResult(
            conversation_id=context.conversation_id,
            objective=context.objective,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=self.get_identifier()),
            last_response=(last_response.message_pieces[0] if last_response else None),
            last_score=None,
            related_conversations=context.related_conversations,
            outcome=AttackOutcome.UNDETERMINED,
            outcome_reason=outcome_reason,
            executed_turns=executed_turns,
            labels=context.memory_labels,
        )

    async def _wait_for_pending_turns_async(self, turn_tasks: list[asyncio.Task[None]]) -> None:
        """
        Wait for any in-flight VAD-committed turn tasks to finish, with a safety timeout.

        Returns as soon as all known turn tasks complete (or the cap elapses, whichever
        comes first). The timeout is a safety net for stuck turns; the common case is to
        return immediately once the last turn's persistence finishes.

        Args:
            turn_tasks: Task handles for every ``on_committed`` invocation launched so far.
                Tasks added after this method starts are not waited on; the dispatcher
                callback machinery makes this race vanishingly unlikely in practice.
        """
        if not turn_tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*turn_tasks, return_exceptions=True),
                timeout=self._max_post_stream_wait_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Timed out after {self._max_post_stream_wait_seconds}s waiting for in-flight turn tasks to "
                "finish; teardown will cancel them. Raise max_post_stream_wait_seconds on the attack "
                "constructor if responses regularly take longer."
            )
