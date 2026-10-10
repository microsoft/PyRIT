# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.message_normalizer._helpers import build_squashed_user_message
from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import Message, MessagePiece


class GenericSystemSquashNormalizer(MessageListNormalizer[Message]):
    """
    Normalizer that combines system messages with the following user message using generic instruction tags.
    """

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Return messages with each system message combined into the following user message.

        The format uses generic instruction tags:
        ### Instructions ###
        {system_content}
        ######
        {user_content}

        Args:
            messages: The list of messages to normalize.

        Returns:
            Messages with system instructions squashed into the following user message.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        system_messages = [message for message in messages if message.api_role == "system"]
        if not system_messages:
            return list(messages)

        result: list[Message] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.api_role != "system":
                result.append(message)
                index += 1
                continue

            system_messages = [message]
            index += 1
            while index < len(messages) and messages[index].api_role == "system":
                system_messages.append(messages[index])
                index += 1

            if index < len(messages) and messages[index].api_role == "user":
                result.append(
                    self._squash_system_messages_into_user(
                        system_messages=system_messages,
                        user_message=messages[index],
                    )
                )
                index += 1
            else:
                result.append(
                    build_squashed_user_message(
                        new_message_content=self._get_system_content(system_messages),
                        source_messages=system_messages,
                    )
                )

        return result

    @staticmethod
    def _get_system_content(system_messages: list[Message]) -> str:
        """
        Combine system-message pieces in message order.

        Args:
            system_messages: The system messages to combine.

        Returns:
            The combined system-message content.
        """
        return "\n\n".join(piece.converted_value for message in system_messages for piece in message.message_pieces)

    def _squash_system_messages_into_user(
        self,
        *,
        system_messages: list[Message],
        user_message: Message,
    ) -> Message:
        """
        Merge system instructions into a user message while preserving its pieces.

        Args:
            system_messages: The system messages to merge.
            user_message: The following user message.

        Returns:
            The user message with the system instructions applied.
        """
        system_content = self._get_system_content(system_messages)
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

        return Message(message_pieces=squashed_pieces)
