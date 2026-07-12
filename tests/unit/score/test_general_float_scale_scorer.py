# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import Message, MessagePiece
from pyrit.score import NumericRange
from pyrit.score.float_scale.self_ask_general_float_scale_scorer import (
    SelfAskGeneralFloatScaleScorer,
)

DEFAULT_RANGE = NumericRange(minimum_value=0, maximum_value=100, category="test_category")


@pytest.fixture
def general_float_scorer_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": 75,
         "rationale": "This is the rationale.",
         "description": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )
    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


async def test_general_float_scorer_score_async(patch_central_database, general_float_scorer_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_float_scorer_response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        scale=DEFAULT_RANGE,
    )
    assert scorer

    score = await scorer.score_text_async(text="test prompt", objective="test objective")

    assert len(score) == 1
    # 75/100 = 0.75
    assert abs(float(score[0].score_value) - 0.75) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


async def test_general_float_scorer_score_async_with_prompt_f_string(
    general_float_scorer_response: Message, patch_central_database
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_float_scorer_response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="Rate this: {prompt}",
        scale=DEFAULT_RANGE,
    )

    score = await scorer.score_text_async(text="this is a test prompt", objective="test objective")

    assert len(score) == 1
    assert abs(float(score[0].score_value) - 0.75) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description
    args = chat_target.send_prompt_async.call_args
    prompt = args[1]["message"].message_pieces[0].converted_value
    assert prompt == "Rate this: this is a test prompt"


async def test_general_float_scorer_forwards_response_json_schema(
    patch_central_database, general_float_scorer_response: Message
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_float_scorer_response])

    schema = {
        "type": "object",
        "properties": {
            "score_value": {"type": "string"},
            "description": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["score_value", "description", "rationale"],
        "additionalProperties": False,
    }
    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        scale=DEFAULT_RANGE,
        response_json_schema=schema,
    )

    await scorer.score_text_async(text="test prompt", objective="test objective")

    _, kwargs = chat_target.send_prompt_async.call_args
    message_piece = kwargs["message"].message_pieces[-1]
    assert message_piece.prompt_metadata["json_schema"] == schema
    assert scorer.get_identifier().params["response_json_schema"] == schema


async def test_general_float_scorer_omits_schema_when_not_provided(
    patch_central_database, general_float_scorer_response: Message
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_float_scorer_response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        scale=DEFAULT_RANGE,
    )

    await scorer.score_text_async(text="test prompt", objective="test objective")

    _, kwargs = chat_target.send_prompt_async.call_args
    message_piece = kwargs["message"].message_pieces[-1]
    assert "json_schema" not in message_piece.prompt_metadata
    assert message_piece.prompt_metadata.get("response_format") == "json"


async def test_general_float_scorer_score_async_handles_custom_keys(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    assert chat_target

    json_response = (
        dedent(
            """
        {"score_custom": 42,
         "rationale_custom": "This is the rationale.",
         "description_custom": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="This is a prompt format string.",
        scale=DEFAULT_RANGE,
        score_value_output_key="score_custom",
        rationale_output_key="rationale_custom",
        description_output_key="description_custom",
    )
    score = await scorer.score_text_async(text="this is a test prompt", objective="test objective")
    assert len(score) == 1
    assert abs(float(score[0].score_value) - 0.42) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


async def test_general_float_scorer_score_async_min_max_range(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    json_response = (
        dedent(
            """
        {"score_value": 5,
         "rationale": "Rationale.",
         "description": "Description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="Prompt.",
        scale=NumericRange(minimum_value=0, maximum_value=10, category="cat"),
    )
    score = await scorer.score_text_async(text="prompt", objective="obj")
    assert len(score) == 1
    # 5/10 = 0.5
    assert abs(float(score[0].score_value) - 0.5) < 1e-6
    assert "Rationale." in score[0].score_rationale
    assert "Description." in score[0].score_value_description


def test_general_float_scorer_init_invalid_min_max():
    with pytest.raises(ValueError):
        NumericRange(minimum_value=10, maximum_value=5, category="test")


def test_get_scorer_metrics_returns_none_when_eval_hash_is_none(patch_central_database):
    """Test that get_scorer_metrics returns None when eval_hash is None."""
    from unittest.mock import patch as _patch

    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="Prompt.",
        scale=DEFAULT_RANGE,
    )
    # Set evaluation_file_mapping with harm_category so the early return before eval_hash is bypassed
    scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["harm/*.csv"],
        result_file="harm/test_metrics.jsonl",
        harm_category="hate_speech",
    )
    # Mock get_identifier to return an identifier with eval_hash=None
    mock_identifier = MagicMock()
    mock_identifier.eval_hash = None
    with _patch.object(scorer, "get_identifier", return_value=mock_identifier):
        result = scorer.get_scorer_metrics()
    assert result is None


def test_general_float_scale_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskGeneralFloatScaleScorer(
            chat_target=None,
            system_prompt_format_string="prompt",
            scale=DEFAULT_RANGE,
        )
