# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack executor module."""

from pyrit.executor.attack.component import (
    ConversationManager,
    ConversationState,
    PrependedConversationConfig,
)
from pyrit.executor.attack.core import (
    AttackAdversarialConfig,
    AttackContext,
    AttackConverterConfig,
    AttackExecutor,
    AttackExecutorResult,
    AttackParameters,
    AttackScoringConfig,
    AttackStrategy,
)
from pyrit.executor.attack.multi_turn import (
    ChunkedRequestAttack,
    ChunkedRequestAttackContext,
    ConversationSession,
    CrescendoAttack,
    CrescendoAttackContext,
    CrescendoAttackResult,
    MultiPromptSendingAttack,
    MultiPromptSendingAttackParameters,
    MultiTurnAttackContext,
    MultiTurnAttackStrategy,
    RedTeamingAttack,
    RTASystemPromptPaths,
    TAPAttack,
    TAPAttackContext,
    TAPAttackResult,
    TreeOfAttacksWithPruningAttack,
    generate_simulated_conversation_async,
)

# Import printer modules last to avoid circular dependencies
from pyrit.executor.attack.printer import AttackResultPrinter, ConsoleAttackResultPrinter, MarkdownAttackResultPrinter
from pyrit.executor.attack.single_turn import (
    ContextComplianceAttack,
    FlipAttack,
    ManyShotJailbreakAttack,
    PromptSendingAttack,
    RolePlayAttack,
    RolePlayPaths,
    SingleTurnAttackContext,
    SingleTurnAttackStrategy,
    SkeletonKeyAttack,
)

__all__ = [
    "AttackStrategy",
    "AttackContext",
    "AttackParameters",
    "ChunkedRequestAttack",
    "ChunkedRequestAttackContext",
    "CrescendoAttack",
    "CrescendoAttackContext",
    "CrescendoAttackResult",
    "MultiPromptSendingAttack",
    "MultiPromptSendingAttackParameters",
    "TAPAttack",
    "TreeOfAttacksWithPruningAttack",
    "TAPAttackContext",
    "TAPAttackResult",
    "SingleTurnAttackStrategy",
    "SingleTurnAttackContext",
    "PromptSendingAttack",
    "FlipAttack",
    "ContextComplianceAttack",
    "ManyShotJailbreakAttack",
    "RolePlayAttack",
    "RolePlayPaths",
    "SkeletonKeyAttack",
    "ConversationSession",
    "MultiTurnAttackStrategy",
    "MultiTurnAttackContext",
    "RedTeamingAttack",
    "RTASystemPromptPaths",
    "ConsoleAttackResultPrinter",
    "MarkdownAttackResultPrinter",
    "AttackResultPrinter",
    "AttackConverterConfig",
    "AttackScoringConfig",
    "AttackAdversarialConfig",
    "ConversationManager",
    "ConversationState",
    "AttackExecutor",
    "AttackExecutorResult",
    "PrependedConversationConfig",
    "generate_simulated_conversation_async",
]
