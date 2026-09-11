# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Multi-turn attack strategies module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttack, ChunkedRequestAttackContext
    from pyrit.executor.attack.multi_turn.crescendo import (
        CrescendoAttack,
        CrescendoAttackContext,
        CrescendoAttackResult,
    )
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
    from pyrit.executor.attack.multi_turn.simulated_conversation import generate_simulated_conversation_async
    from pyrit.executor.attack.multi_turn.tree_of_attacks import (
        TAPAttack,
        TAPAttackContext,
        TAPAttackResult,
        TAPSystemPromptPaths,
        TreeOfAttacksWithPruningAttack,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "ChunkedRequestAttack": "pyrit.executor.attack.multi_turn.chunked_request",
    "ChunkedRequestAttackContext": "pyrit.executor.attack.multi_turn.chunked_request",
    "ConversationSession": "pyrit.executor.attack.multi_turn.multi_turn_attack_strategy",
    "CrescendoAttack": "pyrit.executor.attack.multi_turn.crescendo",
    "CrescendoAttackContext": "pyrit.executor.attack.multi_turn.crescendo",
    "CrescendoAttackResult": "pyrit.executor.attack.multi_turn.crescendo",
    "MultiPromptSendingAttack": "pyrit.executor.attack.multi_turn.multi_prompt_sending",
    "MultiPromptSendingAttackParameters": "pyrit.executor.attack.multi_turn.multi_prompt_sending",
    "MultiTurnAttackContext": "pyrit.executor.attack.multi_turn.multi_turn_attack_strategy",
    "MultiTurnAttackStrategy": "pyrit.executor.attack.multi_turn.multi_turn_attack_strategy",
    "PAIRAttack": "pyrit.executor.attack.multi_turn.pair",
    "RTASystemPromptPaths": "pyrit.executor.attack.multi_turn.red_teaming",
    "RedTeamingAttack": "pyrit.executor.attack.multi_turn.red_teaming",
    "TAPAttack": "pyrit.executor.attack.multi_turn.tree_of_attacks",
    "TAPAttackContext": "pyrit.executor.attack.multi_turn.tree_of_attacks",
    "TAPAttackResult": "pyrit.executor.attack.multi_turn.tree_of_attacks",
    "TAPSystemPromptPaths": "pyrit.executor.attack.multi_turn.tree_of_attacks",
    "TreeOfAttacksWithPruningAttack": "pyrit.executor.attack.multi_turn.tree_of_attacks",
    "generate_simulated_conversation_async": "pyrit.executor.attack.multi_turn.simulated_conversation",
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
