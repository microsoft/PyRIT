# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import ComponentIdentifier, Message, MessagePiece, SeedPrompt, UnvalidatedScore
from pyrit.score import ContentClassifierPaths, NumericRubric, SelfAskScaleScorer

tree_scale_path = SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value
task_scale_path = SelfAskScaleScorer.ScalePaths.TASK_ACHIEVED_SCALE.value
criteria_scale_path = SelfAskScaleScorer.ScalePaths.CRITERIA_SCALE.value
general_system_prompt_path = SelfAskScaleScorer.SystemPaths.GENERAL_SYSTEM_PROMPT.value
red_teamer_system_prompt_path = SelfAskScaleScorer.SystemPaths.RED_TEAMER_SYSTEM_PROMPT.value
criteria_system_prompt_path = SelfAskScaleScorer.SystemPaths.CRITERIA_SYSTEM_PROMPT.value


@pytest.fixture
def scorer_scale_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "1",
         "rationale": "rationale",
         "description": "description"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


@pytest.fixture
def scale_scorer(patch_central_database) -> SelfAskScaleScorer:
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    return SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value),
    )


@pytest.mark.parametrize(
    "scale_path, system_prompt_path",
    [
        (None, None),
        (tree_scale_path, general_system_prompt_path),
        (task_scale_path, red_teamer_system_prompt_path),
        (tree_scale_path, red_teamer_system_prompt_path),
        (task_scale_path, general_system_prompt_path),
        (criteria_scale_path, criteria_system_prompt_path),
    ],
)
async def test_scale_scorer_set_system_prompt(
    scorer_scale_response: Message,
    scale_path: Path | None,
    system_prompt_path: Path | None,
    patch_central_database,
):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_scale_response])

    scorer = SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(scale_path) if scale_path else None,
        system_prompt_template=SeedPrompt.from_yaml_file(system_prompt_path) if system_prompt_path else None,
    )

    await scorer.score_text_async(text="string", objective="task")

    chat_target.set_system_prompt.assert_called_once()

    # assert that the scale score was loaded into system prompt

    assert scorer._system_prompt
    assert str(scorer._scale.minimum_value) in scorer._system_prompt
    assert str(scorer._scale.maximum_value) in scorer._system_prompt


def test_scale_scorer_invalid_scale_file_contents():
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    # When using a YAML with wrong keys the NumericRubric constructor will raise an exception.
    with pytest.raises(ValueError):
        NumericRubric.from_yaml(ContentClassifierPaths.HARMFUL_CONTENT_CLASSIFIER.value)


@pytest.mark.parametrize(
    "scale_args",
    [
        {
            "minimum_value": 0,
            "maximum_value": 1,
        },
        {
            "minimum_value": 0,
            "category": "category",
        },
        {
            "maximum_value": 1,
            "category": "category",
        },
        {
            "minimum_value": 0,
            "maximum_value": 1,
            "category": None,
        },
        {
            "minimum_value": "Blah",
            "maximum_value": 1,
            "category": "test",
        },
        {
            "minimum_value": 2,
            "maximum_value": 1,
            "category": "test",
        },
        {
            "minimum_value": 1,
            "maximum_value": "Blah",
            "category": "test",
        },
    ],
)
def test_validate_scale_arguments_missing_args_raises_value_error(scale_args: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        NumericRubric.model_validate(scale_args)


async def test_scale_scorer_score(scorer_scale_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_scale_response])

    scorer = SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value),
    )

    score = await scorer.score_text_async(text="example text", objective="task")

    assert len(score) == 1

    assert score[0].score_value == "0.0"
    assert score[0].get_value() == 0
    assert "description" in score[0].score_value_description
    assert "rationale" in score[0].score_rationale
    assert score[0].score_type == "float_scale"
    assert score[0].score_category == ["jailbreak"]
    assert score[0].message_piece_id is None
    assert score[0].objective == "task"


