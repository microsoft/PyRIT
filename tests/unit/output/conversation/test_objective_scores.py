# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.output.conversation.json import JsonConversationMemoryPrinter, JsonConversationPrinter
from pyrit.output.conversation.markdown import MarkdownConversationMemoryPrinter, MarkdownConversationPrinter
from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter, PrettyConversationPrinter

OBJECTIVE_SCORER = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests")
REFUSAL_SCORER = ComponentIdentifier(class_name="RefusalScorer", class_module="tests")

PRINTERS = [JsonConversationPrinter, PrettyConversationPrinter, MarkdownConversationPrinter]
MEMORY_PRINTERS = [JsonConversationMemoryPrinter, PrettyConversationMemoryPrinter, MarkdownConversationMemoryPrinter]


class _RecordingSource:
    """Serves the given scores and records the piece ids of every score read."""

    def __init__(self, scores: list[Score]) -> None:
        self._scores = scores
        self.score_reads: list[list[str]] = []

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        return []

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        self.score_reads.append(prompt_ids)
        return [score for score in self._scores if str(score.message_piece_id) in prompt_ids]


def _score(*, piece: MessagePiece, scorer: ComponentIdentifier) -> Score:
    return Score(
        score_type="true_false",
        score_value="false",
        score_rationale=f"{scorer.class_name} rationale",
        message_piece_id=piece.id,
        scorer_class_identifier=scorer,
    )


def _conversation() -> tuple[list[Message], list[Score]]:
    user_piece = MessagePiece(role="user", original_value="objective", conversation_id="c1", sequence=0)
    reply_piece = MessagePiece(role="assistant", original_value="reply", conversation_id="c1", sequence=1)
    scores = [_score(piece=reply_piece, scorer=REFUSAL_SCORER), _score(piece=reply_piece, scorer=OBJECTIVE_SCORER)]
    return [Message(message_pieces=[user_piece]), Message(message_pieces=[reply_piece])], scores


@pytest.mark.parametrize("printer_class", PRINTERS)
async def test_render_async_with_objective_scorer_shows_only_its_score(printer_class):
    messages, scores = _conversation()
    printer = printer_class(source=_RecordingSource(scores))

    rendered = await printer.render_async(messages, include_scores=True, objective_scorer_identifier=OBJECTIVE_SCORER)

    assert "ObjectiveScorer rationale" in rendered
    assert "RefusalScorer rationale" not in rendered


@pytest.mark.parametrize("printer_class", PRINTERS)
async def test_render_async_with_objective_scorer_reads_scores_once(printer_class):
    messages, scores = _conversation()
    source = _RecordingSource(scores)

    await printer_class(source=source).render_async(
        messages, include_scores=True, objective_scorer_identifier=OBJECTIVE_SCORER
    )

    assert source.score_reads == [[str(message.get_piece().id) for message in messages]]


@pytest.mark.parametrize("printer_class", PRINTERS)
async def test_render_async_without_objective_scorer_shows_every_score(printer_class):
    messages, scores = _conversation()

    rendered = await printer_class(source=_RecordingSource(scores)).render_async(messages, include_scores=True)

    assert "ObjectiveScorer rationale" in rendered
    assert "RefusalScorer rationale" in rendered


@pytest.mark.parametrize("printer_class", PRINTERS)
async def test_render_async_objective_scorer_without_include_scores_reads_nothing(printer_class):
    messages, scores = _conversation()
    source = _RecordingSource(scores)

    rendered = await printer_class(source=source).render_async(messages, objective_scorer_identifier=OBJECTIVE_SCORER)

    assert source.score_reads == []
    assert "rationale" not in rendered


@pytest.mark.parametrize("printer_class", MEMORY_PRINTERS)
async def test_memory_printer_render_async_with_objective_scorer_shows_only_its_score(
    printer_class, sqlite_instance: MemoryInterface, patch_central_database
):
    messages, scores = _conversation()
    for message in messages:
        sqlite_instance.add_message_to_memory(request=message)
    sqlite_instance.add_scores_to_memory(scores=scores)

    rendered = await printer_class().render_async(
        messages, include_scores=True, objective_scorer_identifier=OBJECTIVE_SCORER
    )

    assert "ObjectiveScorer rationale" in rendered
    assert "RefusalScorer rationale" not in rendered
