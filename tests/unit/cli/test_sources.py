# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the CLI-side ``RestApiConversationSource`` (pyrit.cli._sources)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from pyrit.backend.models import ScoreView
from pyrit.cli._sources import RestApiConversationSource, _only_known
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score

if TYPE_CHECKING:
    from pydantic import BaseModel


class TestOnlyKnown:
    @pytest.mark.parametrize("model_cls", [MessagePiece, Score])
    @pytest.mark.parametrize("data", [{}, {"view_only": "ignored"}])
    def test_empty_or_unknown_fields_return_empty_dict(self, model_cls: type[BaseModel], data: dict[str, Any]) -> None:
        assert _only_known(model_cls, data=data) == {}

    @pytest.mark.parametrize(
        ("model_cls", "known"),
        [
            (MessagePiece, {"role": None, "original_value": "", "sequence": 0, "prompt_metadata": {"source": "rest"}}),
            (Score, {"score_type": None, "score_rationale": "", "scorer_class_identifier": {"params": {}}}),
        ],
    )
    def test_declared_values_are_preserved_without_mutating_input(
        self, model_cls: type[BaseModel], known: dict[str, Any]
    ) -> None:
        data = {**known, "view_only": "ignored"}
        original = data.copy()

        result = _only_known(model_cls=model_cls, data=data)

        assert result == known
        assert all(result[key] is value for key, value in known.items())
        assert data == original
        assert result is not data


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def get_conversation_messages_async(self, *, attack_result_id, conversation_id):
        self.calls.append((attack_result_id, conversation_id))
        return self._response


def _piece(*, role, text, scores=None):
    piece = {
        "id": str(uuid.uuid4()),
        "role": role,
        "sequence": 0,
        "conversation_id": "conv-1",
        "original_value": text,
        "converted_value": text,
        # view-only extras that must be dropped on hydration:
        "original_value_url": None,
        "converted_value_mime_type": "text/plain",
        "converted_filename": None,
    }
    if scores is not None:
        piece["scores"] = scores
    return piece


def _response(messages):
    return {"conversation_id": "conv-1", "messages": messages}


async def test_get_messages_hydrates_domain_messages():
    response = _response(
        [
            {"role": "user", "turn_number": 0, "message_pieces": [_piece(role="user", text="hello")]},
            {"role": "assistant", "turn_number": 1, "message_pieces": [_piece(role="assistant", text="there")]},
        ]
    )
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")

    assert all(isinstance(message, Message) for message in messages)
    assert [message.get_piece().converted_value for message in messages] == ["hello", "there"]
    assert [message.api_role for message in messages] == ["user", "assistant"]


async def test_get_scores_async_returns_every_score_hydrated_from_the_view():
    piece_json = _piece(role="assistant", text="x")
    scorers = [
        ComponentIdentifier(class_name="RefusalScorer", class_module="tests"),
        ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests", params={"threshold": 0.5}),
    ]
    piece_json["scores"] = [
        ScoreView.from_domain(
            Score(
                score_type="true_false",
                score_value="true",
                message_piece_id=piece_json["id"],
                scorer_class_identifier=scorer,
            )
        ).model_dump(mode="json")
        for scorer in scorers
    ]
    response = _response([{"role": "assistant", "turn_number": 1, "message_pieces": [piece_json]}])
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")
    scores = await source.get_scores_async(prompt_ids=[str(messages[0].get_piece().id)])

    assert [score.scorer_class_identifier.hash for score in scores] == [scorer.hash for scorer in scorers]
    assert [str(score.message_piece_id) for score in scores] == [piece_json["id"], piece_json["id"]]


async def test_get_scores_async_is_empty_for_unscored_piece():
    response = _response([{"role": "user", "turn_number": 0, "message_pieces": [_piece(role="user", text="hi")]}])
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")

    assert await source.get_scores_async(prompt_ids=[str(messages[0].get_piece().id)]) == []


async def test_view_only_fields_are_dropped_on_hydration():
    # Pieces carry view-only keys (urls, mime, filenames) that MessagePiece forbids;
    # hydration must strip them rather than raise.
    response = _response([{"role": "user", "turn_number": 0, "message_pieces": [_piece(role="user", text="hi")]}])
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")

    assert messages[0].get_piece().converted_value == "hi"
