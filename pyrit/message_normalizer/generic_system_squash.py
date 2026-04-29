# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import Message, MessagePiece


class GenericSystemSquashNormalizer(MessageListNormalizer[Message]):
    """
    Normalizer that combines the first system message with the first user message using generic instruction tags.
    """

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Return messages with the first system message combined into the first user message.

        The format uses generic instruction tags:
        ### Instructions ###
        {system_content}
        ######
        {user_content}

        Args:
            messages: The list of messages to normalize.

        Returns:
            A Message with the system message squashed into the first user message.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        # Check if first message is a system message
        first_piece = messages[0].get_piece()
        if first_piece.api_role != "system":
            # No system message to squash, return messages unchanged
            return list(messages)

        if len(messages) == 1:
            # Only system message, convert to user message
            return [Message.from_prompt(prompt=first_piece.converted_value, role="user")]

        user_message_index = next(
            (i for i, message in enumerate(messages[1:], start=1) if message.api_role == "user"),
            -1,
        )
        if user_message_index == -1:
            # Preserve the instruction content without rewriting non-user messages.
            return [Message.from_prompt(prompt=first_piece.converted_value, role="user")] + list(messages[1:])

        # Combine system with the first user message
        system_content = first_piece.converted_value
        user_piece = messages[user_message_index].get_piece()
        user_content = user_piece.converted_value

        combined_content = f"### Instructions ###\n\n{system_content}\n\n######\n\n{user_content}"
        combined_piece = MessagePiece(
            role="user",
            original_value=combined_content,
            conversation_id=user_piece.conversation_id,
            sequence=user_piece.sequence,
        )
        remaining_pieces = list(messages[user_message_index].message_pieces[1:])
        squashed_message = Message(message_pieces=[combined_piece] + remaining_pieces)

        # Remove system (index 0), replace the first user message with the squashed version, preserve all others
        return list(messages[1:user_message_index]) + [squashed_message] + list(messages[user_message_index + 1 :])
