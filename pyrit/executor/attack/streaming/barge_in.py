# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Streaming barge-in attack over realtime audio targets.

Pushes user audio chunks into a continuous Realtime API session, lets server VAD
detect turn boundaries, runs configured audio converters against the buffered raw
audio for each detected turn, swaps the server's raw user item for the converted
audio, manually fires ``response.create``, and observes server-side interruption
when new user audio arrives while the assistant is still speaking. Per-turn
``Message`` pairs are written to ``CentralMemory``; interrupted turns carry
``prompt_metadata["interrupted"] = True`` on both assistant pieces.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
)
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_target import PromptTarget
    from pyrit.prompt_target.common.realtime_audio import (
        RealtimeTargetResult,
        _CommittedEvent,
        _RealtimeEventDispatcher,
    )
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget

logger = logging.getLogger(__name__)


@dataclass
class BargeInAttackContext(AttackContext[AttackParamsT]):
    """
    Context for a streaming barge-in attack.

    Beyond the standard ``AttackContext`` fields, callers supply:

    Attributes:
        conversation_id: Identifier shared by all turns persisted from this session.
        audio_chunks: Async iterator yielding raw PCM16 mono @ 24 kHz chunks. Drives
            the cadence of input; the attack pushes each chunk as it arrives. When
            the iterator exhausts, the attack waits briefly for any in-flight turn
            to resolve, then tears down.
        system_prompt: System prompt to apply to the realtime session.
    """

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_chunks: AsyncIterator[bytes] | None = None
    system_prompt: str = "You are a helpful AI assistant"


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

    _POST_STREAM_SETTLE_SECONDS = 1.0

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
    ) -> None:
        """
        Initialize the streaming barge-in attack.

        Args:
            objective_target: Target to attack. Must declare ``STREAMING_BARGE_IN``
                in its capabilities (validated by ``TARGET_REQUIREMENTS``); the
                server-VAD configuration check happens lazily when the streaming
                session config is sent.
            params_type: Attack parameter dataclass type. Defaults to
                ``AttackParameters``.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=BargeInAttackContext,
            params_type=params_type,
            logger=logger,
        )

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
        """
        target = cast("RealtimeTarget", self._objective_target)
        assert context.audio_chunks is not None  # validated upstream

        connection = await target.connect(conversation_id=context.conversation_id)
        last_result: RealtimeTargetResult | None = None
        executed_turns = 0

        async def on_committed(event: _CommittedEvent) -> None:
            """On each user turn commit, manually fire response.create and record the result."""
            nonlocal last_result, executed_turns
            try:
                turn_future = await target.request_response_async(
                    connection=connection, dispatcher=dispatcher
                )
                last_result = await turn_future
                executed_turns += 1
            except Exception:
                logger.exception("BargeInAttack turn failed while awaiting response.")

        dispatcher: _RealtimeEventDispatcher = await target.subscribe_events_async(
            connection=connection,
            on_user_audio_committed=on_committed,
        )

        try:
            await target.send_streaming_session_config_async(
                connection=connection, system_prompt=context.system_prompt
            )

            async for chunk in context.audio_chunks:
                await target.push_audio_chunk_async(connection=connection, pcm_bytes=chunk)

            # Give server VAD time to commit the buffer and the dispatcher to drain.
            await asyncio.sleep(self._POST_STREAM_SETTLE_SECONDS)
        finally:
            await dispatcher.stop()
            try:
                await connection.close()
            except Exception as e:
                logger.warning(f"Error closing streaming connection: {e}")

        outcome = AttackOutcome.UNDETERMINED
        outcome_reason: str | None
        if executed_turns == 0:
            outcome_reason = "No assistant turns completed (server VAD did not commit any user audio)"
        else:
            outcome_reason = f"{executed_turns} assistant turn(s) completed; no scorer configured"

        return AttackResult(
            conversation_id=context.conversation_id,
            objective=context.objective,
            atomic_attack_identifier=build_atomic_attack_identifier(
                attack_identifier=self.get_identifier()
            ),
            last_response=None,
            last_score=None,
            related_conversations=context.related_conversations,
            outcome=outcome,
            outcome_reason=outcome_reason,
            executed_turns=executed_turns,
            labels=context.memory_labels,
        )
