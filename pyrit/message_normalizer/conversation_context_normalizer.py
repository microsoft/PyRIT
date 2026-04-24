# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.message_normalizer.message_normalizer import MessageStringNormalizer
from pyrit.models import Message, MessagePiece


class ConversationContextNormalizer(MessageStringNormalizer):
    """
    Normalizer that formats conversation history as turn-based text.

    This is the standard format used by attacks like Crescendo and TAP
    for including conversation context in adversarial chat prompts.
    The output format is:

        Turn 1:
        User: <content>
        Assistant: <content>

        Turn 2:
        User: <content>
        ...
    """

    async def normalize_string_async(self, messages: list[Message]) -> str:
        """
        Normalize a list of messages into a turn-based context string.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A formatted string with turn numbers and role prefixes.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        context_parts: list[str] = []
        turn_number = 0

        for message in messages:
            for piece in message.message_pieces:
                # Skip system messages in context formatting
                if piece.api_role == "system":
                    continue

                # Start a new turn when we see a user message
                if piece.api_role == "user":
                    turn_number += 1
                    context_parts.append(f"Turn {turn_number}:")

                # Format the piece content
                content = self._format_piece_content(piece)
                role_label = "User" if piece.api_role == "user" else "Assistant"
                context_parts.append(f"{role_label}: {content}")

        return "\n".join(context_parts)

    def _format_piece_content(self, piece: MessagePiece) -> str:
        """
        Format a single message piece into a content string.

        For text pieces, shows original and converted values (if different).
        For non-text pieces, uses context_description metadata or a placeholder.

        Args:
            piece: The message piece to format.

        Returns:
            The formatted content string.
        """
        data_type = piece.converted_value_data_type or piece.original_value_data_type

        # For non-text pieces, use metadata description or placeholder
        if data_type != "text":
            if piece.prompt_metadata and "context_description" in piece.prompt_metadata:
                description = piece.prompt_metadata["context_description"]
                return f"[{data_type.capitalize()} - {description}]"
            return f"[{data_type.capitalize()}]"

        # For text pieces, include both original and converted if different
        original = piece.original_value
        converted = piece.converted_value

        if original != converted:
            return f"{converted} (original: {original})"
        return converted
