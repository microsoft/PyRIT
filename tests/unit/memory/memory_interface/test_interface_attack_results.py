# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid
from typing import TYPE_CHECKING, Optional

from pyrit.common.utils import to_sha256
from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ConversationReference,
    ConversationType,
    MessagePiece,
    Score,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def create_message_piece(conversation_id: str, prompt_num: int, targeted_harm_categories=None, labels=None):
    """Helper function to create MessagePiece with optional targeted harm categories and labels."""
    return MessagePiece(
        role="user",
        original_value=f"Test prompt {prompt_num}",
        converted_value=f"Test prompt {prompt_num}",
        conversation_id=conversation_id,
        targeted_harm_categories=targeted_harm_categories,
        labels=labels,
    )


def create_attack_result(conversation_id: str, objective_num: int, outcome: AttackOutcome = AttackOutcome.SUCCESS):
    """Helper function to create AttackResult."""
    return AttackResult(
        conversation_id=conversation_id,
        objective=f"Objective {objective_num}",
        outcome=outcome,
    )


def test_add_attack_results_to_memory(sqlite_instance: MemoryInterface):
    """Test adding attack results to memory."""
    # Create sample attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Attack was successful",
        metadata={"test_key": "test_value"},
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
        outcome_reason="Attack failed",
        metadata={"another_key": "another_value"},
    )

    # Add attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Verify they were added by querying all attack results
    all_attack_results: Sequence[AttackResultEntry] = sqlite_instance._query_entries(AttackResultEntry)
    assert len(all_attack_results) == 2

    # Verify the data was stored correctly
    stored_results = [entry.get_attack_result() for entry in all_attack_results]
    conversation_ids = {result.conversation_id for result in stored_results}
    assert conversation_ids == {"conv_1", "conv_2"}


def test_get_attack_results_by_ids(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by their IDs."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Test objective 3",
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.UNDETERMINED,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Get all attack result entries to get their IDs
    all_entries: Sequence[AttackResultEntry] = sqlite_instance._query_entries(AttackResultEntry)
    assert len(all_entries) == 3

    # Get IDs of first two attack results
    attack_result_ids = [str(entry.id) for entry in all_entries[:2]]

    # Retrieve attack results by IDs
    retrieved_results = sqlite_instance.get_attack_results(attack_result_ids=attack_result_ids)

    # Verify correct results were retrieved
    assert len(retrieved_results) == 2
    retrieved_conversation_ids = {result.conversation_id for result in retrieved_results}
    assert retrieved_conversation_ids == {"conv_1", "conv_2"}


def test_get_attack_results_by_conversation_id(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by conversation ID.

    When duplicate rows exist for the same conversation_id (legacy bug),
    get_attack_results deduplicates and returns only the newest entry.
    """
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_1",  # Same conversation ID (simulates legacy duplicate)
        objective="Test objective 2",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_2",  # Different conversation ID
        objective="Test objective 3",
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.UNDETERMINED,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results by conversation ID — deduplication keeps only the newest
    retrieved_results = sqlite_instance.get_attack_results(conversation_id="conv_1")

    assert len(retrieved_results) == 1
    assert retrieved_results[0].conversation_id == "conv_1"


def test_get_attack_results_by_objective(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by objective substring."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective for success",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Another objective for failure",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Different objective entirely",
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.UNDETERMINED,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results by objective substring
    retrieved_results = sqlite_instance.get_attack_results(objective="objective for")

    # Verify correct results were retrieved (should match first two)
    assert len(retrieved_results) == 2
    objectives = {result.objective for result in retrieved_results}
    assert "Test objective for success" in objectives
    assert "Another objective for failure" in objectives


def test_get_attack_results_by_outcome(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by outcome."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.SUCCESS,  # Same outcome
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Test objective 3",
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.FAILURE,  # Different outcome
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results by outcome
    retrieved_results = sqlite_instance.get_attack_results(outcome="success")

    # Verify correct results were retrieved
    assert len(retrieved_results) == 2
    for result in retrieved_results:
        assert result.outcome == AttackOutcome.SUCCESS


def test_get_attack_results_by_objective_sha256(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by objective SHA256."""

    # Create objectives with known SHA256 hashes
    objective1 = "Test objective 1"
    objective2 = "Test objective 2"
    objective3 = "Different objective"

    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective=objective1,
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )
    objective1_sha256 = to_sha256(attack_result1.objective)

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective=objective2,
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )
    objective2_sha256 = to_sha256(attack_result2.objective)

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective=objective3,
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.UNDETERMINED,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results by objective SHA256
    retrieved_results = sqlite_instance.get_attack_results(objective_sha256=[objective1_sha256, objective2_sha256])

    # Verify correct results were retrieved
    assert len(retrieved_results) == 2
    retrieved_objectives = {result.objective for result in retrieved_results}
    assert objective1 in retrieved_objectives
    assert objective2 in retrieved_objectives


