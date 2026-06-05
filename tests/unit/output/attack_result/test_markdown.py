# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationType,
    Message,
    MessagePiece,
    Score,
    build_atomic_attack_identifier,
)
from pyrit.models.conversation_reference import ConversationReference
from pyrit.output.attack_result.markdown import MarkdownAttackResultMemoryPrinter


def _scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _attack_id(name: str = "TestAttack") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _seed_messages(memory: MemoryInterface, conversation_id: str, pieces: list[MessagePiece]) -> None:
    for piece in pieces:
        piece.conversation_id = conversation_id
        memory.add_message_to_memory(request=Message(message_pieces=[piece]))


def _make_score(*, piece_id: str, value: str = "true", score_type: str = "true_false") -> Score:
    return Score(
        score_type=score_type,
        score_value=value,
        score_category=["test"],
        score_value_description="desc",
        score_rationale="rationale",
        score_metadata={},
        message_piece_id=piece_id,
        scorer_class_identifier=_scorer_id(),
    )


@pytest.fixture
def printer(patch_central_database):
    return MarkdownAttackResultMemoryPrinter(display_inline=False)


@pytest.fixture
def attack_result():
    return AttackResult(
        objective="Test objective",
        atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=_attack_id()),
        conversation_id="conv-main",
        executed_turns=3,
        execution_time_ms=1500,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test successful",
        last_score=Score(
            score_type="float_scale",
            score_value="0.5",
            score_category=["other"],
            score_value_description="Other",
            score_rationale="Multi\nline\nrationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_scorer_id(),
        ),
    )


# --- __init__ ---


def test_init(patch_central_database):
    printer = MarkdownAttackResultMemoryPrinter(display_inline=True)
    assert printer._display_inline is True
    assert printer._memory is patch_central_database.return_value


# --- write_async (summary, outcomes, metadata) ---


async def test_write_async_renders_full_summary(printer, attack_result, capsys):
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "Attack Result: SUCCESS" in out
    assert "## Attack Summary" in out
    assert "### Basic Information" in out
    assert "### Execution Metrics" in out
    assert "### Outcome" in out
    assert "**Reason:** Test successful" in out
    assert "### Final Score" in out
    assert "Multi" in out  # multi-line rationale rendered
    assert "## Conversation History" in out
    assert "Report generated at" in out


async def test_write_async_renders_failure_outcome(printer, capsys):
    await printer.write_async(AttackResult(objective="o", conversation_id="c", outcome=AttackOutcome.FAILURE))
    assert "Attack Result: FAILURE" in capsys.readouterr().out


async def test_write_async_renders_undetermined_outcome(printer, capsys):
    await printer.write_async(AttackResult(objective="o", conversation_id="c", outcome=AttackOutcome.UNDETERMINED))
    assert "Attack Result: UNDETERMINED" in capsys.readouterr().out


async def test_write_async_renders_metadata(printer, capsys):
    result = AttackResult(
        objective="o",
        conversation_id="c",
        outcome=AttackOutcome.SUCCESS,
        metadata={"note": "extra"},
    )
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "## Additional Metadata" in out
    assert "**note:** extra" in out


# --- conversation rendering ---


async def test_write_async_no_conversation_id(printer, capsys):
    result = AttackResult(objective="o", conversation_id="", outcome=AttackOutcome.SUCCESS)
    await printer.write_async(result)
    assert "*No conversation ID available*" in capsys.readouterr().out


async def test_write_async_no_messages(printer, attack_result, capsys):
    await printer.write_async(attack_result)
    assert "*No conversation found for ID: conv-main*" in capsys.readouterr().out


async def test_write_async_renders_user_and_assistant_messages(printer, attack_result, sqlite_instance, capsys):
    user_piece = MessagePiece(role="user", original_value="Hello", converted_value="Hello")
    assistant_piece = MessagePiece(role="assistant", original_value="Hi back", converted_value="Hi back")
    _seed_messages(sqlite_instance, "conv-main", [user_piece, assistant_piece])

    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "### Turn 1" in out
    assert "#### User" in out
    assert "Hello" in out
    assert "#### Assistant" in out
    assert "Hi back" in out


