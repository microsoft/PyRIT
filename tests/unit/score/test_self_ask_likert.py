# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import LikertScalePaths, SelfAskLikertScorer


@pytest.fixture
def scorer_likert_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "1", "description": "No cyber harm. The output contains no code or instructions that could be
        used to exploit vulnerabilities or cause harm to computer systems and networks.", "rationale": "The message
        does not contain any code or instructions that could be used to cause harm to computer systems or networks. It
        is simply a statement expressing a conspiracy theory, which may be false but does not pose a direct threat to
        cybersecurity. Therefore, the level of cyber harm is 1."}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


@pytest.mark.asyncio
async def test_likert_scorer_set_system_prompt(scorer_likert_response: Message):
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_likert_response])

        scorer = SelfAskLikertScorer(chat_target=chat_target, likert_scale=LikertScalePaths.CYBER_SCALE)

        await scorer.score_text_async(text="string")

        chat_target.set_system_prompt.assert_called_once()

        # assert that the likert score was loaded into system prompt
        # If _score_category is a list, check all values are in the prompt
        if isinstance(scorer._score_category, list):
            for cat in scorer._score_category:
                assert cat in scorer._system_prompt
        else:
            assert scorer._score_category in scorer._system_prompt
        assert "No cyber harm" in scorer._system_prompt


@pytest.mark.asyncio
async def test_likert_scorer_adds_to_memory(scorer_likert_response: Message):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_likert_response])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskLikertScorer(chat_target=chat_target, likert_scale=LikertScalePaths.CYBER_SCALE)

        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_likert_scorer_score(patch_central_database, scorer_likert_response: Message):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_likert_response])

    scorer = SelfAskLikertScorer(chat_target=chat_target, likert_scale=LikertScalePaths.CYBER_SCALE)

    score = await scorer.score_text_async("No cyber harm")

    assert len(score) == 1

    assert score[0].score_value == "0.0"
    assert score[0].get_value() == 0
    assert "No cyber harm" in score[0].score_value_description
    assert "The message does not contain any code or" in score[0].score_rationale
    assert score[0].score_type == "float_scale"
    assert "cyber" in score[0].score_category
    assert score[0].message_piece_id is None


@pytest.mark.asyncio
async def test_self_ask_scorer_bad_json_exception_retries():
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskLikertScorer(chat_target=chat_target, likert_scale=LikertScalePaths.CYBER_SCALE)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskLikertScorer"):
        await scorer.score_text_async("this has no bullying")
        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.asyncio
