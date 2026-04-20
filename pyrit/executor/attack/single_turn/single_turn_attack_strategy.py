# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Optional, Type, Union

from pyrit.common.logger import logger
from pyrit.executor.attack.core.attack_parameters import AttackParameters, AttackParamsT
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.models import AttackResult
from pyrit.prompt_target import PromptTarget


@dataclass
class SingleTurnAttackContext(AttackContext[AttackParamsT]):
    """
    Context for single-turn attacks.

    Holds execution state for single-turn attacks. The immutable attack parameters
    (objective, next_message, prepended_conversation, memory_labels) are stored in
    the params field inherited from AttackContext.
    """

    # Unique identifier of the main conversation between the attacker and model
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # System prompt for chat-based targets
    system_prompt: Optional[str] = None

    # Arbitrary metadata that downstream attacks or scorers may attach
    metadata: Optional[dict[str, Union[str, int]]] = None


class SingleTurnAttackStrategy(AttackStrategy[SingleTurnAttackContext[Any], AttackResult], ABC):
    """
    Strategy for executing single-turn attacks.
    This strategy is designed to handle attacks that consist of a single turn
    of interaction with the target model.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        context_type: type[SingleTurnAttackContext[Any]] = SingleTurnAttackContext,
        params_type: Type[AttackParamsT] = AttackParameters,  # type: ignore[assignment]
        logger: logging.Logger = logger,
    ):
        """
        Define a base class for single-turn attack strategies.

        Args:
            objective_target (PromptTarget): The target system to attack.
            context_type (type[SingleTurnAttackContext]): The type of context this strategy will use.
            params_type (Type[AttackParamsT]): The type of parameters this strategy accepts.
            logger (logging.Logger): Logger instance for logging events and messages.
        """
        super().__init__(
            objective_target=objective_target,
            context_type=context_type,
            params_type=params_type,
            logger=logger,
        )
