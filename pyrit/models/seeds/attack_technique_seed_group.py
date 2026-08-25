# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueSeedGroup - A group of seeds representing a general attack technique.
For example, this includes jailbreaks, roleplays, or other reusable techniques that
can be applied to multiple objectives.

Extends SeedGroup to enforce that all seeds have is_general_technique=True.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from pyrit.models.seeds.seed_group import SeedGroup
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


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

    prompt_placement: Literal["preserve", "prepend"] = Field(
        default="preserve",
        description=(
            '"preserve" combines existing sequence relationships. During AttackSeedGroup construction, '
            "prompts at the same sequence are grouped when roles are the same and rejected when roles conflict. "
            '"prepend" places technique prompts before base prompts.'
        ),
    )

    @classmethod
    def from_system_prompt(cls, system_prompt: str, *, insertion_index: int | None = None) -> AttackTechniqueSeedGroup:
        """
        Build a technique group carrying a single system-role instruction.

        This is the common shape for jailbreaks and role-play techniques whose only
        payload is a system prompt that should be prepended to every objective. The
        value is wrapped verbatim (``is_jinja_template=False``), so any literal
        ``{{ ... }}`` in ``system_prompt`` is preserved rather than re-rendered.

        The group declares ``prompt_placement="prepend"`` so ``AttackSeedGroup.with_technique``
        places the system framing before the base prompts without relying on a reserved sequence
        value.

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
            prompt_placement="prepend",
        )

    @classmethod
    def from_messages(
        cls,
        *,
        messages: list[Message],
        starting_sequence: int = 0,
        insertion_index: int | None = None,
        prompt_placement: Literal["preserve", "prepend"] = "prepend",
    ) -> AttackTechniqueSeedGroup:
        """
        Build a technique group from conversation messages.

        This supports techniques that generate reusable teaching or priming
        messages programmatically before a generic attack sends each objective.

        Args:
            messages (list[Message]): Conversation messages to convert into technique seeds.
            starting_sequence (int): Sequence number assigned to the first message.
                Prompt sequences are usually normalized when the technique is merged into an
                ``AttackSeedGroup``. If the merged group contains a ``SeedSimulatedConversation``,
                prompt sequences are preserved, so choose a starting value outside that simulated
                conversation's sequence range. Defaults to 0.
            insertion_index (int | None): Where to insert the technique when merging
                into a ``AttackSeedGroup``. Defaults to None.
            prompt_placement (Literal["preserve", "prepend"]): How to place prompts
                when merging into a ``AttackSeedGroup``. Defaults to ``"prepend"``.

        Returns:
            AttackTechniqueSeedGroup: A group containing general-technique prompts.
        """
        seed_prompts = SeedPrompt.from_messages(messages=messages, starting_sequence=starting_sequence)
        for seed_prompt in seed_prompts:
            seed_prompt.is_general_technique = True

        return cls(
            seeds=seed_prompts,
            insertion_index=insertion_index,
            prompt_placement=prompt_placement,
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
