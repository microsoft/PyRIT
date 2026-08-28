# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging  # noqa: TC003
import uuid
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.common.logger import logger
from pyrit.executor.attack.component.conversation_manager import ConversationManager
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import (
    AttackContext,
    AttackStrategy,
    AttackStrategyResultT,
)
from pyrit.memory import CentralMemory
from pyrit.models import Conversation, ConversationReference, ConversationType
from pyrit.prompt_target import CapabilityName

if TYPE_CHECKING:
    from pyrit.executor.attack.component.prepended_conversation_config import (
        PrependedConversationConfig,
    )
    from pyrit.models import (
        Message,
        Score,
    )
    from pyrit.prompt_target import PromptTarget

MultiTurnAttackStrategyContextT = TypeVar("MultiTurnAttackStrategyContextT", bound="MultiTurnAttackContext[Any]")


@dataclass
class ConversationSession:
    """Session for conversations."""

    # Unique identifier of the main conversation between the attacker and model
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Separate identifier used when the attack leverages an adversarial chat
    adversarial_chat_conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class MultiTurnAttackContext(AttackContext[AttackParamsT]):
    """
    Context for multi-turn attacks.

    Holds execution state for multi-turn attacks. The immutable attack parameters
    (objective, next_message, prepended_conversation, memory_labels) are stored in
    the params field inherited from AttackContext.
    """

    # Object holding all conversation-level identifiers for this attack
    session: ConversationSession = field(default_factory=lambda: ConversationSession())

    # Counter of turns that have actually been executed so far
    executed_turns: int = 0

    # Model response produced in the latest turn
    last_response: Message | None = None

    # Score assigned to the latest response by a scorer component
    last_score: Score | None = None


class MultiTurnAttackStrategy(AttackStrategy[MultiTurnAttackStrategyContextT, AttackStrategyResultT], ABC):
    """
    Strategy for executing multi-turn attacks.
    This strategy is designed to handle attacks that consist of multiple turns
    of interaction with the target model.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        context_type: type[MultiTurnAttackStrategyContextT],
        params_type: type[AttackParamsT] = AttackParameters,  # type: ignore[ty:invalid-parameter-default]
        prepended_conversation_config: PrependedConversationConfig | None = None,
        logger: logging.Logger = logger,
    ) -> None:
        """
        Implement the base class for multi-turn attack strategies.

        Args:
            objective_target (PromptTarget): The target system to attack.
            context_type (type[MultiTurnAttackContext]): The type of context this strategy will use.
            params_type (type[AttackParamsT]): The type of parameters this strategy accepts.
            prepended_conversation_config (PrependedConversationConfig | None): Policy for
                prepended conversations. See ``AttackStrategy``.
            logger (logging.Logger): Logger instance for logging events and messages.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=context_type,
            params_type=params_type,
            prepended_conversation_config=prepended_conversation_config,
            logger=logger,
        )

    def _rotate_conversation_for_single_turn_target(
        self,
        *,
        context: MultiTurnAttackContext[Any],
    ) -> None:
        """
        Rotate an unseeded single-turn target conversation before later sends.

        An explicit target normalization context already selects only the
        persisted seed plus the current request, so rotating in that case would
        lose the seed and change the target-facing payload.
        """
        if self._objective_target.configuration.includes(capability=CapabilityName.MULTI_TURN):
            return
        if context.prepended_history_send_context:
            return
        if context.executed_turns == 0:
            return

        old_conversation_id = context.session.conversation_id
        context.related_conversations.add(
            ConversationReference(
                conversation_id=old_conversation_id,
                conversation_type=ConversationType.PRUNED,
                description=f"single-turn target prior turn {context.executed_turns}",
            )
        )

        memory = CentralMemory.get_memory_instance()
        messages = memory.get_conversation_messages(conversation_id=old_conversation_id)
        system_messages = [message for message in messages if message.api_role == "system"]

        if system_messages:
            new_conversation_id, pieces = memory.duplicate_messages(messages=system_messages)
            memory.add_conversation_to_memory(
                conversation=Conversation(
                    conversation_id=new_conversation_id,
                    target_identifier=self._objective_target.get_identifier(),
                )
            )
            memory.add_message_pieces_to_memory(message_pieces=pieces)
            context.session.conversation_id = new_conversation_id
            persisted_messages = list(memory.get_conversation_messages(conversation_id=new_conversation_id))
            context.prepended_history_send_context = ConversationManager.create_prepended_history_send_context(
                target=self._objective_target,
                conversation_id=new_conversation_id,
                prepended_messages=persisted_messages,
            )
        else:
            context.session.conversation_id = str(uuid.uuid4())

        self._logger.debug(
            "Rotated conversation_id for single-turn target: %s -> %s",
            old_conversation_id,
            context.session.conversation_id,
        )