def test_get_attack_results_multiple_filters(sqlite_instance: MemoryInterface):
    """Test retrieving attack results with multiple filters."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective for success",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_1",  # Same conversation ID
        objective="Another objective for failure",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,  # Different outcome
    )

    attack_result3 = AttackResult(
        conversation_id="conv_2",  # Different conversation ID
        objective="Test objective for success",
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.SUCCESS,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results with multiple filters
    retrieved_results = sqlite_instance.get_attack_results(
        conversation_id="conv_1", objective="objective for", outcome="success"
    )

    # Should only match the first result
    assert len(retrieved_results) == 1
    assert retrieved_results[0].conversation_id == "conv_1"
    assert retrieved_results[0].outcome == AttackOutcome.SUCCESS
    assert "objective for" in retrieved_results[0].objective


def test_get_attack_results_no_filters(sqlite_instance: MemoryInterface):
    """Test retrieving all attack results when no filters are provided."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    # Add attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Retrieve all attack results (no filters)
    retrieved_results = sqlite_instance.get_attack_results()

    # Should return all results
    assert len(retrieved_results) == 2


def test_get_attack_results_empty_list(sqlite_instance: MemoryInterface):
    """Test retrieving attack results with empty ID list."""
    # Create and add an attack result
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Try to retrieve with empty list
    retrieved_results = sqlite_instance.get_attack_results(attack_result_ids=[])
    assert len(retrieved_results) == 0


def test_get_attack_results_nonexistent_ids(sqlite_instance: MemoryInterface):
    """Test retrieving attack results with non-existent IDs."""
    # Create and add an attack result
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Try to retrieve with non-existent IDs
    nonexistent_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    retrieved_results = sqlite_instance.get_attack_results(attack_result_ids=nonexistent_ids)
    assert len(retrieved_results) == 0


def test_attack_result_with_last_response_and_score(sqlite_instance: MemoryInterface):
    """Test attack result with last_response and last_score relationships."""
    # Create a message piece first
    message_piece = MessagePiece(
        role="user",
        original_value="Test prompt",
        converted_value="Test prompt",
        conversation_id="conv_1",
    )
    assert message_piece.id is not None, "Message piece ID should not be None"

    # Create a score
    score = Score(
        score_value="1.0",
        score_type="float_scale",
        score_category=["test_category"],
        scorer_class_identifier=ComponentIdentifier(
            class_name="TestScorer",
            class_module="test_module",
        ),
        message_piece_id=message_piece.id,
        score_value_description="Test score description",
        score_rationale="Test score rationale",
        score_metadata={"test": "metadata"},
    )

    # Add message piece and score to memory
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece])
    sqlite_instance.add_scores_to_memory(scores=[score])

    # Create attack result with last_response and last_score
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective with relationships",
        last_response=message_piece,
        last_score=score,
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    # Add attack result to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Retrieve and verify relationships
    all_entries: Sequence[AttackResult] = sqlite_instance.get_attack_results()
    assert len(all_entries) == 1
    assert all_entries[0].conversation_id == "conv_1"
    assert all_entries[0].last_response is not None
    assert all_entries[0].last_response.id == message_piece.id
    assert all_entries[0].last_score is not None
    assert all_entries[0].last_score.id == score.id


def test_attack_result_all_outcomes(sqlite_instance: MemoryInterface):
    """Test attack results with all possible outcomes."""
    outcomes = [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED]
    attack_results = []

    for i, outcome in enumerate(outcomes):
        attack_result = AttackResult(
            conversation_id=f"conv_{i}",
            objective=f"Test objective {i}",
            atomic_attack_identifier=build_atomic_attack_identifier(
                attack_identifier=ComponentIdentifier(class_name=f"TestAttack{i}", class_module="test.module"),
            ),
            executed_turns=i + 1,
            execution_time_ms=(i + 1) * 100,
            outcome=outcome,
            outcome_reason=f"Attack {outcome.value}",
        )
        attack_results.append(attack_result)

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Verify all were added
    all_entries: Sequence[AttackResultEntry] = sqlite_instance._query_entries(AttackResultEntry)
    assert len(all_entries) == 3

    # Verify outcomes were stored correctly
    stored_results = [entry.get_attack_result() for entry in all_entries]
    stored_outcomes = {result.outcome for result in stored_results}
    assert stored_outcomes == set(outcomes)


def test_attack_result_metadata_handling(sqlite_instance: MemoryInterface):
    """Test that attack result metadata is properly stored and retrieved."""
    # Create attack result with various metadata types
    metadata = {
        "string_value": "test_string",
        "int_value": 42,
        "float_value": 3.14,
        "bool_value": True,
        "list_value": ["item1", "item2"],
        "dict_value": {"nested": "value"},
    }

    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective with metadata",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        metadata=metadata,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Retrieve and verify metadata
    all_entries: Sequence[AttackResultEntry] = sqlite_instance._query_entries(AttackResultEntry)
    assert len(all_entries) == 1

    retrieved_result = all_entries[0].get_attack_result()
    assert retrieved_result.metadata == metadata


