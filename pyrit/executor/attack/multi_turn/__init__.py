# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Multi-turn attack strategies module."""

from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttack, ChunkedRequestAttackContext
from pyrit.executor.attack.multi_turn.crescendo import CrescendoAttack, CrescendoAttackContext, CrescendoAttackResult
from pyrit.executor.attack.multi_turn.multi_prompt_sending import (
    MultiPromptSendingAttack,
    MultiPromptSendingAttackParameters,
)
from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import (
    ConversationSession,
    MultiTurnAttackContext,
    MultiTurnAttackStrategy,
)
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack, RTASystemPromptPaths
from pyrit.executor.attack.multi_turn.simulated_conversation import (
    generate_simulated_conversation_async,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TAPAttack,
    TAPAttackContext,
    TAPAttackResult,
    TreeOfAttacksWithPruningAttack,
)

__all__ = [
    "ConversationSession",
    "MultiTurnAttackContext",
    "MultiTurnAttackStrategy",
    "ChunkedRequestAttack",
    "ChunkedRequestAttackContext",
    "MultiPromptSendingAttack",
    "MultiPromptSendingAttackParameters",
    "CrescendoAttack",
    "CrescendoAttackContext",
    "CrescendoAttackResult",
    "RedTeamingAttack",
    "RTASystemPromptPaths",
    "generate_simulated_conversation_async",
    "TreeOfAttacksWithPruningAttack",
    "TAPAttack",
    "TAPAttackResult",
    "TAPAttackContext",
]
