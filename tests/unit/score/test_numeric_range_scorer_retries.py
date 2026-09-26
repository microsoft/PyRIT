# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Numeric-scale scorers retry an out-of-range judge score and give up once retries run out."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions import InvalidJsonException
from pyrit.models import Message, MessagePiece
from pyrit.score import InsecureCodeScorer, NumericRange, NumericRubric, SelfAskScaleScorer
from pyrit.score.float_scale.self_ask_general_float_scale_scorer import SelfAskGeneralFloatScaleScorer


def _scale_scorer(chat_target):
    return SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value),
    )


def _general_float_scorer(chat_target):
    return SelfAskGeneralFloatScaleScorer(
        chat_target=chat_target,
        system_prompt_format_string="Prompt.",
        scale=NumericRange(minimum_value=0, maximum_value=100, category="test"),
    )


def _insecure_code_scorer(chat_target):
    return InsecureCodeScorer.from_harm_categories(chat_target=chat_target)


@pytest.mark.parametrize(
    ("build_scorer", "out_of_range_value"),
    [
        (_scale_scorer, "11"),
        (_general_float_scorer, "150"),
        (_insecure_code_scorer, "1.5"),
    ],
    ids=["SelfAskScaleScorer", "SelfAskGeneralFloatScaleScorer", "InsecureCodeScorer"],
)
async def test_out_of_range_score_on_every_attempt_raises_after_retries(
    build_scorer, out_of_range_value: str, patch_central_database
):
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=f'{{"score_value": "{out_of_range_value}", "rationale": "r", "description": "d"}}',
            )
        ]
    )
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])
    scorer = build_scorer(chat_target)

    with pytest.raises(InvalidJsonException):
        await scorer.score_text_async(text="example text", objective="task")

    # tests/unit/conftest.py pins RETRY_MAX_NUM_ATTEMPTS to 2.
    assert chat_target.send_prompt_async.call_count == 2
