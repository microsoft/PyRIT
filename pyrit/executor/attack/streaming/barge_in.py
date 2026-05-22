# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Streaming barge-in attack over realtime audio targets."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.core.attack_config import AttackConverterConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_target import PromptTarget
    from pyrit.prompt_target.common.realtime_audio import (
        CommittedEvent,
        RealtimeEventDispatcher,
        RealtimeTargetResult,
    )
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget

logger = logging.getLogger(__name__)

_REALTIME_SAMPLE_RATE_HZ = 24000


@dataclass
class BargeInAttackContext(AttackContext[AttackParamsT]):
    """Context for a streaming barge-in attack with audio chunk source and session config."""

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_chunks: AsyncIterator[bytes] | None = None
    system_prompt: str = "You are a helpful AI assistant"


@dataclass
class _BargeInRunState:
    """Mutable per-session state accumulated as turns commit."""

    raw_buffer: bytearray = field(default_factory=bytearray)
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_assistant_message: Message | None = None
    executed_turns: int = 0
    turn_tasks: list[asyncio.Task[None]] = field(default_factory=list)


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

    #: Maximum time to wait after the chunk source exhausts for any in-flight VAD-committed
    #: turn to finish (commit → convert → response.create → response.done → persist). Acts as
    #: a safety cap; the attack returns as soon as the last turn actually completes.
    _MAX_POST_STREAM_WAIT_SECONDS = 30.0

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: AttackConverterConfig | None = None,
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
    ) -> None:
        """
        Initialize the streaming barge-in attack.

        Args:
            objective_target: Target to attack. Must declare ``STREAMING_BARGE_IN`` capability.
                Audio normalization is delegated to ``objective_target.audio_normalizer``.
            attack_converter_config: Converters applied to each committed user turn.
            params_type: Attack parameter dataclass type.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=BargeInAttackContext,
            params_type=params_type,
            logger=logger,
        )
        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters

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
        Set up the attack: nothing beyond ensuring a conversation id is present.
        """
        if not context.conversation_id:
            context.conversation_id = str(uuid.uuid4())

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
        target = cast("RealtimeTarget", self._objective_target)
        if context.audio_chunks is None:
            raise ValueError("BargeInAttackContext.audio_chunks must be set before executing the attack.")

        connection = await target.connect_async(conversation_id=context.conversation_id)
        state = _BargeInRunState()

        async def on_committed(event: CommittedEvent) -> None:
            current_task = asyncio.current_task()
            if current_task is not None:
                state.turn_tasks.append(current_task)
            try:
                await self._handle_committed_turn_async(
                    state=state,
                    event=event,
                    target=target,
                    connection=connection,
                    dispatcher=dispatcher,
                    conversation_id=context.conversation_id,
                )
            except Exception:
                logger.exception("BargeInAttack turn failed in convert-on-commit handler.")

        dispatcher: RealtimeEventDispatcher = await target.subscribe_events_async(
            connection=connection,
            on_user_audio_committed=on_committed,
        )

        try:
            await target.send_streaming_session_config_async(connection=connection, system_prompt=context.system_prompt)

            async for chunk in context.audio_chunks:
                if chunk:
                    state.raw_buffer.extend(chunk)
                await target.push_audio_chunk_async(connection=connection, pcm_bytes=chunk)

            # Wait for any in-flight committed-turn tasks to finish (convert + response +
            # persistence), capped by a safety timeout. The chunk source must end with enough
            # trailing silence for server VAD's silence threshold to fire commit — otherwise
            # the last turn never enters the convert pipeline and there is nothing to wait on.
            await self._wait_for_pending_turns_async(state.turn_tasks)
        finally:
            await dispatcher.stop()
            try:
                await connection.close()
            except Exception as e:
                logger.warning(f"Error closing streaming connection: {e}")

        return self._build_result(state=state, context=context)

    async def _handle_committed_turn_async(
        self,
        *,
        state: _BargeInRunState,
        event: CommittedEvent,
        target: RealtimeTarget,
        connection: Any,
        dispatcher: RealtimeEventDispatcher,
        conversation_id: str,
    ) -> None:
        """Run the convert-on-commit dance for one VAD-committed user audio turn."""
        async with state.turn_lock:
            snapshot = self._snapshot_user_audio(state)

            try:
                converted_pcm, applied_identifiers = await target.audio_normalizer.normalize_async(
                    pcm_bytes=snapshot,
                    sample_rate=_REALTIME_SAMPLE_RATE_HZ,
                    converter_configurations=self._request_converters,
                )
            except Exception:
                logger.exception("Audio converters failed; dropping turn.")
                return

            using_converted_audio = bool(self._request_converters) and converted_pcm != snapshot
            if using_converted_audio:
                await target.swap_user_audio_async(
                    connection=connection,
                    committed_event=event,
                    converted_pcm=converted_pcm,
                )

            turn_future = await target.request_response_async(connection=connection, dispatcher=dispatcher)
            turn_result = await turn_future

            user_audio_pcm = converted_pcm if using_converted_audio else snapshot
            state.last_assistant_message = await self._persist_turn_async(
                target=target,
                conversation_id=conversation_id,
                user_audio_pcm=user_audio_pcm,
                applied_converter_identifiers=applied_identifiers,
                turn_result=turn_result,
            )
            state.executed_turns += 1

    def _snapshot_user_audio(self, state: _BargeInRunState) -> bytes:
        """
        Snapshot the accumulated user PCM and clear the buffer for the next turn.

        Returns:
            Snapshot of buffered PCM bytes prior to clearing.
        """
        snapshot = bytes(state.raw_buffer)
        state.raw_buffer.clear()
        return snapshot

    def _build_result(
        self,
        *,
        state: _BargeInRunState,
        context: BargeInAttackContext[Any],
    ) -> AttackResult:
        """
        Assemble the final ``AttackResult`` from accumulated run state.

        Returns:
            ``AttackResult`` with the last assistant message, executed turn count, and outcome reason.
        """
        if state.executed_turns == 0:
            outcome_reason: str | None = "No assistant turns completed (server VAD did not commit any user audio)"
        else:
            outcome_reason = f"{state.executed_turns} assistant turn(s) completed; no scorer configured"

        return AttackResult(
            conversation_id=context.conversation_id,
            objective=context.objective,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=self.get_identifier()),
            last_response=(state.last_assistant_message.message_pieces[0] if state.last_assistant_message else None),
            last_score=None,
            related_conversations=context.related_conversations,
            outcome=AttackOutcome.UNDETERMINED,
            outcome_reason=outcome_reason,
            executed_turns=state.executed_turns,
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
                timeout=self._MAX_POST_STREAM_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Timed out after {self._MAX_POST_STREAM_WAIT_SECONDS}s waiting for in-flight turn tasks to "
                "finish; teardown will cancel them. Increase _MAX_POST_STREAM_WAIT_SECONDS if responses "
                "regularly take longer."
            )

    async def _persist_turn_async(
        self,
        *,
        target: RealtimeTarget,
        conversation_id: str,
        user_audio_pcm: bytes,
        applied_converter_identifiers: list[ComponentIdentifier],
        turn_result: RealtimeTargetResult,
    ) -> Message:
        """
        Persist the user+assistant Message pair for one completed turn to CentralMemory.

        Returns:
            The assistant Message so callers can surface it as ``last_response``.
        """
        user_audio_path = await target.save_audio(
            user_audio_pcm,
            num_channels=1,
            sample_width=2,
            sample_rate=_REALTIME_SAMPLE_RATE_HZ,
        )
        user_piece = MessagePiece(
            role="user",
            original_value=user_audio_path,
            original_value_data_type="audio_path",
            converted_value=user_audio_path,
            converted_value_data_type="audio_path",
            conversation_id=conversation_id,
        )
        user_piece.converter_identifiers.extend(applied_converter_identifiers)
        user_message = Message(message_pieces=[user_piece])

        response_audio_path = await target.save_audio(
            turn_result.audio_bytes,
            num_channels=1,
            sample_width=2,
            sample_rate=_REALTIME_SAMPLE_RATE_HZ,
        )
        text_piece = construct_response_from_request(
            request=user_piece,
            response_text_pieces=[turn_result.flatten_transcripts()],
            response_type="text",
        ).message_pieces[0]
        audio_piece = construct_response_from_request(
            request=user_piece,
            response_text_pieces=[response_audio_path],
            response_type="audio_path",
        ).message_pieces[0]
        if turn_result.interrupted:
            text_piece.prompt_metadata["interrupted"] = True
            audio_piece.prompt_metadata["interrupted"] = True
        assistant_message = Message(message_pieces=[text_piece, audio_piece])

        memory = CentralMemory.get_memory_instance()
        memory.add_message_to_memory(request=user_message)
        memory.add_message_to_memory(request=assistant_message)
        return assistant_message