def test_attack_result_objective_sha256_auto_generation(sqlite_instance: MemoryInterface):
    """Test that objective SHA256 is always calculated."""

    objective = "Test objective without SHA256"
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective=objective,
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )
    expected_sha256 = to_sha256(attack_result.objective)

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Retrieve and verify that objective_sha256 is calculated
    all_entries: Sequence[AttackResultEntry] = sqlite_instance._query_entries(AttackResultEntry)
    assert len(all_entries) == 1

    # Verify the database stored the correct SHA256
    assert all_entries[0].objective_sha256 == expected_sha256


def test_attack_result_with_attack_generation_conversation_ids(sqlite_instance: MemoryInterface):
    """Test attack result with related_conversations (PRUNED / ADVERSARIAL)."""
    pruned_ids = {"pruned_conv_1", "pruned_conv_2"}
    adversarial_ids = {"adv_conv_1", "adv_conv_2", "adv_conv_3"}

    related_conversations: set[ConversationReference] = {
        *(ConversationReference(cid, ConversationType.PRUNED) for cid in pruned_ids),
        *(ConversationReference(cid, ConversationType.ADVERSARIAL) for cid in adversarial_ids),
    }

    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective with conversation IDs",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        related_conversations=related_conversations,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    entry: AttackResultEntry = sqlite_instance._query_entries(AttackResultEntry)[0]

    assert set(entry.pruned_conversation_ids) == pruned_ids  # type: ignore[arg-type]
    assert set(entry.adversarial_chat_conversation_ids) == adversarial_ids  # type: ignore[arg-type]

    retrieved_result = entry.get_attack_result()
    assert {
        r.conversation_id for r in retrieved_result.get_conversations_by_type(ConversationType.PRUNED)
    } == pruned_ids
    assert {
        r.conversation_id for r in retrieved_result.get_conversations_by_type(ConversationType.ADVERSARIAL)
    } == adversarial_ids


def test_attack_result_without_attack_generation_conversation_ids(sqlite_instance: MemoryInterface):
    """Test attack result without related_conversations."""
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test objective without conversation IDs",
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    entry: AttackResultEntry = sqlite_instance._query_entries(AttackResultEntry)[0]
    assert not entry.pruned_conversation_ids
    assert not entry.adversarial_chat_conversation_ids

    retrieved_result = entry.get_attack_result()
    assert not retrieved_result.get_conversations_by_type(ConversationType.PRUNED)
    assert not retrieved_result.get_conversations_by_type(ConversationType.ADVERSARIAL)


def test_update_attack_result_adversarial_chat_conversation_ids_round_trip(sqlite_instance: MemoryInterface):
    """Test that updating adversarial_chat_conversation_ids is reflected when reading back.

    This catches a regression where the conversation count in the attack history
    was always showing 1 instead of the actual number of conversations.
    """
    # Create attack with no related conversations
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test conversation count",
        outcome=AttackOutcome.UNDETERMINED,
        metadata={"created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"},
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Verify initial state: no related conversations
    results = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(results) == 1
    assert len(results[0].related_conversations) == 0

    # Add first related conversation
    sqlite_instance.update_attack_result(
        conversation_id="conv_1",
        update_fields={"adversarial_chat_conversation_ids": ["branch-1"]},
    )

    results = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(results[0].related_conversations) == 1
    assert {r.conversation_id for r in results[0].related_conversations} == {"branch-1"}

    # Add second related conversation (preserving the first)
    sqlite_instance.update_attack_result(
        conversation_id="conv_1",
        update_fields={"adversarial_chat_conversation_ids": ["branch-1", "branch-2"]},
    )

    results = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(results[0].related_conversations) == 2
    assert {r.conversation_id for r in results[0].related_conversations} == {"branch-1", "branch-2"}

    # Verify they are all ADVERSARIAL type
    for ref in results[0].related_conversations:
        assert ref.conversation_type == ConversationType.ADVERSARIAL


def test_update_attack_result_metadata_does_not_clobber_conversation_ids(sqlite_instance: MemoryInterface):
    """Regression test: updating only attack_metadata must not erase adversarial_chat_conversation_ids.

    This was the root cause of the conversation-count bug. The old _update_entries
    used session.merge() which copied ALL attributes from the (potentially stale)
    detached entry, silently overwriting JSON columns that were not in update_fields.
    """
    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test metadata update preserves conversation ids",
        outcome=AttackOutcome.UNDETERMINED,
        metadata={"created_at": "2026-01-01T00:00:00"},
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Step 1: add related conversations
    sqlite_instance.update_attack_result(
        conversation_id="conv_1",
        update_fields={"adversarial_chat_conversation_ids": ["branch-1", "branch-2"]},
    )

    # Step 2: update ONLY metadata (this is what add_message_async does)
    sqlite_instance.update_attack_result(
        conversation_id="conv_1",
        update_fields={"attack_metadata": {"created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-02T00:00:00"}},
    )

    # Verify conversation ids are still present
    results = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(results[0].related_conversations) == 2, (
        "Updating attack_metadata must not erase adversarial_chat_conversation_ids"
    )
    assert {r.conversation_id for r in results[0].related_conversations} == {"branch-1", "branch-2"}


