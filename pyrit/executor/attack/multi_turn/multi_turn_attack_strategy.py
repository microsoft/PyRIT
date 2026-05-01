# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging  # noqa: TC003
import uuid
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from pyrit.common.logger import logger
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import (
    AttackContext,
    AttackStrategy,
    AttackStrategyResultT,
)
from pyrit.memory import CentralMemory
from pyrit.models import ConversationReference, ConversationType
from pyrit.prompt_target.common.target_capabilities import CapabilityName

if TYPE_CHECKING:
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
    last_response: Optional[Message] = None

    # Score assigned to the latest response by a scorer component
    last_score: Optional[Score] = None


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
        logger: logging.Logger = logger,
    ) -> None:
        """
        Implement the base class for multi-turn attack strategies.

        Args:
            objective_target (PromptTarget): The target system to attack.
            context_type (type[MultiTurnAttackContext]): The type of context this strategy will use.
            params_type (Type[AttackParamsT]): The type of parameters this strategy accepts.
            logger (logging.Logger): Logger instance for logging events and messages.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=context_type,
            params_type=params_type,
            logger=logger,
        )

    def _rotate_conversation_for_single_turn_target(
        self,
        *,
        context: MultiTurnAttackContext[Any],
    ) -> None:
        """
        Create a fresh conversation_id for the objective target if it is a single-turn target.

        For single-turn targets, each turn must use a separate conversation_id because the target
        rejects conversations with prior messages. The prior turn's conversation_id is recorded
        as a PRUNED related conversation on the attack context.

        System messages (e.g., from prepended conversation) are duplicated into the new
        conversation so that the target retains its system prompt context.

        For multi-turn targets this method is a no-op.

        This should be called before each turn (except the first) when sending prompts to the
        objective target.

        Args:
            context: The current attack context.
        """
        if self._objective_target.configuration.includes(capability=CapabilityName.MULTI_TURN):
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

        # Duplicate system messages (e.g., system prompt from prepended conversation)
        # into the new conversation so the target retains its configuration.
        memory = CentralMemory.get_memory_instance()
        messages = memory.get_conversation(conversation_id=old_conversation_id)
        system_messages = [m for m in messages if m.api_role == "system"]

        if system_messages:
            new_conversation_id, pieces = memory.duplicate_messages(messages=system_messages)
            memory.add_message_pieces_to_memory(message_pieces=pieces)
            context.session.conversation_id = new_conversation_id
        else:
            context.session.conversation_id = str(uuid.uuid4())

        self._logger.debug(
            f"Rotated conversation_id for single-turn target: "
            f"{old_conversation_id} -> {context.session.conversation_id}"
        )
