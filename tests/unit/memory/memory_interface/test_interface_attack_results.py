# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid
from typing import Sequence

from pyrit.common.utils import to_sha256
from pyrit.identifiers import ScorerIdentifier
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
        attack_identifier={"name": "test_attack"},
        outcome=outcome,
    )


def test_add_attack_results_to_memory(sqlite_instance: MemoryInterface):
    """Test adding attack results to memory."""
    # Create sample attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        attack_identifier={"name": "test_attack_1", "module": "test_module"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Attack was successful",
        metadata={"test_key": "test_value"},
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        attack_identifier={"name": "test_attack_2", "module": "test_module"},
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
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        attack_identifier={"name": "test_attack_2"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Test objective 3",
        attack_identifier={"name": "test_attack_3"},
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
    """Test retrieving attack results by conversation ID."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective 1",
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_1",  # Same conversation ID
        objective="Test objective 2",
        attack_identifier={"name": "test_attack_2"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_2",  # Different conversation ID
        objective="Test objective 3",
        attack_identifier={"name": "test_attack_3"},
        executed_turns=7,
        execution_time_ms=1500,
        outcome=AttackOutcome.UNDETERMINED,
    )

    # Add all attack results to memory
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Retrieve attack results by conversation ID
    retrieved_results = sqlite_instance.get_attack_results(conversation_id="conv_1")

    # Verify correct results were retrieved
    assert len(retrieved_results) == 2
    for result in retrieved_results:
        assert result.conversation_id == "conv_1"


def test_get_attack_results_by_objective(sqlite_instance: MemoryInterface):
    """Test retrieving attack results by objective substring."""
    # Create and add attack results
    attack_result1 = AttackResult(
        conversation_id="conv_1",
        objective="Test objective for success",
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Another objective for failure",
        attack_identifier={"name": "test_attack_2"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Different objective entirely",
        attack_identifier={"name": "test_attack_3"},
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
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        attack_identifier={"name": "test_attack_2"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.SUCCESS,  # Same outcome
    )

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective="Test objective 3",
        attack_identifier={"name": "test_attack_3"},
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
        attack_identifier={"name": "test_attack"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )
    objective1_sha256 = to_sha256(attack_result1.objective)

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective=objective2,
        attack_identifier={"name": "test_attack"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,
    )
    objective2_sha256 = to_sha256(attack_result2.objective)

    attack_result3 = AttackResult(
        conversation_id="conv_3",
        objective=objective3,
        attack_identifier={"name": "test_attack"},
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
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_1",  # Same conversation ID
        objective="Another objective for failure",
        attack_identifier={"name": "test_attack_2"},
        executed_turns=3,
        execution_time_ms=500,
        outcome=AttackOutcome.FAILURE,  # Different outcome
    )

    attack_result3 = AttackResult(
        conversation_id="conv_2",  # Different conversation ID
        objective="Test objective for success",
        attack_identifier={"name": "test_attack_3"},
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
        attack_identifier={"name": "test_attack_1"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
    )

    attack_result2 = AttackResult(
        conversation_id="conv_2",
        objective="Test objective 2",
        attack_identifier={"name": "test_attack_2"},
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
        attack_identifier={"name": "test_attack"},
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
        attack_identifier={"name": "test_attack"},
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
        scorer_class_identifier=ScorerIdentifier(
            class_name="TestScorer",
            class_module="test_module",
            class_description="",
            identifier_type="instance",
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
        attack_identifier={"name": "test_attack"},
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
            attack_identifier={"name": f"test_attack_{i}"},
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
        attack_identifier={"name": "test_attack"},
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
        attack_identifier={"name": "test_attack"},
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
        attack_identifier={"name": "test_attack"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        related_conversations=related_conversations,
    )

    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    entry: AttackResultEntry = sqlite_instance._query_entries(AttackResultEntry)[0]

    assert set(entry.pruned_conversation_ids) == pruned_ids  # type: ignore
    assert set(entry.adversarial_chat_conversation_ids) == adversarial_ids  # type: ignore

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
        attack_identifier={"name": "test_attack"},
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

    results = sqlite_instance.get_attack_results(labels={"op_name": "test"})
    assert len(results) == 0

    results = sqlite_instance.get_attack_results(labels={"researcher": "roakey"})
    assert len(results) == 0

    results = sqlite_instance.get_attack_results(labels={"non_existing_key": "no_value"})
    assert len(results) == 0


def test_get_attack_results_labels_key_exists_value_mismatch(sqlite_instance: MemoryInterface):
    """Test querying for labels where the key exists but the value doesn't match."""

    # Create attack results with specific label values
    message_piece1 = create_message_piece("conv_1", 1, labels={"op_name": "op_exists", "researcher": "roakey"})
    message_piece2 = create_message_piece("conv_2", 1, labels={"op_name": "another_op", "researcher": "roakey"})
    message_piece3 = create_message_piece("conv_3", 1, labels={"operation": "test_op"})

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece1, message_piece2, message_piece3])

    attack_results = [
        create_attack_result("conv_1", 1, AttackOutcome.SUCCESS),
        create_attack_result("conv_2", 2, AttackOutcome.SUCCESS),
        create_attack_result("conv_3", 3, AttackOutcome.FAILURE),
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Query for key that exists but with wrong value
    results = sqlite_instance.get_attack_results(labels={"op_name": "op_doesnotexist"})
    assert len(results) == 0

    # Query for existing key with correct value
    results = sqlite_instance.get_attack_results(labels={"op_name": "op_exists"})
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
    results = sqlite_instance.get_attack_results(labels={"op_name": "op_exists", "researcher": "not_roakey"})
    assert len(results) == 0

    # Test multiple keys where both match
    results = sqlite_instance.get_attack_results(labels={"op_name": "op_exists", "researcher": "roakey"})
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"
