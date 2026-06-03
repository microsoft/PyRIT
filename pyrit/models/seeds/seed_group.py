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
from collections import defaultdict
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.models.messages.message import Message
from pyrit.models.messages.message_piece import MessagePiece
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_objective import SeedObjective
from pyrit.models.seeds.seed_prompt import SeedPrompt
from pyrit.models.seeds.seed_simulated_conversation import SeedSimulatedConversation

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Polymorphic union of seed types that can appear inside a SeedGroup. The discriminator
# field ``seed_type`` is set per-leaf-class so Pydantic dispatches to the correct constructor
# during validation. Exported so SeedDataset (and any future container) can reuse the same
# tagged union for its own ``seeds`` field.
SeedUnion = Annotated[
    SeedPrompt | SeedObjective | SeedSimulatedConversation,
    Field(discriminator="seed_type"),
]

# Fields that only exist on prompt-type seeds. They are stripped from non-prompt seed dicts so
# dataset/group-level defaults (e.g. ``data_type: image_path``) don't bleed in and trip
# ``extra="forbid"`` on the leaf class. Shared with SeedDataset, which imports it from here.
PROMPT_ONLY_SEED_KEYS = ("data_type", "role", "sequence", "parameters")


class SeedGroup(BaseModel):
    """
    A container for grouping prompts that need to be sent together.

    This class handles:
    - Grouping of SeedPrompt, SeedObjective, and SeedSimulatedConversation
    - Consistent group IDs and roles across seeds
    - Prepended conversation and next message extraction
    - Validation of sequence overlaps between SeedPrompts and SeedSimulatedConversation

    All prompts in the group share the same `prompt_group_id`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    seeds: list[SeedUnion]

    @model_validator(mode="before")
    @classmethod
    def _coerce_seeds(cls, data: Any) -> Any:
        """
        Normalize dict seed inputs so the polymorphic discriminator can dispatch.

        Concrete Seed instances pass through; dicts are tagged with a default
        ``seed_type="prompt"`` when missing and have the construction-time
        ``is_jinja_template`` flag propagated in. ``data_type`` is stripped for
        non-prompt seeds because they narrow it to ``Literal["text"]``; dataset-level
        defaults must not bleed into them.

        Returns:
            The data with normalized ``seeds`` (passes through unchanged if not a dict).

        Raises:
            ValueError: If the group has no seeds or a seed has an unsupported type.
        """
        if not isinstance(data, dict):
            return data

        data = dict(data)
        is_jinja_template = data.pop("is_jinja_template", False)
        raw_seeds = data.get("seeds")
        if not raw_seeds:
            raise ValueError("SeedGroup cannot be empty.")

        normalized: list[Any] = []
        for seed in raw_seeds:
            if isinstance(seed, Seed):
                normalized.append(seed)
                continue
            if not isinstance(seed, dict):
                raise ValueError(f"Invalid seed type: {type(seed)}")
            seed = dict(seed)
            seed.setdefault("seed_type", "prompt")
            seed["is_jinja_template"] = is_jinja_template
            if seed["seed_type"] == "prompt":
                seed.setdefault("role", "user")
            else:
                # Non-prompt seeds narrow data_type to Literal["text"] and don't have
                # role/sequence/parameters fields. Drop them so dataset/group-level
                # defaults don't bleed in and trip extra="forbid".
                for prompt_only in PROMPT_ONLY_SEED_KEYS:
                    seed.pop(prompt_only, None)
            normalized.append(seed)

        data["seeds"] = normalized
        return data

    @model_validator(mode="after")
    def _finalize(self) -> SeedGroup:
        """
        Validate the group and reorder seeds into canonical order.

        Canonical order is: objective, simulated_conversation, then prompts sorted by sequence.

        Returns:
            SeedGroup: The validated, reordered group.
        """
        self._check_invariants()

        objective = self._get_objective()
        simulated_conv = self._get_simulated_conversation()
        sorted_prompts = sorted(self.prompts, key=lambda p: p.sequence if p.sequence is not None else 0)

        new_seeds: list[SeedUnion] = []
        if objective:
            new_seeds.append(objective)
        if simulated_conv:
            new_seeds.append(simulated_conv)
        new_seeds.extend(sorted_prompts)
        self.seeds = new_seeds
        return self

    # =========================================================================
    # Validation
    # =========================================================================

    def _check_invariants(self) -> None:
        """
        Validate the seed group state.

        Renamed from ``validate`` because that name shadows ``BaseModel.validate`` and
        would silently return ``None`` instead of constructing when called on the class.
        Subclasses override this hook to add stronger invariants.

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
        """
        Ensure at most one objective is present.

        Raises:
            ValueError: If more than one SeedObjective exists.

        """
        if len([s for s in self.seeds if isinstance(s, SeedObjective)]) > 1:
            raise ValueError("SeedGroup can only have one objective.")

    def _enforce_max_one_simulated_conversation(self) -> None:
        """
        Ensure at most one simulated conversation is present.

        Raises:
            ValueError: If more than one SeedSimulatedConversation exists.

        """
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
        if len(existing_group_ids) == 1:
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

    def _get_objective(self) -> SeedObjective | None:
        """
        Get the objective seed if present.

        Returns:
            SeedObjective | None: Objective seed when available; otherwise None.

        """
        for seed in self.seeds:
            if isinstance(seed, SeedObjective):
                return seed
        return None

    def _get_simulated_conversation(self) -> SeedSimulatedConversation | None:
        """
        Get the simulated conversation seed if present.

        Returns:
            SeedSimulatedConversation | None: Simulated conversation seed when available; otherwise None.

        """
        for seed in self.seeds:
            if isinstance(seed, SeedSimulatedConversation):
                return seed
        return None

    @property
    def prompts(self) -> Sequence[SeedPrompt]:
        """Get all SeedPrompt instances from this group."""
        return [seed for seed in self.seeds if isinstance(seed, SeedPrompt)]

    @property
    def objective(self) -> SeedObjective | None:
        """Get the objective for this group."""
        return self._get_objective()

    @property
    def harm_categories(self) -> list[str]:
        """
        Returns a deduplicated list of all harm categories from all seeds.

        Returns:
            List of harm categories with duplicates removed.

        """
        categories: list[str] = []
        for seed in self.seeds:
            if seed.harm_categories:
                categories.extend(seed.harm_categories)
        return list(set(categories))

    # =========================================================================
    # Simulated Conversation
    # =========================================================================

    @property
    def simulated_conversation_config(self) -> SeedSimulatedConversation | None:
        """Get the simulated conversation configuration if set."""
        return self._get_simulated_conversation()

    @property
    def has_simulated_conversation(self) -> bool:
        """Check if this group uses simulated conversation generation."""
        return self._get_simulated_conversation() is not None

    # =========================================================================
    # Message Extraction
    # =========================================================================

    @property
    def prepended_conversation(self) -> list[Message] | None:
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
        return self._prompts_to_messages(list(self.prompts))

    @property
    def next_message(self) -> Message | None:
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
    def user_messages(self) -> list[Message]:
        """
        Returns all prompts as user Messages, one per sequence.

        Returns:
            All user messages in sequence order, or empty list if no prompts.

        """
        if not self.prompts:
            return []

        return self._prompts_to_messages(list(self.prompts))

    def _get_last_sequence_role(self) -> str | None:
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

    def _prompts_to_messages(self, prompts: Sequence[SeedPrompt]) -> list[Message]:
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
        Render seed values as templates with provided parameters.

        Args:
            kwargs: Key-value pairs to replace in seed values.

        """
        for seed in self.seeds:
            seed.value = seed.render_template_value(**kwargs)

    def is_single_turn(self) -> bool:
        """
        Check if this is a single-turn group (single request without objective).

        Returns:
            bool: True when the group is a single request and has no objective.

        """
        return self.is_single_request() and not self.objective

    def is_single_request(self) -> bool:
        """
        Check if all prompts are in a single sequence.

        Returns:
            bool: True when all prompts share one sequence number.

        """
        unique_sequences = {prompt.sequence for prompt in self.prompts}
        return len(unique_sequences) == 1

    def is_single_part_single_text_request(self) -> bool:
        """
        Check if this is a single text prompt.

        Returns:
            bool: True when there is exactly one prompt and it is text.

        """
        return len(self.prompts) == 1 and self.prompts[0].data_type == "text"

    def __repr__(self) -> str:
        """
        Return a concise representation of the seed group.

        Returns:
            str: Seed group summary string.

        """
        sim_info = " (simulated)" if self.has_simulated_conversation else ""
        return f"<SeedGroup(seeds={len(self.seeds)}{sim_info})>"
