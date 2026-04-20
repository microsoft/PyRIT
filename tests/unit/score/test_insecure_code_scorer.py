# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import MessagePiece, Score, UnvalidatedScore
from pyrit.prompt_target import PromptChatTarget
from pyrit.score import InsecureCodeScorer


@pytest.fixture
def mock_chat_target(patch_central_database):
    return MagicMock(spec=PromptChatTarget)


@pytest.mark.asyncio
async def test_insecure_code_scorer_valid_response(mock_chat_target):
    # Initialize the scorer
    scorer = InsecureCodeScorer(
        chat_target=mock_chat_target,
    )

    # Create an UnvalidatedScore mock response
    unvalidated_score = UnvalidatedScore(
        raw_score_value="0.8",
        score_value_description="A valid score",
        score_category=["security"],
        score_rationale="This code has potential vulnerabilities.",
        score_metadata=None,
        scorer_class_identifier=ScorerIdentifier(
            class_name="InsecureCodeScorer",
            class_module="pyrit.score",
            class_description="",
            identifier_type="instance",
        ),
        message_piece_id="12345",
        objective=None,
    )

    # Patch _memory.add_scores_to_memory to prevent sqlite errors and check for call
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()) as mock_add_scores:
        with patch.object(scorer, "_score_value_with_llm", new=AsyncMock(return_value=unvalidated_score)):
            # Create a message piece object
            message = MessagePiece(role="user", original_value="sample code").to_message()

            # Call the score_async method
            scores = await scorer.score_async(message)

            # Assertions
            assert len(scores) == 1
            assert isinstance(scores[0], Score)
            assert scores[0].score_value == "0.8"
            mock_add_scores.assert_called_once_with(scores=[scores[0]])


@pytest.mark.asyncio
async def test_insecure_code_scorer_invalid_json(mock_chat_target):
    # Initialize the scorer
    scorer = InsecureCodeScorer(
        chat_target=mock_chat_target,
    )

    # Patch scorer._memory.add_scores_to_memory to make it a mock
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()) as mock_add_scores:
        # Mock _score_value_with_llm to raise InvalidJsonException
        with patch.object(
            scorer, "_score_value_with_llm", new=AsyncMock(side_effect=InvalidJsonException(message="Invalid JSON"))
        ):
            message = MessagePiece(role="user", original_value="sample code").to_message()

            with pytest.raises(InvalidJsonException, match="Error in scorer InsecureCodeScorer.*Invalid JSON"):
                await scorer.score_async(message)

            # Ensure memory functions were not called
            mock_add_scores.assert_not_called()


@pytest.mark.asyncio
async def test_score_async_unsupported_data_type_returns_empty_list(mock_chat_target, patch_central_database):
    scorer = InsecureCodeScorer(
        chat_target=mock_chat_target,
    )

    request = MessagePiece(
        role="assistant",
        original_value="image_data",
        converted_value="image_data",
        converted_value_data_type="image_path",
    ).to_message()

    # With raise_on_no_valid_pieces=False (default), returns empty list for unsupported data types
    # (FloatScaleScorer does not create synthetic scores like TrueFalseScorer)
    scores = await scorer.score_async(request)
    assert len(scores) == 0
