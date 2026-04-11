# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import Message


class HistorySquashNormalizer(MessageListNormalizer[Message]):
    """
    Squashes a multi-turn conversation into a single user message.

    Previous turns are formatted as labeled context and prepended to the
    latest message.  Used by the normalization pipeline to adapt prompts
    for targets that do not support multi-turn conversations.
    """

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Combine all messages into a single user message.

        When there is only one message it is returned unchanged.  Otherwise
        all prior turns are formatted as ``Role: content`` lines under a
        ``[Conversation History]`` header and the last message's content
        appears under a ``[Current Message]`` header.

        Args:
            messages: The conversation messages to squash.

        Returns:
            list[Message]: A single-element list containing the squashed message.

        Raises:
            ValueError: If the messages list is empty.
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        if len(messages) == 1:
            return list(messages)

        history_lines = self._format_history(messages=messages[:-1])
        current_parts = [piece.converted_value for piece in messages[-1].message_pieces]

        combined = (
            "[Conversation History]\n" + "\n".join(history_lines) + "\n\n[Current Message]\n" + "\n".join(current_parts)
        )

        return [Message.from_prompt(prompt=combined, role="user")]

    def _format_history(self, *, messages: list[Message]) -> list[str]:
        """
        Format prior messages as ``Role: content`` lines.

        Args:
            messages: The history messages to format.

        Returns:
            list[str]: One line per message piece.
        """
        lines: list[str] = []
        for msg in messages:
            lines.extend(f"{piece.api_role.capitalize()}: {piece.converted_value}" for piece in msg.message_pieces)
        return lines
