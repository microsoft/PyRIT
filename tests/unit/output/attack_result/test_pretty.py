# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import MagicMock, patch

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import AttackOutcome, AttackResult, ConversationType, Message, MessagePiece, Score
from pyrit.models.conversation_reference import ConversationReference
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_conversation.return_value = []
    memory.get_prompt_scores.return_value = []
    with patch("pyrit.memory.CentralMemory") as mock_cm:
        mock_cm.get_memory_instance.return_value = memory
        yield memory


@pytest.fixture
def printer(mock_memory):
    return PrettyAttackResultMemoryPrinter(width=80, indent_size=2, enable_colors=False)


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
    p = PrettyAttackResultMemoryPrinter(width=120, indent_size=4, enable_colors=False)
    assert p._width == 120
    assert p._indent == "    "
    assert p._enable_colors is False


def test_init_default_colors_enabled(mock_memory):
    p = PrettyAttackResultMemoryPrinter()
    assert p._enable_colors is True


def test_format_colored_no_colors(printer):
    result = printer._format_colored("hello")
    assert "hello" in result
    assert result.endswith("\n")


def test_format_colored_with_colors_disabled(printer):
    printer._enable_colors = False
    result = printer._format_colored("test text", "SOME_COLOR")
    assert "test text" in result
    assert result.endswith("\n")


def test_get_outcome_color_success(printer):
    color = printer._get_outcome_color(AttackOutcome.SUCCESS)
    assert isinstance(color, str)


def test_get_outcome_color_failure(printer):
    color = printer._get_outcome_color(AttackOutcome.FAILURE)
    assert isinstance(color, str)


def test_get_outcome_color_undetermined(printer):
    color = printer._get_outcome_color(AttackOutcome.UNDETERMINED)
    assert isinstance(color, str)


def test_render_header(printer, sample_attack_result):
    result = printer._render_header(sample_attack_result)
    assert "ATTACK RESULT" in result
    assert "SUCCESS" in result


def test_render_footer(printer):
    result = printer._render_footer()
    assert "Report generated at" in result


def test_render_section_header(printer):
    result = printer._render_section_header("Test Section")
    assert "Test Section" in result


def test_render_metadata(printer):
    metadata = {"key1": "value1", "key2": 42}
    result = printer._render_metadata(metadata)
    assert "key1" in result
    assert "value1" in result
    assert "key2" in result
    assert "42" in result


def test_render_score(printer, sample_score):
    result = printer._score_printer._render_score(sample_score)
    assert "MockScorer" in result
    assert "true_false" in result
    assert "true" in result


def test_render_score_with_rationale(printer):
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
    result = printer._score_printer._render_score(score)
    assert "Rationale" in result


def test_extract_reasoning_summary_valid_json(printer):
    import json

    data = {"summary": [{"text": "First"}, {"text": "Second"}]}
    result = printer._conversation_printer._extract_reasoning_summary(json.dumps(data))
    assert result == "First\nSecond"


def test_extract_reasoning_summary_invalid_json(printer):
    result = printer._conversation_printer._extract_reasoning_summary("not json")
    assert result == ""


def test_extract_reasoning_summary_no_summary_key(printer):
    import json

    result = printer._conversation_printer._extract_reasoning_summary(json.dumps({"other": "data"}))
    assert result == ""


def test_extract_reasoning_summary_summary_not_list(printer):
    import json

    result = printer._conversation_printer._extract_reasoning_summary(json.dumps({"summary": "not a list"}))
    assert result == ""


async def test_render_conversation_async_no_conversation_id(printer):
    result = AttackResult(objective="test", conversation_id="")
    content = await printer._render_conversation_async(result)
    assert "No conversation ID" in content


async def test_render_conversation_async_no_messages(printer, mock_memory):
    mock_memory.get_conversation.return_value = []
    result = AttackResult(objective="test", conversation_id="conv-123")
    content = await printer._render_conversation_async(result)
    assert "No conversation found" in content


