# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, TypeVar

from pyrit.models import Message, SeedAttackGroup, SeedGroup

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptChatTarget
    from pyrit.score import TrueFalseScorer

AttackParamsT = TypeVar("AttackParamsT", bound="AttackParameters")


@dataclass(frozen=True)
class AttackParameters:
    """
    Immutable parameters for attack execution.

    This class defines the standard contract for attack parameters. All attacks
    at a given level of the hierarchy share the same parameter signature.

    Attacks that don't accept certain parameters should use the `excluding()` factory
    to create a derived params type without those fields. Attacks that need additional
    parameters should extend this class with new fields.
    """

    # Natural-language description of what the attack tries to achieve (required)
    objective: str

    # Optional message to send to the objective target (overrides objective if provided)
    next_message: Optional[Message] = None

    # Conversation that is automatically prepended to the target model
    prepended_conversation: Optional[List[Message]] = None

    # Additional labels that can be applied to the prompts throughout the attack
    memory_labels: Optional[Dict[str, str]] = field(default_factory=dict)

    def __str__(self) -> str:
        """Return a nicely formatted string representation of the attack parameters."""
        lines = [f"{self.__class__.__name__}:"]
        lines.append(f"  objective: {self.objective}")

        if self.next_message is not None:
            piece_count = len(self.next_message.message_pieces)
            msg_value = self.next_message.get_value()
            # Truncate long messages for display
            if len(msg_value) > 100:
                msg_value = msg_value[:100] + "..."
            lines.append(f"  next_message: ({piece_count} piece(s)) {msg_value}")
        else:
            lines.append("  next_message: None")

        if self.prepended_conversation:
            lines.append(f"  prepended_conversation: {len(self.prepended_conversation)} message(s)")
            for i, msg in enumerate(self.prepended_conversation):
                role = msg.api_role if hasattr(msg, "api_role") else "unknown"
                piece_count = len(msg.message_pieces)
                value = msg.get_value()
                if len(value) > 60:
                    value = value[:60] + "..."
                lines.append(f"    [{i}] {role} ({piece_count} piece(s)): {value}")
        else:
            lines.append("  prepended_conversation: None")

        if self.memory_labels:
            lines.append(f"  memory_labels: {self.memory_labels}")

        return "\n".join(lines)

    @classmethod
    async def from_seed_group_async(
        cls: Type[AttackParamsT],
        *,
        seed_group: SeedAttackGroup,
        adversarial_chat: Optional["PromptChatTarget"] = None,
        objective_scorer: Optional["TrueFalseScorer"] = None,
        **overrides: Any,
    ) -> AttackParamsT:
        """
        Create an AttackParameters instance from a SeedAttackGroup.

        Extracts standard fields from the seed group and applies any overrides.
        If the seed_group has a simulated conversation config,
        generates the simulated conversation using the provided adversarial_chat and scorer.

        Args:
            seed_group: The seed attack group to extract parameters from.
            adversarial_chat: The adversarial chat target for generating simulated conversations.
                Required if seed_group has a simulated conversation config.
            objective_scorer: The scorer for evaluating simulated conversations.
                Required if seed_group has a simulated conversation config.
            **overrides: Field overrides to apply. Must be valid fields for this params type.

        Returns:
            An instance of this AttackParameters type.

        Raises:
            ValueError: If seed_group has no objective or if overrides contain invalid fields.
            ValueError: If seed_group has simulated conversation but adversarial_chat/scorer not provided.
        """
        # Import here to avoid circular imports
        from pyrit.executor.attack.multi_turn.simulated_conversation import (
            generate_simulated_conversation_async,
        )

        # Get valid field names for this params type
        valid_fields = {f.name for f in dataclasses.fields(cls)}

        # Validate overrides don't contain invalid fields
        invalid_fields = set(overrides.keys()) - valid_fields
        if invalid_fields:
            raise ValueError(
                f"{cls.__name__} does not accept parameters: {invalid_fields}. Accepted parameters: {valid_fields}"
            )

        # Validate seed_group state before extracting parameters
        seed_group.validate()

        # SeedAttackGroup validates in __init__ that objective is set
        assert seed_group.objective is not None

        # Build params dict, only including fields this class accepts
        params: Dict[str, Any] = {}

        if "objective" in valid_fields:
            params["objective"] = seed_group.objective.value

        if "memory_labels" in valid_fields:
            params["memory_labels"] = {}

        # Determine which group to use for extracting prepended_conversation/next_message
        extraction_group: SeedGroup = seed_group

        # Handle simulated conversation generation if configured
        if seed_group.has_simulated_conversation:
            simulated_conversation_config = seed_group.simulated_conversation_config
            assert simulated_conversation_config is not None  # Guaranteed by has_simulated_conversation

            if adversarial_chat is None:
                raise ValueError("adversarial_chat is required when seed_group has a simulated conversation config")
            if objective_scorer is None:
                raise ValueError("objective_scorer is required when seed_group has a simulated conversation config")

            # Generate the simulated conversation - returns List[SeedPrompt]
            simulated_prompts = await generate_simulated_conversation_async(
                objective=seed_group.objective.value,
                adversarial_chat=adversarial_chat,
                objective_scorer=objective_scorer,
                num_turns=simulated_conversation_config.num_turns,
                starting_sequence=simulated_conversation_config.sequence,
                adversarial_chat_system_prompt_path=simulated_conversation_config.adversarial_chat_system_prompt_path,
                simulated_target_system_prompt_path=simulated_conversation_config.simulated_target_system_prompt_path,
                next_message_system_prompt_path=simulated_conversation_config.next_message_system_prompt_path,
            )

            # Merge simulated prompts with existing static prompts from the seed_group
            all_prompts = list(seed_group.prompts) + simulated_prompts

            # Create a temporary prompts-only SeedGroup for extraction
            # This group contains only prompts (no objective, no simulated config)
            # and will use the standard sequence-based logic for prepended_conversation/next_message
            if all_prompts:
                extraction_group = SeedGroup(seeds=all_prompts)

        # Use extraction_group properties for prepended_conversation/next_message
        if "next_message" in valid_fields:
            params["next_message"] = extraction_group.next_message

        if "prepended_conversation" in valid_fields:
            params["prepended_conversation"] = extraction_group.prepended_conversation

        # Apply overrides (already validated above)
        params.update(overrides)

        return cls(**params)

    @classmethod
    def excluding(cls, *field_names: str) -> Type["AttackParameters"]:
        """
        Create a new AttackParameters subclass that excludes the specified fields.

        This factory method creates a frozen dataclass without the specified fields.
        The resulting class inherits the `from_seed_group()` behavior and will raise
        if excluded fields are passed as overrides.

        Args:
            *field_names: Names of fields to exclude from the new params type.

        Returns:
            A new AttackParameters subclass without the specified fields.

        Raises:
            ValueError: If any field_name is not a valid field of this class.

        Example:
            RolePlayAttackParameters = AttackParameters.excluding("next_message", "prepended_conversation")
        """
        # Validate all field names exist
        current_fields = {f.name for f in dataclasses.fields(cls)}
        invalid = set(field_names) - current_fields
        if invalid:
            raise ValueError(f"Cannot exclude non-existent fields: {invalid}. Valid fields: {current_fields}")

        # Build new fields list excluding the specified ones
        new_fields: List[Any] = []
        for f in dataclasses.fields(cls):
            if f.name not in field_names:
                # Preserve field defaults
                if f.default is not dataclasses.MISSING:
                    new_fields.append((f.name, f.type, field(default=f.default)))
                elif f.default_factory is not dataclasses.MISSING:
                    new_fields.append((f.name, f.type, field(default_factory=f.default_factory)))
                else:
                    new_fields.append((f.name, f.type))

        # Generate a descriptive class name
        excluded_str = "_".join(sorted(field_names))
        class_name = f"{cls.__name__}Excluding_{excluded_str}"

        # Create the new dataclass WITHOUT inheritance
        # This ensures dataclasses.fields() only returns the new class's fields
        new_cls = dataclasses.make_dataclass(
            class_name,
            new_fields,
            frozen=True,
        )

        # Attach from_seed_group_async that delegates to the parent classmethod
        # We need to call the underlying function with the new class type (c) so that
        # dataclasses.fields(cls) returns only the reduced field set.
        # Access via __dict__ to get the classmethod descriptor and extract __func__.
        _classmethod_descriptor = cls.__dict__["from_seed_group_async"]
        original_method = _classmethod_descriptor.__func__

        async def from_seed_group_async_wrapper(
            c: Any, /, *, seed_group: Any, adversarial_chat: Any = None, objective_scorer: Any = None, **ov: Any
        ) -> Any:
            return await original_method(
                c, seed_group=seed_group, adversarial_chat=adversarial_chat, objective_scorer=objective_scorer, **ov
            )

        new_cls.from_seed_group_async = classmethod(from_seed_group_async_wrapper)  # type: ignore[attr-defined]

        return new_cls
