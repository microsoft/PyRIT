# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Optional, Type, TypeVar

from pyrit.common.logger import logger
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import (
    AttackContext,
    AttackStrategy,
    AttackStrategyResultT,
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
        params_type: Type[AttackParamsT] = AttackParameters,  # type: ignore[assignment]
        logger: logging.Logger = logger,
    ):
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
