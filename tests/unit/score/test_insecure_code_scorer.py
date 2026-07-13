# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, SeedPrompt, UnvalidatedScore
from pyrit.prompt_target import PromptTarget
from pyrit.score import InsecureCodeScorer


@pytest.fixture
def mock_chat_target(patch_central_database):
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockChatTarget", class_module="mock")
    return target


async def test_insecure_code_scorer_valid_response(mock_chat_target):
    # Initialize the scorer
    scorer = InsecureCodeScorer.from_harm_categories(chat_target=mock_chat_target)

    # Create an UnvalidatedScore mock response
    unvalidated_score = UnvalidatedScore(
        raw_score_value="0.8",
        score_value_description="A valid score",
        score_category=["security"],
        score_rationale="This code has potential vulnerabilities.",
        score_metadata=None,
        scorer_class_identifier=ComponentIdentifier(
            class_name="InsecureCodeScorer",
            class_module="pyrit.score",
        ),
        message_piece_id="12345",
        objective=None,
    )

    # Patch _memory.add_scores_to_memory to prevent sqlite errors and check for call
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()) as mock_add_scores:
        with patch(
            "pyrit.score.float_scale.insecure_code_scorer._run_llm_scoring_async",
            new=AsyncMock(return_value=unvalidated_score),
        ):
            # Create a message piece object
            message = MessagePiece(role="user", original_value="sample code").to_message()

            # Call the score_async method
            scores = await scorer.score_async(message)

            # Assertions
            assert len(scores) == 1
            assert isinstance(scores[0], Score)
            assert scores[0].score_value == "0.8"
            mock_add_scores.assert_called_once_with(scores=[scores[0]])


async def test_insecure_code_scorer_invalid_json(mock_chat_target):
    # Initialize the scorer
    scorer = InsecureCodeScorer.from_harm_categories(chat_target=mock_chat_target)

    # Patch scorer._memory.add_scores_to_memory to make it a mock
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()) as mock_add_scores:
        # Mock _run_llm_scoring_async to raise InvalidJsonException
        with patch(
            "pyrit.score.float_scale.insecure_code_scorer._run_llm_scoring_async",
            new=AsyncMock(side_effect=InvalidJsonException(message="Invalid JSON")),
        ):
            message = MessagePiece(role="user", original_value="sample code").to_message()

            with pytest.raises(InvalidJsonException, match="Error in scorer InsecureCodeScorer.*Invalid JSON"):
                await scorer.score_async(message)

            # Ensure memory functions were not called
            mock_add_scores.assert_not_called()


async def test_insecure_code_scorer_real_response_handler_accepts_category_snapshot(mock_chat_target):
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value='{"score_value": 0.5, "rationale": "Potential issue", "metadata": "m"}',
            )
        ]
    )
    mock_chat_target.send_prompt_async = AsyncMock(return_value=[response])
    scorer = InsecureCodeScorer.from_harm_categories(
        chat_target=mock_chat_target,
        harm_categories=["security", "privacy"],
    )

    scores = await scorer.score_text_async("sample code")

    assert scores[0].score_category == ["security", "privacy"]
    assert scores[0].get_value() == pytest.approx(0.5)


async def test_score_async_unsupported_data_type_returns_zero(mock_chat_target, patch_central_database):
    scorer = InsecureCodeScorer.from_harm_categories(chat_target=mock_chat_target)

    request = MessagePiece(
        role="assistant",
        original_value="image_data",
        converted_value="image_data",
        converted_value_data_type="image_path",
    ).to_message()

    # Unified FloatScaleScorer fallback: returns a single Score(0.0) when all pieces are filtered
    # out (mirrors TrueFalseScorer's no-pieces fallback).
    scores = await scorer.score_async(request)
    assert len(scores) == 1
    assert scores[0].score_type == "float_scale"
    assert scores[0].get_value() == 0.0


def test_insecure_code_scorer_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        InsecureCodeScorer(chat_target=None, system_prompt="rubric", harm_categories="security")


def test_insecure_code_scorer_system_prompt_variants(mock_chat_target):
    seed = SeedPrompt(value="seed rubric", data_type="text")
    scorer_seed = InsecureCodeScorer(
        chat_target=mock_chat_target,
        system_prompt=seed,
        harm_categories="security",
    )
    assert scorer_seed._system_prompt == "seed rubric"

    scorer_str = InsecureCodeScorer(
        chat_target=mock_chat_target,
        system_prompt="verbatim rubric",
        harm_categories=["security", "privacy"],
    )
    assert scorer_str._system_prompt == "verbatim rubric"

    with pytest.raises(TypeError, match="system_prompt must be a SeedPrompt or str"):
        InsecureCodeScorer(chat_target=mock_chat_target, system_prompt=123, harm_categories="security")


def test_insecure_code_factory_uses_categories_for_prompt_and_metadata(mock_chat_target):
    scorer = InsecureCodeScorer.from_harm_categories(
        chat_target=mock_chat_target,
        harm_categories=["security", "privacy"],
    )

    assert "security, privacy" in scorer._system_prompt
    assert scorer._harm_categories == ("security", "privacy")


def test_insecure_code_factory_snapshots_harm_categories(mock_chat_target):
    harm_categories = ["security"]
    scorer = InsecureCodeScorer.from_harm_categories(
        chat_target=mock_chat_target,
        harm_categories=harm_categories,
    )

    harm_categories.append("privacy")

    assert scorer._harm_categories == ("security",)
    assert "privacy" not in scorer._system_prompt