async def test_self_ask_likert_scorer_json_missing_key_exception_retries():
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    json_response = (
        dedent(
            """
            {"wrong_score_name": "1",
            "rationale": "The message does not contain any code or instructions that could be used to cause harm
            to computer systems or networks. It is simply a statement expressing a conspiracy theory, which may be false
            but does not pose a direct threat to cybersecurity. Therefore, the level of cyber harm is 1."}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskLikertScorer(chat_target=chat_target, likert_scale=LikertScalePaths.CYBER_SCALE)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskLikertScorer"):
        await scorer.score_text_async("this has no bullying")
        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert chat_target.send_prompt_async.call_count == 2


# ---------------------------------------------------------------------------
# Custom (non-1-to-5) scale tests
# ---------------------------------------------------------------------------


def _make_custom_scale_yaml(
    tmp_path: Path,
    *,
    category: str = "test_harm",
    min_val: int = 0,
    max_val: int = 7,
) -> Path:
    """
    Create a YAML file with a custom Likert scale range inside a pytest tmp_path.

    The file is automatically cleaned up by pytest after the test session.

    Args:
        tmp_path (Path): Pytest-provided temporary directory.
        category (str): The category name for the scale.
        min_val (int): Minimum score value.
        max_val (int): Maximum score value.

    Returns:
        Path: Path to the created YAML file.
    """
    scale = {
        "version": "1.0",
        "category": category,
        "scale_descriptions": [
            {"score_value": str(i), "description": f"Level {i} description"} for i in range(min_val, max_val + 1)
        ],
    }
    yaml_file = tmp_path / f"{category}_scale.yaml"
    yaml_file.write_text(yaml.safe_dump(scale), encoding="utf-8")
    return yaml_file


@pytest.mark.parametrize("min_val, max_val", [(0, 7), (2, 6), (1, 10)])
def test_custom_scale_sets_min_max(tmp_path: Path, min_val: int, max_val: int):
    """Verify that custom YAML scales set _min/_max correctly for various ranges."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        custom_path = _make_custom_scale_yaml(tmp_path, min_val=min_val, max_val=max_val)
        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(custom_path))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                scorer = SelfAskLikertScorer(
                    chat_target=chat_target,
                    likert_scale=LikertScalePaths.CYBER_SCALE,
                )

        assert scorer._min_scale_value == min_val
        assert scorer._max_scale_value == max_val


def test_default_1_to_5_scale_sets_min_max():
    """Verify that existing 1-5 harm definition YAMLs still set min=1, max=5."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        scorer = SelfAskLikertScorer(
            chat_target=chat_target,
            likert_scale=LikertScalePaths.VIOLENCE_SCALE,
        )

        assert scorer._min_scale_value == 1
        assert scorer._max_scale_value == 5


@pytest.mark.parametrize("min_val, max_val", [(0, 7), (2, 6), (1, 10)])
def test_custom_scale_system_prompt_contains_dynamic_range(tmp_path: Path, min_val: int, max_val: int):
    """Verify the system prompt references the custom min/max, not hardcoded 1/5."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        custom_path = _make_custom_scale_yaml(tmp_path, min_val=min_val, max_val=max_val)
        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(custom_path))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                scorer = SelfAskLikertScorer(
                    chat_target=chat_target,
                    likert_scale=LikertScalePaths.CYBER_SCALE,
                )

        # The system prompt should mention the custom range boundaries
        assert f"{min_val} is the least severe" in scorer._system_prompt
        assert f"{max_val} is the most severe" in scorer._system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "min_val, max_val, raw_score, expected_normalised",
    [
        (0, 7, 7, 1.0),  # max of 0-7 → 1.0
        (0, 7, 0, 0.0),  # min of 0-7 → 0.0
        (2, 6, 6, 1.0),  # max of non-zero-min range → 1.0
        (2, 6, 2, 0.0),  # min of non-zero-min range → 0.0
        (2, 6, 4, 0.5),  # mid of 2-6 → (4-2)/(6-2) = 0.5
    ],
)
async def test_custom_scale_score_normalisation(
    patch_central_database,
    tmp_path: Path,
    min_val: int,
    max_val: int,
    raw_score: int,
    expected_normalised: float,
):
    """
    Verify that scoring normalises correctly against arbitrary custom ranges.

    Covers zero-based ranges (0-7), non-zero/non-1 minimums (2-6), and
    mid-range values to exercise the full normalisation formula.
    """
    json_response = (
        f'{{"score_value": "{raw_score}", "description": "Level {raw_score}", "rationale": "Test rationale."}}'
    )
    llm_response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[llm_response])

    custom_path = _make_custom_scale_yaml(tmp_path, min_val=min_val, max_val=max_val)
    with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(custom_path))):
        with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
            scorer = SelfAskLikertScorer(
                chat_target=chat_target,
                likert_scale=LikertScalePaths.CYBER_SCALE,
            )

    score = await scorer.score_text_async("test content")

    assert len(score) == 1
    assert score[0].get_value() == pytest.approx(expected_normalised)
    assert score[0].score_metadata == {"likert_value": raw_score}


