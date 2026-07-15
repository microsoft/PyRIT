# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.message_normalizer._helpers import build_squashed_user_message
from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import Message, MessagePiece


class GenericSystemSquashNormalizer(MessageListNormalizer[Message]):
    """
    Normalizer that combines system messages with the first user message using generic instruction tags.
    """

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Return messages with all system messages combined into the first user message.

        The format uses generic instruction tags:
        ### Instructions ###
        {system_content}
        ######
        {user_content}

        Args:
            messages: The list of messages to normalize.

        Returns:
            Messages with system instructions squashed into the first user message.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        system_messages = [message for message in messages if message.api_role == "system"]
        if not system_messages:
            return list(messages)

        system_content = "\n\n".join(
            piece.converted_value for message in system_messages for piece in message.message_pieces
        )
        non_system_messages = [message for message in messages if message.api_role != "system"]

        if not non_system_messages:
            return [
                build_squashed_user_message(
                    new_message_content=system_content,
                    source_messages=system_messages,
                )
            ]

        user_message_index = next(
            (i for i, message in enumerate(non_system_messages) if message.api_role == "user"),
            -1,
        )
        if user_message_index == -1:
            return [
                build_squashed_user_message(
                    new_message_content=system_content,
                    source_messages=system_messages,
                )
            ] + non_system_messages

        # Combine system with the first user message, preserving non-text pieces (e.g. images) and their order.
        user_message = non_system_messages[user_message_index]
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

        return (
            non_system_messages[:user_message_index]
            + [squashed_message]
            + non_system_messages[user_message_index + 1 :]
        )
