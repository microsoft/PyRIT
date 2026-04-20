# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from pyrit.common.utils import combine_dict
from pyrit.executor.attack.component.prepended_conversation_config import (
    PrependedConversationConfig,
)
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import ChatMessageRole, Message, MessagePiece, Score
from pyrit.prompt_normalizer.prompt_converter_configuration import (
    PromptConverterConfiguration,
)
from pyrit.prompt_normalizer.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget

if TYPE_CHECKING:
    from pyrit.executor.attack.core import AttackContext

logger = logging.getLogger(__name__)


def mark_messages_as_simulated(messages: Sequence[Message]) -> List[Message]:
    """
    Mark assistant messages as simulated_assistant for traceability.

    This function converts all assistant roles to simulated_assistant in the
    provided messages. This is useful when loading conversations from YAML files
    or other sources where the responses are not from actual targets.

    Args:
        messages (Sequence[Message]): The messages to mark as simulated.

    Returns:
        List[Message]: The same messages with assistant roles converted to simulated_assistant.
            Modifies the messages in place and also returns them for convenience.
    """
    result = list(messages)
    for message in result:
        for piece in message.message_pieces:
            if piece._role == "assistant":
                piece._role = "simulated_assistant"
    return result


def get_adversarial_chat_messages(
    prepended_conversation: List[Message],
    *,
    adversarial_chat_conversation_id: str,
    attack_identifier: Dict[str, str],
    adversarial_chat_target_identifier: Dict[str, str],
    labels: Optional[Dict[str, str]] = None,
) -> List[Message]:
    """
    Transform prepended conversation messages for adversarial chat with swapped roles.

    This function creates new Message objects with swapped roles for use in adversarial
    chat conversations. From the adversarial chat's perspective:
    - "user" messages become "assistant" (prompts it generated)
    - "assistant" messages become "user" (responses it received)
    - System messages are skipped (adversarial chat has its own system prompt)

    All messages receive new UUIDs to distinguish them from the originals.

    Args:
        prepended_conversation: The original conversation messages to transform.
        adversarial_chat_conversation_id: Conversation ID for the adversarial chat.
        attack_identifier: Attack identifier to associate with messages.
        adversarial_chat_target_identifier: Target identifier for the adversarial chat.
        labels: Optional labels to associate with the messages.

    Returns:
        List of transformed messages with swapped roles and new IDs.
    """
    if not prepended_conversation:
        return []

    role_swap: Dict[ChatMessageRole, ChatMessageRole] = {
        "user": "assistant",
        "assistant": "user",
        "simulated_assistant": "user",
    }

    result: List[Message] = []

    for message in prepended_conversation:
        for piece in message.message_pieces:
            # Skip system messages - adversarial chat has its own system prompt
            if piece.api_role == "system":
                continue

            # Create a new piece with swapped role for adversarial chat
            swapped_role = role_swap.get(piece.api_role, piece.api_role)

            adversarial_piece = MessagePiece(
                id=uuid.uuid4(),
                role=swapped_role,
                original_value=piece.original_value,
                converted_value=piece.converted_value,
                original_value_data_type=piece.original_value_data_type,
                converted_value_data_type=piece.converted_value_data_type,
                conversation_id=adversarial_chat_conversation_id,
                attack_identifier=attack_identifier,
                prompt_target_identifier=adversarial_chat_target_identifier,
                labels=labels,
            )

            result.append(adversarial_piece.to_message())

    logger.debug(f"Created {len(result)} adversarial chat messages with swapped roles")
    return result


async def build_conversation_context_string_async(messages: List[Message]) -> str:
    """
    Build a formatted context string from a list of messages.

    This is a convenience function that uses ConversationContextNormalizer
    to format messages into a "Turn N: User/Assistant" format suitable for
    use in system prompts.

    Args:
        messages: The conversation messages to format.

    Returns:
        A formatted string representing the conversation context.
        Returns empty string if no messages provided.
    """
    if not messages:
        return ""
    normalizer = ConversationContextNormalizer()
    return await normalizer.normalize_string_async(messages)


def get_prepended_turn_count(prepended_conversation: Optional[List[Message]]) -> int:
    """
    Count the number of turns (assistant responses) in a prepended conversation.

    This is used to offset iteration counts so that executed_turns reflects
    the total conversation depth including prepended messages.

    Args:
        prepended_conversation: The prepended conversation messages, or None.

    Returns:
        int: The number of assistant messages in the prepended conversation.
            Returns 0 if prepended_conversation is None or empty.
    """
    if not prepended_conversation:
        return 0
    return sum(1 for msg in prepended_conversation if msg.api_role == "assistant")


