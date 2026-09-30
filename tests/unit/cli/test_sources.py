# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the CLI-side ``RestApiConversationSource`` (pyrit.cli._sources)."""

import uuid

from pyrit.cli._sources import RestApiConversationSource
from pyrit.models import Message, Score


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


async def test_objective_score_selected_by_hash_and_served_by_piece_id():
    objective = [
        {
            "score_value": "false",
            "score_type": "true_false",
            "score_rationale": "complied",
            "scorer_type": "SelfAskRefusalScorer",
            "scorer_class_identifier": {"hash": "AUX"},
        },
        {
            "score_value": "true",
            "score_type": "true_false",
            "score_rationale": "achieved",
            "scorer_type": "TrueFalseCompositeScorer",
            "scorer_class_identifier": {"hash": "OBJ"},
        },
    ]
    assistant_piece = _piece(role="assistant", text="there", scores=objective)
    response = _response([{"role": "assistant", "turn_number": 1, "message_pieces": [assistant_piece]}])
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1", objective_hash="OBJ")

    messages = await source.get_messages_async(conversation_id="conv-1")
    piece_id = str(messages[0].get_piece().id)
    scores = await source.get_scores_async(prompt_ids=[piece_id])

    assert len(scores) == 1
    assert isinstance(scores[0], Score)
    # The objective (composite) score is surfaced, not the first (refusal) one.
    assert scores[0].score_value == "true"
    assert scores[0].score_rationale == "achieved"


async def test_objective_score_falls_back_to_class_name():
    scores_json = [
        {"score_value": "true", "score_type": "true_false", "scorer_type": "MyObjective"},
    ]
    response = _response(
        [
            {
                "role": "assistant",
                "turn_number": 1,
                "message_pieces": [_piece(role="assistant", text="x", scores=scores_json)],
            }
        ]
    )
    source = RestApiConversationSource(
        client=_FakeClient(response), attack_result_id="aid-1", objective_class="MyObjective"
    )

    messages = await source.get_messages_async(conversation_id="conv-1")
    scores = await source.get_scores_async(prompt_ids=[str(messages[0].get_piece().id)])

    assert [score.score_value for score in scores] == ["true"]


async def test_no_objective_scorer_yields_no_scores():
    scores_json = [{"score_value": "true", "score_type": "true_false", "scorer_type": "X"}]
    response = _response(
        [
            {
                "role": "assistant",
                "turn_number": 1,
                "message_pieces": [_piece(role="assistant", text="x", scores=scores_json)],
            }
        ]
    )
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")
    scores = await source.get_scores_async(prompt_ids=[str(messages[0].get_piece().id)])

    assert scores == []


async def test_view_only_fields_are_dropped_on_hydration():
    # Pieces carry view-only keys (urls, mime, filenames) that MessagePiece forbids;
    # hydration must strip them rather than raise.
    response = _response([{"role": "user", "turn_number": 0, "message_pieces": [_piece(role="user", text="hi")]}])
    source = RestApiConversationSource(client=_FakeClient(response), attack_result_id="aid-1")

    messages = await source.get_messages_async(conversation_id="conv-1")

    assert messages[0].get_piece().converted_value == "hi"
