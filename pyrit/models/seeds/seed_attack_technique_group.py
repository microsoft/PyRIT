# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedAttackTechniqueGroup - A group of seeds representing a general attack technique.
For example, this includes jailbreaks, roleplays, or other reusable techniques that
can be applied to multiple objectives.

Extends SeedGroup to enforce that all seeds have is_general_technique=True.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from pyrit.models.seeds.seed_group import SeedGroup

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.seed import Seed


class SeedAttackTechniqueGroup(SeedGroup):
    """
    A group of seeds representing a general attack technique.

    This class extends SeedGroup with technique-specific validation:
    - Requires all seeds to have is_general_technique=True

    All other functionality (simulated conversation, prepended conversation,
    next_message, etc.) is inherited from SeedGroup.
    """

    def __init__(
        self,
        *,
        seeds: Sequence[Union[Seed, dict[str, Any]]],
    ):
        """
        Initialize a SeedAttackTechniqueGroup.

        Args:
            seeds: Sequence of seeds. All seeds must have is_general_technique=True.

        Raises:
            ValueError: If seeds is empty.
            ValueError: If any seed does not have is_general_technique=True.
        """
        super().__init__(seeds=seeds)

    def validate(self) -> None:
        """
        Validate the seed attack technique group state.

        Extends SeedGroup validation to require all seeds to be general strategies.

        Raises:
            ValueError: If validation fails.
        """
        super().validate()
        self._enforce_all_general_strategy()

    def _enforce_all_general_strategy(self) -> None:
        """
        Ensure all seeds have is_general_technique=True.

        Raises:
            ValueError: If any seed does not have is_general_technique=True.
        """
        non_general = [seed for seed in self.seeds if not seed.is_general_technique]
        if non_general:
            non_general_types = [type(s).__name__ for s in non_general]
            raise ValueError(
                f"All seeds in SeedAttackTechniqueGroup must have is_general_technique=True. "
                f"Found {len(non_general)} seed(s) without it: {non_general_types}"
            )
