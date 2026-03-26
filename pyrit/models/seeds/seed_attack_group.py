# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedAttackGroup - A group of seeds for use in attack scenarios.

Extends SeedGroup to enforce exactly one objective is present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.seed import Seed


class SeedAttackGroup(SeedGroup):
    """
    A group of seeds for use in attack scenarios.

    This class extends SeedGroup with attack-specific validation:
    - Requires exactly one SeedObjective (not optional like in SeedGroup)

    All other functionality (simulated conversation, prepended conversation,
    next_message, etc.) is inherited from SeedGroup.
    """

    def __init__(
        self,
        *,
        seeds: Sequence[Union[Seed, dict[str, Any]]],
    ):
        """
        Initialize a SeedAttackGroup.

        Args:
            seeds: Sequence of seeds. Must include exactly one SeedObjective.

        Raises:
            ValueError: If seeds is empty.
            ValueError: If exactly one objective is not provided.

        """
        super().__init__(seeds=seeds)

    def validate(self) -> None:
        """
        Validate the seed attack group state.

        Extends SeedGroup validation to require exactly one objective.

        Raises:
            ValueError: If validation fails.

        """
        super().validate()
        self._enforce_exactly_one_objective()

    def _enforce_exactly_one_objective(self) -> None:
        """
        Ensure exactly one objective is present.

        Raises:
            ValueError: If the group does not contain exactly one SeedObjective.

        """
        objective_count = len([s for s in self.seeds if isinstance(s, SeedObjective)])
        if objective_count != 1:
            seed_summary = ", ".join(
                f"{type(s).__name__}(value={repr(s.value[:80])})" if hasattr(s, "value") else repr(s)
                for s in self.seeds
            )
            raise ValueError(
                f"SeedAttackGroup must have exactly one objective. Found {objective_count}. "
                f"Seeds ({len(self.seeds)}): [{seed_summary}]"
            )

    @property
    def objective(self) -> SeedObjective:
        """
        Get the objective for this attack group.

        Unlike SeedGroup.objective which may return None, SeedAttackGroup
        guarantees exactly one objective exists.

        Returns:
            The SeedObjective for this attack group.

        """
        obj = self._get_objective()
        assert obj is not None, "SeedAttackGroup should always have an objective"
        return obj