def test_update_attack_result_stale_entry_does_not_overwrite(sqlite_instance: MemoryInterface):
    """Regression test: merging a stale entry must not overwrite concurrent updates.

    Simulates the race condition where entry is loaded, then another update modifies
    the DB, and finally the stale entry is used for an unrelated update.
    """
    from pyrit.memory.memory_models import AttackResultEntry

    attack_result = AttackResult(
        conversation_id="conv_1",
        objective="Test stale merge",
        outcome=AttackOutcome.UNDETERMINED,
        metadata={"created_at": "2026-01-01T00:00:00"},
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Load entry (will become stale)
    stale_entries = sqlite_instance._query_entries(
        AttackResultEntry, conditions=AttackResultEntry.conversation_id == "conv_1"
    )
    assert stale_entries[0].adversarial_chat_conversation_ids is None

    # Concurrent update adds conversation ids
    sqlite_instance.update_attack_result(
        conversation_id="conv_1",
        update_fields={"adversarial_chat_conversation_ids": ["branch-1"]},
    )

    # Now update with the stale entry (only metadata)
    sqlite_instance._update_entries(
        entries=[stale_entries[0]],
        update_fields={"attack_metadata": {"updated_at": "2026-01-02T00:00:00"}},
    )

    # Verify the concurrent update was NOT lost
    results = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(results[0].related_conversations) == 1, (
        "Stale entry merge must not overwrite concurrent adversarial_chat_conversation_ids update"
    )
    assert results[0].related_conversations.pop().conversation_id == "branch-1"


def test_get_attack_results_by_harm_category_single(sqlite_instance: MemoryInterface):
    """Test filtering attack results by a single harm category."""

    # Create message pieces with harm categories using helper function
    message_piece1 = create_message_piece("conv_1", 1, targeted_harm_categories=["violence", "illegal"])
    message_piece2 = create_message_piece("conv_2", 2, targeted_harm_categories=["illegal"])
    message_piece3 = create_message_piece("conv_3", 3, targeted_harm_categories=["violence"])

    # Add message pieces to memory
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    # Create attack results using helper function
    attack_result1 = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    attack_result2 = create_attack_result("conv_2", 2, AttackOutcome.FAILURE)
    attack_result3 = create_attack_result("conv_3", 3, AttackOutcome.SUCCESS)

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    violence_results = sqlite_instance.get_attack_results(targeted_harm_categories=["violence"])
    assert len(violence_results) == 2
    conversation_ids = {result.conversation_id for result in violence_results}
    assert conversation_ids == {"conv_1", "conv_3"}

    illegal_results = sqlite_instance.get_attack_results(targeted_harm_categories=["illegal"])
    assert len(illegal_results) == 2
    conversation_ids = {result.conversation_id for result in illegal_results}
    assert conversation_ids == {"conv_1", "conv_2"}


def test_get_attack_results_by_harm_category_multiple(sqlite_instance: MemoryInterface):
    """Test filtering attack results by multiple harm categories (AND logic)."""

    # Create message pieces with different harm category combinations
    message_piece1 = create_message_piece("conv_1", 1, targeted_harm_categories=["violence", "illegal", "hate"])
    message_piece2 = create_message_piece("conv_2", 2, targeted_harm_categories=["violence", "illegal"])
    message_piece3 = create_message_piece("conv_3", 3, targeted_harm_categories=["violence"])

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    # Create attack results
    attack_result1 = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    attack_result2 = create_attack_result("conv_2", 2, AttackOutcome.SUCCESS)
    attack_result3 = create_attack_result("conv_3", 3, AttackOutcome.FAILURE)

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Test filtering by multiple harm categories
    violence_and_illegal_results = sqlite_instance.get_attack_results(targeted_harm_categories=["violence", "illegal"])
    assert len(violence_and_illegal_results) == 2
    conversation_ids = {result.conversation_id for result in violence_and_illegal_results}
    assert conversation_ids == {"conv_1", "conv_2"}
    all_three_results = sqlite_instance.get_attack_results(targeted_harm_categories=["violence", "illegal", "hate"])
    assert len(all_three_results) == 1
    assert all_three_results[0].conversation_id == "conv_1"


def test_get_attack_results_by_labels_single(sqlite_instance: MemoryInterface):
    """Test filtering attack results by single label."""

    # Create message pieces with labels
    message_piece1 = create_message_piece("conv_1", 1, labels={"operation": "test_op", "operator": "roakey"})
    message_piece2 = create_message_piece("conv_2", 2, labels={"operation": "test_op"})
    message_piece3 = create_message_piece("conv_3", 3, labels={"operation": "other_op", "operator": "roakey"})

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    # Create attack results
    attack_result1 = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    attack_result2 = create_attack_result("conv_2", 2, AttackOutcome.FAILURE)
    attack_result3 = create_attack_result("conv_3", 3, AttackOutcome.SUCCESS)

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Test filtering by labels
    test_op_results = sqlite_instance.get_attack_results(labels={"operation": "test_op"})
    assert len(test_op_results) == 2
    conversation_ids = {result.conversation_id for result in test_op_results}
    assert conversation_ids == {"conv_1", "conv_2"}
    roakey_results = sqlite_instance.get_attack_results(labels={"operator": "roakey"})
    assert len(roakey_results) == 2
    conversation_ids = {result.conversation_id for result in roakey_results}
    assert conversation_ids == {"conv_1", "conv_3"}


def test_get_attack_results_by_labels_multiple(sqlite_instance: MemoryInterface):
    """Test filtering attack results by multiple labels (AND logic)."""

    # Create message pieces with multiple labels using helper function
    message_piece1 = create_message_piece(
        "conv_1", 1, labels={"operation": "test_op", "operator": "roakey", "phase": "initial"}
    )
    message_piece2 = create_message_piece(
        "conv_2", 2, labels={"operation": "test_op", "operator": "roakey", "phase": "final"}
    )
    message_piece3 = create_message_piece("conv_3", 3, labels={"operation": "test_op", "phase": "initial"})

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    # Create attack results
    attack_results = [
        create_attack_result("conv_1", 1, AttackOutcome.SUCCESS),
        create_attack_result("conv_2", 2, AttackOutcome.SUCCESS),
        create_attack_result("conv_3", 3, AttackOutcome.FAILURE),
    ]

    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Test filtering by multiple labels (AND logic)
    roakey_initial_results = sqlite_instance.get_attack_results(labels={"operator": "roakey", "phase": "initial"})
    assert len(roakey_initial_results) == 1
    assert roakey_initial_results[0].conversation_id == "conv_1"

    test_op_roakey_results = sqlite_instance.get_attack_results(labels={"operation": "test_op", "operator": "roakey"})
    assert len(test_op_roakey_results) == 2
    conversation_ids = {result.conversation_id for result in test_op_roakey_results}
    assert conversation_ids == {"conv_1", "conv_2"}


def test_get_attack_results_by_harm_category_and_labels(sqlite_instance: MemoryInterface):
    """Test filtering attack results by both harm categories and labels."""

    # Create message pieces with both harm categories and labels using helper function
    message_piece1 = create_message_piece(
        "conv_1",
        1,
        targeted_harm_categories=["violence", "illegal"],
        labels={"operation": "test_op", "operator": "roakey"},
    )
    message_piece2 = create_message_piece(
        "conv_2", 2, targeted_harm_categories=["violence"], labels={"operation": "test_op", "operator": "roakey"}
    )
    message_piece3 = create_message_piece(
        "conv_3",
        3,
        targeted_harm_categories=["violence", "illegal"],
        labels={"operation": "other_op", "operator": "bob"},
    )

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    # Create attack results
    attack_results = [
        create_attack_result("conv_1", 1, AttackOutcome.SUCCESS),
        create_attack_result("conv_2", 2, AttackOutcome.SUCCESS),
        create_attack_result("conv_3", 3, AttackOutcome.FAILURE),
    ]

    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Test filtering by both harm categories and labels
    violence_illegal_roakey_results = sqlite_instance.get_attack_results(
        targeted_harm_categories=["violence", "illegal"], labels={"operator": "roakey"}
    )
    assert len(violence_illegal_roakey_results) == 1
    assert violence_illegal_roakey_results[0].conversation_id == "conv_1"

    # Test filtering by harm category and operation
    violence_test_op_results = sqlite_instance.get_attack_results(
        targeted_harm_categories=["violence"], labels={"operation": "test_op"}
    )
    assert len(violence_test_op_results) == 2
    conversation_ids = {result.conversation_id for result in violence_test_op_results}
    assert conversation_ids == {"conv_1", "conv_2"}


def test_get_attack_results_harm_category_no_matches(sqlite_instance: MemoryInterface):
    """Test filtering by harm category that doesn't exist."""

    # Create attack result without the harm category we'll search for
    message_piece = create_message_piece("conv_1", 1, targeted_harm_categories=["violence"])
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece])

    attack_result = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Search for non-existent harm category
    results = sqlite_instance.get_attack_results(targeted_harm_categories=["nonexistent"])
    assert len(results) == 0


