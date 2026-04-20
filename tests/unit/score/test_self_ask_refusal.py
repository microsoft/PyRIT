# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import SelfAskRefusalScorer


@pytest.fixture
def scorer_true_false_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True", "description": "This is true", "rationale": "rationale for true"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


@pytest.mark.asyncio
async def test_refusal_scorer_score(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    score = await scorer.score_text_async("true false")

    assert len(score) == 1
    assert score[0].get_value() is True
    assert score[0].score_value_description == "This is true"
    assert score[0].score_rationale == "rationale for true"
    assert score[0].scorer_class_identifier.class_name == "SelfAskRefusalScorer"


@pytest.mark.asyncio
async def test_refusal_scorer_set_system_prompt(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false")

    chat_target.set_system_prompt.assert_called_once()


@pytest.mark.asyncio
async def test_refusal_scorer_no_task(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false")

    _, kwargs = chat_target.send_prompt_async.call_args
    assert kwargs["message"].message_pieces[0].original_value == "response_to_evaluate_input: true false"


@pytest.mark.asyncio
async def test_refusal_scorer_with_task(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false", objective="task")

    _, kwargs = chat_target.send_prompt_async.call_args

    expected = "conversation_objective: task\nresponse_to_evaluate_input: true false"
    assert kwargs["message"].message_pieces[0].original_value == expected


@pytest.mark.asyncio
async def test_refusal_scorer_image_non_block(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with tempfile.NamedTemporaryFile(delete=True) as temp_image:
        result = await scorer.score_image_async(temp_image.name)
        assert result[0].get_value() is False

    # Any image doesn't need LLM evaluation, it just checks whether it was blocked
    chat_target.send_prompt_async.assert_not_called()


@pytest.mark.asyncio
async def test_refusal_scorer_adds_to_memory(scorer_true_false_response: Message):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskRefusalScorer(chat_target=chat_target)
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_refusal_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskRefusalScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.asyncio
async def test_self_ask_objective_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()

    json_response = (
        dedent(
            """
            {"bad_value_key": "True", "rationale": "rationale for true"}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskRefusalScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.asyncio
async def test_score_async_filtered_response(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    chat_target = MagicMock()
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    request = MessagePiece(role="assistant", original_value="blocked response", response_error="blocked").to_message()
    memory.add_message_pieces_to_memory(message_pieces=request.message_pieces)
    scores = await scorer.score_async(request)

    assert len(scores) == 1
    assert scores[0].score_value == "true"
    assert scores[0].score_value_description == "Refusal detected"
    assert scores[0].score_rationale == "Content was filtered, constituting a refusal."
    assert scores[0].scorer_class_identifier.class_name == "SelfAskRefusalScorer"
