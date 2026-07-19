# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pydantic import ValidationError
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.score import LikertScale, LikertScaleEntry, LikertScalePaths, SelfAskLikertScorer


@pytest.fixture
def likert_scale() -> LikertScale:
    return LikertScale(
        category="test_harm",
        scale_descriptions=[
            LikertScaleEntry(score_value=0, description="No harm"),
            LikertScaleEntry(score_value=3, description="Severe harm"),
        ],
    )


@pytest.fixture
def scorer_likert_response() -> Message:
    json_response = (
        dedent(
            """
            {"score_value": "0",
             "description": "No harm",
             "rationale": "The response contains no harmful content."}
            """
        )
        .strip()
        .replace("\n", " ")
    )
    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


def _mock_target(*, response: Message | None = None) -> MagicMock:
    target = MagicMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    if response is not None:
        target.send_prompt_async = AsyncMock(return_value=[response])
    return target


def _write_likert_yaml(
    tmp_path: Path,
    *,
    category: str = "test_harm",
    minimum_value: int = 0,
    maximum_value: int = 7,
) -> Path:
    path = tmp_path / "likert.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "category": category,
                "scale_descriptions": [
                    {"score_value": str(value), "description": f"Level {value}"}
                    for value in range(minimum_value, maximum_value + 1)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


async def test_likert_scorer_sets_system_prompt_and_scores(
    patch_central_database,
    scorer_likert_response: Message,
    likert_scale: LikertScale,
):
    target = _mock_target(response=scorer_likert_response)
    scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=likert_scale)

    scores = await scorer.score_text_async("No harm")

    target.set_system_prompt.assert_called_once()
    assert likert_scale.category in scorer._system_prompt
    assert "No harm" in scorer._system_prompt
    assert scores[0].score_value == "0.0"
    assert scores[0].score_category == ["test_harm"]
    assert scores[0].score_metadata == {"likert_value": 0}


async def test_likert_scorer_accepts_float_string_score_value(patch_central_database):
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value='{"score_value": "4.0", "description": "Some harm", "rationale": "Reason"}',
            )
        ]
    )
    target = _mock_target(response=response)
    scale = LikertScale(
        category="harm",
        scale_descriptions=[
            LikertScaleEntry(score_value=1, description="None"),
            LikertScaleEntry(score_value=4, description="Some"),
            LikertScaleEntry(score_value=5, description="Severe"),
        ],
    )

    score = await SelfAskLikertScorer.from_likert_scale(
        chat_target=target,
        likert_scale=scale,
    ).score_text_async("text")

    assert score[0].score_metadata["likert_value"] == 4
    assert score[0].get_value() == pytest.approx(0.75)


@pytest.mark.parametrize("raw_score", ["4", "4.5"])
async def test_likert_scorer_retries_score_not_matching_entry(
    patch_central_database,
    raw_score: str,
):
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=(f'{{"score_value": "{raw_score}", "description": "Some harm", "rationale": "Reason"}}'),
            )
        ]
    )
    target = _mock_target(response=response)
    scale = LikertScale(
        category="harm",
        scale_descriptions=[
            LikertScaleEntry(score_value=1, description="None"),
            LikertScaleEntry(score_value=5, description="Severe"),
        ],
    )
    scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskLikertScorer"):
        await scorer.score_text_async("text")

    assert target.send_prompt_async.call_count == 2


async def test_likert_scorer_adds_to_memory(scorer_likert_response: Message, likert_scale: LikertScale):
    memory = MagicMock(MemoryInterface)
    target = _mock_target(response=scorer_likert_response)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=likert_scale)
        await scorer.score_text_async(text="string")

    memory.add_scores_to_memory.assert_called_once()


async def test_likert_scorer_bad_json_retries(patch_central_database, likert_scale: LikertScale):
    target = _mock_target(response=Message(message_pieces=[MessagePiece(role="assistant", original_value="not json")]))
    scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=likert_scale)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskLikertScorer"):
        await scorer.score_text_async("text")


@pytest.mark.parametrize("minimum_value, maximum_value", [(0, 7), (2, 6), (1, 10)])
def test_likert_scale_from_yaml_derives_range(
    tmp_path: Path,
    minimum_value: int,
    maximum_value: int,
):
    scale = LikertScale.from_yaml(
        _write_likert_yaml(
            tmp_path,
            minimum_value=minimum_value,
            maximum_value=maximum_value,
        )
    )

    assert scale.minimum_value == minimum_value
    assert scale.maximum_value == maximum_value


@pytest.mark.parametrize("minimum_value, maximum_value", [(0, 7), (2, 6), (1, 10)])
def test_likert_factory_renders_dynamic_range(
    patch_central_database,
    tmp_path: Path,
    minimum_value: int,
    maximum_value: int,
):
    scale = LikertScale.from_yaml(
        _write_likert_yaml(
            tmp_path,
            minimum_value=minimum_value,
            maximum_value=maximum_value,
        )
    )
    scorer = SelfAskLikertScorer.from_likert_scale(
        chat_target=_mock_target(),
        likert_scale=scale,
    )

    assert f"{minimum_value} is the least severe" in scorer._system_prompt
    assert f"{maximum_value} is the most severe" in scorer._system_prompt


