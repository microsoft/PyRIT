# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from abc import abstractmethod

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.output._derivation import select_objective_scores
from pyrit.output.base import PrinterBase
from pyrit.output.conversation.source import ConversationSource


class ConversationPrinterBase(PrinterBase):
    """
    Abstract base class for printing conversation message histories.

    Data access goes through an injected ``ConversationSource``; subclasses
    provide only rendering via ``render_async`` (and image display, if any).
    """

    _REASONING_RENDER_WARNING = "⚠ WARNING: Reasoning summary failed to render; conversation is intact."

    _source: ConversationSource

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

    async def _select_objective_scores_async(
        self,
        *,
        messages: list[Message],
        objective_scorer_identifier: ComponentIdentifier | None,
    ) -> dict[str, Score] | None:
        """
        Read the conversation's scores once and pick each piece's objective score.

        Args:
            messages (list[Message]): The messages being rendered.
            objective_scorer_identifier (ComponentIdentifier | None): The scorer whose score to keep.
                None shows every score, so nothing is picked up front.

        Returns:
            dict[str, Score] | None: Each piece's objective score keyed by piece id, or None when
                every score is shown.
        """
        if objective_scorer_identifier is None:
            return None
        pieces = [piece for message in messages for piece in message.message_pieces]
        if not pieces:
            return {}
        scores = await self._source.get_scores_async(prompt_ids=[str(piece.id) for piece in pieces])
        return select_objective_scores(
            pieces=pieces, scores=scores, objective_scorer_identifier=objective_scorer_identifier
        )

    async def _get_piece_scores_async(
        self,
        *,
        piece: MessagePiece,
        objective_scores: dict[str, Score] | None,
    ) -> list[Score]:
        """
        Return the scores to render for a piece.

        When an ``objective_scores`` dict is passed, use the scores already selected for the
        conversation without fetching again. Otherwise, fetch all scores attached to this piece.

        Args:
            piece (MessagePiece): The piece being rendered.
            objective_scores (dict[str, Score] | None): Objective scores selected for the
                conversation keyed by piece id, or None to fetch every score on the piece.

        Returns:
            list[Score]: The scores to render for the piece.
        """
        if objective_scores is None:
            return await self._source.get_scores_async(prompt_ids=[str(piece.id)])
        score = objective_scores.get(str(piece.id))
        return [score] if score is not None else []

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
        objective_scorer_identifier: ComponentIdentifier | None = None,
    ) -> str:
        """
        Render a list of messages and return as a string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to include scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to include reasoning summaries. Defaults to False.
            objective_scorer_identifier (ComponentIdentifier | None): With ``include_scores``, show only
                this scorer's score on each piece. Defaults to None (every score).

        Returns:
            str: The rendered conversation text.
        """
