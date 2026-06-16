# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Streaming barge-in attack over realtime audio targets."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.component.conversation_manager import ConversationManager
from pyrit.executor.attack.core.attack_config import AttackConverterConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
)
from pyrit.models.identifiers.atomic_attack_identifier import AtomicAttackIdentifier
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pyrit.prompt_target import PromptTarget, RealtimeTarget

logger = logging.getLogger(__name__)


@dataclass
class BargeInAttackContext(AttackContext[AttackParamsT]):
    """
    Context for a streaming barge-in attack with an audio chunk source.

    ``prepended_conversation`` (inherited from ``AttackContext``) is persisted to memory
    on setup, but only the leading system message is propagated to the live realtime
    session as session instructions. User / assistant turns from the prepended history
    are not pushed through ``conversation.item.create``, so the model conditions only on
    the system prompt plus live audio chunks.
    """

    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_chunks: AsyncIterator[bytes] | None = None


class BargeInAttack(AttackStrategy["BargeInAttackContext[Any]", AttackResult]):
    """
    Streaming attack that drives a Realtime API session with server VAD + barge-in.

    The attack pushes user audio chunks through the target, lets server VAD detect
    turn boundaries, manually fires ``response.create`` after each commit, and
    observes assistant turns (including interrupted ones) via per-turn futures
    returned by the target's ``request_response_async``.
    """

    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
        required=frozenset({CapabilityName.STREAMING_AUDIO}),
    )

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
            objective_target: Target to attack. Must declare the ``STREAMING_AUDIO``
                capability (today only ``RealtimeTarget`` does).
            attack_converter_config: Converters applied to each committed user turn.
            prompt_normalizer: Normalizer used to apply converters and persist messages.
                Defaults to a fresh ``PromptNormalizer``.
            params_type: Attack parameter dataclass type.

        Raises:
            ValueError: If ``objective_target`` does not declare the ``STREAMING_AUDIO``
                capability.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=BargeInAttackContext,
            params_type=params_type,
            logger=logger,
        )
        self._realtime_target = cast("RealtimeTarget", objective_target)
        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_manager = ConversationManager(
            prompt_normalizer=self._prompt_normalizer,
        )

    def _validate_context(self, *, context: BargeInAttackContext[Any]) -> None:
        """
        Validate the context before executing.

        Raises:
            ValueError: If the objective is missing/empty or ``audio_chunks`` is not set
                to an async iterator of PCM bytes.
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
        non-streaming attacks. The session is opened with ``persist_prepended_conversation=False``
        in ``_perform_async`` so this is the single writer for prepended history.

        Prepended messages are recorded in memory but are NOT pushed into the live realtime
        session beyond the system prompt — the model only conditions on the system message
        and live audio chunks.
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
        """No-op teardown — connection / dispatcher are closed inside the session's ``run_async``."""
        return

    async def _perform_async(self, *, context: BargeInAttackContext[Any]) -> AttackResult:
        """
        Drive the realtime streaming session and collect per-turn assistant messages.

        Returns:
            An ``AttackResult`` capturing the last assistant turn (if any) and the
            number of completed turns.

        Raises:
            ValueError: If ``context.audio_chunks`` is ``None``.
        """
        if context.audio_chunks is None:
            raise ValueError("BargeInAttackContext.audio_chunks must be set before executing the attack.")

        session = self._realtime_target.open_streaming_session(
            audio_chunks=context.audio_chunks,
            prompt_normalizer=self._prompt_normalizer,
            conversation_id=context.conversation_id,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            prepended_conversation=context.prepended_conversation,
            persist_prepended_conversation=False,
        )

        last_response: Message | None = None
        executed_turns = 0
        async for assistant_message in session.run_async():
            last_response = assistant_message
            executed_turns += 1

        return self._build_result(
            last_response=last_response,
            executed_turns=executed_turns,
            context=context,
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
            atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=self.get_identifier()),
            last_response=(last_response.message_pieces[0] if last_response else None),
            last_score=None,
            related_conversations=context.related_conversations,
            outcome=AttackOutcome.UNDETERMINED,
            outcome_reason=outcome_reason,
            executed_turns=executed_turns,
            labels=context.memory_labels,
        )
