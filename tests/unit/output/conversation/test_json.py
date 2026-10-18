# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.output.conversation.json import JsonConversationPrinter


class _StubSource:
    """Minimal ``ConversationSource`` returning canned scores per piece id."""

    def __init__(self, scores_by_piece: dict[str, list[Score]] | None = None) -> None:
        self._scores = scores_by_piece or {}

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        return []

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        return [score for prompt_id in prompt_ids for score in self._scores.get(prompt_id, [])]


def _piece(*, role: str = "user", original: str = "hello", data_type: str = "text") -> MessagePiece:
    return MessagePiece(
        role=role,
        original_value=original,
        original_value_data_type=data_type,
        conversation_id="c1",
        sequence=0,
    )


def _message(piece: MessagePiece) -> Message:
    return Message(message_pieces=[piece])


def _score(*, piece_id: str) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["hate"],
        score_rationale="because",
        objective="make it fail",
        message_piece_id=piece_id,
        scorer_class_identifier=ComponentIdentifier(class_name="MockScorer", class_module="tests"),
    )


async def test_build_async_maps_message_and_piece_fields():
    piece = _piece(original="hi there")
    structured = await JsonConversationPrinter(source=_StubSource()).build_async([_message(piece)])

    assert len(structured) == 1
    assert structured[0]["role"] == "user"
    assert structured[0]["is_simulated"] is False
    assert structured[0]["pieces"][0]["data_type"] == "text"
    assert structured[0]["pieces"][0]["original_value"] == "hi there"
    assert structured[0]["pieces"][0]["response_error"] == "none"


async def test_build_async_omits_scores_when_not_requested():
    piece = _piece()
    source = _StubSource({str(piece.id): [_score(piece_id=str(piece.id))]})
    structured = await JsonConversationPrinter(source=source).build_async([_message(piece)], include_scores=False)

    assert "scores" not in structured[0]["pieces"][0]


async def test_build_async_includes_scores_when_requested():
    piece = _piece()
    source = _StubSource({str(piece.id): [_score(piece_id=str(piece.id))]})
    structured = await JsonConversationPrinter(source=source).build_async([_message(piece)], include_scores=True)

    scores = structured[0]["pieces"][0]["scores"]
    assert scores[0]["scorer"] == "MockScorer"
    assert scores[0]["score_value"] == "true"
    assert scores[0]["objective"] == "make it fail"


async def test_build_async_drops_reasoning_pieces_by_default():
    reasoning = MessagePiece(
        role="assistant",
        original_value=json.dumps({"summary": [{"type": "summary_text", "text": "thinking"}]}),
        original_value_data_type="reasoning",
        conversation_id="c1",
        sequence=0,
    )
    structured = await JsonConversationPrinter(source=_StubSource()).build_async([_message(reasoning)])

    assert structured == []


async def test_build_async_extracts_reasoning_summary_when_included():
    reasoning = MessagePiece(
        role="assistant",
        original_value=json.dumps({"summary": [{"type": "summary_text", "text": "thinking"}]}),
        original_value_data_type="reasoning",
        conversation_id="c1",
        sequence=0,
    )
    structured = await JsonConversationPrinter(source=_StubSource()).build_async(
        [_message(reasoning)], include_reasoning_summaries=True
    )

    piece = structured[0]["pieces"][0]
    assert piece["data_type"] == "reasoning"
    assert piece["reasoning_summary"] == "thinking"


async def test_build_async_reasoning_summary_none_on_bad_payload():
    reasoning = MessagePiece(
        role="assistant",
        original_value="not-json",
        original_value_data_type="reasoning",
        conversation_id="c1",
        sequence=0,
    )
    structured = await JsonConversationPrinter(source=_StubSource()).build_async(
        [_message(reasoning)], include_reasoning_summaries=True
    )

    assert structured[0]["pieces"][0]["reasoning_summary"] is None


async def test_render_async_returns_valid_json():
    piece = _piece()
    text = await JsonConversationPrinter(source=_StubSource()).render_async([_message(piece)])

    parsed = json.loads(text)
    assert parsed[0]["pieces"][0]["original_value"] == "hello"


async def test_render_async_empty_messages_is_empty_array():
    text = await JsonConversationPrinter(source=_StubSource()).render_async([])
    assert json.loads(text) == []


def test_memory_printer_defaults_to_memory_source(patch_central_database):
    from pyrit.output.conversation.json import JsonConversationMemoryPrinter

    printer = JsonConversationMemoryPrinter()
    assert isinstance(printer, JsonConversationPrinter)


async def test_build_async_includes_partial_content_for_blocked_piece():
    piece = MessagePiece(
        role="assistant",
        original_value="",
        original_value_data_type="text",
        conversation_id="c1",
        sequence=0,
        response_error="blocked",
        prompt_metadata={"partial_content": "the beginning of the answer"},
    )
    structured = await JsonConversationPrinter(source=_StubSource()).build_async([_message(piece)])

    assert structured[0]["pieces"][0]["partial_content"] == "the beginning of the answer"


async def test_build_async_omits_partial_content_when_not_blocked():
    piece = MessagePiece(
        role="assistant",
        original_value="ok",
        original_value_data_type="text",
        conversation_id="c1",
        sequence=0,
        prompt_metadata={"partial_content": "ignored unless blocked"},
    )
    structured = await JsonConversationPrinter(source=_StubSource()).build_async([_message(piece)])

    assert "partial_content" not in structured[0]["pieces"][0]
