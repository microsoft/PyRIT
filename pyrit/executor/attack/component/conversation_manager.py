# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrit.common.utils import combine_dict
from pyrit.executor.attack.component.prepended_conversation_config import (
    PrependedConversationConfig,
)
from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.message_normalizer._helpers import get_unflattenable_converter_output_types
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    Conversation,
    Message,
    MessagePiece,
    Score,
    UndeterminedScoreError,
)
from pyrit.prompt_normalizer.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import CapabilityName, PromptTarget
from pyrit.prompt_target.common.target_history import filter_non_replayable_messages

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.executor.attack.core import AttackContext
    from pyrit.prompt_normalizer.converter_configuration import (
        ConverterConfiguration,
    )

logger = logging.getLogger(__name__)


def mark_messages_as_simulated(messages: Sequence[Message]) -> list[Message]:
    """
    Mark assistant messages as simulated_assistant for traceability.

    This function converts all assistant roles to simulated_assistant in the
    provided messages. This is useful when loading conversations from YAML files
    or other sources where the responses are not from actual targets.

    Args:
        messages (Sequence[Message]): The messages to mark as simulated.

    Returns:
        list[Message]: The same messages with assistant roles converted to simulated_assistant.
            Modifies the messages in place and also returns them for convenience.
    """
    result = list(messages)
    for message in result:
        for piece in message.message_pieces:
            if piece.role == "assistant":
                piece.role = "simulated_assistant"
    return result