async def test_scale_scorer_score_custom_scale(scorer_scale_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    # set a higher score to test the scaling
    scorer_scale_response.message_pieces[0].original_value = scorer_scale_response.message_pieces[
        0
    ].original_value.replace("1", "53")
    scorer_scale_response.message_pieces[0].converted_value = scorer_scale_response.message_pieces[0].original_value

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_scale_response])

    scale = NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value).model_copy(
        update={"minimum_value": 1, "maximum_value": 100}
    )
    scorer = SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=scale,
    )

    score = await scorer.score_text_async(text="example text", objective="task")

    assert len(score) == 1

    expected_score_value = (53 - 1) / (100 - 1)
    assert score[0].score_value == str(expected_score_value)
    assert score[0].get_value() == expected_score_value
    assert "description" in score[0].score_value_description
    assert "rationale" in score[0].score_rationale
    assert score[0].score_type == "float_scale"
    assert score[0].score_category == ["jailbreak"]
    assert score[0].message_piece_id is None
    assert score[0].objective == "task"


async def test_scale_scorer_score_calls_send_chat(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value),
    )

    score = UnvalidatedScore(
        raw_score_value="1",
        score_rationale="rationale",
        score_category=["jailbreak"],
        score_value_description="description",
        score_metadata={"meta": "metadata"},
        scorer_class_identifier=ComponentIdentifier(
            class_name="SelfAskScaleScorer",
            class_module="pyrit.score",
        ),
        message_piece_id=str(uuid.uuid4()),
        objective="task",
    )

    with patch(
        "pyrit.score.float_scale.self_ask_scale_scorer._run_llm_scoring_async",
        new=AsyncMock(return_value=score),
    ) as mock_run:
        await scorer.score_text_async(text="example text", objective="task")
    assert mock_run.call_count == 1


async def test_scale_scorer_non_text_sends_prepended_text(patch_central_database):
    """Test that non-text content (e.g., image_path) uses prepended text for objective context."""
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskScaleScorer.from_scale(
        chat_target=chat_target,
        scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TASK_ACHIEVED_SCALE.value),
        validator=ScorerPromptValidator(supported_data_types=["image_path"], is_objective_required=True),
    )

    score = UnvalidatedScore(
        raw_score_value="95",
        score_rationale="Image matches objective",
        score_category=["task_achievement"],
        score_value_description="description",
        score_metadata={},
        scorer_class_identifier=ComponentIdentifier(
            class_name="SelfAskScaleScorer",
            class_module="pyrit.score",
        ),
        message_piece_id=str(uuid.uuid4()),
        objective="Generate a cat",
    )

    with patch(
        "pyrit.score.float_scale.self_ask_scale_scorer._run_llm_scoring_async",
        new=AsyncMock(return_value=score),
    ) as mock_run:
        await scorer.score_image_async(image_path="/path/to/image.png", objective="Generate a cat")

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    # Non-text content should send prepended_text with objective
    assert call_kwargs.kwargs["prepended_text"] == "objective: Generate a cat\nresponse:"
    assert call_kwargs.kwargs["data_type"] == "image_path"
    assert call_kwargs.kwargs["value"] == "/path/to/image.png"


def test_scale_init_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskScaleScorer(
            chat_target=None,
            system_prompt="rubric",
            scale=NumericRubric(minimum_value=0, maximum_value=1, category="test"),
        )


def test_scale_factory_default_system_prompt(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskScaleScorer.from_scale(chat_target=chat_target)
    assert scorer._system_prompt
    assert scorer._scale.minimum_value < scorer._scale.maximum_value


def test_scale_factory_renders_minimal_inline_scale(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scale = NumericRubric(minimum_value=0, maximum_value=10, category="custom")

    scorer = SelfAskScaleScorer.from_scale(chat_target=chat_target, scale=scale)

    assert "scale from 0 to 10" in scorer._system_prompt
    assert "{{" not in scorer._system_prompt


def test_scale_init_system_prompt_str_and_invalid_type(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scale = NumericRubric(minimum_value=1, maximum_value=7, category="c")
    scorer = SelfAskScaleScorer(chat_target=chat_target, system_prompt="verbatim", scale=scale)
    assert scorer._system_prompt == "verbatim"

    with pytest.raises(TypeError, match="system_prompt must be a SeedPrompt or str"):
        SelfAskScaleScorer(chat_target=chat_target, system_prompt=123, scale=scale)
