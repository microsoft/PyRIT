# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack executor module."""

from pyrit.executor.attack.component import (
    ConversationManager,
    ConversationState,
    PrependedConversationConfig,
)
from pyrit.executor.attack.compound import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialAttackResult,
    SequentialChildAttack,
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
    PAIRAttack,
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

# Backward-compatibility aliases — import from pyrit.output.attack_result directly.
# TODO: Remove these re-exports in two releases (target removal: 0.16.0).
from pyrit.output.attack_result.base import AttackResultPrinterBase as AttackResultPrinter
from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter as MarkdownAttackResultPrinter
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter as ConsoleAttackResultPrinter

__all__ = [
    "AttackAdversarialConfig",
    "AttackContext",
    "AttackConverterConfig",
    "AttackExecutor",
    "AttackExecutorResult",
    "AttackParameters",
    "AttackResultPrinter",
    "AttackScoringConfig",
    "AttackStrategy",
    "ChunkedRequestAttack",
    "ChunkedRequestAttackContext",
    "ConsoleAttackResultPrinter",
    "ContextComplianceAttack",
    "ConversationManager",
    "ConversationSession",
    "ConversationState",
    "CrescendoAttack",
    "CrescendoAttackContext",
    "CrescendoAttackResult",
    "FlipAttack",
    "ManyShotJailbreakAttack",
    "MarkdownAttackResultPrinter",
    "MultiPromptSendingAttack",
    "MultiPromptSendingAttackParameters",
    "MultiTurnAttackContext",
    "MultiTurnAttackStrategy",
    "PAIRAttack",
    "PrependedConversationConfig",
    "PromptSendingAttack",
    "RTASystemPromptPaths",
    "RedTeamingAttack",
    "RolePlayAttack",
    "RolePlayPaths",
    "SequenceCompletionPolicy",
    "SequentialAttack",
    "SequentialAttackResult",
    "SequentialChildAttack",
    "SingleTurnAttackContext",
    "SingleTurnAttackStrategy",
    "SkeletonKeyAttack",
    "TAPAttack",
    "TAPAttackContext",
    "TAPAttackResult",
    "TAPSystemPromptPaths",
    "TreeOfAttacksWithPruningAttack",
    "generate_simulated_conversation_async",
]
