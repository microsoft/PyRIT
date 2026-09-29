# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.output.conversation.source import ObjectiveScoreConversationSource

OBJECTIVE_SCORER = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests", params={"threshold": 0.5})
REFUSAL_SCORER = ComponentIdentifier(class_name="RefusalScorer", class_module="tests")


class _StubSource:
    """Minimal ``ConversationSource`` returning canned messages and the scores stored on requested pieces."""

    def __init__(self, *, pieces: list[MessagePiece], scores: list[Score]) -> None:
        self._messages = [Message(message_pieces=[piece]) for piece in pieces]
        self._scores = scores
        self.score_requests: list[list[str]] = []

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        return self._messages

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        self.score_requests.append(prompt_ids)
        return [score for score in self._scores if str(score.message_piece_id) in prompt_ids]


def _piece(*, original_prompt_id: uuid.UUID | None = None) -> MessagePiece:
    return MessagePiece(role="assistant", original_value="reply", original_prompt_id=original_prompt_id)


def _score(*, owner: MessagePiece | uuid.UUID, label: str, scorer: ComponentIdentifier | None) -> Score:
    owner_id = owner.id if isinstance(owner, MessagePiece) else owner
    return Score(
        score_type="true_false",
        score_value="true",
        score_rationale=label,
        message_piece_id=owner_id,
        scorer_class_identifier=scorer,
    )


async def _labels_for(
    pieces: list[MessagePiece], scores: list[Score], *, scorer: ComponentIdentifier | None = OBJECTIVE_SCORER
) -> list[str | None]:
    source = ObjectiveScoreConversationSource(
        source=_StubSource(pieces=pieces, scores=scores), objective_scorer_identifier=scorer
    )
    await source.get_messages_async(conversation_id="conv-1")
    selected = await source.get_scores_async(prompt_ids=[str(piece.id) for piece in pieces])
    return [score.score_rationale for score in selected]


async def test_get_scores_async_prefers_hash_match_over_earlier_class_name_match():
    piece = _piece()
    same_class_other_params = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests")
    scores = [
        _score(owner=piece, label="class-only", scorer=same_class_other_params),
        _score(owner=piece, label="exact", scorer=OBJECTIVE_SCORER),
    ]

    assert await _labels_for([piece], scores) == ["exact"]


async def test_get_scores_async_falls_back_to_class_name_match():
    piece = _piece()
    same_class = ComponentIdentifier(class_name="ObjectiveScorer", class_module="elsewhere")
    scores = [
        _score(owner=piece, label="auxiliary", scorer=REFUSAL_SCORER),
        _score(owner=piece, label="same-class", scorer=same_class),
    ]

    assert await _labels_for([piece], scores) == ["same-class"]


async def test_get_scores_async_drops_auxiliary_and_unidentified_scores():
    piece = _piece()
    scores = [
        _score(owner=piece, label="auxiliary", scorer=REFUSAL_SCORER),
        _score(owner=piece, label="unidentified", scorer=None),
    ]

    assert await _labels_for([piece], scores) == []


async def test_get_scores_async_keeps_one_score_per_piece():
    first, second = _piece(), _piece()
    scores = [
        _score(owner=first, label="first", scorer=OBJECTIVE_SCORER),
        _score(owner=first, label="duplicate", scorer=OBJECTIVE_SCORER),
        _score(owner=second, label="second", scorer=OBJECTIVE_SCORER),
    ]

    assert await _labels_for([first, second], scores) == ["first", "second"]


async def test_get_scores_async_keeps_score_on_the_piece_that_stores_it():
    first, second = _piece(), _piece()

    assert await _labels_for(
        [first, second], [_score(owner=first, label="message-level", scorer=OBJECTIVE_SCORER)]
    ) == ["message-level"]


async def test_get_scores_async_reads_duplicated_piece_score_from_original():
    original_id = uuid.uuid4()
    duplicate = _piece(original_prompt_id=original_id)

    assert await _labels_for([duplicate], [_score(owner=original_id, label="original", scorer=OBJECTIVE_SCORER)]) == [
        "original"
    ]


async def test_get_messages_async_reads_scores_once_per_conversation():
    first, second = _piece(), _piece()
    inner = _StubSource(pieces=[first, second], scores=[])
    source = ObjectiveScoreConversationSource(source=inner, objective_scorer_identifier=OBJECTIVE_SCORER)

    await source.get_messages_async(conversation_id="conv-1")
    await source.get_scores_async(prompt_ids=[str(first.id)])
    await source.get_scores_async(prompt_ids=[str(second.id)])

    assert inner.score_requests == [sorted([str(first.id), str(second.id)])]


async def test_get_scores_async_without_objective_scorer_returns_no_scores():
    piece = _piece()
    scores = [_score(owner=piece, label="objective", scorer=OBJECTIVE_SCORER)]

    assert await _labels_for([piece], scores, scorer=None) == []
