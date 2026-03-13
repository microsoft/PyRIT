# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility functions for generating simulated conversations using adversarial chat.

These utilities help create prepended_conversation content by running an adversarial chat
against a simulated (compliant) target before executing the actual attack.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import Message, SeedPrompt, SeedSimulatedConversation

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.prompt_target import PromptChatTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


async def generate_simulated_conversation_async(
    *,
    objective: str,
    adversarial_chat: PromptChatTarget,
    objective_scorer: TrueFalseScorer,
    num_turns: int = 3,
    starting_sequence: int = 0,
    adversarial_chat_system_prompt_path: Union[str, Path],
    simulated_target_system_prompt_path: Optional[Union[str, Path]] = None,
    next_message_system_prompt_path: Optional[Union[str, Path]] = None,
    attack_converter_config: Optional[AttackConverterConfig] = None,
    memory_labels: Optional[dict[str, str]] = None,
) -> list[SeedPrompt]:
    """
    Generate a simulated conversation between an adversarial chat and a target.

    This utility runs a RedTeamingAttack with `score_last_turn_only=True` against a simulated
    target (the same LLM as adversarial_chat, optionally configured with a system prompt).
    The resulting conversation is returned as a list of SeedPrompts that can be merged with
    other SeedPrompts in a SeedGroup for use as `prepended_conversation` and `next_message`.

    Use cases:
    - Creating role-play scenarios dynamically (e.g., movie script, video game)
    - Establishing conversational context before attacking a real target
    - Generating multi-turn jailbreak setups without hardcoded responses

    Args:
        objective: The objective for the adversarial chat to work toward.
        adversarial_chat: The adversarial LLM that generates attack prompts.
            This same LLM is also used as the simulated target.
        objective_scorer: Scorer to evaluate the final turn.
        num_turns: Number of conversation turns to generate. Defaults to 3.
        starting_sequence: The starting sequence number for the generated SeedPrompts.
            Each message gets an incrementing sequence number. Defaults to 0.
        adversarial_chat_system_prompt_path: Path to the system prompt for the adversarial chat.
        simulated_target_system_prompt_path: Path to the system prompt for the simulated target.
            If None, no system prompt is used for the simulated target.
        next_message_system_prompt_path: Optional path to a system prompt for generating
            a final user message. If provided, after the simulated conversation, a single
            LLM call generates a user message that attempts to get the target to fulfill
            the objective in their next response. The prompt template receives `objective`
            and `conversation_so_far` parameters.
        attack_converter_config: Converter configuration for the attack. Defaults to None.
        memory_labels: Labels to associate with the conversation in memory. Defaults to None.

    Returns:
        List of SeedPrompts representing the generated conversation, with sequence numbers
        starting from `starting_sequence` and incrementing by 1 for each message.
        User messages have role="user", assistant messages have role="assistant".
        If next_message_system_prompt_path is provided, the last message will be a user message
        generated to elicit the objective fulfillment.

    Raises:
        ValueError: If num_turns is not a positive integer.
    """
    # Use the same LLM for both adversarial chat and simulated target
    # They get different system prompts to play different roles
    simulated_target = adversarial_chat
    if num_turns <= 0:
        raise ValueError("num_turns must be a positive integer")

    # Load and configure simulated target system prompt using centralized validation
    # Returns None if no path is provided (no system prompt for simulated target)
    simulated_target_system_prompt = SeedSimulatedConversation.load_simulated_target_system_prompt(
        objective=objective,
        num_turns=num_turns,
        simulated_target_system_prompt_path=simulated_target_system_prompt_path,
    )

    # Create adversarial config for the simulation
    adversarial_config = AttackAdversarialConfig(
        target=adversarial_chat,
        system_prompt_path=adversarial_chat_system_prompt_path,
    )

    # Create scoring config
    scoring_config = AttackScoringConfig(
        objective_scorer=objective_scorer,
        use_score_as_feedback=False,  # Don't need feedback for last-turn-only scoring
    )

    # Create the RedTeamingAttack with simulated target and score_last_turn_only
    attack = RedTeamingAttack(
        objective_target=simulated_target,
        attack_adversarial_config=adversarial_config,
        attack_converter_config=attack_converter_config,
        attack_scoring_config=scoring_config,
        max_turns=num_turns,
        score_last_turn_only=True,
    )

    # Execute the simulated attack
    logger.info(f"Generating {num_turns}-turn simulated conversation for objective: {objective[:50]}...")

    # Build prepended_conversation - only include system message if prompt is provided
    prepended_conversation: list[Message] = []
    if simulated_target_system_prompt:
        prepended_conversation.append(Message.from_system_prompt(simulated_target_system_prompt))

    result = await attack.execute_async(
        objective=objective,
        prepended_conversation=prepended_conversation if prepended_conversation else None,
        memory_labels=memory_labels,
    )

    # Extract the conversation from memory and filter for prepended_conversation use
    memory = CentralMemory.get_memory_instance()
    raw_messages = list(memory.get_conversation(conversation_id=result.conversation_id))

    # Filter out system messages - keep the actual conversation
    # System prompts are set separately on each target during attack execution
    conversation_messages: list[Message] = [msg for msg in raw_messages if msg.api_role != "system"]

    # If next_message_system_prompt_path is provided, generate a final user message
    if next_message_system_prompt_path:
        next_message = await _generate_next_message_async(
            objective=objective,
            conversation_messages=conversation_messages,
            adversarial_chat=adversarial_chat,
            next_message_system_prompt_path=next_message_system_prompt_path,
        )
        conversation_messages.append(next_message)

    # Convert to SeedPrompts for the return value
    seed_prompts = SeedPrompt.from_messages(conversation_messages, starting_sequence=starting_sequence)

    logger.info(
        f"Generated simulated conversation with {len(seed_prompts)} SeedPrompts "
        f"(starting_sequence={starting_sequence}, outcome: {result.outcome.name})"
    )

    return seed_prompts


