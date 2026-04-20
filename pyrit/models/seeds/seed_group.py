# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedGroup - Container for grouping seeds together.

Provides functionality for grouping prompts, objectives, and simulated conversation
configurations together with consistent group IDs and roles.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Union

from pyrit.common.yaml_loadable import YamlLoadable
from pyrit.models.message import Message
from pyrit.models.message_piece import MessagePiece
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation

logger = logging.getLogger(__name__)


class SeedGroup(YamlLoadable):
    """
    A container for grouping prompts that need to be sent together.

    This class handles:
    - Grouping of SeedPrompt, SeedObjective, and SeedSimulatedConversation
    - Consistent group IDs and roles across seeds
    - Prepended conversation and next message extraction
    - Validation of sequence overlaps between SeedPrompts and SeedSimulatedConversation

    All prompts in the group share the same `prompt_group_id`.
    """

    seeds: List[Seed]

    def __init__(
        self,
        *,
        seeds: Sequence[Union[Seed, Dict[str, Any]]],
    ):
        """
        Initialize a SeedGroup.

        Args:
            seeds: Sequence of seeds. Can include:
                - SeedObjective (or dict with seed_type="objective")
                - SeedSimulatedConversation (or dict with seed_type="simulated_conversation")
                - SeedPrompt for prompts (or dict with seed_type="prompt" or no seed_type)
                Note: is_objective and is_simulated_conversation are deprecated since 0.13.0.

        Raises:
            ValueError: If seeds is empty.
            ValueError: If multiple objectives are provided.
            ValueError: If SeedPrompt sequences overlap with SeedSimulatedConversation range.
        """
        if not seeds:
            raise ValueError("SeedGroup cannot be empty.")

        self.seeds = []
        for seed in seeds:
            if isinstance(seed, Seed):
                self.seeds.append(seed)
            elif isinstance(seed, dict):
                # Support new seed_type field with backward compatibility for deprecated fields
                seed_type = seed.pop("seed_type", None)
                is_objective = seed.pop("is_objective", False)

                if is_objective:
                    warnings.warn(
                        "is_objective is deprecated since 0.13.0. Use seed_type='objective' instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    # Only error if seed_type is explicitly set to a conflicting value
                    if seed_type is not None and seed_type != "objective":
                        raise ValueError("Conflicting seed_type and is_objective values.")

                if seed_type == "simulated_conversation":
                    # SeedSimulatedConversation doesn't use data_type (always text)
                    seed.pop("data_type", None)
                    self.seeds.append(SeedSimulatedConversation.from_dict(seed))
                elif seed_type == "objective" or (seed_type is None and is_objective):
                    # SeedObjective doesn't use data_type (always text)
                    seed.pop("data_type", None)
                    self.seeds.append(SeedObjective(**seed))
                else:
                    self.seeds.append(SeedPrompt(**seed))
            else:
                raise ValueError(f"Invalid seed type: {type(seed)}")

        # Validate and normalize the seeds
        self.validate()

        # Extract simulated conversation config
        self._simulated_conversation_config = self._get_simulated_conversation()

        # Reconstruct seeds in canonical order: objective, simulated_conversation, sorted prompts
        objective = self._get_objective()
        simulated_conv = self._simulated_conversation_config
        sorted_prompts = sorted(self.prompts, key=lambda p: p.sequence if p.sequence is not None else 0)

        self.seeds = []
        if objective:
            self.seeds.append(objective)
        if simulated_conv:
            self.seeds.append(simulated_conv)
        self.seeds.extend(sorted_prompts)

    # =========================================================================
    # Validation
    # =========================================================================

    def validate(self) -> None:
        """
        Validate the seed group state.

        This method can be called after external modifications to seeds
        to ensure the group remains in a valid state. It is automatically
        called during initialization.

        Raises:
            ValueError: If validation fails.
        """
        if not self.seeds:
            raise ValueError("SeedGroup cannot be empty.")
        self._enforce_consistent_group_id()
        self._enforce_consistent_role()
        self._enforce_max_one_objective()
        self._enforce_max_one_simulated_conversation()
        self._enforce_no_sequence_overlap_with_simulated()

    def _enforce_max_one_objective(self) -> None:
        """Ensure at most one objective is present."""
        if len([s for s in self.seeds if isinstance(s, SeedObjective)]) > 1:
            raise ValueError("SeedGroup can only have one objective.")

    def _enforce_max_one_simulated_conversation(self) -> None:
        """Ensure at most one simulated conversation is present."""
        if len([s for s in self.seeds if isinstance(s, SeedSimulatedConversation)]) > 1:
            raise ValueError("SeedGroup can only have one simulated conversation.")

    def _enforce_consistent_group_id(self) -> None:
        """
        Ensure all seeds share the same group ID.

        If any seeds have a group ID, all must match. If none have one, assigns a new UUID.

        Raises:
            ValueError: If multiple different group IDs exist.
        """
        existing_group_ids = {seed.prompt_group_id for seed in self.seeds if seed.prompt_group_id is not None}

        if len(existing_group_ids) > 1:
            raise ValueError("Inconsistent group IDs found across seeds.")
        elif len(existing_group_ids) == 1:
            group_id = existing_group_ids.pop()
            for seed in self.seeds:
                seed.prompt_group_id = group_id
        else:
            new_group_id = uuid.uuid4()
            for seed in self.seeds:
                seed.prompt_group_id = new_group_id

    def _enforce_consistent_role(self) -> None:
        """
        Ensure all prompts in a sequence have consistent roles.

        Raises:
            ValueError: If roles are inconsistent within a sequence.
            ValueError: If no roles are set in a multi-sequence group.
        """
        grouped_prompts = defaultdict(list)
        for prompt in self.prompts:
            grouped_prompts[prompt.sequence].append(prompt)

        num_sequences = len(grouped_prompts)
        for sequence, prompts in grouped_prompts.items():
            roles = {prompt.role for prompt in prompts if prompt.role is not None}
            if not roles and num_sequences > 1:
                raise ValueError(
                    f"No roles set for sequence {sequence} in a multi-sequence group. "
                    "Please ensure at least one prompt within a sequence has an assigned role."
                )
            if len(roles) > 1:
                raise ValueError(f"Inconsistent roles found for sequence {sequence}: {roles}")
            role = roles.pop() if roles else "user"
            for prompt in prompts:
                prompt.role = role

    def _enforce_no_sequence_overlap_with_simulated(self) -> None:
        """
        Ensure SeedPrompt sequences don't overlap with SeedSimulatedConversation range.

        When a SeedSimulatedConversation is present, it will generate turns that occupy
        sequence numbers [config.sequence, config.sequence + config.num_turns * 2 - 1].
        SeedPrompts must not have sequences that fall within this range.

        Raises:
            ValueError: If any SeedPrompt sequence overlaps with the simulated range.
        """
        simulated_config = self._get_simulated_conversation()
        if simulated_config is None:
            return

        simulated_range = simulated_config.sequence_range

        for prompt in self.prompts:
            if prompt.sequence in simulated_range:
                raise ValueError(
                    f"SeedPrompt sequence {prompt.sequence} overlaps with SeedSimulatedConversation "
                    f"range {list(simulated_range)}. Adjust the SeedPrompt sequence or the "
                    f"SeedSimulatedConversation sequence/num_turns to avoid overlap."
                )

    # =========================================================================
    # Seed Accessors
    # =========================================================================

    def _get_objective(self) -> Optional[SeedObjective]:
        """Get the objective seed if present."""
        for seed in self.seeds:
            if isinstance(seed, SeedObjective):
                return seed
        return None

    def _get_simulated_conversation(self) -> Optional[SeedSimulatedConversation]:
        """Get the simulated conversation seed if present."""
        for seed in self.seeds:
            if isinstance(seed, SeedSimulatedConversation):
                return seed
        return None

    @property
    def prompts(self) -> Sequence[SeedPrompt]:
        """Get all SeedPrompt instances from this group."""
        return [seed for seed in self.seeds if isinstance(seed, SeedPrompt)]

    @property
    def objective(self) -> Optional[SeedObjective]:
        """Get the objective for this group."""
        return self._get_objective()

    @property
    def harm_categories(self) -> List[str]:
        """
        Returns a deduplicated list of all harm categories from all seeds.

        Returns:
            List of harm categories with duplicates removed.
        """
        categories: List[str] = []
        for seed in self.seeds:
            if seed.harm_categories:
                categories.extend(seed.harm_categories)
        return list(set(categories))

    # =========================================================================
    # Simulated Conversation
    # =========================================================================

    @property
    def simulated_conversation_config(self) -> Optional[SeedSimulatedConversation]:
        """Get the simulated conversation configuration if set."""
        return self._simulated_conversation_config

    @property
    def has_simulated_conversation(self) -> bool:
        """Check if this group uses simulated conversation generation."""
        return self._simulated_conversation_config is not None

    # =========================================================================
    # Message Extraction
    # =========================================================================

    @property
    def prepended_conversation(self) -> Optional[List[Message]]:
        """
        Returns Messages that should be prepended as conversation history.

        Returns all messages except the last user sequence.

        Returns:
            Messages for conversation history, or None if empty.
        """
        if not self.prompts:
            return None

        last_role = self._get_last_sequence_role()
        unique_sequences = sorted({prompt.sequence for prompt in self.prompts})

        if last_role == "user":
            if len(unique_sequences) <= 1:
                return None

            last_sequence = unique_sequences[-1]
            prepended_prompts = [p for p in self.prompts if p.sequence != last_sequence]

            if not prepended_prompts:
                return None

            return self._prompts_to_messages(prepended_prompts)
        else:
            return self._prompts_to_messages(list(self.prompts))

    @property
    def next_message(self) -> Optional[Message]:
        """
        Returns a Message containing only the last turn's prompts if it's a user message.

        Returns:
            Message for the current/last turn if user role, or None otherwise.
        """
        if not self.prompts:
            return None

        last_role = self._get_last_sequence_role()

        if last_role != "user":
            return None

        unique_sequences = sorted({prompt.sequence for prompt in self.prompts})
        last_sequence = unique_sequences[-1]
        current_turn_prompts = [p for p in self.prompts if p.sequence == last_sequence]

        if not current_turn_prompts:
            return None

        messages = self._prompts_to_messages(current_turn_prompts)
        return messages[0] if messages else None

    @property
    def user_messages(self) -> List[Message]:
        """
        Returns all prompts as user Messages, one per sequence.

        Returns:
            All user messages in sequence order, or empty list if no prompts.
        """
        if not self.prompts:
            return []

        return self._prompts_to_messages(list(self.prompts))

    def _get_last_sequence_role(self) -> Optional[str]:
        """
        Get the role of the last sequence.

        Returns:
            The role of the last sequence, or None if no prompts exist.
        """
        if not self.prompts:
            return None

        unique_sequences = sorted({prompt.sequence for prompt in self.prompts})
        last_sequence = unique_sequences[-1]
        last_sequence_prompts = [p for p in self.prompts if p.sequence == last_sequence]

        return last_sequence_prompts[0].role if last_sequence_prompts else None

    def _prompts_to_messages(self, prompts: Sequence[SeedPrompt]) -> List[Message]:
        """
        Convert a sequence of SeedPrompts to Messages.

        Groups prompts by sequence number and creates one Message per sequence.

        Args:
            prompts: The prompts to convert.

        Returns:
            Messages created from the prompts.
        """
        sequence_groups = defaultdict(list)
        for prompt in prompts:
            sequence_groups[prompt.sequence].append(prompt)

        messages = []
        for sequence in sorted(sequence_groups.keys()):
            sequence_prompts = sequence_groups[sequence]

            message_pieces = []
            for prompt in sequence_prompts:
                role = prompt.role or "user"
                if role == "assistant":
                    role = "simulated_assistant"

                piece = MessagePiece(
                    role=role,
                    original_value=prompt.value,
                    original_value_data_type=prompt.data_type or "text",
                    prompt_target_identifier=None,
                    conversation_id=str(prompt.prompt_group_id),
                    sequence=sequence,
                    prompt_metadata=prompt.metadata,
                )
                message_pieces.append(piece)

            messages.append(Message(message_pieces=message_pieces))

        return messages

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def render_template_value(self, **kwargs: Any) -> None:
        """
        Renders seed values as templates with provided parameters.

        Args:
            kwargs: Key-value pairs to replace in seed values.
        """
        for seed in self.seeds:
            seed.value = seed.render_template_value(**kwargs)

    def is_single_turn(self) -> bool:
        """Check if this is a single-turn group (single request without objective)."""
        return self.is_single_request() and not self.objective

    def is_single_request(self) -> bool:
        """Check if all prompts are in a single sequence."""
        unique_sequences = {prompt.sequence for prompt in self.prompts}
        return len(unique_sequences) == 1

    def is_single_part_single_text_request(self) -> bool:
        """Check if this is a single text prompt."""
        return len(self.prompts) == 1 and self.prompts[0].data_type == "text"

    def __repr__(self) -> str:
        sim_info = " (simulated)" if self.has_simulated_conversation else ""
        return f"<SeedGroup(seeds={len(self.seeds)}{sim_info})>"
