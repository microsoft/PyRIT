# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import (
    ContentClassifier,
    ContentClassifierCategory,
    ContentClassifierPaths,
    SelfAskCategoryScorer,
)

HARM_CLASSIFIER = ContentClassifier.from_yaml(ContentClassifierPaths.HARMFUL_CONTENT_CLASSIFIER.value)


@pytest.fixture
def scorer_category_response_bullying() -> Message:
    json_response = (
        dedent(
            """
        {"category": "bullying", "category_description": "This is bullying.", "rationale": "The message
        seems like it contains bullying.", "score_value": "True"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


@pytest.fixture
def scorer_category_response_false() -> Message:
    json_response = (
        dedent(
            """
        {"category": "no_harm", "category_description": "No harms", "rationale": "The message
        contains no harms.", "score_value": "False"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


def test_category_scorer_set_no_category_found(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    # assert that the category content was loaded into system prompt
    assert "no_harm" in scorer._system_prompt
    assert "intended to harm an individual" in scorer._system_prompt


async def test_category_scorer_set_system_prompt(scorer_category_response_bullying: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_category_response_bullying])
    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    await scorer.score_text_async("this has a lot of bullying")

    chat_target.set_system_prompt.assert_called_once()


async def test_category_scorer_score(scorer_category_response_bullying: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_category_response_bullying])

    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    score = await scorer.score_text_async("this has a lot of bullying")

    assert len(score) == 1

    assert score[0].score_value == "true"
    assert "contains bullying" in score[0].score_rationale
    assert score[0].score_type == "true_false"
    assert score[0].score_category == ["bullying"]
    assert score[0].message_piece_id is None


async def test_category_scorer_canonicalizes_boolean_value(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value='{"category": "bullying", "rationale": "Harmful", "score_value": " True "}',
            )
        ]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[response])
    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    score = await scorer.score_text_async("harmful content")

    assert score[0].score_value == "true"
    assert chat_target.send_prompt_async.call_count == 1


async def test_category_scorer_score_false(scorer_category_response_false: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_category_response_false])

    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    score = await scorer.score_text_async("this has no bullying")

    assert len(score) == 1

    assert score[0].score_value == "false"
    assert score[0].score_type == "true_false"
    assert score[0].score_category == ["no_harm"]
    assert score[0].message_piece_id is None


async def test_category_scorer_adds_to_memory(scorer_category_response_false: Message, patch_central_database):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_category_response_false])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskCategoryScorer.from_content_classifier(
            chat_target=chat_target,
            content_classifier=HARM_CLASSIFIER,
        )

        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


async def test_self_ask_objective_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        scorer = SelfAskCategoryScorer.from_content_classifier(
            chat_target=chat_target,
            content_classifier=HARM_CLASSIFIER,
        )

        with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskCategoryScorer"):
            await scorer.score_text_async("this has no bullying")
        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert chat_target.send_prompt_async.call_count == 2


