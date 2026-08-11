# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackSeedGroup - A group of seeds for use in attack scenarios.

Extends SeedGroup to enforce exactly one objective is present.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.seeds.attack_technique_seed_group import AttackTechniqueSeedGroup
    from pyrit.models.seeds.seed import Seed


class AttackSeedGroup(SeedGroup):
    """
    A group of seeds for use in attack scenarios.

    This class extends SeedGroup with attack-specific validation:
    - Requires exactly one SeedObjective (not optional like in SeedGroup)

    All other functionality (simulated conversation, prepended conversation,
    next_message, etc.) is inherited from SeedGroup.
    """

    def _check_invariants(self) -> None:
        """
        Validate the seed attack group state.

        Extends SeedGroup validation to require exactly one objective.

        Raises:
            ValueError: If validation fails.

        """
        super()._check_invariants()
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
                f"AttackSeedGroup must have exactly one objective. Found {objective_count}. "
                f"Seeds ({len(self.seeds)}): [{seed_summary}]"
            )

    @property
    def objective(self) -> SeedObjective:
        """
        The objective for this attack group.

        Unlike SeedGroup.objective which may return None, AttackSeedGroup
        guarantees exactly one objective exists.

        Returns:
            The SeedObjective for this attack group.

        Raises:
            ValueError: If the attack group does not have an objective.
        """
        obj = self._get_objective()
        if obj is None:
            raise ValueError("AttackSeedGroup should always have an objective")
        return obj

    def is_compatible_with_technique(self, *, technique: AttackTechniqueSeedGroup) -> bool:
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
        seed_groups: Sequence[AttackSeedGroup],
        technique: AttackTechniqueSeedGroup,
    ) -> list[AttackSeedGroup]:
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

    def with_technique(self, *, technique: AttackTechniqueSeedGroup) -> AttackSeedGroup:
        """
        Return a new AttackSeedGroup with technique seeds merged in.

        The original group is not mutated. Technique seeds are inserted at
        ``technique.insertion_index`` (or appended at the end when ``None``).

        Args:
            technique: A validated AttackTechniqueSeedGroup whose seeds will be merged.

        Returns:
            A new AttackSeedGroup with the merged seeds.

        Raises:
            ValueError: If the technique contains a SeedSimulatedConversation whose
                sequence range overlaps with existing prompt sequences.
            ValueError: If preserving prompt placement combines conflicting roles at
                the same sequence.
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

        base_seeds = [copy.deepcopy(seed) for seed in self.seeds]
        technique_seeds = [copy.deepcopy(seed) for seed in technique.seeds]
        idx = technique.insertion_index
        merged_seeds = (
            base_seeds + technique_seeds if idx is None else base_seeds[:idx] + technique_seeds + base_seeds[idx:]
        )

        # Clear group IDs so the new group assigns a fresh one.
        # ``_enforce_consistent_group_id`` in the constructor will overwrite
        # all of them with a single new UUID.
        for seed in merged_seeds:
            seed.prompt_group_id = None

        self._normalize_prompt_sequences(
            base_seeds=base_seeds,
            technique_seeds=technique_seeds,
            prepend_technique=technique.prompt_placement == "prepend",
        )

        return AttackSeedGroup(seeds=merged_seeds)

    @staticmethod
    def _normalize_prompt_sequences(
        *,
        base_seeds: Sequence[Seed],
        technique_seeds: Sequence[Seed],
        prepend_technique: bool,
    ) -> None:
        """Normalize merged prompt sequences while preserving source-relative order."""
        all_seeds = [*base_seeds, *technique_seeds]
        # Simulated conversations reserve an absolute sequence range; renumbering only prompts
        # could invalidate that range or create an overlap.
        if any(isinstance(seed, SeedSimulatedConversation) for seed in all_seeds):
            return

        seed_groups = (technique_seeds, base_seeds) if prepend_technique else (all_seeds,)
        next_sequence = 0
        for seeds in seed_groups:
            prompts = [seed for seed in seeds if isinstance(seed, SeedPrompt)]
            rank_by_sequence = {value: rank for rank, value in enumerate(sorted({p.sequence for p in prompts}))}
            for prompt in prompts:
                prompt.sequence = next_sequence + rank_by_sequence[prompt.sequence]
            next_sequence += len(rank_by_sequence)
