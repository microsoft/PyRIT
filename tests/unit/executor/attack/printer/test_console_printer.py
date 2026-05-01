# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.printer.console_printer import ConsoleAttackResultPrinter
from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import AttackOutcome, AttackResult, ConversationType, Message, MessagePiece, Score
from pyrit.models.conversation_reference import ConversationReference


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_conversation.return_value = []
    memory.get_prompt_scores.return_value = []
    with patch("pyrit.executor.attack.printer.console_printer.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = memory
        yield memory


@pytest.fixture
def printer(mock_memory):
    return ConsoleAttackResultPrinter(width=80, indent_size=2, enable_colors=False)


@pytest.fixture
def sample_score():
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test score",
        score_rationale="Test rationale",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id(),
    )


@pytest.fixture
def sample_attack_result():
    return AttackResult(
        objective="Test objective",
        atomic_attack_identifier=build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(class_name="TestAttack", class_module="test_module"),
        ),
        conversation_id="test-conv-123",
        executed_turns=3,
        execution_time_ms=1500,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test successful",
        last_score=Score(
            score_type="float_scale",
            score_value="0.75",
            score_category=["harm"],
            score_value_description="Score",
            score_rationale="Rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_mock_scorer_id(),
        ),
    )


@pytest.fixture
def sample_message_piece():
    return MessagePiece(
        role="user",
        original_value="Hello world",
        converted_value="Hello world",
        converted_value_data_type="text",
    )


@pytest.fixture
def sample_message(sample_message_piece):
    return Message(message_pieces=[sample_message_piece])


def test_init_stores_width_and_indent(mock_memory):
    p = ConsoleAttackResultPrinter(width=120, indent_size=4, enable_colors=False)
    assert p._width == 120
    assert p._indent == "    "
    assert p._enable_colors is False


def test_init_default_colors_enabled(mock_memory):
    p = ConsoleAttackResultPrinter()
    assert p._enable_colors is True


