# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import Message


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
            # Only system message, convert to user message. Carry the system
            # piece's prompt_metadata so downstream normalizers (e.g.
            # JsonSchemaNormalizer) still see request-level metadata; without
            # this a fresh piece from Message.from_prompt would have empty
            # metadata and any subsequent capability adaptation would silently
            # no-op.
            return [
                Message.from_prompt(
                    prompt=first_piece.converted_value,
                    role="user",
                    prompt_metadata=dict(first_piece.prompt_metadata),
                )
            ]

        # Combine system with first user message
        system_content = first_piece.converted_value
        user_piece = messages[1].get_piece()
        user_content = user_piece.converted_value

        combined_content = f"### Instructions ###\n\n{system_content}\n\n######\n\n{user_content}"
        # Carry the user piece's prompt_metadata onto the squashed piece. The
        # user piece represents the current request being sent (the system
        # message is being subsumed into its envelope), so its metadata is
        # authoritative for downstream normalizers.
        squashed_message = Message.from_prompt(
            prompt=combined_content,
            role="user",
            prompt_metadata=dict(user_piece.prompt_metadata),
        )
        # Return the squashed message followed by remaining messages (skip first two)
        return [squashed_message] + list(messages[2:])