def get_adversarial_chat_messages(
    prepended_conversation: list[Message],
    *,
    adversarial_chat_conversation_id: str,
) -> list[Message]:
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

    Returns:
        List of transformed messages with swapped roles and new IDs.
    """
    if not prepended_conversation:
        return []

    role_swap: dict[ChatMessageRole, ChatMessageRole] = {
        "user": "assistant",
        "assistant": "user",
        "simulated_assistant": "user",
    }

    result: list[Message] = []

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
            )

            result.append(adversarial_piece.to_message())

    logger.debug(f"Created {len(result)} adversarial chat messages with swapped roles")
    return result


async def build_conversation_context_string_async(messages: list[Message]) -> str:
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


def get_prepended_turn_count(prepended_conversation: list[Message] | None) -> int:
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
    last_assistant_message_scores: list[Score] = field(default_factory=list)


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
        prompt_normalizer: PromptNormalizer | None = None,
    ) -> None:
        """
        Initialize the conversation manager.

        Args:
            prompt_normalizer: Optional prompt normalizer for converting prompts.
                If not provided, a default PromptNormalizer instance will be created.
        """
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._memory = CentralMemory.get_memory_instance()

    def get_conversation(self, conversation_id: str) -> list[Message]:
        """
        Retrieve a conversation by its ID.

        Args:
            conversation_id: The ID of the conversation to retrieve.

        Returns:
            A list of messages in the conversation, ordered by creation time.
            Returns empty list if no messages exist.
        """
        conversation = self._memory.get_conversation_messages(conversation_id=conversation_id)
        return list(conversation)

    def get_last_message(self, *, conversation_id: str, role: ChatMessageRole | None = None) -> MessagePiece | None:
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
        target: PromptTarget,
        conversation_id: str,
        system_prompt: str,
    ) -> None:
        """
        Set or update the system prompt for a conversation.

        Args:
            target: The target to set the system prompt on. Must handle the
                SYSTEM_PROMPT capability (natively or via an ADAPT policy).
            conversation_id: Unique identifier for the conversation.
            system_prompt: The system prompt text.

        Raises:
            ValueError: If target cannot handle the SYSTEM_PROMPT capability.
        """
        target.configuration.ensure_can_handle(capability=CapabilityName.SYSTEM_PROMPT)

        target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
        )

    async def initialize_context_async(
        self,
        *,
        context: AttackContext[Any],
        target: PromptTarget,
        conversation_id: str,
        request_converters: list[ConverterConfiguration] | None = None,
        prepended_conversation_config: PrependedConversationConfig | None = None,
        max_turns: int | None = None,
        memory_labels: dict[str, str] | None = None,
    ) -> ConversationState:
        """
        Initialize attack context with prepended conversation and merged labels.

        This is the primary method for setting up an attack context. It:
        1. Merges memory_labels from attack strategy with context labels
        2. Persists prepended_conversation structurally with role-scoped converters
        3. Updates context.executed_turns for multi-turn attacks

        For all PromptTarget types, prepended messages are added to memory with
        simulated_assistant roles and new UUIDs.

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
            ValueError: If conversation_id is empty.
        """
        if not conversation_id:
            raise ValueError("conversation_id cannot be empty")

        # Merge memory labels: attack strategy labels + context labels
        context.memory_labels = combine_dict(existing_dict=memory_labels, new_dict=context.memory_labels)
        state = ConversationState()
        prepended_conversation = context.prepended_conversation
        context.prepended_history_send_context = None

        if not prepended_conversation:
            logger.debug(f"No prepended conversation for context initialization: {conversation_id}")
            return state

        return await self._process_prepended_conversation_async(
            context=context,
            prepended_conversation=prepended_conversation,
            conversation_id=conversation_id,
            request_converters=request_converters,
            prepended_conversation_config=prepended_conversation_config,
            max_turns=max_turns,
            target_identifier=target.get_identifier(),
            target=target,
        )

    async def add_prepended_conversation_to_memory_async(
        self,
        *,
        prepended_conversation: list[Message],
        conversation_id: str,
        request_converters: list[ConverterConfiguration] | None = None,
        prepended_conversation_config: PrependedConversationConfig | None = None,
        max_turns: int | None = None,
        target_identifier: ComponentIdentifier | None = None,
        target: PromptTarget | None = None,
    ) -> int:
        """
        Add prepended conversation messages to memory for a target.

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
            target_identifier (ComponentIdentifier | None): The target the conversation is held
                with, if known. Recorded once per conversation.
            target (PromptTarget | None): Target that will receive the first live request.

        Returns:
            The number of turns (assistant messages) added.

        Raises:
            ValueError: If max_turns is exceeded by the prepended conversation.
        """
        valid_messages = self.get_persistable_prepended_messages(prepended_conversation=prepended_conversation)
        if not valid_messages:
            return 0

        if target and target_identifier is None:
            target_identifier = target.get_identifier()

        # Assistant history represents simulated target output, so the absent-config
        # path must use the same safe role default as an explicit default config.
        config = prepended_conversation_config or PrependedConversationConfig()
        apply_to_roles = config.apply_converters_to_roles
        requires_prepended_adaptation = bool(
            target and not target.configuration.includes(capability=CapabilityName.EDITABLE_HISTORY)
        )

        turn_count = 0
        prepared_messages: list[Message] = []

        for message in valid_messages:
            message_copy = message.duplicate()

            message_copy.set_simulated_role()

            for piece in message_copy.message_pieces:
                piece.conversation_id = conversation_id

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
                if requires_prepended_adaptation:
                    self._validate_flattenable_converter_output(
                        source_message=message,
                        converted_message=message_copy,
                    )

            prepared_messages.append(message_copy)

        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for i, message in enumerate(prepared_messages):
            self._memory.add_message_to_memory(request=message)
            logger.debug(f"Added prepended message {i + 1}/{len(prepared_messages)} to memory")

        return turn_count

    @staticmethod
    def get_persistable_prepended_messages(
        *,
        prepended_conversation: list[Message],
    ) -> list[Message]:
        """
        Return prepended messages that can be recovered from memory at send time.

        Args:
            prepended_conversation: Candidate prepended messages.

        Returns:
            list[Message]: Non-empty messages containing at least one persistable piece.
        """
        persistable_messages = [
            message
            for message in prepended_conversation
            if message and message.message_pieces and any(not piece.not_in_memory for piece in message.message_pieces)
        ]
        return filter_non_replayable_messages(messages=persistable_messages)

    @staticmethod
    def create_prepended_history_send_context(
        *,
        target: PromptTarget,
        conversation_id: str,
        prepended_messages: list[Message],
    ) -> PrependedHistorySendContext | None:
        """
        Build persisted-prefix state for a target without editable history.

        Returns:
            PrependedHistorySendContext | None: Per-send state, or ``None`` for
                editable-history targets or empty prepended history.
        """
        if not prepended_messages or target.configuration.includes(capability=CapabilityName.EDITABLE_HISTORY):
            return None

        return PrependedHistorySendContext(
            conversation_id=conversation_id,
            seed_message_ids=tuple(message.get_piece().id for message in prepended_messages),
            replay_seed_each_send=not target.configuration.includes(capability=CapabilityName.MULTI_TURN),
        )

    async def _process_prepended_conversation_async(
        self,
        *,
        context: AttackContext[Any],
        prepended_conversation: list[Message],
        conversation_id: str,
        request_converters: list[ConverterConfiguration] | None,
        prepended_conversation_config: PrependedConversationConfig | None,
        max_turns: int | None,
        target_identifier: ComponentIdentifier | None = None,
        target: PromptTarget,
    ) -> ConversationState:
        """
        Process prepended conversation for a target.

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
            target_identifier (ComponentIdentifier | None): The objective target the
                conversation is held with, if known.
            target: The objective target that will receive the conversation.

        Returns:
            ConversationState with turn_count and scores.
        """
        state = ConversationState()
        is_multi_turn = max_turns is not None

        valid_messages = self.get_persistable_prepended_messages(prepended_conversation=prepended_conversation)
        if not valid_messages:
            return state

        existing_message_ids = {
            message.get_piece().id for message in self.get_conversation(conversation_id=conversation_id)
        }

        # Use the lower-level method to add messages to memory
        state.turn_count = await self.add_prepended_conversation_to_memory_async(
            prepended_conversation=prepended_conversation,
            conversation_id=conversation_id,
            request_converters=request_converters,
            prepended_conversation_config=prepended_conversation_config,
            max_turns=max_turns,
            target_identifier=target_identifier,
            target=target,
        )
        persisted_messages = [
            message
            for message in self.get_conversation(conversation_id)
            if message.get_piece().id not in existing_message_ids
        ]
        context.prepended_history_send_context = self.create_prepended_history_send_context(
            target=target,
            conversation_id=conversation_id,
            prepended_messages=persisted_messages,
        )
        # Update context for multi-turn attacks to reflect prepended_conversation

        final_prepended_message = valid_messages[-1]

        if is_multi_turn and final_prepended_message.api_role == "assistant":
            # Update executed_turns
            if hasattr(context, "executed_turns"):
                context.executed_turns = state.turn_count  # type: ignore[ty:invalid-assignment]

            # Extract scores on final prepended assistant message if it exists and are relevant.
            # The prepended pieces were re-keyed with new ids when added to memory, so look
            # them up by conversation_id and filter to the last assistant turn. Only extract
            # true_false scores with score_value=False so attacks can use the rationale for
            # feedback without re-scoring.
            memory_pieces = self._memory.get_message_pieces(conversation_id=conversation_id)
            assistant_pieces = [piece for piece in memory_pieces if piece.api_role == "assistant"]
            last_assistant_sequence = max((piece.sequence for piece in assistant_pieces), default=None)
            assistant_piece_ids = [
                str(piece.id) for piece in assistant_pieces if piece.sequence == last_assistant_sequence
            ]
            existing_scores = (
                self._memory.get_prompt_scores(prompt_ids=assistant_piece_ids) if assistant_piece_ids else []
            )
            for score in existing_scores:
                if score.score_type != "true_false":
                    continue
                try:
                    # Undetermined is not a refutation, so it is not feedback-worthy here.
                    is_false = score.get_value() is False
                except UndeterminedScoreError:
                    continue
                if is_false:
                    state.last_assistant_message_scores.append(score)
                    # context.last_score gets the first matching score for single-score use cases.
                    if hasattr(context, "last_score") and context.last_score is None:
                        context.last_score = score  # type: ignore[ty:invalid-assignment]

        return state

    @staticmethod
    def _validate_flattenable_converter_output(
        *,
        source_message: Message,
        converted_message: Message,
    ) -> None:
        """
        Reject non-text output produced by this prepended conversion pass.

        Raises:
            ValueError: If an applied converter produced non-text prepended history.
        """
        output_types = get_unflattenable_converter_output_types(
            source_messages=[source_message],
            converted_messages=[converted_message],
        )
        if output_types:
            raise ValueError(
                "Cannot flatten prepended conversation for a target without editable history after "
                f"request converters produced non-text output types {sorted(output_types)}. Prepended "
                "conversion must produce text."
            )

    async def _apply_converters_async(
        self,
        *,
        message: Message,
        request_converters: list[ConverterConfiguration],
        apply_to_roles: list[ChatMessageRole],
    ) -> None:
        """
        Apply converters to message pieces.

        Args:
            message: The message containing pieces to convert.
            request_converters: Converter configurations to apply.
            apply_to_roles: Only apply to pieces with these roles.
        """
        if message.api_role not in apply_to_roles:
            return

        # Apply to the complete message so ConverterConfiguration.indexes_to_apply remains relative
        # to the original piece list. Converting one temporary piece at a time would reset every
        # selected piece to index zero.
        await self._prompt_normalizer.convert_values_async(
            message=message,
            converter_configurations=request_converters,
        )
