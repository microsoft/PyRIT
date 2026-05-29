# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.memory import MemoryInterface
from pyrit.models import AttackOutcome, AttackResult, ConversationType, Message, MessagePiece, Score
from pyrit.models.conversation_reference import ConversationReference
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter


def _scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _attack_id(name: str = "TestAttack") -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test_module")


def _seed_messages(memory: MemoryInterface, conversation_id: str, pieces: list[MessagePiece]) -> None:
    # Each piece becomes its own Message so add_message_to_memory can auto-assign sequence.
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
    return PrettyAttackResultMemoryPrinter(width=80, indent_size=2, enable_colors=False)


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
            score_value="0.75",
            score_category=["harm"],
            score_value_description="Score",
            score_rationale="Rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_scorer_id(),
        ),
    )


# --- __init__ tests ---


def test_init_stores_width_and_indent(patch_central_database):
    p = PrettyAttackResultMemoryPrinter(width=120, indent_size=4, enable_colors=False)
    assert p._width == 120
    assert p._indent == "    "
    assert p._enable_colors is False


def test_init_default_colors_enabled(patch_central_database):
    assert PrettyAttackResultMemoryPrinter()._enable_colors is True


# --- write_async tests (success, failure, metadata) ---


async def test_write_async_renders_success_header_summary_and_footer(printer, attack_result, capsys):
    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "ATTACK RESULT" in out
    assert "SUCCESS" in out
    assert "Test objective" in out
    assert "TestAttack" in out
    assert "conv-main" in out
    assert "Test successful" in out
    assert "Final Score" in out
    assert "Report generated at" in out


async def test_write_async_renders_failure_outcome(printer, capsys):
    result = AttackResult(objective="o", conversation_id="c", outcome=AttackOutcome.FAILURE)
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "FAILURE" in out


async def test_write_async_renders_undetermined_outcome(printer, capsys):
    result = AttackResult(objective="o", conversation_id="c", outcome=AttackOutcome.UNDETERMINED)
    await printer.write_async(result)
    assert "UNDETERMINED" in capsys.readouterr().out


async def test_write_async_renders_metadata(printer, capsys):
    result = AttackResult(objective="o", conversation_id="c", outcome=AttackOutcome.SUCCESS, metadata={"note": "extra"})
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "note" in out
    assert "extra" in out


# --- conversation rendering paths ---


async def test_write_async_no_conversation_id_shown(printer, capsys):
    result = AttackResult(objective="o", conversation_id="", outcome=AttackOutcome.SUCCESS)
    await printer.write_async(result)
    assert "No conversation ID" in capsys.readouterr().out


async def test_write_async_no_messages_for_conversation(printer, attack_result, capsys):
    # No messages have been seeded for conv-main.
    await printer.write_async(attack_result)
    assert "No conversation found for ID: conv-main" in capsys.readouterr().out


async def test_write_async_renders_user_and_assistant_messages(printer, attack_result, sqlite_instance, capsys):
    user_piece = MessagePiece(role="user", original_value="Hello", converted_value="Hello")
    assistant_piece = MessagePiece(role="assistant", original_value="Hi back", converted_value="Hi back")
    _seed_messages(sqlite_instance, "conv-main", [user_piece, assistant_piece])

    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "Turn 1" in out
    assert "USER" in out
    assert "Hello" in out
    assert "Hi back" in out


async def test_write_async_renders_original_and_converted_when_different(
    printer, attack_result, sqlite_instance, capsys
):
    piece = MessagePiece(role="user", original_value="Original", converted_value="Converted")
    _seed_messages(sqlite_instance, "conv-main", [piece])

    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "Original" in out
    assert "Converted" in out