def test_print_colored_no_colors(printer, capsys):
    printer._print_colored("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.out


def test_print_colored_with_colors_disabled(printer, capsys):
    printer._enable_colors = False
    printer._print_colored("test text", "SOME_COLOR")
    captured = capsys.readouterr()
    assert "test text" in captured.out


def test_get_outcome_color_success(printer):
    color = printer._get_outcome_color(AttackOutcome.SUCCESS)
    assert isinstance(color, str)


def test_get_outcome_color_failure(printer):
    color = printer._get_outcome_color(AttackOutcome.FAILURE)
    assert isinstance(color, str)


def test_get_outcome_color_undetermined(printer):
    color = printer._get_outcome_color(AttackOutcome.UNDETERMINED)
    assert isinstance(color, str)


def test_print_header(printer, sample_attack_result, capsys):
    printer._print_header(sample_attack_result)
    captured = capsys.readouterr()
    assert "ATTACK RESULT" in captured.out
    assert "SUCCESS" in captured.out


def test_print_footer(printer, capsys):
    printer._print_footer()
    captured = capsys.readouterr()
    assert "Report generated at" in captured.out


def test_print_section_header(printer, capsys):
    printer._print_section_header("Test Section")
    captured = capsys.readouterr()
    assert "Test Section" in captured.out


def test_print_metadata(printer, capsys):
    metadata = {"key1": "value1", "key2": 42}
    printer._print_metadata(metadata)
    captured = capsys.readouterr()
    assert "key1" in captured.out
    assert "value1" in captured.out
    assert "key2" in captured.out
    assert "42" in captured.out


def test_print_score(printer, sample_score, capsys):
    printer._print_score(sample_score)
    captured = capsys.readouterr()
    assert "MockScorer" in captured.out
    assert "true_false" in captured.out
    assert "true" in captured.out


def test_print_score_with_rationale(printer, capsys):
    score = Score(
        score_type="float_scale",
        score_value="0.8",
        score_category=["harm"],
        score_value_description="desc",
        score_rationale="Multi\nline\nrationale",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id(),
    )
    printer._print_score(score)
    captured = capsys.readouterr()
    assert "Rationale" in captured.out


def test_extract_reasoning_summary_valid_json(printer):
    import json

    data = {"summary": [{"text": "First"}, {"text": "Second"}]}
    result = printer._extract_reasoning_summary(json.dumps(data))
    assert result == "First\nSecond"


def test_extract_reasoning_summary_invalid_json(printer):
    result = printer._extract_reasoning_summary("not json")
    assert result == ""


def test_extract_reasoning_summary_no_summary_key(printer):
    import json

    result = printer._extract_reasoning_summary(json.dumps({"other": "data"}))
    assert result == ""


def test_extract_reasoning_summary_summary_not_list(printer):
    import json

    result = printer._extract_reasoning_summary(json.dumps({"summary": "not a list"}))
    assert result == ""


async def test_print_conversation_async_no_conversation_id(printer, capsys):
    result = AttackResult(objective="test", conversation_id="")
    await printer.print_conversation_async(result)
    captured = capsys.readouterr()
    assert "No conversation ID" in captured.out


async def test_print_conversation_async_no_messages(printer, mock_memory, capsys):
    mock_memory.get_conversation.return_value = []
    result = AttackResult(objective="test", conversation_id="conv-123")
    await printer.print_conversation_async(result)
    captured = capsys.readouterr()
    assert "No conversation found" in captured.out


async def test_print_messages_async_empty_list(printer, capsys):
    await printer.print_messages_async(messages=[])
    captured = capsys.readouterr()
    assert "No messages to display" in captured.out


@patch("pyrit.executor.attack.printer.console_printer.display_image_response", new_callable=AsyncMock)
async def test_print_messages_async_user_message(mock_display, printer, sample_message, capsys):
    await printer.print_messages_async(messages=[sample_message])
    captured = capsys.readouterr()
    assert "Turn 1" in captured.out
    assert "USER" in captured.out
    assert "Hello world" in captured.out


@patch("pyrit.executor.attack.printer.console_printer.display_image_response", new_callable=AsyncMock)
async def test_print_messages_async_assistant_message(mock_display, printer, capsys):
    piece = MessagePiece(
        role="assistant",
        original_value="Response",
        converted_value="Response",
        converted_value_data_type="text",
    )
    msg = Message(message_pieces=[piece])
    await printer.print_messages_async(messages=[msg])
    captured = capsys.readouterr()
    assert "Response" in captured.out


@patch("pyrit.executor.attack.printer.console_printer.display_image_response", new_callable=AsyncMock)
async def test_print_messages_async_converted_differs(mock_display, printer, capsys):
    piece = MessagePiece(
        role="user",
        original_value="Original",
        converted_value="Converted",
        converted_value_data_type="text",
    )
    msg = Message(message_pieces=[piece])
    await printer.print_messages_async(messages=[msg])
    captured = capsys.readouterr()
    assert "Original" in captured.out
    assert "Converted" in captured.out


async def test_print_summary_async(printer, sample_attack_result, capsys):
    await printer.print_summary_async(sample_attack_result)
    captured = capsys.readouterr()
    assert "Test objective" in captured.out
    assert "TestAttack" in captured.out
    assert "test-conv-123" in captured.out
    assert "SUCCESS" in captured.out
    assert "Test successful" in captured.out


async def test_print_result_async_basic(printer, sample_attack_result, mock_memory, capsys):
    mock_memory.get_conversation.return_value = []
    await printer.print_result_async(sample_attack_result)
    captured = capsys.readouterr()
    assert "ATTACK RESULT" in captured.out
    assert "Report generated at" in captured.out


async def test_print_result_async_with_metadata(printer, mock_memory, capsys):
    result = AttackResult(
        objective="test",
        conversation_id="conv-1",
        outcome=AttackOutcome.FAILURE,
        metadata={"note": "extra info"},
    )
    mock_memory.get_conversation.return_value = []
    await printer.print_result_async(result)
    captured = capsys.readouterr()
    assert "note" in captured.out
    assert "extra info" in captured.out


async def test_print_pruned_conversations_no_pruned(printer, capsys):
    result = AttackResult(objective="test", conversation_id="conv-1")
    await printer._print_pruned_conversations_async(result)
    captured = capsys.readouterr()
    assert captured.out == ""


async def test_print_pruned_conversations_with_messages(printer, mock_memory, capsys):
    piece = MessagePiece(
        role="assistant",
        original_value="Pruned response",
        converted_value="Pruned response",
        converted_value_data_type="text",
    )
    mock_memory.get_conversation.return_value = [Message(message_pieces=[piece])]
    mock_memory.get_prompt_scores.return_value = []

    ref = ConversationReference(conversation_id="pruned-conv", conversation_type=ConversationType.PRUNED)
    result = AttackResult(
        objective="test",
        conversation_id="conv-1",
        related_conversations={ref},
    )
    await printer._print_pruned_conversations_async(result)
    captured = capsys.readouterr()
    assert "PRUNED" in captured.out
    assert "Pruned response" in captured.out


async def test_print_adversarial_conversation_no_refs(printer, capsys):
    result = AttackResult(objective="test", conversation_id="conv-1")
    await printer._print_adversarial_conversation_async(result)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_wrapped_text(printer, capsys):
    printer._print_wrapped_text("Short text", "")
    captured = capsys.readouterr()
    assert "Short text" in captured.out


def test_print_wrapped_text_with_newlines(printer, capsys):
    printer._print_wrapped_text("Line one\nLine two\n\nLine four", "")
    captured = capsys.readouterr()
    assert "Line one" in captured.out
    assert "Line two" in captured.out
    assert "Line four" in captured.out