def test_get_attack_results_labels_no_matches(sqlite_instance: MemoryInterface):
    """Test filtering by labels that don't exist."""

    # Create attack result without the labels we'll search for
    message_piece = create_message_piece("conv_1", 1, labels={"operation": "test_op"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece])

    attack_result = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Search for non-existent labels
    results = sqlite_instance.get_attack_results(labels={"nonexistent": "value"})
    assert len(results) == 0


def test_get_attack_results_labels_query_on_empty_labels(sqlite_instance: MemoryInterface):
    """Test querying for labels when records have no labels at all"""

    # Create attack results with NO labels
    message_piece1 = create_message_piece("conv_1", 1)
    message_piece2 = create_message_piece("conv_2", 1)

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2])

    attack_result1 = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    attack_result2 = create_attack_result("conv_2", 2, AttackOutcome.FAILURE)

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    results = sqlite_instance.get_attack_results(labels={"operation": "test"})
    assert len(results) == 0

    results = sqlite_instance.get_attack_results(labels={"researcher": "roakey"})
    assert len(results) == 0

    results = sqlite_instance.get_attack_results(labels={"non_existing_key": "no_value"})
    assert len(results) == 0


def test_get_attack_results_labels_key_exists_value_mismatch(sqlite_instance: MemoryInterface):
    """Test querying for labels where the key exists but the value doesn't match."""

    # Create attack results with specific label values
    message_piece1 = create_message_piece("conv_1", 1, labels={"operation": "op_exists", "researcher": "roakey"})
    message_piece2 = create_message_piece("conv_2", 1, labels={"operation": "another_op", "researcher": "roakey"})
    message_piece3 = create_message_piece("conv_3", 1, labels={"operation": "test_op"})

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    attack_results = [
        create_attack_result("conv_1", 1, AttackOutcome.SUCCESS),
        create_attack_result("conv_2", 2, AttackOutcome.SUCCESS),
        create_attack_result("conv_3", 3, AttackOutcome.FAILURE),
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Query for key that exists but with wrong value
    results = sqlite_instance.get_attack_results(labels={"operation": "op_doesnotexist"})
    assert len(results) == 0

    # Query for existing key with correct value
    results = sqlite_instance.get_attack_results(labels={"operation": "op_exists"})
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"

    # Another key exists but wrong value
    results = sqlite_instance.get_attack_results(labels={"researcher": "not_roakey"})
    assert len(results) == 0

    # Correct key and value
    results = sqlite_instance.get_attack_results(labels={"researcher": "roakey"})
    assert len(results) == 2
    assert results[0].conversation_id == "conv_1"

    # Key exists in some records but not others, and we query for wrong value
    results = sqlite_instance.get_attack_results(
        labels={"operation": "wrong_value"}
    )  # operation exists in conv_3 but with "test_op"
    assert len(results) == 0

    # Correct key and value for the third record
    results = sqlite_instance.get_attack_results(labels={"operation": "test_op"})
    assert len(results) == 1
    assert results[0].conversation_id == "conv_3"

    # Test multiple keys where one matches and one doesn't
    results = sqlite_instance.get_attack_results(labels={"operation": "op_exists", "researcher": "not_roakey"})
    assert len(results) == 0

    # Test multiple keys where both match
    results = sqlite_instance.get_attack_results(labels={"operation": "op_exists", "researcher": "roakey"})
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


# ---------------------------------------------------------------------------
# get_unique_attack_labels tests
# ---------------------------------------------------------------------------


def test_get_unique_attack_labels_empty(sqlite_instance: MemoryInterface):
    """Returns empty dict when there are no attack results."""
    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_single(sqlite_instance: MemoryInterface):
    """Returns labels from a single attack result's message pieces."""
    message = create_message_piece("conv_1", 1, labels={"env": "prod", "team": "red"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message])

    ar = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod"], "team": ["red"]}


def test_get_unique_attack_labels_multiple_attacks_merges_values(sqlite_instance: MemoryInterface):
    """Values from different attacks are merged and sorted."""
    msg1 = create_message_piece("conv_1", 1, labels={"env": "prod", "team": "red"})
    msg2 = create_message_piece("conv_2", 2, labels={"env": "staging", "team": "red"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg1, msg2])

    ar1 = create_attack_result("conv_1", 1)
    ar2 = create_attack_result("conv_2", 2)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod", "staging"], "team": ["red"]}


def test_get_unique_attack_labels_no_pieces(sqlite_instance: MemoryInterface):
    """Attack results without any message pieces return empty dict."""
    ar = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_pieces_without_labels(sqlite_instance: MemoryInterface):
    """Message pieces with no labels are skipped."""
    msg = create_message_piece("conv_1", 1)  # labels=None
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg])

    ar = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_ignores_non_attack_pieces(sqlite_instance: MemoryInterface):
    """Labels on pieces not linked to any attack are excluded."""
    msg = create_message_piece("conv_no_attack", 1, labels={"env": "prod"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg])

    # No AttackResult for "conv_no_attack"
    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_non_string_values_skipped(sqlite_instance: MemoryInterface):
    """Non-string label values are ignored."""
    msg = create_message_piece("conv_1", 1, labels={"env": "prod", "count": 42})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg])

    ar = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod"]}


