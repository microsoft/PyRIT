# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Singe turn attack strategies module."""

from pyrit.executor.attack.single_turn.bijection_learning import BijectionLearningAttack, BijectionLearningParameters
from pyrit.executor.attack.single_turn.context_compliance import ContextComplianceAttack
from pyrit.executor.attack.single_turn.flip_attack import FlipAttack
from pyrit.executor.attack.single_turn.many_shot_jailbreak import ManyShotJailbreakAttack
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.role_play import RolePlayAttack, RolePlayPaths
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import (
    SingleTurnAttackContext,
    SingleTurnAttackStrategy,
)
from pyrit.executor.attack.single_turn.skeleton_key import SkeletonKeyAttack

__all__ = [
    "BijectionLearningAttack",
    "BijectionLearningParameters",
    "ContextComplianceAttack",
    "FlipAttack",
    "PromptSendingAttack",
    "SingleTurnAttackContext",
    "SingleTurnAttackStrategy",
    "ManyShotJailbreakAttack",
    "RolePlayAttack",
    "RolePlayPaths",
    "SkeletonKeyAttack",
]
