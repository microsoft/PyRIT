# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Requirements for the dataset input supplied to a technique."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models.seeds.attack_seed_group import AttackSeedGroup


@dataclass(frozen=True)
class SeedGroupRequirements:
    """Dataset input constraints and permission for objective-only adaptation."""

    objective_only: bool = False
    try_adapt: bool = False

    def __post_init__(self) -> None:
        """
        Reject adaptation permission without an input constraint.

        Raises:
            ValueError: If try_adapt is enabled without objective_only.
        """
        if self.try_adapt and not self.objective_only:
            raise ValueError("try_adapt requires an objective_only input constraint.")

    def check(self, *, seed_group: AttackSeedGroup) -> list[str]:
        """
        Inspect the source group without changing it or applying adaptation.

        Args:
            seed_group (AttackSeedGroup): The incoming dataset group, without technique seeds.

        Returns:
            list[str]: Unmet constraints, including those that could be adapted.
        """
        if self.objective_only and len(seed_group.seeds) != 1:
            return [
                "Technique accepts only an objective; dataset prompts and simulated conversations are not accepted."
            ]
        return []