@dataclass
class ConversationState:
    """Container for conversation state data returned from context initialization."""

    turn_count: int = 0

    # Scores from the last assistant message (for attack-specific interpretation)
    # Used by Crescendo to detect refusals and objective achievement
    last_assistant_message_scores: List[Score] = field(default_factory=list)


class ConversationManager:
    """
    Manages conversations for attacks, handling message history,
    system prompts, and conversation state.

    This class provides methods to:
    - Initialize attack context with prepended conversations
    - Retrieve conversation history
    - Set system prompts for chat targets
    """

    def __init__(
        self,
        *,
        attack_identifier: Dict[str, str],
        prompt_normalizer: Optional[PromptNormalizer] = None,
    ):
        """
        Initialize the conversation manager.

        Args:
            attack_identifier: The identifier of the attack this manager belongs to.
            prompt_normalizer: Optional prompt normalizer for converting prompts.
                If not provided, a default PromptNormalizer instance will be created.
        """
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._memory = CentralMemory.get_memory_instance()
        self._attack_identifier = attack_identifier

    def get_conversation(self, conversation_id: str) -> List[Message]:
        """
        Retrieve a conversation by its ID.

        Args:
            conversation_id: The ID of the conversation to retrieve.

        Returns:
            A list of messages in the conversation, ordered by creation time.
            Returns empty list if no messages exist.
        """
        conversation = self._memory.get_conversation(conversation_id=conversation_id)
        return list(conversation)

    def get_last_message(
        self, *, conversation_id: str, role: Optional[ChatMessageRole] = None
    ) -> Optional[MessagePiece]:
        """
        Retrieve the most recent message from a conversation.

        Args:
            conversation_id: The ID of the conversation to retrieve from.
            role: If provided, return only the last message matching this role.

        Returns:
            The last message piece, or None if no messages exist.
        """
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None

        if role:
            for m in reversed(conversation):
                piece = m.get_piece()
                if piece.api_role == role:
                    return piece
            return None

        return conversation[-1].get_piece()

    def set_system_prompt(
        self,
        *,
        target: PromptChatTarget,
        conversation_id: str,
        system_prompt: str,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Set or update the system prompt for a conversation.

        Args:
            target: The chat target to set the system prompt on.
            conversation_id: Unique identifier for the conversation.
            system_prompt: The system prompt text.
            labels: Optional labels to associate with the system prompt.
        """
        target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
            attack_identifier=self._attack_identifier,
            labels=labels,
        )

    async def initialize_context_async(
        self,
        *,
        context: "AttackContext[Any]",
        target: PromptTarget,
        conversation_id: str,
        request_converters: Optional[List[PromptConverterConfiguration]] = None,
        prepended_conversation_config: Optional["PrependedConversationConfig"] = None,
        max_turns: Optional[int] = None,
        memory_labels: Optional[Dict[str, str]] = None,
    ) -> ConversationState:
        """
        Initialize attack context with prepended conversation and merged labels.

        This is the primary method for setting up an attack context. It:
        1. Merges memory_labels from attack strategy with context labels
        2. Processes prepended_conversation based on target type and config
        3. Updates context.executed_turns for multi-turn attacks
        4. Sets context.next_message if there's an unanswered user message

        For PromptChatTarget:
            - Adds prepended messages to memory with simulated_assistant role
            - All messages get new UUIDs

        For non-chat PromptTarget:
            - If `config.non_chat_target_behavior="normalize_first_turn"`: normalizes
              conversation to string and prepends to context.next_message
            - If `config.non_chat_target_behavior="raise"`: raises ValueError

        Args:
            context: The attack context to initialize.
            target: The objective target for the conversation.
            conversation_id: Unique identifier for the conversation.
            request_converters: Converters to apply to messages.
            prepended_conversation_config: Configuration for handling prepended conversation.
            max_turns: Maximum turns allowed (for validation and state tracking).
            memory_labels: Labels from the attack strategy to merge with context labels.

        Returns:
            ConversationState with turn_count and last_assistant_message_scores.

        Raises:
            ValueError: If conversation_id is empty, or if prepended_conversation
                requires a PromptChatTarget but target is not one.
        """
        if not conversation_id:
            raise ValueError("conversation_id cannot be empty")

        # Merge memory labels: attack strategy labels + context labels
        context.memory_labels = combine_dict(existing_dict=memory_labels, new_dict=context.memory_labels)

        state = ConversationState()
        prepended_conversation = context.prepended_conversation

        if not prepended_conversation:
            logger.debug(f"No prepended conversation for context initialization: {conversation_id}")
            return state

        # Handle target type compatibility
        is_chat_target = isinstance(target, PromptChatTarget)
        if not is_chat_target:
            return await self._handle_non_chat_target_async(
                context=context,
                prepended_conversation=prepended_conversation,
                config=prepended_conversation_config,
            )

        # Process prepended conversation for objective target
        return await self._process_prepended_for_chat_target_async(
            context=context,
            prepended_conversation=prepended_conversation,
            conversation_id=conversation_id,
            request_converters=request_converters,
            prepended_conversation_config=prepended_conversation_config,
            max_turns=max_turns,
        )

    async def _handle_non_chat_target_async(
        self,
        *,
        context: "AttackContext[Any]",
        prepended_conversation: List[Message],
        config: Optional["PrependedConversationConfig"],
    ) -> ConversationState:
        """
        Handle prepended conversation for non-chat targets.

        Args:
            context: The attack context.
            prepended_conversation: Messages to prepend.
            config: Configuration for non-chat target behavior.

        Returns:
            Empty ConversationState (non-chat targets don't track turns).

        Raises:
            ValueError: If config requires raising for non-chat targets.
        """
        if config is None:
            config = PrependedConversationConfig()

        if config.non_chat_target_behavior == "raise":
            raise ValueError(
                "prepended_conversation requires the objective target to be a PromptChatTarget. "
                "Non-chat objective targets do not support conversation history. "
                "Use PrependedConversationConfig with non_chat_target_behavior='normalize_first_turn' "
                "to normalize the conversation into the first message instead."
            )

        # Normalize conversation to string
        normalizer = config.get_message_normalizer()
        normalized_context = await normalizer.normalize_string_async(prepended_conversation)

        # Prepend to next_message if it exists, otherwise create new message
        if context.next_message is not None:
            # Find an existing text piece to prepend to
            text_piece = None
            for piece in context.next_message.message_pieces:
                if piece.original_value_data_type == "text":
                    text_piece = piece
                    break

            if text_piece:
                # Prepend context to the existing text piece
                text_piece.original_value = f"{normalized_context}\n\n{text_piece.original_value}"
                text_piece.converted_value = f"{normalized_context}\n\n{text_piece.converted_value}"
            else:
                # No text piece found (multimodal message), add a new text piece at the beginning
                context_piece = MessagePiece(
                    id=uuid.uuid4(),
                    role="user",
                    original_value=normalized_context,
                    converted_value=normalized_context,
                    original_value_data_type="text",
                    converted_value_data_type="text",
                )
                # Create a new message with the context piece prepended
                context.next_message = Message(
                    message_pieces=[context_piece] + list(context.next_message.message_pieces)
                )
        else:
            # Create new message with just the context
            context.next_message = Message.from_prompt(prompt=normalized_context, role="user")

        logger.debug(f"Normalized prepended conversation for non-chat target: {len(normalized_context)} characters")
        return ConversationState()

    async def add_prepended_conversation_to_memory_async(
        self,
        *,
        prepended_conversation: List[Message],
        conversation_id: str,
        request_converters: Optional[List[PromptConverterConfiguration]] = None,
        prepended_conversation_config: Optional["PrependedConversationConfig"] = None,
        max_turns: Optional[int] = None,
    ) -> int:
        """
        Add prepended conversation messages to memory for a chat target.

        This is a lower-level method that handles adding messages to memory without
        modifying any attack context state. It can be called directly by attacks
        that manage their own state (like TAP nodes) or internally by
        initialize_context_async for standard attacks.

        Messages are added with:
        - Duplicated message objects (preserves originals)
        - simulated_assistant role for assistant messages (for traceability)
        - Converters applied based on config

        Args:
            prepended_conversation: Messages to add to memory.
            conversation_id: Conversation ID to assign to all messages.
            request_converters: Optional converters to apply to messages.
            prepended_conversation_config: Optional configuration for converter roles.
            max_turns: If provided, validates that turn count doesn't exceed this limit.

        Returns:
            The number of turns (assistant messages) added.

        Raises:
            ValueError: If max_turns is exceeded by the prepended conversation.
        """
        # Filter valid messages
        valid_messages = [msg for msg in prepended_conversation if msg and msg.message_pieces]
        if not valid_messages:
            return 0

        # Get roles that should have converters applied
        apply_to_roles = (
            prepended_conversation_config.apply_converters_to_roles if prepended_conversation_config else None
        )

        turn_count = 0

        for i, message in enumerate(valid_messages):
            message_copy = message.duplicate_message()

            message_copy.set_simulated_role()

            for piece in message_copy.message_pieces:
                piece.conversation_id = conversation_id
                piece.attack_identifier = self._attack_identifier

            # Count turns at message level (only assistant/simulated_assistant messages)
            # A multi-part response still counts as one turn
            if message_copy.api_role == "assistant":
                turn_count += 1
                if max_turns is not None and turn_count > max_turns:
                    raise ValueError(
                        f"Prepended conversation has {turn_count} turns, "
                        f"exceeding max_turns={max_turns}. Reduce prepended turns or increase max_turns."
                    )

            # Apply converters if configured
            if request_converters:
                await self._apply_converters_async(
                    message=message_copy,
                    request_converters=request_converters,
                    apply_to_roles=apply_to_roles,
                )

            # Add to memory
            self._memory.add_message_to_memory(request=message_copy)
            logger.debug(f"Added prepended message {i + 1}/{len(valid_messages)} to memory")

        return turn_count

    async def _process_prepended_for_chat_target_async(
        self,
        *,
        context: "AttackContext[Any]",
        prepended_conversation: List[Message],
        conversation_id: str,
        request_converters: Optional[List[PromptConverterConfiguration]],
        prepended_conversation_config: Optional["PrependedConversationConfig"],
        max_turns: Optional[int],
    ) -> ConversationState:
        """
        Process prepended conversation for a chat target.

        Adds messages to memory with:
        - New UUIDs for all pieces
        - simulated_assistant role for assistant messages
        - Converters applied based on config

        Args:
            context: The attack context.
            prepended_conversation: Messages to add to memory.
            conversation_id: Conversation ID for the messages.
            request_converters: Converters to apply.
            prepended_conversation_config: Configuration for converter roles.
            max_turns: Maximum turns for validation.

        Returns:
            ConversationState with turn_count and scores.
        """
        state = ConversationState()
        is_multi_turn = max_turns is not None

        # Filter valid messages
        valid_messages = [msg for msg in prepended_conversation if msg and msg.message_pieces]
        if not valid_messages:
            return state

        # Use the lower-level method to add messages to memory
        state.turn_count = await self.add_prepended_conversation_to_memory_async(
            prepended_conversation=prepended_conversation,
            conversation_id=conversation_id,
            request_converters=request_converters,
            prepended_conversation_config=prepended_conversation_config,
            max_turns=max_turns,
        )

        # Update context for multi-turn attacks to reflect prepended_conversation

        final_prepended_message = valid_messages[-1]

        if is_multi_turn and final_prepended_message.api_role == "assistant":
            # Update executed_turns
            if hasattr(context, "executed_turns"):
                context.executed_turns = state.turn_count

            # Extract scores on final prepended assistant message if it exists and are relavent
            # Multi-part messages (e.g., text + image) may have scores on multiple pieces
            # only extract true_false scores with score_value=False. This allows attacks
            # to use the score's rationale for feedback without re-scoring.
            for piece in final_prepended_message.message_pieces:
                for score in piece.scores:
                    if score.score_type == "true_false" and score.get_value() is False:
                        state.last_assistant_message_scores.append(score)
                        # context.last_score gets the first matching score for single-score use cases.
                        if hasattr(context, "last_score") and context.last_score is None:
                            context.last_score = score

        return state

    async def _apply_converters_async(
        self,
        *,
        message: Message,
        request_converters: List[PromptConverterConfiguration],
        apply_to_roles: Optional[List[ChatMessageRole]],
    ) -> None:
        """
        Apply converters to message pieces.

        Args:
            message: The message containing pieces to convert.
            request_converters: Converter configurations to apply.
            apply_to_roles: If provided, only apply to pieces with these roles.
                If None, apply to all roles.
        """
        for piece in message.message_pieces:
            # Filter by role if specified
            if apply_to_roles is not None and piece.api_role not in apply_to_roles:
                continue

            temp_message = Message(message_pieces=[piece])
            await self._prompt_normalizer.convert_values(
                message=temp_message,
                converter_configurations=request_converters,
            )
