# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueSeedGroup - A group of seeds representing a general attack technique.
For example, this includes jailbreaks, roleplays, or other reusable techniques that
can be applied to multiple objectives.

Extends SeedGroup to enforce that all seeds have is_general_technique=True.
"""

from __future__ import annotations

from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt


class AttackTechniqueSeedGroup(SeedGroup):
    """
    A group of seeds representing a general attack technique.

    This class extends SeedGroup with technique-specific validation:
    - Requires all seeds to have is_general_technique=True

    All other functionality (simulated conversation, prepended conversation,
    next_message, etc.) is inherited from SeedGroup.
    """

    # Where to insert technique seeds when merging into a AttackSeedGroup via ``with_technique()``.
    # ``None`` (default) appends at the end; an integer inserts before that position.
    insertion_index: int | None = None

    @classmethod
    def from_system_prompt(cls, system_prompt: str, *, insertion_index: int | None = None) -> AttackTechniqueSeedGroup:
        """
        Build a technique group carrying a single system-role instruction.

        This is the common shape for jailbreaks and role-play techniques whose only
        payload is a system prompt that should be prepended to every objective. The
        value is wrapped verbatim (``is_jinja_template=False``), so any literal
        ``{{ ... }}`` in ``system_prompt`` is preserved rather than re-rendered.

        Args:
            system_prompt (str): The system-role instruction text.
            insertion_index (int | None): Where to insert the seed when merging into a
                ``AttackSeedGroup``. ``None`` (default) appends at the end.

        Returns:
            AttackTechniqueSeedGroup: A group with a single general-technique system seed.
        """
        return cls(
            seeds=[SeedPrompt(value=system_prompt, data_type="text", role="system", is_general_technique=True)],
            insertion_index=insertion_index,
        )

    def _check_invariants(self) -> None:
        """
        Validate the seed attack technique group state.

        Extends SeedGroup validation to require all seeds to be general strategies
        and to contain no objectives.

        Raises:
            ValueError: If validation fails.
        """
        super()._check_invariants()
        self._enforce_all_general_strategy()
        self._enforce_no_objectives()

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
                f"All seeds in AttackTechniqueSeedGroup must have is_general_technique=True. "
                f"Found {len(non_general)} seed(s) without it: {non_general_types}"
            )

    def _enforce_no_objectives(self) -> None:
        """
        Ensure no SeedObjective seeds are present.

        Raises:
            ValueError: If any seed is a SeedObjective.
        """
        objectives = [seed for seed in self.seeds if isinstance(seed, SeedObjective)]
        if objectives:
            raise ValueError(
                f"AttackTechniqueSeedGroup must not contain objectives. Found {len(objectives)} SeedObjective(s)."
            )