def test_get_unique_attack_labels_keys_sorted(sqlite_instance: MemoryInterface):
    """Returned keys and values are sorted alphabetically."""
    msg1 = create_message_piece("conv_1", 1, labels={"zoo": "z_val", "alpha": "a"})
    msg2 = create_message_piece("conv_2", 2, labels={"alpha": "b"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg1, msg2])

    ar1 = create_attack_result("conv_1", 1)
    ar2 = create_attack_result("conv_2", 2)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_attack_labels()
    assert list(result.keys()) == ["alpha", "zoo"]
    assert result["alpha"] == ["a", "b"]
    assert result["zoo"] == ["z_val"]


def test_get_unique_attack_labels_non_dict_labels_skipped(sqlite_instance: MemoryInterface):
    """Labels stored as a non-dict JSON value (e.g. a string) are skipped."""
    from contextlib import closing

    from sqlalchemy import text

    # Insert a real attack + piece with normal labels first
    msg1 = create_message_piece("conv_1", 1, labels={"env": "prod"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg1])
    ar1 = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    # Insert a second attack and use raw SQL to set labels to a JSON string
    msg2 = create_message_piece("conv_2", 2, labels={"placeholder": "x"})
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[msg2])
    ar2 = create_attack_result("conv_2", 2)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar2])
    with closing(sqlite_instance.get_session()) as session:
        session.execute(
            text('UPDATE "PromptMemoryEntries" SET labels = \'"just_a_string"\' WHERE conversation_id = :cid'),
            {"cid": "conv_2"},
        )
        session.commit()

    result = sqlite_instance.get_unique_attack_labels()
    # Only the dict labels from conv_1 should appear
    assert result == {"env": ["prod"]}


