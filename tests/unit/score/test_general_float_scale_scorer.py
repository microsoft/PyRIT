# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import Message, MessagePiece
from pyrit.score.float_scale.self_ask_general_float_scale_scorer import (
    SelfAskGeneralFloatScaleScorer,
)


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


@pytest.mark.asyncio
async def test_general_float_scorer_score_async(patch_central_database, general_float_scorer_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_float_scorer_response])

    scorer = SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        category="test_category",
    )
    assert scorer

    score = await scorer.score_text_async(text="test prompt", objective="test objective")

    assert len(score) == 1
    # 75/100 = 0.75
    assert abs(float(score[0].score_value) - 0.75) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


@pytest.mark.asyncio
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
        category="test_category",
    )

    score = await scorer.score_text_async(text="this is a test prompt", objective="test objective")

    assert len(score) == 1
    assert abs(float(score[0].score_value) - 0.75) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description
    args = chat_target.send_prompt_async.call_args
    prompt = args[1]["message"].message_pieces[0].converted_value
    assert prompt == "Rate this: this is a test prompt"


@pytest.mark.asyncio
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
        category="test_category",
        score_value_output_key="score_custom",
        rationale_output_key="rationale_custom",
        description_output_key="description_custom",
        min_value=0,
        max_value=100,
    )
    score = await scorer.score_text_async(text="this is a test prompt", objective="test objective")
    assert len(score) == 1
    assert abs(float(score[0].score_value) - 0.42) < 1e-6
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


@pytest.mark.asyncio
async def test_general_float_scorer_score_async_min_max_scale(patch_central_database):
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
        category="cat",
        min_value=0,
        max_value=10,
    )
    score = await scorer.score_text_async(text="prompt", objective="obj")
    assert len(score) == 1
    # 5/10 = 0.5
    assert abs(float(score[0].score_value) - 0.5) < 1e-6
    assert "Rationale." in score[0].score_rationale
    assert "Description." in score[0].score_value_description


def test_general_float_scorer_init_invalid_min_max():
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    with pytest.raises(ValueError):
        SelfAskGeneralFloatScaleScorer(
            chat_target=chat_target,
            system_prompt_format_string="Prompt.",
            min_value=10,
            max_value=5,
        )
