# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from abc import abstractmethod
from typing import ClassVar

from pyrit.models import Message, MessagePiece, Score
from pyrit.output.base import PrinterBase


class ConversationPrinterBase(PrinterBase):
    """
    Abstract base class for printing conversation message histories.

    Subclasses implement data-fetching methods (``_get_scores_async``,
    ``_display_image_async``) and rendering via ``render_async``.
    """

    _REASONING_STATUSES: ClassVar[frozenset[str]] = frozenset({"in_progress", "completed", "incomplete"})

    @staticmethod
    def _is_reasoning_piece(*, piece: MessagePiece) -> bool:
        """
        Check whether either stored representation marks the piece as reasoning.

        Returns:
            bool: True when the original or converted data type is reasoning.
        """
        return piece.original_value_data_type == "reasoning" or piece.converted_value_data_type == "reasoning"

    @classmethod
    def _get_reasoning_value(cls, *, piece: MessagePiece) -> str:
        """
        Return the value associated with the reasoning-typed representation.

        Args:
            piece (MessagePiece): The reasoning piece whose serialized value should be returned.

        Returns:
            str: The converted value when it remains reasoning, otherwise the original value.

        Raises:
            ValueError: If neither representation is reasoning.
        """
        if piece.converted_value_data_type == "reasoning":
            return piece.converted_value
        if piece.original_value_data_type == "reasoning":
            return piece.original_value
        raise ValueError("Message piece is not a reasoning piece.")

    @staticmethod
    def _get_renderable_pieces(
        *,
        message: Message,
        include_reasoning_trace: bool,
    ) -> list[MessagePiece]:
        """
        Return message pieces visible under the selected reasoning policy.

        Args:
            message (Message): The message whose pieces should be filtered.
            include_reasoning_trace (bool): Whether reasoning pieces should remain visible.

        Returns:
            list[MessagePiece]: The pieces that should be rendered.
        """
        return [
            piece
            for piece in message.message_pieces
            if include_reasoning_trace or not ConversationPrinterBase._is_reasoning_piece(piece=piece)
        ]

    @classmethod
@classmethod
def _extract_reasoning_summary(cls, reasoning_value: str) -> str:
    try:
        data = json.loads(reasoning_value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Reasoning piece must contain valid JSON.") from exc

    if not isinstance(data, dict) or data.get("type") != "reasoning" or not isinstance(data.get("id"), str):
        raise ValueError("Reasoning piece must be an OpenAI Responses item with type='reasoning' and a string 'id'.")

    def _extract_text(item: object, expected_type: str, label: str) -> str:
        if not isinstance(item, dict) or item.get("type") != expected_type or not isinstance(item.get("text"), str):
            raise ValueError(f"Each reasoning {label} item must have type='{expected_type}' and a string 'text'.")
        return item["text"]

    summary = data.get("summary")
    if not isinstance(summary, list):
        raise ValueError("Reasoning piece 'summary' must be a list.")
    parts = [_extract_text(item, "summary_text", "summary") for item in summary]

    status = data.get("status")
    if status is not None and status not in cls._REASONING_STATUSES:
        raise ValueError("Reasoning piece 'status' must be in_progress, completed, incomplete, or null.")

    content = data.get("content")
    if content is not None:
        if not isinstance(content, list):
            raise ValueError("Reasoning piece 'content' must be a list or null.")
        for item in content:
            _extract_text(item, "reasoning_text", "content")

    return "\n".join(parts)

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
        include_reasoning_trace: bool = False,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_trace (bool): Whether to include reasoning traces. Defaults to False.

        Returns:
            str: The rendered conversation text.
        """
