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
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.prompt_target.openai.openai_realtime_target import _REALTIME_COMMITTED_ITEM_ID_KEY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_target import PromptTarget
    from pyrit.prompt_target.common.realtime_audio import (
        CommittedEvent,
    )
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget

logger = logging.getLogger(__name__)


@dataclass
class BargeInAttackContext(AttackContext[AttackParamsT]):
    """Context for a streaming barge-in attack with an audio chunk source."""

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_chunks: AsyncIterator[bytes] | None = None


@dataclass
class _BargeInRunState:
    """Mutable per-session state shared between ``_perform_async`` and ``on_committed``."""

    raw_buffer: bytearray = field(default_factory=bytearray)
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
        prompt_normalizer: PromptNormalizer | None = None,
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
    ) -> None:
        """
        Initialize the streaming barge-in attack.

        Args:
            objective_target: Target to attack. Must declare ``STREAMING_BARGE_IN`` capability.
            attack_converter_config: Converters applied to each committed user turn.
            prompt_normalizer: Normalizer used to apply converters and persist messages.
                Defaults to a fresh ``PromptNormalizer``.
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
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()

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
                    target=target,
                )
                last_response = response
                executed_turns += 1
            except Exception:
                logger.exception("BargeInAttack turn failed in convert-on-commit handler.")

        await target.subscribe_events_async(
            connection=connection,
            conversation_id=context.conversation_id,
            on_user_audio_committed=on_committed,
        )

        try:
            await target.send_streaming_session_config_async(
                connection=connection, conversation=context.prepended_conversation
            )

            async for chunk in context.audio_chunks:
                if chunk:
                    state.raw_buffer.extend(chunk)
                await target.push_audio_chunk_async(connection=connection, pcm_bytes=chunk)

            # Wait for any in-flight committed-turn tasks to finish, capped by a safety timeout.
            # The chunk source must end with enough trailing silence for server VAD's silence
            # threshold to fire commit — otherwise the last turn never enters the pipeline.
            await self._wait_for_pending_turns_async(state.turn_tasks)
        finally:
            await target.cleanup_conversation(context.conversation_id)

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
        target: RealtimeTarget,
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
        # Snapshot the locally-accumulated raw PCM and reset for the next turn.
        snapshot = bytes(state.raw_buffer)
        state.raw_buffer.clear()

        # PromptNormalizer.send_prompt_async needs an audio_path-shaped Message,
        # so persist the snapshot to a durable WAV before wrapping.
        snapshot_path = await target.save_audio(
            snapshot,
            num_channels=1,
            sample_width=2,
            sample_rate=target.SAMPLE_RATE_HZ,
        )
        # Stash the server-assigned item id so the target's streaming branch
        # can swap the raw buffer for converter-transformed audio.
        piece = MessagePiece(
            role="user",
            original_value=snapshot_path,
            original_value_data_type="audio_path",
            converted_value=snapshot_path,
            converted_value_data_type="audio_path",
            conversation_id=context.conversation_id,
            prompt_metadata={_REALTIME_COMMITTED_ITEM_ID_KEY: event.item_id},
        )
        message = Message(message_pieces=[piece])

        return await self._prompt_normalizer.send_prompt_async(
            message=message,
            target=target,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            conversation_id=context.conversation_id,
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
                timeout=self._MAX_POST_STREAM_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Timed out after {self._MAX_POST_STREAM_WAIT_SECONDS}s waiting for in-flight turn tasks to "
                "finish; teardown will cancel them. Increase _MAX_POST_STREAM_WAIT_SECONDS if responses "
                "regularly take longer."
            )