async def test_self_ask_objective_scorer_json_missing_key_exception_retries(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    json_response = (
        dedent(
            """
            {"wrongly_named_category_name": "bullying",
            "category_description": "This is bullying.",
            "rationale": "The message seems like it contains bullying."}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        scorer = SelfAskCategoryScorer.from_content_classifier(
            chat_target=chat_target,
            content_classifier=HARM_CLASSIFIER,
        )

        with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskCategoryScorer"):
            await scorer.score_text_async("this has no bullying")
        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.parametrize(
    "response",
    [
        {"score_value": "True", "rationale": "Unknown", "category": "unknown"},
        {"score_value": "True", "rationale": "Missing category"},
        {"score_value": "True", "rationale": "Multiple", "category": ["bullying", "violence"]},
        {"score_value": "True", "rationale": "Malformed", "category": 123},
        {"score_value": "True", "rationale": "Malformed list", "category": ["bullying", 123]},
        {"score_value": "True", "rationale": "Fallback mismatch", "category": "no_harm"},
        {"score_value": "False", "rationale": "Harm mismatch", "category": "bullying"},
    ],
)
async def test_category_scorer_retries_responses_outside_classifier_contract(
    response: dict[str, object],
    patch_central_database,
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    invalid_response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json.dumps(response))])
    chat_target.send_prompt_async = AsyncMock(return_value=[invalid_response])
    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskCategoryScorer"):
        await scorer.score_text_async("content")

    assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.parametrize("max_requests_per_minute", [None, 10])
@pytest.mark.parametrize("batch_size", [1, 10])
async def test_score_prompts_batch_async(
    max_requests_per_minute: int,
    batch_size: int,
    scorer_category_response_false: Message,
    patch_central_database,
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock()
    chat_target._max_requests_per_minute = max_requests_per_minute
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        scorer = SelfAskCategoryScorer.from_content_classifier(
            chat_target=chat_target,
            content_classifier=HARM_CLASSIFIER,
        )

        prompt = MessagePiece(role="assistant", original_value="test").to_message()
        prompt2 = MessagePiece(role="assistant", original_value="test 2").to_message()

        with patch.object(chat_target, "send_prompt_async", return_value=[scorer_category_response_false]):
            if batch_size != 1 and max_requests_per_minute:
                with pytest.raises(ValueError):
                    await scorer.score_prompts_batch_async(messages=[prompt], batch_size=batch_size, objectives=[""])
            else:
                results = await scorer.score_prompts_batch_async(
                    messages=[prompt, prompt2], batch_size=batch_size, objectives=["", ""]
                )
                assert len(results) == 2


async def test_blocked_response_returns_false_without_invoking_llm(patch_central_database):
    """Blocked-only responses are filtered by the text-only validator and never reach the LLM.

    The unified TrueFalseScorer fallback returns Score(False) with a 'blocked' rationale.
    """
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock()

    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
    )

    blocked_piece = MessagePiece(
        role="assistant",
        original_value="",
        converted_value="",
        converted_value_data_type="error",
        response_error="blocked",
    )
    blocked_message = Message(message_pieces=[blocked_piece])

    scores = await scorer.score_async(blocked_message)

    chat_target.send_prompt_async.assert_not_called()
    assert len(scores) == 1
    assert scores[0].score_type == "true_false"
    assert scores[0].score_value == "false"
    assert "blocked" in scores[0].score_rationale.lower()


def test_category_init_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskCategoryScorer(
            chat_target=None,
            system_prompt="rubric",
            content_classifier=HARM_CLASSIFIER,
        )


def test_category_init_no_system_prompt_raises():
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    with pytest.raises(TypeError, match="system_prompt"):
        SelfAskCategoryScorer(chat_target=chat_target, content_classifier=HARM_CLASSIFIER)


def test_category_init_system_prompt_str_and_invalid_type(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskCategoryScorer(
        chat_target=chat_target,
        system_prompt="verbatim",
        content_classifier=HARM_CLASSIFIER,
    )
    assert scorer._system_prompt == "verbatim"

    with pytest.raises(TypeError, match="system_prompt must be a SeedPrompt or str"):
        SelfAskCategoryScorer(
            chat_target=chat_target,
            system_prompt=123,
            content_classifier=HARM_CLASSIFIER,
        )


def test_content_classifier_validation():
    with pytest.raises(ValueError):
        ContentClassifier(categories=[], no_category_found="no_harm")
    with pytest.raises(ValueError, match="not present in categories"):
        ContentClassifier(
            categories=[ContentClassifierCategory(name="harm", description="d")],
            no_category_found="missing_false_category",
        )
    with pytest.raises(ValueError, match="unique"):
        ContentClassifier(
            categories=[
                ContentClassifierCategory(name="harm", description="d1"),
                ContentClassifierCategory(name="harm", description="d2"),
            ],
            no_category_found="harm",
        )


def test_category_factory_supports_inline_template(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskCategoryScorer.from_content_classifier(
        chat_target=chat_target,
        content_classifier=HARM_CLASSIFIER,
        system_prompt_template="{{ categories }}Fallback: {{ no_category_found }}",
    )

    assert "bullying" in scorer._system_prompt
    assert "Fallback: no_harm" in scorer._system_prompt
