# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechnique - Bundles an AttackStrategy with an optional SeedAttackTechniqueGroup.

Represents "how to attack" independently of "what to attack" (the objective).
"""

from __future__ import annotations

from typing import Any

from pyrit.executor.attack import AttackStrategy
from pyrit.models import SeedAttackTechniqueGroup


class AttackTechnique:
    """
    Bundles an attack strategy with an optional technique seed group.

    This cleanly separates "how to attack" (the strategy + reusable technique seeds)
    from "what to attack" (the objective, which lives on SeedAttackGroup / AtomicAttack).
    """

    def __init__(
        self,
        *,
        attack: AttackStrategy[Any, Any],
        seed_technique: SeedAttackTechniqueGroup | None = None,
    ) -> None:
        self._attack = attack
        self._seed_technique = seed_technique

    @property
    def attack(self) -> AttackStrategy[Any, Any]:
        return self._attack

    @property
    def seed_technique(self) -> SeedAttackTechniqueGroup | None:
        return self._seed_technique