async def _generate_next_message_async(
    *,
    objective: str,
    conversation_messages: list[Message],
    adversarial_chat: PromptChatTarget,
    next_message_system_prompt_path: Union[str, Path],
) -> Message:
    """
    Generate a single next message using the adversarial chat LLM.

    This function formats the conversation so far and uses a system prompt to generate
    a user message that attempts to get the target to fulfill the objective.

    Args:
        objective: The objective to work toward.
        conversation_messages: The conversation generated so far as Messages.
        adversarial_chat: The LLM to use for generation.
        next_message_system_prompt_path: Path to the system prompt template.

    Returns:
        Message: The generated next message.

    Raises:
        ValueError: If no response is received from the adversarial chat.
    """
    # Format the conversation context using ConversationContextNormalizer
    normalizer = ConversationContextNormalizer()
    conversation_context = await normalizer.normalize_string_async(conversation_messages)

    # Load and render the system prompt template
    template = SeedPrompt.from_yaml_with_required_parameters(
        template_path=next_message_system_prompt_path,
        required_parameters=["objective", "conversation_context"],
        error_message="Next message system prompt must have objective and conversation_context parameters",
    )

    system_prompt = template.render_template_value(
        objective=objective,
        conversation_context=conversation_context,
    )

    # Use the adversarial chat to generate the next message
    # Create a simple user message asking for generation
    request_message = Message.from_prompt(
        role="user",
        prompt="Generate the next user message based on the instructions above.",
    )

    # Set the system prompt on the target
    adversarial_chat.set_system_prompt(
        system_prompt=system_prompt,
        conversation_id=request_message.conversation_id,
    )

    responses: list[Message] = await adversarial_chat.send_prompt_async(message=request_message)

    if not responses:
        raise ValueError("No response received from adversarial chat when generating next message")

    # Change the role from assistant to user since this is a user message to be sent to the target
    response = responses[0]
    for piece in response.message_pieces:
        piece.role = "user"

    return response