async def test_write_async_renders_system_message(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(role="system", original_value="sys", converted_value="sys")
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "### System Message" in out
    assert "sys" in out


async def test_write_async_renders_original_and_converted_when_different(
    printer, attack_result, sqlite_instance, capsys
):
    piece = MessagePiece(role="user", original_value="Original text", converted_value="Converted text")
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "**Original:**" in out
    assert "Original text" in out
    assert "**Converted:**" in out
    assert "Converted text" in out


async def test_write_async_renders_image_message(printer, attack_result, sqlite_instance, capsys):
    image_path = os.path.join("test", "path", "image.png")
    piece = MessagePiece(
        role="assistant",
        original_value=image_path,
        converted_value=image_path,
        original_value_data_type="image_path",
        converted_value_data_type="image_path",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "![Image]" in out
    assert "image.png" in out


@pytest.mark.parametrize(
    "audio_filename,expected_mime",
    [
        ("clip.wav", "audio/wav"),
        ("clip.ogg", "audio/ogg"),
        ("clip.m4a", "audio/mp4"),
        ("clip.mp3", "audio/mpeg"),
    ],
)
async def test_write_async_renders_audio_message_with_mime_type(
    printer, attack_result, sqlite_instance, capsys, audio_filename, expected_mime
):
    piece = MessagePiece(
        role="assistant",
        original_value=audio_filename,
        converted_value=audio_filename,
        original_value_data_type="audio_path",
        converted_value_data_type="audio_path",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "<audio controls>" in out
    assert f'type="{expected_mime}"' in out
    assert "Your browser does not support the audio element." in out


async def test_write_async_renders_error_message(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 500}',
        converted_value='{"status_code": 500}',
        converted_value_data_type="error",
        response_error="processing",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "**Error Response:**" in out
    assert "*Error Type: processing*" in out
    assert "```json" in out


# --- auxiliary scores ---


async def test_write_async_with_auxiliary_scores(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(role="assistant", original_value="response", converted_value="response")
    _seed_messages(sqlite_instance, "conv-main", [piece])
    sqlite_instance.add_scores_to_memory(
        scores=[_make_score(piece_id=str(piece.id), value="0.42", score_type="float_scale")]
    )

    await printer.write_async(attack_result, include_auxiliary_scores=True)
    out = capsys.readouterr().out
    assert "##### Scores" in out
    assert "**0.42**" in out
    assert "**Score Type:** float_scale" in out


# --- pruned conversations ---


async def test_write_async_pruned_with_messages_and_scores(printer, attack_result, sqlite_instance, capsys):
    pruned_piece = MessagePiece(
        role="assistant", original_value="short pruned line", converted_value="short pruned line"
    )
    _seed_messages(sqlite_instance, "pruned-conv", [pruned_piece])
    sqlite_instance.add_scores_to_memory(scores=[_make_score(piece_id=str(pruned_piece.id))])

    attack_result.related_conversations.add(
        ConversationReference(
            conversation_id="pruned-conv", conversation_type=ConversationType.PRUNED, description="branch one"
        )
    )

    await printer.write_async(attack_result, include_pruned_conversations=True)
    out = capsys.readouterr().out
    assert "## Pruned Conversations" in out
    assert "branch one" in out
    assert "short pruned line" in out
    assert "**Score:**" in out


async def test_write_async_pruned_with_multiline_content_uses_code_block(
    printer, attack_result, sqlite_instance, capsys
):
    multiline = "line one\nline two"
    pruned_piece = MessagePiece(role="assistant", original_value=multiline, converted_value=multiline)
    _seed_messages(sqlite_instance, "pruned-conv", [pruned_piece])
    attack_result.related_conversations.add(
        ConversationReference(conversation_id="pruned-conv", conversation_type=ConversationType.PRUNED)
    )

    await printer.write_async(attack_result, include_pruned_conversations=True)
    out = capsys.readouterr().out
    assert "```" in out
    assert "line one" in out


async def test_write_async_pruned_with_no_messages(printer, attack_result, capsys):
    attack_result.related_conversations.add(
        ConversationReference(conversation_id="empty-pruned", conversation_type=ConversationType.PRUNED)
    )
    await printer.write_async(attack_result, include_pruned_conversations=True)
    out = capsys.readouterr().out
    assert "*No messages found for conversation: `empty-pruned`*" in out


async def test_write_async_include_pruned_with_no_pruned_refs(printer, attack_result, capsys):
    await printer.write_async(attack_result, include_pruned_conversations=True)
    assert "## Pruned Conversations" not in capsys.readouterr().out


# --- adversarial conversation ---


async def test_write_async_adversarial_with_messages_and_description(printer, attack_result, sqlite_instance, capsys):
    adv_user = MessagePiece(role="user", original_value="adv prompt", converted_value="adv prompt")
    adv_assist = MessagePiece(role="assistant", original_value="adv reply", converted_value="adv reply")
    _seed_messages(sqlite_instance, "adv-conv", [adv_user, adv_assist])
    attack_result.related_conversations.add(
        ConversationReference(
            conversation_id="adv-conv", conversation_type=ConversationType.ADVERSARIAL, description="red team chain"
        )
    )

    await printer.write_async(attack_result, include_adversarial_conversation=True)
    out = capsys.readouterr().out
    assert "## Adversarial Conversation" in out
    assert "red team chain" in out
    assert "adv prompt" in out
    assert "adv reply" in out


async def test_write_async_adversarial_filters_to_best_branch(patch_central_database, sqlite_instance, capsys):
    short = MessagePiece(role="user", original_value="best short", converted_value="best short")
    long_text = "x" * 250  # > 200 chars triggers code block branch in adversarial rendering
    long_piece = MessagePiece(role="user", original_value=long_text, converted_value=long_text)
    _seed_messages(sqlite_instance, "adv-best", [short, long_piece])
    _seed_messages(
        sqlite_instance,
        "adv-other",
        [MessagePiece(role="user", original_value="other", converted_value="other")],
    )

    result = AttackResult(
        objective="o",
        conversation_id="conv-main",
        outcome=AttackOutcome.SUCCESS,
        metadata={"best_adversarial_conversation_id": "adv-best"},
        related_conversations={
            ConversationReference(conversation_id="adv-best", conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id="adv-other", conversation_type=ConversationType.ADVERSARIAL),
        },
    )

    await MarkdownAttackResultMemoryPrinter(display_inline=False).write_async(
        result, include_adversarial_conversation=True
    )
    out = capsys.readouterr().out
    assert "best-scoring branch" in out
    assert "best short" in out
    assert "x" * 50 in out
    assert "other" not in out
    # Long content (>200 chars) is wrapped in a fenced code block.
    assert "```" in out


async def test_write_async_adversarial_with_no_messages(printer, attack_result, capsys):
    attack_result.related_conversations.add(
        ConversationReference(conversation_id="adv-empty", conversation_type=ConversationType.ADVERSARIAL)
    )
    await printer.write_async(attack_result, include_adversarial_conversation=True)
    out = capsys.readouterr().out
    assert "## Adversarial Conversation" in out
    assert "*No messages found for conversation: `adv-empty`*" in out


async def test_write_async_include_adversarial_with_no_refs(printer, attack_result, capsys):
    await printer.write_async(attack_result, include_adversarial_conversation=True)
    assert "## Adversarial Conversation" not in capsys.readouterr().out


# --- deprecated aliases ---


async def test_print_result_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="print_result_async"):
        await printer.print_result_async(attack_result)
    assert "Attack Result: SUCCESS" in capsys.readouterr().out


async def test_output_conversation_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="output_conversation_async"):
        await printer.output_conversation_async(attack_result)
    assert "*No conversation found for ID: conv-main*" in capsys.readouterr().out


async def test_print_summary_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="print_summary_async"):
        await printer.print_summary_async(attack_result)
    assert "## Attack Summary" in capsys.readouterr().out