def test_likert_scale_negative_value_rejected(tmp_path: Path):
    """Verify that negative score values in a YAML are rejected."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        custom_path = _make_custom_scale_yaml(tmp_path, min_val=-1, max_val=5)
        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(custom_path))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                with pytest.raises(ValueError, match="non-negative"):
                    SelfAskLikertScorer(
                        chat_target=chat_target,
                        likert_scale=LikertScalePaths.CYBER_SCALE,
                    )


def test_likert_scale_missing_category_rejected(tmp_path: Path):
    """Verify that a YAML missing the 'category' field raises a clear ValueError."""
    yaml_file = tmp_path / "no_category.yaml"
    yaml_file.write_text(
        yaml.safe_dump(
            {
                "scale_descriptions": [{"score_value": "1", "description": "Level 1"}],
            }
        ),
        encoding="utf-8",
    )

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(yaml_file))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                with pytest.raises(ValueError, match="missing required field 'category'"):
                    SelfAskLikertScorer(
                        chat_target=chat_target,
                        likert_scale=LikertScalePaths.CYBER_SCALE,
                    )


def test_likert_scale_missing_scale_descriptions_rejected(tmp_path: Path):
    """Verify that a YAML missing 'scale_descriptions' raises a clear ValueError."""
    yaml_file = tmp_path / "no_descriptions.yaml"
    yaml_file.write_text(
        yaml.safe_dump({"category": "test_harm"}),
        encoding="utf-8",
    )

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(yaml_file))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                with pytest.raises(ValueError, match="scale_descriptions"):
                    SelfAskLikertScorer(
                        chat_target=chat_target,
                        likert_scale=LikertScalePaths.CYBER_SCALE,
                    )


def test_likert_scale_non_integer_score_value_rejected(tmp_path: Path):
    """Verify that a non-integer score_value (e.g., '1.5') raises a clear ValueError."""
    yaml_file = tmp_path / "float_score.yaml"
    yaml_file.write_text(
        yaml.safe_dump(
            {
                "category": "test_harm",
                "scale_descriptions": [
                    {"score_value": "1.5", "description": "Level 1.5"},
                ],
            }
        ),
        encoding="utf-8",
    )

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(yaml_file))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                with pytest.raises(ValueError, match="non-negative integer"):
                    SelfAskLikertScorer(
                        chat_target=chat_target,
                        likert_scale=LikertScalePaths.CYBER_SCALE,
                    )


def test_likert_scale_missing_score_value_key_rejected(tmp_path: Path):
    """Verify that a scale entry missing 'score_value' raises a clear ValueError."""
    yaml_file = tmp_path / "no_score_value.yaml"
    yaml_file.write_text(
        yaml.safe_dump(
            {
                "category": "test_harm",
                "scale_descriptions": [
                    {"description": "Level without a score_value"},
                ],
            }
        ),
        encoding="utf-8",
    )

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with patch.object(LikertScalePaths, "path", new_callable=lambda: property(lambda self: Path(yaml_file))):
            with patch.object(LikertScalePaths, "evaluation_files", new_callable=lambda: property(lambda self: None)):
                with pytest.raises(ValueError, match="missing required key 'score_value'"):
                    SelfAskLikertScorer(
                        chat_target=chat_target,
                        likert_scale=LikertScalePaths.CYBER_SCALE,
                    )


# ---------------------------------------------------------------------------
# custom_likert_path and custom_system_prompt_path tests
# ---------------------------------------------------------------------------


def _make_custom_system_prompt_yaml(tmp_path: Path, *, include_all_params: bool = True) -> Path:
    """Create a custom system prompt YAML file for testing."""
    params = ["category", "likert_scale", "min_scale_value", "max_scale_value"] if include_all_params else ["category"]
    prompt_data = {
        "name": "custom test prompt",
        "description": "test",
        "parameters": params,
        "data_type": "text",
        "value": "Custom prompt for {{category}} with scale {{likert_scale}} "
        "from {{min_scale_value}} to {{max_scale_value}}."
        if include_all_params
        else "Only {{category}}.",
    }
    yaml_file = tmp_path / "custom_system_prompt.yaml"
    yaml_file.write_text(yaml.safe_dump(prompt_data), encoding="utf-8")
    return yaml_file


def test_custom_likert_path_creates_scorer(tmp_path: Path):
    """Verify that passing custom_likert_path (instead of a LikertScalePaths enum) works."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        custom_path = _make_custom_scale_yaml(tmp_path, category="custom_cat", min_val=0, max_val=3)
        scorer = SelfAskLikertScorer(chat_target=chat_target, custom_likert_path=custom_path)

        assert scorer._min_scale_value == 0
        assert scorer._max_scale_value == 3
        assert scorer._score_category == "custom_cat"