@pytest.mark.parametrize(
    "minimum_value, maximum_value, raw_score, expected",
    [
        (0, 7, 7, 1.0),
        (0, 7, 0, 0.0),
        (2, 6, 6, 1.0),
        (2, 6, 2, 0.0),
        (2, 6, 4, 0.5),
    ],
)
async def test_likert_scorer_normalizes_using_scale_object(
    patch_central_database,
    tmp_path: Path,
    minimum_value: int,
    maximum_value: int,
    raw_score: int,
    expected: float,
):
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=(f'{{"score_value": "{raw_score}", "description": "Level", "rationale": "Reason"}}'),
            )
        ]
    )
    target = _mock_target(response=response)
    scale = LikertScale.from_yaml(
        _write_likert_yaml(
            tmp_path,
            minimum_value=minimum_value,
            maximum_value=maximum_value,
        )
    )
    scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)

    score = await scorer.score_text_async("content")

    assert score[0].get_value() == pytest.approx(expected)


def test_likert_factory_supports_seed_prompt_template(
    patch_central_database,
    likert_scale: LikertScale,
):
    template = SeedPrompt(
        value=("{{ category }}: {{ likert_scale }} from {{ min_scale_value }} to {{ max_scale_value }}"),
        data_type="text",
    )

    scorer = SelfAskLikertScorer.from_likert_scale(
        chat_target=_mock_target(),
        likert_scale=likert_scale,
        system_prompt_template=template,
    )

    assert "test_harm:" in scorer._system_prompt
    assert "from 0 to 3" in scorer._system_prompt


def test_likert_factory_supports_inline_template(
    patch_central_database,
    likert_scale: LikertScale,
):
    scorer = SelfAskLikertScorer.from_likert_scale(
        chat_target=_mock_target(),
        likert_scale=likert_scale,
        system_prompt_template=(
            "{{ category }}: {{ likert_scale }} from {{ min_scale_value }} to {{ max_scale_value }}"
        ),
    )

    assert "test_harm:" in scorer._system_prompt
    assert "from 0 to 3" in scorer._system_prompt


def test_likert_factory_rejects_template_missing_required_parameters(
    likert_scale: LikertScale,
):
    with pytest.raises(ValueError, match="must reference these parameters"):
        SelfAskLikertScorer.from_likert_scale(
            chat_target=_mock_target(),
            likert_scale=likert_scale,
            system_prompt_template="{{ category }}",
        )


def test_likert_preset_loads_scale_and_evaluation_metadata():
    scale = LikertScalePaths.CYBER_SCALE.load()

    assert scale.category == "cyber"
    assert scale.evaluation_files == LikertScalePaths.CYBER_SCALE.evaluation_files


def test_likert_factory_applies_evaluation_metadata(patch_central_database):
    scale = LikertScalePaths.EXPLOITS_SCALE.load()
    scorer = SelfAskLikertScorer.from_likert_scale(
        chat_target=_mock_target(),
        likert_scale=scale,
    )

    assert scorer.evaluation_file_mapping is not None
    assert scorer.evaluation_file_mapping.harm_category == scale.evaluation_files.harm_category


@pytest.mark.parametrize(
    "contents, expected_error",
    [
        ({"scale_descriptions": [{"score_value": 1, "description": "Level"}]}, "category"),
        ({"category": "harm"}, "scale_descriptions"),
        (
            {
                "category": "harm",
                "scale_descriptions": [{"score_value": -1, "description": "Level"}],
            },
            "non-negative integer",
        ),
        (
            {
                "category": "harm",
                "scale_descriptions": [{"score_value": "1.5", "description": "Level"}],
            },
            "non-negative integer",
        ),
        (
            {
                "category": "harm",
                "scale_descriptions": [{"score_value": 1}],
            },
            "description",
        ),
        (
            {
                "category": "harm",
                "scale_descriptions": [{"score_value": 1, "description": "Only"}],
            },
            "unique and strictly increasing",
        ),
        (
            {
                "category": "harm",
                "scale_descriptions": [
                    {"score_value": 1, "description": "First"},
                    {"score_value": 1, "description": "Duplicate"},
                ],
            },
            "unique and strictly increasing",
        ),
        (
            {
                "category": "harm",
                "scale_descriptions": [
                    {"score_value": 2, "description": "Second"},
                    {"score_value": 1, "description": "First"},
                ],
            },
            "unique and strictly increasing",
        ),
    ],
)
def test_likert_scale_rejects_invalid_yaml(
    tmp_path: Path,
    contents: dict[str, object],
    expected_error: str,
):
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(contents), encoding="utf-8")

    with pytest.raises((ValueError, ValidationError), match=expected_error):
        LikertScale.from_yaml(path)


def test_likert_scale_rejects_non_mapping_yaml(tmp_path: Path):
    path = tmp_path / "invalid.yaml"
    path.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping"):
        LikertScale.from_yaml(path)


def test_likert_scale_snapshots_entry_sequence():
    entries = [
        LikertScaleEntry(score_value=0, description="None"),
        LikertScaleEntry(score_value=1, description="Some"),
    ]
    scale = LikertScale(category="harm", scale_descriptions=entries)

    entries.append(LikertScaleEntry(score_value=2, description="Severe"))

    assert scale.entries == (
        LikertScaleEntry(score_value=0, description="None"),
        LikertScaleEntry(score_value=1, description="Some"),
    )


def test_likert_init_no_chat_target_raises(likert_scale: LikertScale):
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskLikertScorer(
            chat_target=None,
            system_prompt="rubric",
            likert_scale=likert_scale,
        )


def test_likert_init_system_prompt_variants(
    patch_central_database,
    likert_scale: LikertScale,
):
    target = _mock_target()
    scorer = SelfAskLikertScorer(
        chat_target=target,
        system_prompt="verbatim rubric",
        likert_scale=likert_scale,
    )
    assert scorer._system_prompt == "verbatim rubric"

    with pytest.raises(TypeError, match="system_prompt must be a SeedPrompt or str"):
        SelfAskLikertScorer(
            chat_target=target,
            system_prompt=123,
            likert_scale=likert_scale,
        )
