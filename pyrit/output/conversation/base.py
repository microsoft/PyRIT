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
    def _extract_reasoning_summary(cls, reasoning_value: str) -> str:
        """
        Extract a provider-visible reasoning summary from an OpenAI Responses item.

        The expected value is the JSON serialization of an OpenAI Responses
        ``reasoning`` output item. The returned text is a provider-generated
        summary, not raw model chain-of-thought.

        Args:
            reasoning_value (str): Serialized OpenAI Responses reasoning item.

        Returns:
            str: The concatenated summary text. An empty summary list produces an empty string.

        Raises:
            ValueError: If the value is not valid JSON or does not match the expected
                OpenAI Responses reasoning-summary shape.
        """
        try:
            data = json.loads(reasoning_value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Reasoning pieces must contain a valid JSON object.") from exc

        if not isinstance(data, dict) or data.get("type") != "reasoning":
            raise ValueError("Reasoning pieces must contain an OpenAI Responses item with type='reasoning'.")

        if not isinstance(data.get("id"), str):
            raise ValueError("Reasoning pieces must contain a string 'id'.")

        summary = data.get("summary")
        if not isinstance(summary, list):
            raise ValueError("Reasoning pieces must contain a 'summary' list.")

        parts: list[str] = []
        for item in summary:
            if (
                not isinstance(item, dict)
                or item.get("type") != "summary_text"
                or not isinstance(item.get("text"), str)
            ):
                raise ValueError("Each reasoning summary item must have type='summary_text' and a string 'text'.")
            parts.append(item["text"])

        status = data.get("status")
        if status is not None and status not in cls._REASONING_STATUSES:
            raise ValueError("Reasoning piece 'status' must be in_progress, completed, incomplete, or null.")

        encrypted_content = data.get("encrypted_content")
        if encrypted_content is not None and not isinstance(encrypted_content, str):
            raise ValueError("Reasoning piece 'encrypted_content' must be a string or null.")

        content = data.get("content")
        if content is not None:
            if not isinstance(content, list):
                raise ValueError("Reasoning piece 'content' must be a list or null.")
            for item in content:
                if (
                    not isinstance(item, dict)
                    or item.get("type") != "reasoning_text"
                    or not isinstance(item.get("text"), str)
                ):
                    raise ValueError("Each reasoning content item must have type='reasoning_text' and a string 'text'.")

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
