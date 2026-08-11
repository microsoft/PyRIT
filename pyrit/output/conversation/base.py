# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from abc import abstractmethod

from pyrit.models import Message, MessagePiece, Score
from pyrit.output.base import PrinterBase


class ConversationPrinterBase(PrinterBase):
    """
    Abstract base class for printing conversation message histories.

    Subclasses implement data-fetching methods (``_get_scores_async``,
    ``_display_image_async``) and rendering via ``render_async``.
    """

    _REASONING_RENDER_WARNING = "⚠ WARNING: Reasoning summary failed to render; conversation is intact."

    @staticmethod
    def _is_reasoning_piece(*, piece: MessagePiece) -> bool:
        """
        Check whether either stored representation marks the piece as reasoning.

        Returns:
            bool: True when the original data type is reasoning.
        """
        return piece.original_value_data_type == "reasoning"

    @classmethod
    def _get_reasoning_value(cls, *, piece: MessagePiece) -> str:
        """
        Return the value associated with the reasoning-typed representation.

        Args:
            piece (MessagePiece): The reasoning piece whose serialized value should be returned.

        Returns:
            str: The original value when it remains reasoning.

        Raises:
            ValueError: If neither representation is reasoning.
        """
        if piece.original_value_data_type == "reasoning":
            return piece.original_value
        raise ValueError("Message piece is not a reasoning piece.")

    @staticmethod
    def _get_renderable_pieces(
        *,
        message: Message,
        include_reasoning_summaries: bool,
    ) -> list[MessagePiece]:
        """
        Return message pieces visible under the selected reasoning policy.

        Args:
            message (Message): The message whose pieces should be filtered.
            include_reasoning_summaries (bool): Whether reasoning pieces should remain visible.

        Returns:
            list[MessagePiece]: The pieces that should be rendered.
        """
        return [
            piece
            for piece in message.message_pieces
            if include_reasoning_summaries or not ConversationPrinterBase._is_reasoning_piece(piece=piece)
        ]

    @staticmethod
    def _extract_reasoning_summary(reasoning_value: str) -> str:
        """
        Extract a provider-visible reasoning summary from an OpenAI Responses item.

        The expected value is a JSON object containing a ``summary`` list. Only
        fields consumed by the output formatter are validated.

        Args:
            reasoning_value (str): Serialized OpenAI Responses reasoning item.

        Returns:
            str: The concatenated summary text. An empty summary list produces an empty string.

        Raises:
            ValueError: If the value is not a JSON object with a list of summary
                items containing string ``text`` values.
        """
        try:
            data = json.loads(reasoning_value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Reasoning pieces must contain a valid JSON object.") from exc

        if not isinstance(data, dict):
            raise ValueError("Reasoning pieces must contain a valid JSON object.")

        summary = data.get("summary")
        if not isinstance(summary, list):
            raise ValueError("Reasoning pieces must contain a 'summary' list.")

        parts: list[str] = []
        for item in summary:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("Each reasoning summary item must contain a string 'text'.")
            parts.append(item["text"])

        return "\n".join(parts)

    @abstractmethod
    async def _get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Fetch scores for given prompt piece IDs.

        Args:
            prompt_ids (list[str]): The message piece IDs to fetch scores for.

        Returns:
            list[Score]: The scores associated with the given piece IDs.
        """

    async def _display_image_async(self, piece: MessagePiece) -> None:
        """
        Display an image from a message piece. No-op by default.

        Args:
            piece (MessagePiece): The message piece that may contain image data.
        """

    @abstractmethod
    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_summaries: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to include reasoning summaries. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
