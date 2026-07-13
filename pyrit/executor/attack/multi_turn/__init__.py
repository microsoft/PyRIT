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
from pyrit.executor.attack.multi_turn.pair import PAIRAttack
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack, RTASystemPromptPaths
from pyrit.executor.attack.multi_turn.simulated_conversation import (
    generate_simulated_conversation_async,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TAPAttack,
    TAPAttackContext,
    TAPAttackResult,
    TAPSystemPromptPaths,
    TreeOfAttacksWithPruningAttack,
)

__all__ = [
    "ChunkedRequestAttack",
    "ChunkedRequestAttackContext",
    "ConversationSession",
    "CrescendoAttack",
    "CrescendoAttackContext",
    "CrescendoAttackResult",
    "MultiPromptSendingAttack",
    "MultiPromptSendingAttackParameters",
    "MultiTurnAttackContext",
    "MultiTurnAttackStrategy",
    "PAIRAttack",
    "RTASystemPromptPaths",
    "RedTeamingAttack",
    "TAPAttack",
    "TAPAttackContext",
    "TAPAttackResult",
    "TAPSystemPromptPaths",
    "TreeOfAttacksWithPruningAttack",
    "generate_simulated_conversation_async",
]
