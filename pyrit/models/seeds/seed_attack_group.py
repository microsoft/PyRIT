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
    from pyrit.models.seeds.seed_attack_technique_group import SeedAttackTechniqueGroup


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
    ) -> None:
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

        Raises:
            ValueError: If the attack group does not have an objective.
        """
        obj = self._get_objective()
        if obj is None:
            raise ValueError("SeedAttackGroup should always have an objective")
        return obj

    def is_compatible_with_technique(self, *, technique: SeedAttackTechniqueGroup) -> bool:
        """
        Check whether this seed group can be merged with the given technique.

        A technique containing a ``SeedSimulatedConversation`` is incompatible
        with seed groups that have ``SeedPrompt`` objects whose sequences fall
        within the simulated conversation's range.

        Args:
            technique: The technique group to check compatibility with.

        Returns:
            True if the merge would succeed, False if it would cause a
            sequence overlap.
        """
        sim = technique.simulated_conversation_config
        if sim is None:
            return True
        sim_range = sim.sequence_range
        return not any(p.sequence in sim_range for p in self.prompts)

    @staticmethod
    def filter_compatible(
        *,
        seed_groups: Sequence[SeedAttackGroup],
        technique: SeedAttackTechniqueGroup,
    ) -> list[SeedAttackGroup]:
        """
        Return only the seed groups compatible with the given technique.

        A seed group is incompatible when the technique carries a
        ``SeedSimulatedConversation`` whose sequence range overlaps with
        the group's prompt sequences.

        Args:
            seed_groups: Candidate seed groups.
            technique: The technique to check compatibility against.

        Returns:
            The compatible subset of *seed_groups*.
        """
        return [sg for sg in seed_groups if sg.is_compatible_with_technique(technique=technique)]

    def with_technique(self, *, technique: SeedAttackTechniqueGroup) -> SeedAttackGroup:
        """
        Return a new SeedAttackGroup with technique seeds merged in.

        The original group is not mutated. Technique seeds are inserted at
        ``technique.insertion_index`` (or appended at the end when ``None``).

        Args:
            technique: A validated SeedAttackTechniqueGroup whose seeds will be merged.

        Returns:
            A new SeedAttackGroup with the merged seeds.

        Raises:
            ValueError: If the technique contains a SeedSimulatedConversation whose
                sequence range overlaps with existing prompt sequences.
        """
        # Pre-merge compatibility check with a clear error message
        if not self.is_compatible_with_technique(technique=technique):
            sim = technique.simulated_conversation_config
            assert sim is not None  # guaranteed by is_compatible_with_technique
            prompt_sequences = sorted({p.sequence for p in self.prompts})
            raise ValueError(
                f"Cannot merge technique containing a SeedSimulatedConversation "
                f"(sequence range {list(sim.sequence_range)}) with a seed group that has "
                f"SeedPrompts at sequences {prompt_sequences}. Seed groups with prompts "
                f"overlapping the simulated conversation range are incompatible."
            )

        base = list(self.seeds)
        idx = technique.insertion_index
        technique_seeds = list(technique.seeds)
        merged_seeds = base + technique_seeds if idx is None else base[:idx] + technique_seeds + base[idx:]

        # Clear group IDs so the new group assigns a fresh one.
        # This mutates the seed objects, but _enforce_consistent_group_id
        # in the constructor will immediately overwrite with a new UUID.
        for seed in merged_seeds:
            seed.prompt_group_id = None

        return SeedAttackGroup(seeds=merged_seeds)