async def test_write_async_renders_system_message(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(role="system", original_value="sys-prompt", converted_value="sys-prompt")
    _seed_messages(sqlite_instance, "conv-main", [piece])

    await printer.write_async(attack_result)
    assert "SYSTEM" in capsys.readouterr().out


# --- blocked-content paths ---


async def test_write_async_blocked_message_without_partial_content(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value_data_type="error",
        response_error="blocked",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])

    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "BLOCKED BY TARGET" in out
    assert "content filter" in out
    # The raw error JSON should not be rendered as the message body.
    assert "status_code" not in out


async def test_write_async_blocked_message_with_partial_content(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value_data_type="error",
        response_error="blocked",
        prompt_metadata={"partial_content": "before cutoff"},
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])

    await printer.write_async(attack_result)
    out = capsys.readouterr().out
    assert "BLOCKED BY TARGET" in out
    assert "Partial content" in out
    assert "before cutoff" in out


# --- auxiliary scores path ---


async def test_write_async_with_auxiliary_scores(printer, attack_result, sqlite_instance, capsys):
    piece = MessagePiece(role="assistant", original_value="response", converted_value="response")
    _seed_messages(sqlite_instance, "conv-main", [piece])
    sqlite_instance.add_scores_to_memory(scores=[_make_score(piece_id=str(piece.id), value="true")])

    await printer.write_async(attack_result, include_auxiliary_scores=True)
    out = capsys.readouterr().out
    assert "Scores" in out
    assert "MockScorer" in out


# --- pruned conversations paths ---


async def test_write_async_pruned_conversations_with_messages_and_scores(
    printer, attack_result, sqlite_instance, capsys
):
    pruned_piece = MessagePiece(role="assistant", original_value="pruned response", converted_value="pruned response")
    _seed_messages(sqlite_instance, "pruned-conv", [pruned_piece])
    sqlite_instance.add_scores_to_memory(scores=[_make_score(piece_id=str(pruned_piece.id))])

    attack_result.related_conversations.add(
        ConversationReference(
            conversation_id="pruned-conv", conversation_type=ConversationType.PRUNED, description="branch one"
        )
    )

    await printer.write_async(attack_result, include_pruned_conversations=True)
    out = capsys.readouterr().out
    assert "PRUNED" in out
    assert "branch one" in out
    assert "pruned response" in out
    assert "Score" in out


async def test_write_async_pruned_conversation_with_no_messages(printer, attack_result, capsys):
    attack_result.related_conversations.add(
        ConversationReference(conversation_id="empty-pruned", conversation_type=ConversationType.PRUNED)
    )
    await printer.write_async(attack_result, include_pruned_conversations=True)
    out = capsys.readouterr().out
    assert "PRUNED" in out
    assert "No messages found for conversation: empty-pruned" in out


# --- adversarial conversation paths ---


async def test_write_async_adversarial_conversation_with_messages(printer, attack_result, sqlite_instance, capsys):
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
    assert "Adversarial Conversation" in out
    assert "red team chain" in out
    assert "adv prompt" in out
    assert "adv reply" in out


async def test_write_async_adversarial_filters_to_best_branch(printer, sqlite_instance, capsys):
    best_piece = MessagePiece(role="user", original_value="best-branch-prompt", converted_value="best-branch-prompt")
    other_piece = MessagePiece(role="user", original_value="other-branch-prompt", converted_value="other-branch-prompt")
    _seed_messages(sqlite_instance, "adv-best", [best_piece])
    _seed_messages(sqlite_instance, "adv-other", [other_piece])

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

    await PrettyAttackResultMemoryPrinter(enable_colors=False).write_async(
        result, include_adversarial_conversation=True
    )
    out = capsys.readouterr().out
    assert "best-scoring branch" in out
    assert "best-branch-prompt" in out
    assert "other-branch-prompt" not in out


async def test_write_async_adversarial_with_no_messages(printer, attack_result, capsys):
    attack_result.related_conversations.add(
        ConversationReference(conversation_id="adv-empty", conversation_type=ConversationType.ADVERSARIAL)
    )
    await printer.write_async(attack_result, include_adversarial_conversation=True)
    out = capsys.readouterr().out
    assert "Adversarial Conversation" in out
    assert "No messages found for conversation: adv-empty" in out