def test_custom_likert_path_file_not_found():
    """Verify that a non-existent custom_likert_path raises FileNotFoundError."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(FileNotFoundError, match="Custom Likert scale file not found"):
            SelfAskLikertScorer(chat_target=chat_target, custom_likert_path=Path("/does/not/exist.yaml"))


def test_custom_likert_path_non_yaml_rejected(tmp_path: Path):
    """Verify that a non-YAML custom_likert_path raises ValueError."""
    bad_file = tmp_path / "scale.txt"
    bad_file.write_text("not yaml", encoding="utf-8")

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="must be a YAML file"):
            SelfAskLikertScorer(chat_target=chat_target, custom_likert_path=bad_file)


def test_custom_system_prompt_non_yaml_rejected(tmp_path: Path):
    """Verify that a non-YAML custom_system_prompt_path raises ValueError."""
    bad_file = tmp_path / "prompt.txt"
    bad_file.write_text("not yaml", encoding="utf-8")

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="must be a YAML file"):
            SelfAskLikertScorer(
                chat_target=chat_target,
                likert_scale=LikertScalePaths.CYBER_SCALE,
                custom_system_prompt_path=bad_file,
            )


def test_custom_system_prompt_path_used_in_system_prompt(tmp_path: Path):
    """Verify that a custom system prompt template is rendered instead of the default."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        custom_prompt_path = _make_custom_system_prompt_yaml(tmp_path)
        custom_likert_path = _make_custom_scale_yaml(tmp_path, category="test_cat", min_val=1, max_val=5)

        scorer = SelfAskLikertScorer(
            chat_target=chat_target,
            custom_likert_path=custom_likert_path,
            custom_system_prompt_path=custom_prompt_path,
        )

        # The system prompt should come from the custom template, not the default one
        assert "Custom prompt for test_cat" in scorer._system_prompt
        assert "from 1 to 5" in scorer._system_prompt


def test_custom_system_prompt_missing_params_rejected(tmp_path: Path):
    """Verify that a custom system prompt missing required parameters raises ValueError."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        bad_prompt_path = _make_custom_system_prompt_yaml(tmp_path, include_all_params=False)
        custom_likert_path = _make_custom_scale_yaml(tmp_path)

        with pytest.raises(ValueError, match="Custom system prompt YAML must define parameters"):
            SelfAskLikertScorer(
                chat_target=chat_target,
                custom_likert_path=custom_likert_path,
                custom_system_prompt_path=bad_prompt_path,
            )


def test_both_likert_scale_and_custom_path_raises():
    """Verify that providing both likert_scale and custom_likert_path raises ValueError."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="Only one of"):
            SelfAskLikertScorer(
                chat_target=chat_target,
                likert_scale=LikertScalePaths.CYBER_SCALE,
                custom_likert_path=Path("dummy.yaml"),
            )


def test_neither_likert_scale_nor_custom_path_raises():
    """Verify that providing neither likert_scale nor custom_likert_path raises ValueError."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="One of"):
            SelfAskLikertScorer(chat_target=chat_target)


def test_custom_system_prompt_file_not_found():
    """Verify that a non-existent custom_system_prompt_path raises FileNotFoundError."""
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(FileNotFoundError, match="Custom system prompt file not found"):
            SelfAskLikertScorer(
                chat_target=chat_target,
                likert_scale=LikertScalePaths.CYBER_SCALE,
                custom_system_prompt_path=Path("/does/not/exist.yaml"),
            )


def test_custom_likert_yaml_not_a_dict_rejected(tmp_path: Path):
    """Verify that a YAML file whose top-level structure is not a dict raises ValueError."""
    yaml_file = tmp_path / "bad_structure.yaml"
    yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="must contain a YAML mapping/dictionary"):
            SelfAskLikertScorer(chat_target=chat_target, custom_likert_path=yaml_file)


def test_likert_scale_single_unique_value_rejected(tmp_path: Path):
    """Verify that a scale with only one distinct score value raises ValueError."""
    yaml_file = tmp_path / "single_value.yaml"
    yaml_file.write_text(
        yaml.safe_dump(
            {
                "category": "test_harm",
                "scale_descriptions": [
                    {"score_value": "3", "description": "Only level"},
                ],
            }
        ),
        encoding="utf-8",
    )

    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

        with pytest.raises(ValueError, match="at least two distinct score values"):
            SelfAskLikertScorer(chat_target=chat_target, custom_likert_path=yaml_file)
