# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

from pyrit.models import Message, MessagePiece
from pyrit.output.conversation.base import ConversationPrinterBase
from pyrit.output.conversation.source import ConversationSource, MemoryConversationSource
from pyrit.output.score.json import JsonScorePrinter
from pyrit.output.sink import Sink


class JsonConversationPrinter(ConversationPrinterBase):
    """
    JSON printer for conversation message histories.

    Unlike the pretty/markdown printers (which compose rendered *strings*), this
    printer builds a structured list of dicts via ``build_async`` and serializes
    it in ``render_async``. Exposing the structured form lets the scenario layer
    stitch many conversations into one JSON document without re-parsing strings.
    Scores are fetched through the injected ``ConversationSource``.
    """

    def __init__(
        self,
        *,
        source: ConversationSource,
        sink: Sink | None = None,
        indent: int = 2,
        score_printer: JsonScorePrinter | None = None,
    ) -> None:
        """
        Initialize the JSON conversation printer.

        Args:
            source (ConversationSource): Data source used to fetch inline scores.
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
            score_printer (JsonScorePrinter | None): Score printer whose ``build`` supplies inline
                score dicts. Defaults to a new ``JsonScorePrinter``.
        """
        super().__init__(sink=sink)
        self._source = source
        self._indent = indent
        self._score_printer = score_printer or JsonScorePrinter()

    async def build_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_summaries: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Build the structured (dict) representation of a conversation.

        Args:
            messages (list[Message]): The messages to serialize.
            include_scores (bool): Whether to attach inline objective scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to keep reasoning pieces. Defaults to False.

        Returns:
            list[dict[str, Any]]: One dict per message with renderable pieces.
        """
        result: list[dict[str, Any]] = []
        for message in messages:
            pieces = self._get_renderable_pieces(
                message=message,
                include_reasoning_summaries=include_reasoning_summaries,
            )
            if not pieces:
                continue
            piece_dicts = [
                await self._build_piece_async(piece=piece, include_scores=include_scores) for piece in pieces
            ]
            result.append(
                {
                    "role": message.api_role,
                    "is_simulated": message.is_simulated,
                    "pieces": piece_dicts,
                }
            )
        return result

    async def render_async(
        self,
        messages: list[Message],
        *,
        include_scores: bool = False,
        include_reasoning_summaries: bool = False,
    ) -> str:
        """
        Render a conversation as a JSON string.

        Args:
            messages (list[Message]): The messages to render.
            include_scores (bool): Whether to attach inline objective scores. Defaults to False.
            include_reasoning_summaries (bool): Whether to keep reasoning pieces. Defaults to False.

        Returns:
            str: The conversation serialized as indented JSON.
        """
        structured = await self.build_async(
            messages,
            include_scores=include_scores,
            include_reasoning_summaries=include_reasoning_summaries,
        )
        return json.dumps(structured, indent=self._indent, default=str, ensure_ascii=False)

    async def _build_piece_async(self, *, piece: MessagePiece, include_scores: bool) -> dict[str, Any]:
        """
        Build the structured representation of a single message piece.

        Args:
            piece (MessagePiece): The piece to serialize.
            include_scores (bool): Whether to attach inline objective scores.

        Returns:
            dict[str, Any]: The piece's curated fields (a reasoning summary for
                reasoning pieces, otherwise the original/converted values).
        """
        if self._is_reasoning_piece(piece=piece):
            return {"data_type": "reasoning", "reasoning_summary": self._safe_reasoning_summary(piece=piece)}

        data: dict[str, Any] = {
            "data_type": piece.original_value_data_type,
            "original_value": piece.original_value,
            "converted_value": piece.converted_value,
            "response_error": piece.response_error,
        }
        if piece.is_blocked():
            partial_content = piece.prompt_metadata.get("partial_content")
            if partial_content:
                data["partial_content"] = str(partial_content)
        if include_scores:
            scores = await self._source.get_scores_async(prompt_ids=[str(piece.id)])
            if scores:
                data["scores"] = [self._score_printer.build(score) for score in scores]
        return data

    def _safe_reasoning_summary(self, *, piece: MessagePiece) -> str | None:
        """
        Extract a reasoning summary, returning None when it can't be parsed.

        Args:
            piece (MessagePiece): The reasoning piece.

        Returns:
            str | None: The summary text, or None on extraction failure.
        """
        try:
            return self._extract_reasoning_summary(self._get_reasoning_value(piece=piece))
        except ValueError:
            return None


class JsonConversationMemoryPrinter(JsonConversationPrinter):
    """JSON conversation printer backed by ``CentralMemory`` (framework / notebook path)."""

    def __init__(self, *, sink: Sink | None = None, indent: int = 2) -> None:
        """
        Initialize with a CentralMemory-backed source.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
        """
        super().__init__(source=MemoryConversationSource(), sink=sink, indent=indent)
