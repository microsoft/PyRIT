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
    TAPSystemPromptPaths,
    TreeOfAttacksWithPruningAttack,
    generate_simulated_conversation_async,
)
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

# Import printer modules last to avoid circular dependencies
from pyrit.printer.attack_result.base import AttackResultPrinterBase as AttackResultPrinter
from pyrit.printer.attack_result.console import ConsoleAttackMemoryPrinter as ConsoleAttackResultPrinter
from pyrit.printer.attack_result.markdown import MarkdownAttackMemoryPrinter as MarkdownAttackResultPrinter

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
    "TAPSystemPromptPaths",
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