# --- reasoning trace path ---


async def test_write_async_renders_reasoning_summary_when_requested(printer, attack_result, sqlite_instance, capsys):
    reasoning_value = '{"summary": [{"text": "step one"}, {"text": "step two"}]}'
    piece = MessagePiece(
        role="assistant",
        original_value=reasoning_value,
        converted_value=reasoning_value,
        original_value_data_type="reasoning",
        converted_value_data_type="reasoning",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])

    content = await printer._render_conversation_async(attack_result, include_reasoning_trace=True)
    assert "Reasoning Summary" in content
    assert "step one" in content
    assert "step two" in content


# --- deprecated aliases (smoke check that they still forward to write_async) ---


async def test_print_result_async_emits_deprecation_warning_and_still_writes(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="print_result_async"):
        await printer.print_result_async(attack_result)
    assert "ATTACK RESULT" in capsys.readouterr().out


async def test_print_conversation_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="print_conversation_async"):
        await printer.print_conversation_async(attack_result)
    assert "No conversation found" in capsys.readouterr().out


async def test_output_conversation_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="output_conversation_async"):
        await printer.output_conversation_async(attack_result)
    assert "No conversation found" in capsys.readouterr().out


async def test_print_summary_async_emits_deprecation_warning(printer, attack_result, capsys):
    with pytest.warns(DeprecationWarning, match="print_summary_async"):
        await printer.print_summary_async(attack_result)
    assert "Test objective" in capsys.readouterr().out


async def test_print_messages_async_emits_deprecation_warning(printer, capsys):
    with pytest.warns(DeprecationWarning, match="print_messages_async"):
        await printer.print_messages_async([])
    assert "No messages to display" in capsys.readouterr().out


# --- early-return branches: include flags but no related refs ---


async def test_write_async_include_pruned_with_no_pruned_refs(printer, attack_result, capsys):
    await printer.write_async(attack_result, include_pruned_conversations=True)
    assert "Pruned Conversations" not in capsys.readouterr().out


async def test_write_async_include_adversarial_with_no_refs(printer, attack_result, capsys):
    await printer.write_async(attack_result, include_adversarial_conversation=True)
    assert "Adversarial Conversation" not in capsys.readouterr().out


# --- colors-enabled smoke test ---


async def test_write_async_with_colors_enabled_emits_ansi_codes(
    patch_central_database, attack_result, sqlite_instance, capsys
):
    p = PrettyAttackResultMemoryPrinter(width=80, indent_size=2, enable_colors=True)
    # Seed a message with newlines + an empty line so the wrap helper exercises both branches.
    piece = MessagePiece(role="assistant", original_value="line1\n\nline3", converted_value="line1\n\nline3")
    _seed_messages(sqlite_instance, "conv-main", [piece])
    await p.write_async(attack_result)
    assert "\x1b[" in capsys.readouterr().out


async def test_write_async_invalid_reasoning_summary_is_silently_skipped(
    printer, attack_result, sqlite_instance, capsys
):
    # Reasoning piece with non-JSON value should not blow up, just produce no summary section.
    piece = MessagePiece(
        role="assistant",
        original_value="not-json",
        converted_value="not-json",
        original_value_data_type="reasoning",
        converted_value_data_type="reasoning",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])
    content = await printer._render_conversation_async(attack_result, include_reasoning_trace=True)
    assert "Reasoning Summary" not in content


async def test_write_async_reasoning_summary_without_summary_key_is_silently_skipped(
    printer, attack_result, sqlite_instance
):
    piece = MessagePiece(
        role="assistant",
        original_value='{"other": "data"}',
        converted_value='{"other": "data"}',
        original_value_data_type="reasoning",
        converted_value_data_type="reasoning",
    )
    _seed_messages(sqlite_instance, "conv-main", [piece])
    content = await printer._render_conversation_async(attack_result, include_reasoning_trace=True)
    assert "Reasoning Summary" not in content
