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

        # ``self`` and ``technique`` may be shared across multiple ``with_technique``
        # calls (e.g. the dispatcher reuses one ``bundle.seed_technique`` instance
        # across every objective). Deepcopy first so the per-seed mutation below
        # and the fresh group_id assigned by ``AttackSeedGroup.__init__`` only
        # touch the returned group, leaving the originals untouched as the
        # docstring promises.
        merged_seeds = [copy.deepcopy(seed) for seed in merged_seeds]

        # Clear group IDs so the new group assigns a fresh one.
        # ``_enforce_consistent_group_id`` in the constructor will overwrite
        # all of them with a single new UUID.
        for seed in merged_seeds:
            seed.prompt_group_id = None

        # Normalize prompt sequences to dense, 0-based order preserving relative
        # ordering. A technique whose seed leads the conversation (e.g. a system
        # prompt built at ``sequence=-1`` by ``from_system_prompt``) is thereby
        # prepended cleanly: it lands at sequence 0 and the existing turns shift
        # up (user 0 -> 1, assistant 1 -> 2, ...), rather than leaving a negative
        # or sparse sequence. This keeps the merge robust no matter how the base
        # group was numbered. Skipped when a simulated conversation is present,
        # since its ``sequence_range`` is absolute and self-consistent.
        has_simulated = any(isinstance(seed, SeedSimulatedConversation) for seed in merged_seeds)
        if not has_simulated:
            prompt_seeds = [seed for seed in merged_seeds if isinstance(seed, SeedPrompt)]
            rank_by_sequence = {seq: rank for rank, seq in enumerate(sorted({p.sequence for p in prompt_seeds}))}
            for seed in prompt_seeds:
                seed.sequence = rank_by_sequence[seed.sequence]

        return AttackSeedGroup(seeds=merged_seeds)
