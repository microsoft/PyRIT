# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.exceptions import AdversarialChatResponseBlockedException
from pyrit.models import (
    AttackSeedGroup,
    ConversationReference,
    ConversationType,
    Message,
    ScoringExpectation,
    SeedGroup,
    SeedPrompt,
)

if TYPE_CHECKING:
    import uuid

    from pyrit.models import SeedUnion
    from pyrit.prompt_target import PromptTarget
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
    next_message: Message | None = None

    # Conversation that is automatically prepended to the target model
    prepended_conversation: list[Message] | None = None

    # Additional labels that can be applied to the prompts throughout the attack
    memory_labels: dict[str, str] | None = field(default_factory=dict)
    operation_id: uuid.UUID | None = None

    # Harm categories targeted by this attack, derived from the seed group's
    # seeds. Stamped onto the produced AttackResult.
    targeted_harm_categories: list[str] = field(default_factory=list)

    # Conversations used to prepare this attack before its context was created.
    source_conversations: frozenset[ConversationReference] = field(default_factory=frozenset)

    # Per-execution scoring criteria, supplied directly or by seed preparation.
    expectation: ScoringExpectation | None = None

    def __str__(self) -> str:
        """Return a nicely formatted string representation of the attack parameters."""
        lines = [f"{self.__class__.__name__}:"]
        lines.append(f"  objective: {self.objective}")
        if self.expectation is not None:
            lines.append(f"  expectation: {self.expectation}")

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
        cls: type[AttackParamsT],
        *,
        seed_group: AttackSeedGroup,
        adversarial_chat: PromptTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        objective_target: PromptTarget | None = None,
        **overrides: Any,
    ) -> AttackParamsT:
        """
        Create an AttackParameters instance from a AttackSeedGroup.

        Extracts standard fields from the seed group and applies any overrides.
        If the seed_group has a simulated conversation config,
        generates the simulated conversation using the provided adversarial_chat and scorer.
        If it has a simulated tool history config, the adversarial_chat writes a tool-call
        history against the tools the objective_target reports. When an objective_target that
        reports its tools is given, every function call in the prepended history must use one
        of those tools with arguments its schema accepts.

        Args:
            seed_group: The seed attack group to extract parameters from.
            adversarial_chat: The adversarial chat target for generating simulated history.
                Required if seed_group has a simulated conversation or tool history config.
            objective_scorer: The scorer for evaluating simulated conversations.
                Required if seed_group has a simulated conversation config.
            objective_target: The target the attack sends to, whose tools the prepended history
                must call. Required if seed_group has a simulated tool history config.
            **overrides: Field overrides to apply. Must be valid fields for this params type.

        Returns:
            An instance of this AttackParameters type.

        Raises:
            AdversarialChatResponseBlockedException: If simulated-conversation preparation was
                blocked and this parameter type cannot represent a completed preparation failure.
            TypeError: If ``seed_group`` is not a ``AttackSeedGroup``.
            ValueError: If overrides contain invalid fields, if seed_group has simulated
                history but a required dependency is not provided, if the objective target
                reports no tools for a simulated tool history, or if the prepended history calls
                a tool the objective target does not offer.
        """
        # Import here to avoid circular imports
        from pyrit.executor.attack.multi_turn.simulated_conversation import generate_simulated_conversation_async

        if not isinstance(seed_group, AttackSeedGroup):
            raise TypeError(
                f"seed_group must be a AttackSeedGroup, got {type(seed_group).__name__}. "
                "Plain SeedGroup does not enforce the 'exactly one objective' invariant required for an attack."
            )

        # Get valid field names for this params type
        valid_fields = {f.name for f in dataclasses.fields(cls)}

        # Validate overrides don't contain invalid fields
        invalid_fields = set(overrides.keys()) - valid_fields
        if invalid_fields:
            raise ValueError(
                f"{cls.__name__} does not accept parameters: {invalid_fields}. Accepted parameters: {valid_fields}"
            )

        # AttackSeedGroup's Pydantic validator guarantees exactly one objective is present.
        assert seed_group.objective is not None

        # Build params dict, only including fields this class accepts
        params: dict[str, Any] = {}

        if "objective" in valid_fields:
            params["objective"] = seed_group.objective.value

        if "expectation" in valid_fields:
            params["expectation"] = AttackParameters._resolve_seed_expectation(
                seed_group=seed_group, overrides=overrides
            )

        if "memory_labels" in valid_fields:
            params["memory_labels"] = {}

        if "targeted_harm_categories" in valid_fields:
            params["targeted_harm_categories"] = list(seed_group.harm_categories)

        if "source_conversations" in valid_fields:
            params["source_conversations"] = frozenset()

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

            simulated_result = await generate_simulated_conversation_async(
                objective=seed_group.objective.value,
                adversarial_chat=adversarial_chat,
                objective_scorer=objective_scorer,
                num_turns=simulated_conversation_config.num_turns,
                starting_sequence=simulated_conversation_config.sequence,
                adversarial_chat_system_prompt=simulated_conversation_config.adversarial_chat_system_prompt,
                simulated_target_system_prompt=simulated_conversation_config.simulated_target_system_prompt,
                next_message_system_prompt=simulated_conversation_config.next_message_system_prompt,
            )
            simulated_prompts = simulated_result.seed_prompts
            if "source_conversations" in valid_fields:
                params["source_conversations"] = frozenset(simulated_result.related_conversations)
            if simulated_result.preparation_failure is not None:
                if "preparation_failure" not in valid_fields:
                    raise AdversarialChatResponseBlockedException(message=simulated_result.preparation_failure.reason)
                params["preparation_failure"] = simulated_result.preparation_failure

            # Merge simulated prompts with existing static prompts from the seed_group
            all_prompts: list[SeedUnion] = [*seed_group.prompts, *simulated_prompts]

            # Create a temporary prompts-only SeedGroup for extraction
            # This group contains only prompts (no objective, no simulated config)
            # and will use the standard sequence-based logic for prepended_conversation/next_message
            if all_prompts:
                extraction_group = SeedGroup(seeds=all_prompts)

        if seed_group.simulated_tool_history_config is not None:
            history, reference = await _generate_tool_history_async(
                seed_group=seed_group, adversarial_chat=adversarial_chat, objective_target=objective_target
            )
            if "source_conversations" in valid_fields:
                params["source_conversations"] = frozenset({reference})
            extraction_group = SeedGroup(seeds=[*seed_group.prompts, *history])

        if objective_target is not None:
            await _check_prepended_tool_calls_async(
                history=extraction_group.prepended_conversation or [], objective_target=objective_target
            )

        # Use extraction_group properties for prepended_conversation/next_message
        if "next_message" in valid_fields:
            params["next_message"] = extraction_group.next_message

        if "prepended_conversation" in valid_fields:
            params["prepended_conversation"] = extraction_group.prepended_conversation

        # Apply overrides (already validated above)
        params.update(overrides)

        return cls(**params)

    @staticmethod
    def _resolve_seed_expectation(
        *, seed_group: AttackSeedGroup, overrides: dict[str, Any]
    ) -> ScoringExpectation | None:
        """
        Use an explicit override, including None, before seed-authored criteria.

        Returns:
            ScoringExpectation | None: The selected criteria.

        Raises:
            TypeError: If the override is not a typed expectation or None.
        """
        if "expectation" in overrides:
            expectation = overrides["expectation"]
            if expectation is not None and not isinstance(expectation, ScoringExpectation):
                raise TypeError("expectation must be a ScoringExpectation or None.")
            return expectation
        if seed_group.objective.conditions:
            return seed_group.scoring_expectation
        return None

    @classmethod
    def excluding(cls, *field_names: str) -> type[AttackParameters]:
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
            ReducedParameters = AttackParameters.excluding("next_message", "prepended_conversation")
        """
        # Validate all field names exist
        current_fields = {f.name for f in dataclasses.fields(cls)}
        invalid = set(field_names) - current_fields
        if invalid:
            raise ValueError(f"Cannot exclude non-existent fields: {invalid}. Valid fields: {current_fields}")

        # Build new fields list excluding the specified ones
        new_fields: list[Any] = []
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

        async def from_seed_group_wrapper_async(
            c: Any,
            /,
            *,
            seed_group: Any,
            adversarial_chat: Any = None,
            objective_scorer: Any = None,
            objective_target: Any = None,
            **ov: Any,
        ) -> Any:
            return await original_method(
                c,
                seed_group=seed_group,
                adversarial_chat=adversarial_chat,
                objective_scorer=objective_scorer,
                objective_target=objective_target,
                **ov,
            )

        new_cls.from_seed_group_async = classmethod(from_seed_group_wrapper_async)  # type: ignore[ty:unresolved-attribute]

        return new_cls  # type: ignore[ty:invalid-return-type]


async def _generate_tool_history_async(
    *,
    seed_group: AttackSeedGroup,
    adversarial_chat: PromptTarget | None,
    objective_target: PromptTarget | None,
) -> tuple[list[SeedPrompt], ConversationReference]:
    """
    Write a tool-call history for the seed group's objective against the objective target's tools.

    Returns:
        tuple[list[SeedPrompt], ConversationReference]: The history, placed at the configured
            sequence, and the adversarial conversation that wrote it.

    Raises:
        ValueError: If a dependency is missing or the objective target reports no tools.
    """
    # Import here to avoid circular imports
    from pyrit.executor.promptgen import SimulatedToolHistoryGenerator

    config = seed_group.simulated_tool_history_config
    assert config is not None and seed_group.objective is not None
    if adversarial_chat is None:
        raise ValueError("adversarial_chat is required when seed_group has a simulated tool history config")
    if objective_target is None:
        raise ValueError("objective_target is required when seed_group has a simulated tool history config")
    tools = await objective_target.get_tool_definitions_async()
    if not tools:
        raise ValueError(
            f"{type(objective_target).__name__} reports no function tools, so no tool history can be written for it. "
            "Use a target that advertises function tools."
        )
    result = await SimulatedToolHistoryGenerator(
        adversarial_chat=adversarial_chat,
        system_prompt=config.adversarial_chat_system_prompt,
        max_attempts=config.max_attempts,
        max_messages=config.max_messages,
    ).execute_async(objective=seed_group.objective, tools=tools, shape=config.shape)
    history = [
        prompt.model_copy(update={"sequence": config.sequence + index}) for index, prompt in enumerate(result.history)
    ]
    reference = ConversationReference(
        conversation_id=result.conversation_id,
        conversation_type=ConversationType.PREPARATION,
        description="simulated tool history generation",
    )
    return history, reference


async def _check_prepended_tool_calls_async(*, history: list[Message], objective_target: PromptTarget) -> None:
    """
    Reject a replayed history whose function calls the objective target could not have made.

    Raises:
        ValueError: If a call names a tool the target does not report, or passes arguments its
            schema rejects. Targets that do not report their tools are not checked.
    """
    # Import here to avoid circular imports
    from pyrit.prompt_target.common.tool_call_history import check_tool_calls

    if not any(
        piece.converted_value_data_type == "function_call" for message in history for piece in message.message_pieces
    ):
        return
    tools = await objective_target.get_tool_definitions_async()
    if tools is None:
        return
    issues = check_tool_calls(messages=history, tools=tools)
    if issues:
        raise ValueError(
            f"The history calls tools that {type(objective_target).__name__} cannot use: " + " ".join(issues)
        )
