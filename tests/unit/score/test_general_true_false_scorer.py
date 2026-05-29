# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import Message, MessagePiece
from pyrit.score import SelfAskGeneralTrueFalseScorer


@pytest.fixture
def general_scorer_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True",
         "rationale": "This is the rationale.",
         "description": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


async def test_general_scorer_score_async(patch_central_database, general_scorer_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_scorer_response])

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        category="test_category",
    )
    assert scorer

    score = await scorer.score_text_async(
        text="test prompt",
    )

    assert len(score) == 1
    assert score[0].score_value == "true"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


async def test_general_scorer_score_async_with_prompt_f_string(
    general_scorer_response: Message, patch_central_database
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_scorer_response])

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="Is this cyberbullying? {prompt}",
        category="test_category",
    )

    score = await scorer.score_text_async(
        text="this is a test prompt",
    )

    assert len(score) == 1
    assert score[0].score_value == "true"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description
    args = chat_target.send_prompt_async.call_args
    prompt = args[1]["message"].message_pieces[0].converted_value
    assert prompt == "Is this cyberbullying? this is a test prompt"


async def test_general_scorer_score_async_handles_custom_keys(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    assert chat_target

    json_response = (
        dedent(
            """
        {"score_value": "false",
         "rationale_diff_key": "This is the rationale.",
         "description": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )
    # Simulate a response missing some keys
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    assert chat_target

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="This is a prompt format string.",
        category="test_category",
        rationale_output_key="rationale_diff_key",
    )
    score = await scorer.score_text_async(text="this is a test prompt")
    assert len(score) == 1
    assert score[0].score_value == "false"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


def test_true_false_get_scorer_metrics_returns_none_when_eval_hash_is_none(patch_central_database):
    """Test that TrueFalseScorer.get_scorer_metrics returns None when eval_hash is None."""
    from unittest.mock import patch as _patch

    from pyrit.score.true_false.self_ask_true_false_scorer import (
        SelfAskTrueFalseScorer,
    )

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer(chat_target=chat_target)
    mock_identifier = MagicMock()
    mock_identifier.eval_hash = None
    with _patch.object(scorer, "get_identifier", return_value=mock_identifier):
        result = scorer.get_scorer_metrics()
    assert result is None


def test_true_false_get_scorer_metrics_returns_metrics_when_eval_hash_is_set(patch_central_database):
    """Test that TrueFalseScorer.get_scorer_metrics returns metrics when eval_hash is set."""
    from unittest.mock import patch as _patch

    from pyrit.score.true_false.self_ask_true_false_scorer import (
        SelfAskTrueFalseScorer,
    )

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer(chat_target=chat_target)

    mock_identifier = MagicMock()
    mock_identifier.eval_hash = "abc123"

    mock_metrics = MagicMock()
    mock_result_file = MagicMock()
    mock_result_file.exists.return_value = True

    with _patch.object(scorer, "get_identifier", return_value=mock_identifier):
        with _patch(
            "pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash",
            return_value=mock_metrics,
        ) as mock_find:
            with _patch(
                "pyrit.common.path.SCORER_EVALS_PATH",
                new=MagicMock(__truediv__=MagicMock(return_value=mock_result_file)),
            ):
                result = scorer.get_scorer_metrics()

    assert result is mock_metrics
    mock_find.assert_called_once()
