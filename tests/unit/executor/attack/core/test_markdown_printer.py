# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack.printer.markdown_printer import MarkdownAttackResultPrinter
from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult, Message, MessagePiece, Score


def _mock_scorer_id(name: str = "MockScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test_module",
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_memory():
    memory = MagicMock(spec=CentralMemory)
    with patch("pyrit.executor.attack.printer.markdown_printer.CentralMemory") as mock_central_memory:
        mock_central_memory.get_memory_instance.return_value = memory
        mock_central_memory.get_conversation.return_value = []
        yield memory


@pytest.fixture
def markdown_printer(patch_central_database):
    return MarkdownAttackResultPrinter(display_inline=False)


@pytest.fixture
def sample_boolean_score():
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test true score",
        score_rationale="Line 1\nLine 2\nLine 3",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.fixture
def sample_float_score():
    return Score(
        score_type="float_scale",
        score_value="0.5",
        score_category=["other"],
        score_value_description="Other score",
        score_rationale="Other rationale",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("OtherScorer"),
    )


@pytest.fixture
def sample_attack_result():
    return AttackResult(
        objective="Test objective",
        attack_identifier={"__type__": "TestAttack"},
        conversation_id="test-conv-123",
        executed_turns=3,
        execution_time_ms=1500,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test successful",
        last_score=Score(
            score_type="float_scale",
            score_value="0.5",
            score_category=["other"],
            score_value_description="Other score",
            score_rationale="Other rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_mock_scorer_id("OtherScorer"),
        ),
    )


@pytest.fixture
def sample_message_piece():
    return MessagePiece(
        role="user", original_value="Original text", converted_value="Converted text", converted_value_data_type="text"
    )


@pytest.fixture
def sample_message(sample_message_piece):
    return Message(message_pieces=[sample_message_piece])


def test_init(mock_memory):
    """Test MarkdownAttackResultPrinter initialization."""
    printer = MarkdownAttackResultPrinter(display_inline=True)
    assert printer._display_inline is True
    assert printer._memory is mock_memory


def test_format_score_bool(markdown_printer, sample_boolean_score):
    """Test score formatting with boolean value."""
    formatted = markdown_printer._format_score(sample_boolean_score)
    assert "**Value:** True" in formatted
    assert "**Score Type:** true_false" in formatted
    assert "**Category:** test" in formatted
    # Empty metadata dict is not printed
    assert "**Metadata:**" not in formatted


def test_format_score_float(markdown_printer, sample_float_score):
    """Test score formatting with float value."""
    formatted = markdown_printer._format_score(sample_float_score)
    assert "0.5" in formatted
    assert "**Score Type:** float_scale" in formatted
    assert "**Category:** other" in formatted


def test_format_score_multiline_rationale(markdown_printer, sample_boolean_score):
    """Test score formatting with multi-line rationale."""
    formatted = markdown_printer._format_score(sample_boolean_score)
    assert "Line 1" in formatted
    assert "Line 2" in formatted
    assert "Line 3" in formatted


def test_get_audio_mime_type(markdown_printer):
    """Test audio MIME type detection."""
    assert markdown_printer._get_audio_mime_type(audio_path="test.wav") == "audio/wav"
    assert markdown_printer._get_audio_mime_type(audio_path="test.ogg") == "audio/ogg"
    assert markdown_printer._get_audio_mime_type(audio_path="test.m4a") == "audio/mp4"
    assert markdown_printer._get_audio_mime_type(audio_path="test.mp3") == "audio/mpeg"


def test_format_image_content(markdown_printer):
    """Test image content formatting."""
    image_path = os.path.join("test", "path", "image.png")
    formatted = markdown_printer._format_image_content(image_path=image_path)
    assert formatted[0].startswith("![Image]")
    assert "image.png" in formatted[0]


def test_format_audio_content(markdown_printer):
    """Test audio content formatting."""
    audio_path = "test.wav"
    formatted = markdown_printer._format_audio_content(audio_path=audio_path)
    assert "<audio controls>" in formatted
    assert 'type="audio/wav"' in "\n".join(formatted)
    assert "Your browser does not support the audio element." in formatted


def test_format_error_content(markdown_printer, sample_message_piece):
    """Test error content formatting."""
    sample_message_piece.response_error = "TestError"
    formatted = markdown_printer._format_error_content(piece=sample_message_piece)
    assert "**Error Response:**\n" in formatted
    assert "*Error Type: TestError*\n" in formatted
    assert "```json" in formatted


def test_format_text_content_with_conversion(markdown_printer, sample_message_piece):
    """Test text content formatting when original and converted values differ."""
    formatted = markdown_printer._format_text_content(piece=sample_message_piece, show_original=True)
    assert "**Original:**\n" in formatted
    assert "Original text\n" in formatted
    assert "\n**Converted:**\n" in formatted
    assert "Converted text\n" in formatted


def test_format_text_content_without_conversion(markdown_printer, sample_message_piece):
    """Test text content formatting when values are the same."""
    sample_message_piece.converted_value = sample_message_piece.original_value
    formatted = markdown_printer._format_text_content(piece=sample_message_piece, show_original=True)
    assert "**Original:**" not in formatted
    assert sample_message_piece.original_value + "\n" in formatted


@pytest.mark.asyncio
async def test_format_piece_content_image(markdown_printer, sample_message_piece):
    """Test piece content formatting for images."""
    sample_message_piece.converted_value_data_type = "image_path"
    sample_message_piece.converted_value = "test.png"
    formatted = await markdown_printer._format_piece_content_async(piece=sample_message_piece, show_original=False)
    assert any("![Image]" in line for line in formatted)


@pytest.mark.asyncio
async def test_format_piece_content_audio(markdown_printer, sample_message_piece):
    """Test piece content formatting for audio."""
    sample_message_piece.converted_value_data_type = "audio_path"
    sample_message_piece.converted_value = "test.wav"
    formatted = await markdown_printer._format_piece_content_async(piece=sample_message_piece, show_original=False)
    assert any("<audio controls>" in line for line in formatted)


@pytest.mark.asyncio
async def test_format_piece_content_error(markdown_printer, sample_message_piece):
    """Test piece content formatting for errors."""
    sample_message_piece.response_error = "TestError"
    formatted = await markdown_printer._format_piece_content_async(piece=sample_message_piece, show_original=False)
    assert any("**Error Response:**" in line for line in formatted)


@pytest.mark.asyncio
async def test_print_result_async(markdown_printer, sample_attack_result, mock_memory, capsys):
    """Test full attack result printing."""

    await markdown_printer.print_result_async(sample_attack_result)
    captured = capsys.readouterr()

    # Check for main sections
    assert "Attack Result: SUCCESS" in captured.out
    assert "## Attack Summary" in captured.out
    assert "### Basic Information" in captured.out
    assert "### Execution Metrics" in captured.out
    assert "### Outcome" in captured.out
    assert "### Final Score" in captured.out
    assert "## Conversation History" in captured.out


@pytest.mark.asyncio
async def test_print_conversation_async(markdown_printer, sample_attack_result, mock_memory, capsys):
    """Test conversation history printing."""
    await markdown_printer.print_conversation_async(sample_attack_result)
    captured = capsys.readouterr()

    assert "*No conversation found for ID: test-conv-123*" in captured.out


@pytest.mark.asyncio
async def test_print_summary_async(markdown_printer, sample_attack_result, capsys):
    """Test attack summary printing."""
    await markdown_printer.print_summary_async(sample_attack_result)
    captured = capsys.readouterr()

    assert "## Attack Summary" in captured.out
    assert "Test objective" in captured.out
    assert "TestAttack" in captured.out
    assert "test-conv-123" in captured.out
