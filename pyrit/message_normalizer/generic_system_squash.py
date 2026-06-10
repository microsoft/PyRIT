# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.message_normalizer._helpers import build_squashed_user_message
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
            # Only system message, convert to user message.
            return [
                build_squashed_user_message(
                    new_message_content=first_piece.converted_value, source_messages=messages[:1]
                )
            ]

        user_message_index = next(
            (i for i, message in enumerate(messages[1:], start=1) if message.api_role == "user"),
            -1,
        )
        if user_message_index == -1:
            # Preserve the instruction content without rewriting non-user messages.
            return [
                build_squashed_user_message(
                    new_message_content=first_piece.converted_value, source_messages=messages[:1]
                )
            ] + list(messages[1:])

        # Combine system with the first user message, preserving non-text pieces (e.g. images) and their order.
        system_content = first_piece.converted_value
        user_message = messages[user_message_index]
        # Propagate prompt_metadata from the user message's first piece so downstream normalizers
        # (e.g. JsonSchemaNormalizer) still see request-level metadata after squashing.
        propagated_metadata = dict(user_message.message_pieces[0].prompt_metadata)
        text_piece_index = next(
            (i for i, piece in enumerate(user_message.message_pieces) if piece.converted_value_data_type == "text"),
            -1,
        )

        if text_piece_index == -1:
            # No text piece to merge into; prepend an instruction-only text piece so non-text pieces are preserved.
            template_piece = user_message.get_piece()
            instruction_piece = MessagePiece(
                role="user",
                original_value=f"### Instructions ###\n\n{system_content}\n\n######",
                conversation_id=template_piece.conversation_id,
                sequence=template_piece.sequence,
                prompt_metadata=propagated_metadata,
            )
            squashed_pieces = [instruction_piece] + list(user_message.message_pieces)
        else:
            text_piece = user_message.message_pieces[text_piece_index]
            combined_piece = MessagePiece(
                role="user",
                original_value=f"### Instructions ###\n\n{system_content}\n\n######\n\n{text_piece.converted_value}",
                conversation_id=text_piece.conversation_id,
                sequence=text_piece.sequence,
                prompt_metadata=propagated_metadata,
            )
            squashed_pieces = (
                list(user_message.message_pieces[:text_piece_index])
                + [combined_piece]
                + list(user_message.message_pieces[text_piece_index + 1 :])
            )

        squashed_message = Message(message_pieces=squashed_pieces)

        # Remove system (index 0), replace the first user message with the squashed version, preserve all others
        return list(messages[1:user_message_index]) + [squashed_message] + list(messages[user_message_index + 1 :])
