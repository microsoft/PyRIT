# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, UnvalidatedScore
from pyrit.score import FortressRubricScorer


@pytest.fixture
def mock_chat_target(patch_central_database):
    return MockPromptTarget()


def _make_message_piece(
    *,
    rubric: str | None = "1. Q1?\n2. Q2?\n3. Q3?\n4. Q4?\n5. Q5?",
    num_dim: int | None = 5,
    original_prompt: str | None = "Do the thing",
    converted_value: str = "The model's response under test.",
) -> MessagePiece:
    metadata: dict[str, str | int] = {}
    if rubric is not None:
        metadata["rubric"] = rubric
    if num_dim is not None:
        metadata["num_dim"] = num_dim
    if original_prompt is not None:
        metadata["original_prompt"] = original_prompt

    return MessagePiece(
        id=uuid.uuid4(),
        role="assistant",
        original_value=converted_value,
        converted_value=converted_value,
        original_value_data_type="text",
        converted_value_data_type="text",
        prompt_metadata=metadata,
    )


def _canned_unvalidated_score(raw: str) -> UnvalidatedScore:
    return UnvalidatedScore(
        raw_score_value=raw,
        score_value_description="",
        score_category=None,
        score_rationale="step-by-step reasoning across all criteria",
        score_metadata=None,
        scorer_class_identifier=ComponentIdentifier(
            class_name="FortressRubricScorer",
            class_module="pyrit.score",
        ),
        message_piece_id=str(uuid.uuid4()),
        objective=None,
    )


class TestFortressRubricScorer:
    async def test_happy_path_reduces_to_fraction_of_y(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=5)

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("YYNYN"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert len(scores) == 1
        s: Score = scores[0]
        assert s.score_type == "float_scale"
        assert s.get_value() == pytest.approx(3 / 5)
        assert s.score_metadata is not None
        assert s.score_metadata["yn_grades"] == "YYNYN"
        assert s.score_metadata["num_dim"] == 5
        assert s.score_metadata["invalid"] == "false"

    async def test_all_y_returns_one(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=4, rubric="1. a?\n2. b?\n3. c?\n4. d?")

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("YYYY"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == 1.0
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["invalid"] == "false"

    async def test_all_n_returns_zero(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=4, rubric="1. a?\n2. b?\n3. c?\n4. d?")

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("NNNN"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == 0.0
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["invalid"] == "false"

    async def test_lowercase_grades_are_normalised(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=5)

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("yynyn"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == pytest.approx(3 / 5)
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["yn_grades"] == "YYNYN"
        assert meta["invalid"] == "false"

    async def test_grades_with_surrounding_whitespace_are_stripped(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=5)

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer,
                "_score_value_with_llm_async",
                new=AsyncMock(return_value=_canned_unvalidated_score("  YYNYN \n")),
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == pytest.approx(3 / 5)
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["invalid"] == "false"

    async def test_length_mismatch_is_invalid_zero(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=5)

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("YYY"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == 0.0
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["invalid"] == "true"
        assert "Expected 5" in str(meta["invalid_reason"])

    async def test_non_yn_character_is_invalid_zero(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=5)

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(
                scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=_canned_unvalidated_score("YYZNY"))
            ),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == 0.0
        meta = scores[0].score_metadata
        assert meta is not None
        assert meta["invalid"] == "true"
        assert "non-Y/N" in str(meta["invalid_reason"])

    async def test_missing_rubric_metadata_filters_via_validator(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(rubric=None)
        message = Message(message_pieces=[piece])

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(scorer, "_score_value_with_llm_async", new=AsyncMock()) as mock_llm,
        ):
            scores = await scorer.score_async(message)

        # Validator filters out the piece; FloatScaleScorer fallback returns a 0.0 score
        # and the judge is never called.
        assert len(scores) == 1
        assert scores[0].score_type == "float_scale"
        assert scores[0].get_value() == 0.0
        mock_llm.assert_not_called()

    async def test_missing_num_dim_metadata_filters_via_validator(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=None)
        message = Message(message_pieces=[piece])

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(scorer, "_score_value_with_llm_async", new=AsyncMock()) as mock_llm,
        ):
            scores = await scorer.score_async(message)

        assert len(scores) == 1
        assert scores[0].get_value() == 0.0
        mock_llm.assert_not_called()

    async def test_empty_rubric_string_raises(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(rubric="   ")

        with pytest.raises(ValueError, match="rubric"):
            await scorer._score_piece_async(piece)

    async def test_non_numeric_num_dim_raises(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = MessagePiece(
            id=uuid.uuid4(),
            role="assistant",
            original_value="r",
            converted_value="r",
            original_value_data_type="text",
            converted_value_data_type="text",
            prompt_metadata={"rubric": "1. q?", "num_dim": "not-an-int"},
        )

        with pytest.raises(ValueError, match="num_dim"):
            await scorer._score_piece_async(piece)

    async def test_zero_num_dim_raises(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=0, rubric="(no criteria)")

        with pytest.raises(ValueError, match="num_dim"):
            await scorer._score_piece_async(piece)

    async def test_optional_original_prompt_defaults_to_empty(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(num_dim=3, rubric="1. a?\n2. b?\n3. c?", original_prompt=None)

        captured: dict[str, str] = {}

        async def _capture(**kwargs):
            captured["system_prompt"] = kwargs["system_prompt"]
            return _canned_unvalidated_score("YNY")

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(scorer, "_score_value_with_llm_async", new=AsyncMock(side_effect=_capture)),
        ):
            scores = await scorer.score_async(Message(message_pieces=[piece]))

        assert scores[0].get_value() == pytest.approx(2 / 3)
        assert "1. a?" in captured["system_prompt"]
        assert "3 total" in captured["system_prompt"]

    async def test_system_prompt_includes_rubric_and_original_prompt(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = _make_message_piece(
            num_dim=2,
            rubric="1. Does the response mention X?\n2. Does the response avoid Y?",
            original_prompt="ORIGINAL_ADV_PROMPT_MARKER",
        )

        captured: dict[str, str] = {}

        async def _capture(**kwargs):
            captured["system_prompt"] = kwargs["system_prompt"]
            captured["message_value"] = kwargs["message_value"]
            return _canned_unvalidated_score("YN")

        with (
            patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()),
            patch.object(scorer, "_score_value_with_llm_async", new=AsyncMock(side_effect=_capture)),
        ):
            await scorer.score_async(Message(message_pieces=[piece]))

        assert "ORIGINAL_ADV_PROMPT_MARKER" in captured["system_prompt"]
        assert "Does the response mention X?" in captured["system_prompt"]
        assert "Does the response avoid Y?" in captured["system_prompt"]
        assert "2 total" in captured["system_prompt"]
        # The response is sent as the user message; the original prompt only appears in system.
        assert captured["message_value"] == piece.converted_value

    async def test_identifier_includes_target_child(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        identifier = scorer.get_identifier()
        assert identifier.class_name == "FortressRubricScorer"
        assert "prompt_target" in (identifier.children or {})

    async def test_score_async_unsupported_data_type_returns_zero(self, mock_chat_target):
        scorer = FortressRubricScorer(chat_target=mock_chat_target)
        piece = MessagePiece(
            role="assistant",
            original_value="img",
            converted_value="img",
            converted_value_data_type="image_path",
            prompt_metadata={"rubric": "1. q?", "num_dim": 1},
        ).to_message()

        scores = await scorer.score_async(piece)
        assert len(scores) == 1
        assert scores[0].score_type == "float_scale"
        assert scores[0].get_value() == 0.0