# ============================================================================
# Attack class and converter class filtering tests
# ============================================================================


def _make_attack_result_with_identifier(
    conversation_id: str,
    class_name: str,
    converter_class_names: Optional[list[str]] = None,
) -> AttackResult:
    """Helper to create an AttackResult with a ComponentIdentifier containing converters."""
    children: dict = {}
    if converter_class_names is not None:
        children["request_converters"] = [
            ComponentIdentifier(
                class_name=name,
                class_module="pyrit.prompt_converter",
            )
            for name in converter_class_names
        ]

    return AttackResult(
        conversation_id=conversation_id,
        objective=f"Objective for {conversation_id}",
        atomic_attack_identifier=build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(
                class_name=class_name,
                class_module="pyrit.attacks",
                children=children,
            ),
        ),
    )


def test_get_attack_results_by_attack_class(sqlite_instance: MemoryInterface):
    """Test filtering attack results by attack_class matches class_name in JSON."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_class="CrescendoAttack")
    assert len(results) == 2
    assert {r.conversation_id for r in results} == {"conv_1", "conv_3"}


def test_get_attack_results_by_attack_class_no_match(sqlite_instance: MemoryInterface):
    """Test that attack_class filter returns empty when nothing matches."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(attack_class="NonExistentAttack")
    assert len(results) == 0


def test_get_attack_results_by_attack_class_case_sensitive(sqlite_instance: MemoryInterface):
    """Test that attack_class filter is case-sensitive (exact match)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(attack_class="crescendoattack")
    assert len(results) == 0


def test_get_attack_results_by_attack_class_no_identifier(sqlite_instance: MemoryInterface):
    """Test that attacks with no attack_identifier (empty JSON) are excluded by attack_class filter."""
    ar1 = create_attack_result("conv_1", 1)  # No attack_identifier → stored as {}
    ar2 = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(attack_class="CrescendoAttack")
    assert len(results) == 1
    assert results[0].conversation_id == "conv_2"


def test_get_attack_results_converter_classes_none_returns_all(sqlite_instance: MemoryInterface):
    """Test that converter_classes=None (omitted) returns all attacks unfiltered."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack")  # No converters (None)
    ar3 = create_attack_result("conv_3", 3)  # No identifier at all
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(converter_classes=None)
    assert len(results) == 3


def test_get_attack_results_converter_classes_empty_matches_no_converters(sqlite_instance: MemoryInterface):
    """Test that converter_classes=[] returns only attacks with no converters."""
    ar_with_conv = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar_no_conv_none = _make_attack_result_with_identifier("conv_2", "Attack")  # converter_ids=None
    ar_no_conv_empty = _make_attack_result_with_identifier("conv_3", "Attack", [])  # converter_ids=[]
    ar_no_identifier = create_attack_result("conv_4", 4)  # No identifier → stored as {}
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[ar_with_conv, ar_no_conv_none, ar_no_conv_empty, ar_no_identifier]
    )

    results = sqlite_instance.get_attack_results(converter_classes=[])
    conv_ids = {r.conversation_id for r in results}
    # Should include attacks with no converters (None key, empty array, or empty identifier)
    assert "conv_1" not in conv_ids, "Should not include attacks that have converters"
    assert "conv_2" in conv_ids, "Should include attacks where converter key is absent (None)"
    assert "conv_3" in conv_ids, "Should include attacks with empty converter list"
    assert "conv_4" in conv_ids, "Should include attacks with empty attack_identifier"


