# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Attack executor module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.component import ConversationManager, ConversationState, PrependedConversationConfig
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
        attack_outcome_from_score,
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
        ManyShotJailbreakAttack,
        PromptSendingAttack,
        SingleTurnAttackContext,
        SingleTurnAttackStrategy,
        SkeletonKeyAttack,
    )
    from pyrit.executor.attack.streaming import BargeInAttack, BargeInAttackContext

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AttackAdversarialConfig": "pyrit.executor.attack.core",
    "AttackContext": "pyrit.executor.attack.core",
    "AttackConverterConfig": "pyrit.executor.attack.core",
    "AttackExecutor": "pyrit.executor.attack.core",
    "AttackExecutorResult": "pyrit.executor.attack.core",
    "AttackParameters": "pyrit.executor.attack.core",
    "AttackScoringConfig": "pyrit.executor.attack.core",
    "AttackStrategy": "pyrit.executor.attack.core",
    "attack_outcome_from_score": "pyrit.executor.attack.core",
    "BargeInAttack": "pyrit.executor.attack.streaming",
    "BargeInAttackContext": "pyrit.executor.attack.streaming",
    "ChunkedRequestAttack": "pyrit.executor.attack.multi_turn",
    "ChunkedRequestAttackContext": "pyrit.executor.attack.multi_turn",
    "ConversationManager": "pyrit.executor.attack.component",
    "ConversationSession": "pyrit.executor.attack.multi_turn",
    "ConversationState": "pyrit.executor.attack.component",
    "CrescendoAttack": "pyrit.executor.attack.multi_turn",
    "CrescendoAttackContext": "pyrit.executor.attack.multi_turn",
    "CrescendoAttackResult": "pyrit.executor.attack.multi_turn",
    "ManyShotJailbreakAttack": "pyrit.executor.attack.single_turn",
    "MultiPromptSendingAttack": "pyrit.executor.attack.multi_turn",
    "MultiPromptSendingAttackParameters": "pyrit.executor.attack.multi_turn",
    "MultiTurnAttackContext": "pyrit.executor.attack.multi_turn",
    "MultiTurnAttackStrategy": "pyrit.executor.attack.multi_turn",
    "PAIRAttack": "pyrit.executor.attack.multi_turn",
    "PrependedConversationConfig": "pyrit.executor.attack.component",
    "PromptSendingAttack": "pyrit.executor.attack.single_turn",
    "RTASystemPromptPaths": "pyrit.executor.attack.multi_turn",
    "RedTeamingAttack": "pyrit.executor.attack.multi_turn",
    "SequenceCompletionPolicy": "pyrit.executor.attack.compound",
    "SequentialAttack": "pyrit.executor.attack.compound",
    "SequentialAttackResult": "pyrit.executor.attack.compound",
    "SequentialChildAttack": "pyrit.executor.attack.compound",
    "SingleTurnAttackContext": "pyrit.executor.attack.single_turn",
    "SingleTurnAttackStrategy": "pyrit.executor.attack.single_turn",
    "SkeletonKeyAttack": "pyrit.executor.attack.single_turn",
    "TAPAttack": "pyrit.executor.attack.multi_turn",
    "TAPAttackContext": "pyrit.executor.attack.multi_turn",
    "TAPAttackResult": "pyrit.executor.attack.multi_turn",
    "TAPSystemPromptPaths": "pyrit.executor.attack.multi_turn",
    "TreeOfAttacksWithPruningAttack": "pyrit.executor.attack.multi_turn",
    "generate_simulated_conversation_async": "pyrit.executor.attack.multi_turn",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
