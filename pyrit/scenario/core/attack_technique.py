# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechnique - Bundles an AttackStrategy with an optional AttackTechniqueSeedGroup.

Represents "how to attack" independently of "what to attack" (the objective).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.models import AttackTechniqueIdentifier, ComponentIdentifier, Identifiable, SeedIdentifier

if TYPE_CHECKING:
    from pyrit.executor.attack import AttackStrategy
    from pyrit.models import AttackTechniqueSeedGroup


class AttackTechnique(Identifiable):
    """
    Bundles an attack technique with an optional technique seed group.

    An AttackTechnique encapsulates the full attack configuration — the technique
    (including its target, converters, and scorer) plus any reusable technique seeds
    (e.g. jailbreak templates). The objectives that define which weaknesses to probe
    live separately on the AttackSeedGroup / AtomicAttack.
    """

    def __init__(
        self,
        *,
        attack: AttackStrategy[Any, Any],
        seed_technique: AttackTechniqueSeedGroup | None = None,
    ) -> None:
        """Initialize an AttackTechnique."""
        self._attack = attack
        self._seed_technique = seed_technique

    @property
    def attack(self) -> AttackStrategy[Any, Any]:
        """The attack technique."""
        return self._attack

    @property
    def seed_technique(self) -> AttackTechniqueSeedGroup | None:
        """The optional technique seed group."""
        return self._seed_technique

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity for this attack technique.

        The identifier always contains the attack technique as ``children["attack"]``.
        When a seed technique is present, its seeds are added as
        ``children["technique_seeds"]``.

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        technique_seeds: list[SeedIdentifier] | None = None
        if self._seed_technique is not None:
            technique_seed_ids = [SeedIdentifier.from_seed(seed) for seed in self._seed_technique.seeds]
            if technique_seed_ids:
                technique_seeds = list(technique_seed_ids)

        return AttackTechniqueIdentifier.of(
            self,
            attack=self._attack.get_identifier(),
            technique_seeds=technique_seeds,
        )