def test_get_attack_results_converter_classes_single_match(sqlite_instance: MemoryInterface):
    """Test that converter_types with one type returns attacks using that converter."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["ROT13Converter"])
    ar3 = _make_attack_result_with_identifier("conv_3", "Attack", ["Base64Converter", "ROT13Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(converter_classes=["Base64Converter"])
    conv_ids = {r.conversation_id for r in results}
    assert conv_ids == {"conv_1", "conv_3"}


def test_get_attack_results_converter_classes_and_logic(sqlite_instance: MemoryInterface):
    """Test that multiple converter_types use AND logic — all must be present."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["ROT13Converter"])
    ar3 = _make_attack_result_with_identifier("conv_3", "Attack", ["Base64Converter", "ROT13Converter"])
    ar4 = _make_attack_result_with_identifier("conv_4", "Attack", ["Base64Converter", "ROT13Converter", "UrlConverter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3, ar4])

    results = sqlite_instance.get_attack_results(converter_classes=["Base64Converter", "ROT13Converter"])
    conv_ids = {r.conversation_id for r in results}
    # conv_3 and conv_4 have both; conv_1 and conv_2 have only one
    assert conv_ids == {"conv_3", "conv_4"}


def test_get_attack_results_converter_classes_case_insensitive(sqlite_instance: MemoryInterface):
    """Test that converter class matching is case-insensitive."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(converter_classes=["base64converter"])
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


def test_get_attack_results_converter_classes_no_match(sqlite_instance: MemoryInterface):
    """Test that converter_types filter returns empty when no attack has the converter."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(converter_classes=["NonExistentConverter"])
    assert len(results) == 0


def test_get_attack_results_attack_class_and_converter_classes_combined(sqlite_instance: MemoryInterface):
    """Test combining attack_type and converter_types filters."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack", ["Base64Converter"])
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack", ["ROT13Converter"])
    ar4 = _make_attack_result_with_identifier("conv_4", "CrescendoAttack")  # No converters
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3, ar4])

    results = sqlite_instance.get_attack_results(attack_class="CrescendoAttack", converter_classes=["Base64Converter"])
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


def test_get_attack_results_attack_class_with_no_converters(sqlite_instance: MemoryInterface):
    """Test combining attack_type with converter_types=[] (no converters)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")  # No converters
    ar3 = _make_attack_result_with_identifier("conv_3", "ManualAttack")  # No converters
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_class="CrescendoAttack", converter_classes=[])
    assert len(results) == 1
    assert results[0].conversation_id == "conv_2"


# ============================================================================
# Unique attack class and converter class name tests
# ============================================================================


def test_get_unique_attack_class_names_empty(sqlite_instance: MemoryInterface):
    """Test that no attacks returns empty list."""
    result = sqlite_instance.get_unique_attack_class_names()
    assert result == []


def test_get_unique_attack_class_names_sorted_unique(sqlite_instance: MemoryInterface):
    """Test that unique class names are returned sorted, with duplicates removed."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    result = sqlite_instance.get_unique_attack_class_names()
    assert result == ["CrescendoAttack", "ManualAttack"]


def test_get_unique_attack_class_names_skips_empty_identifier(sqlite_instance: MemoryInterface):
    """Test that attacks with empty attack_identifier (no class_name) are excluded."""
    ar_no_id = create_attack_result("conv_1", 1)  # No attack_identifier → stored as {}
    ar_with_id = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar_no_id, ar_with_id])

    result = sqlite_instance.get_unique_attack_class_names()
    assert result == ["CrescendoAttack"]


def test_get_unique_converter_class_names_empty(sqlite_instance: MemoryInterface):
    """Test that no attacks returns empty list."""
    result = sqlite_instance.get_unique_converter_class_names()
    assert result == []


def test_get_unique_converter_class_names_sorted_unique(sqlite_instance: MemoryInterface):
    """Test that unique converter class names are returned sorted, with duplicates removed."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter", "ROT13Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["Base64Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_converter_class_names()
    assert result == ["Base64Converter", "ROT13Converter"]


def test_get_unique_converter_class_names_skips_no_converters(sqlite_instance: MemoryInterface):
    """Test that attacks with no converters don't contribute names."""
    ar_no_conv = _make_attack_result_with_identifier("conv_1", "Attack")  # No converters
    ar_with_conv = _make_attack_result_with_identifier("conv_2", "Attack", ["Base64Converter"])
    ar_empty_id = create_attack_result("conv_3", 3)  # Empty attack_identifier
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar_no_conv, ar_with_conv, ar_empty_id])

    result = sqlite_instance.get_unique_converter_class_names()
    assert result == ["Base64Converter"]