async def test_render_messages_async_empty_list(printer):
    content = await printer._conversation_printer.render_async(messages=[])
    assert "No messages to display" in content


async def test_render_messages_async_user_message(printer, sample_message):
    content = await printer._conversation_printer.render_async(messages=[sample_message])
    assert "Turn 1" in content
    assert "USER" in content
    assert "Hello world" in content


async def test_render_messages_async_assistant_message(printer):
    piece = MessagePiece(
        role="assistant",
        original_value="Response",
        converted_value="Response",
        converted_value_data_type="text",
    )
    msg = Message(message_pieces=[piece])
    content = await printer._conversation_printer.render_async(messages=[msg])
    assert "Response" in content


async def test_render_messages_async_converted_differs(printer):
    piece = MessagePiece(
        role="user",
        original_value="Original",
        converted_value="Converted",
        converted_value_data_type="text",
    )
    msg = Message(message_pieces=[piece])
    content = await printer._conversation_printer.render_async(messages=[msg])
    assert "Original" in content
    assert "Converted" in content


async def test_render_summary_async(printer, sample_attack_result):
    content = await printer._render_summary_async(sample_attack_result)
    assert "Test objective" in content
    assert "TestAttack" in content
    assert "test-conv-123" in content
    assert "SUCCESS" in content
    assert "Test successful" in content


async def test_write_async_basic(printer, sample_attack_result, mock_memory, capsys):
    mock_memory.get_conversation.return_value = []
    await printer.write_async(sample_attack_result)
    captured = capsys.readouterr()
    assert "ATTACK RESULT" in captured.out
    assert "Report generated at" in captured.out


async def test_write_async_with_metadata(printer, mock_memory, capsys):
    result = AttackResult(
        objective="test",
        conversation_id="conv-1",
        outcome=AttackOutcome.FAILURE,
        metadata={"note": "extra info"},
    )
    mock_memory.get_conversation.return_value = []
    await printer.write_async(result)
    captured = capsys.readouterr()
    assert "note" in captured.out
    assert "extra info" in captured.out


async def test_render_pruned_conversations_no_pruned(printer):
    result = AttackResult(objective="test", conversation_id="conv-1")
    content = await printer._render_pruned_conversations_async(result)
    assert content == ""


async def test_render_pruned_conversations_with_messages(printer, mock_memory):
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
    content = await printer._render_pruned_conversations_async(result)
    assert "PRUNED" in content
    assert "Pruned response" in content


async def test_render_adversarial_conversation_no_refs(printer):
    result = AttackResult(objective="test", conversation_id="conv-1")
    content = await printer._render_adversarial_conversation_async(result)
    assert content == ""


def test_render_wrapped_text(printer):
    result = printer._conversation_printer._render_wrapped_text("Short text", "")
    assert "Short text" in result


def test_render_wrapped_text_with_newlines(printer):
    result = printer._conversation_printer._render_wrapped_text("Line one\nLine two\n\nLine four", "")
    assert "Line one" in result
    assert "Line two" in result
    assert "Line four" in result


async def test_render_messages_async_blocked_without_partial_content(printer):
    piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value_data_type="error",
        response_error="blocked",
    )
    msg = Message(message_pieces=[piece])
    content = await printer._conversation_printer.render_async(messages=[msg])
    assert "BLOCKED BY TARGET" in content
    assert "content filter" in content
    # Should NOT print the raw error JSON as the message body
    assert "status_code" not in content


async def test_render_messages_async_blocked_with_partial_content(printer):
    piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value_data_type="error",
        response_error="blocked",
        prompt_metadata={"partial_content": "The model started to say something before being cut off"},
    )
    msg = Message(message_pieces=[piece])
    content = await printer._conversation_printer.render_async(messages=[msg])
    assert "BLOCKED BY TARGET" in content
    assert "Partial content" in content
    assert "before filter triggered" in content
    assert "The model started to say something before being cut off" in content
