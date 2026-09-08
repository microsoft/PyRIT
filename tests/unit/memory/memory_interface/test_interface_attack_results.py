# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from unit.mocks import get_mock_target_identifier, make_scenario_result

from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor, MemoryInterface
from pyrit.memory.memory_interface import _AttackResultQuery
from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    IdentifierFilter,
    IdentifierType,
    MessagePiece,
    ScenarioRunState,
    Score,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def create_attack_result(
    conversation_id: str,
    objective_num: int,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    labels: dict[str, str] | None = None,
    targeted_harm_categories: list[str] | None = None,
):
    """Helper function to create AttackResult."""
    return AttackResult(
        conversation_id=conversation_id,
        objective=f"Objective {objective_num}",
        outcome=outcome,
        labels=labels or {},
        targeted_harm_categories=targeted_harm_categories or [],
    )


_BASE_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _make_attack_result(
    conversation_id: str,
    *,
    executed_turns: int = 1,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    ts_offset: int = 0,
    updated_at_offset: int | None = None,
    created_at_offset: int | None = None,
    attack_result_id: str | None = None,
) -> AttackResult:
    """Build an AttackResult with an explicit timestamp and recency metadata.

    Used by the pagination tests, which need deterministic control over the row
    timestamp and the ``attack_metadata`` ``updated_at``/``created_at`` recency keys.
    """
    metadata: dict[str, str] = {}
    if updated_at_offset is not None:
        metadata["updated_at"] = (_BASE_TS + timedelta(seconds=updated_at_offset)).isoformat()
    if created_at_offset is not None:
        metadata["created_at"] = (_BASE_TS + timedelta(seconds=created_at_offset)).isoformat()
    kwargs: dict = {
        "conversation_id": conversation_id,
        "objective": f"Objective {conversation_id}",
        "executed_turns": executed_turns,
        "execution_time_ms": 0,
        "outcome": outcome,
        "metadata": metadata,
        "timestamp": _BASE_TS + timedelta(seconds=ts_offset),
    }
    if attack_result_id is not None:
        kwargs["attack_result_id"] = attack_result_id
    return AttackResult(**kwargs)


def _after(page: "Sequence[AttackResult]") -> AttackResultKeysetCursor:
    """Build the keyset anchor for the next page from the last row of ``page``."""
    return AttackResultKeysetCursor.from_attack_result(page[-1])


def _drain_keyset(memory: MemoryInterface, *, page_size: int, **filters) -> list[AttackResult]:
    """Page through get_attack_results with the keyset cursor until exhausted."""
    drained: list[AttackResult] = []
    after: AttackResultKeysetCursor | None = None
    while True:
        page = list(memory.get_attack_results(limit=page_size, after=after, **filters))
        drained.extend(page)
        if len(page) < page_size:
            break
        after = _after(page)
    return drained


def test_attack_result_query_snapshots_mutable_inputs():
    """The internal query remains stable when caller-owned containers change."""
    attack_classes = ["CrescendoAttack"]
    labels = {"operator": ["alice"]}
    query = _AttackResultQuery(attack_classes=attack_classes, labels=labels)

    attack_classes.append("ManualAttack")
    labels["operator"].append("bob")

    assert query.attack_classes == ("CrescendoAttack",)
    assert query.labels == {"operator": ("alice",)}
    field_name = "limit"
    with pytest.raises(FrozenInstanceError):
        setattr(query, field_name, 10)


def test_attack_result_query_requires_keyword_arguments():
    """The internal query does not expose field ordering as a positional API."""
    with pytest.raises(TypeError):
        _AttackResultQuery(["id"])  # ty: ignore[too-many-positional-arguments]


def test_get_attack_results_forwards_all_parameters_to_query(sqlite_instance: MemoryInterface):
    """The compatibility API maps every parameter onto the internal query."""
    cursor = AttackResultKeysetCursor(timestamp=_BASE_TS, attack_result_id=str(uuid.uuid4()))
    identifier_filter = IdentifierFilter(
        identifier_type=IdentifierType.ATTACK,
        property_path="$.hash",
        value="hash",
    )

    with patch.object(sqlite_instance, "_query_attack_results", return_value=[]) as query_mock:
        sqlite_instance.get_attack_results(
            attack_result_ids=["id"],
            conversation_id="conversation",
            objective="objective",
            objective_sha256=["sha"],
            outcome="success",
            attack_classes=["Attack"],
            atomic_attack_eval_hashes=["eval"],
            converter_classes=["Converter"],
            converter_classes_match="any",
            has_converters=True,
            include_scenario_attacks=False,
            labels={"operator": ["alice"]},
            targeted_harm_categories=["violence"],
            identifier_filters=[identifier_filter],
            scenario_result_id=str(uuid.uuid4()),
            min_turns=1,
            max_turns=5,
            limit=10,
            after=cursor,
        )

    query = query_mock.call_args.kwargs["query"]
    assert isinstance(query, _AttackResultQuery)
    assert query.attack_result_ids == ("id",)
    assert query.conversation_id == "conversation"
    assert query.objective == "objective"
    assert query.objective_sha256 == ("sha",)
    assert query.outcome == "success"
    assert query.attack_classes == ("Attack",)
    assert query.atomic_attack_eval_hashes == ("eval",)
    assert query.converter_classes == ("Converter",)
    assert query.converter_classes_match == "any"
    assert query.has_converters is True
    assert query.include_scenario_attacks is False
    assert query.labels == {"operator": ("alice",)}
    assert query.targeted_harm_categories == ("violence",)
    assert query.identifier_filters == (identifier_filter,)
    assert query.scenario_result_id is not None
    assert query.min_turns == 1
    assert query.max_turns == 5
    assert query.limit == 10
    assert query.after == cursor


def test_query_attack_results_matches_compatibility_api(sqlite_instance: MemoryInterface):
    """The internal query executor and compatibility API return the same page."""
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result("conv-1", executed_turns=2, outcome=AttackOutcome.SUCCESS, ts_offset=1),
            _make_attack_result("conv-2", executed_turns=5, outcome=AttackOutcome.SUCCESS, ts_offset=2),
            _make_attack_result("conv-3", executed_turns=7, outcome=AttackOutcome.FAILURE, ts_offset=3),
        ]
    )

    query = _AttackResultQuery(outcome=AttackOutcome.SUCCESS.value, min_turns=3, limit=1)
    direct = sqlite_instance._query_attack_results(query=query)
    compatibility = sqlite_instance.get_attack_results(
        outcome=AttackOutcome.SUCCESS.value,
        min_turns=3,
        limit=1,
    )

    assert [result.attack_result_id for result in direct] == [result.attack_result_id for result in compatibility]


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
            atomic_attack_identifier=AtomicAttackIdentifier.build(
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
        *(ConversationReference(conversation_id=cid, conversation_type=ConversationType.PRUNED) for cid in pruned_ids),
        *(
            ConversationReference(conversation_id=cid, conversation_type=ConversationType.ADVERSARIAL)
            for cid in adversarial_ids
        ),
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


def test_get_attack_results_by_labels_single(sqlite_instance: MemoryInterface):
    """Test filtering attack results by single label."""

    # Create attack results with labels
    attack_result1 = create_attack_result(
        "conv_1", 1, AttackOutcome.SUCCESS, labels={"operation": "test_op", "operator": "roakey"}
    )
    attack_result2 = create_attack_result("conv_2", 2, AttackOutcome.FAILURE, labels={"operation": "test_op"})
    attack_result3 = create_attack_result(
        "conv_3", 3, AttackOutcome.SUCCESS, labels={"operation": "other_op", "operator": "roakey"}
    )

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


def test_get_attack_results_by_labels_empty_sequence_value_skips_key(sqlite_instance: MemoryInterface):
    """An empty sequence for a label key is skipped (no filter applied for that key).

    Includes an attack with empty labels and another with no ``operator`` key to guard
    against adding an unintended label constraint when all values for a key are empty.
    """
    ar1 = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS, labels={"operator": "roakey"})
    ar2 = create_attack_result("conv_2", 2, AttackOutcome.SUCCESS, labels={"operator": "alice"})
    ar3 = create_attack_result("conv_3", 3, AttackOutcome.SUCCESS, labels={"phase": "initial"})
    ar4 = create_attack_result("conv_4", 4, AttackOutcome.SUCCESS, labels={})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3, ar4])

    # Empty sequence for "operator" → filter is ignored entirely, all attacks match
    # (including conv_3 which has no "operator" key and conv_4 which has no labels).
    results = sqlite_instance.get_attack_results(labels={"operator": []})
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2", "conv_3", "conv_4"}

    # Mixed: one key with empty-sequence (ignored) + one real filter should behave
    # exactly like the real filter alone.
    mixed = sqlite_instance.get_attack_results(labels={"operator": [], "phase": "initial"})
    assert {r.conversation_id for r in mixed} == {"conv_3"}


@pytest.mark.parametrize(
    "bad_key",
    [
        "foo')=1 OR 1=1 --",  # SQL break-out attempt
        "key with spaces",
        'key"quoted',
        "",
    ],
)
def test_get_attack_results_rejects_invalid_label_keys(sqlite_instance: MemoryInterface, bad_key: str):
    """Label keys are interpolated into JSON path expressions by the per-backend
    helpers, so keys outside the ``[A-Za-z0-9_.-]+`` allowlist must be rejected
    before reaching the SQL layer (defense against JSON-path / SQL injection).
    """
    with pytest.raises(ValueError, match="Invalid label key"):
        sqlite_instance.get_attack_results(labels={bad_key: "value"})


def test_get_attack_results_by_labels_multiple(sqlite_instance: MemoryInterface):
    """Test filtering attack results by multiple labels (AND logic)."""

    # Create attack results with multiple labels
    attack_results = [
        create_attack_result(
            "conv_1",
            1,
            AttackOutcome.SUCCESS,
            labels={"operation": "test_op", "operator": "roakey", "phase": "initial"},
        ),
        create_attack_result(
            "conv_2",
            2,
            AttackOutcome.SUCCESS,
            labels={"operation": "test_op", "operator": "roakey", "phase": "final"},
        ),
        create_attack_result(
            "conv_3",
            3,
            AttackOutcome.FAILURE,
            labels={"operation": "test_op", "phase": "initial"},
        ),
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


def test_get_attack_results_by_labels_or_within_key(sqlite_instance: MemoryInterface):
    """Test that a sequence value for a label key matches any of the values (OR-within-key)."""

    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            create_attack_result("conv_1", 1, labels={"operator": "alice"}),
            create_attack_result("conv_2", 2, labels={"operator": "bob"}),
            create_attack_result("conv_3", 3, labels={"operator": "charlie"}),
        ]
    )

    results = sqlite_instance.get_attack_results(labels={"operator": ["alice", "bob"]})
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2"}


def test_get_attack_results_by_labels_or_within_key_and_across_keys(sqlite_instance: MemoryInterface):
    """Test that OR-within-key composes with AND-across-keys."""

    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            create_attack_result("conv_1", 1, labels={"operator": "alice", "operation": "red"}),
            create_attack_result("conv_2", 2, labels={"operator": "bob", "operation": "red"}),
            create_attack_result("conv_3", 3, labels={"operator": "charlie", "operation": "red"}),
            create_attack_result("conv_4", 4, labels={"operator": "alice", "operation": "blue"}),
        ]
    )

    results = sqlite_instance.get_attack_results(labels={"operator": ["alice", "bob"], "operation": ["red"]})
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2"}


def test_get_attack_results_labels_no_matches(sqlite_instance: MemoryInterface):
    """Test filtering by labels that don't exist."""

    # Create attack result with labels that don't match the search
    attack_result = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS, labels={"operation": "test_op"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    # Search for non-existent labels
    results = sqlite_instance.get_attack_results(labels={"nonexistent": "value"})
    assert len(results) == 0


def test_get_attack_results_labels_query_on_empty_labels(sqlite_instance: MemoryInterface):
    """Test querying for labels when records have no labels at all"""

    # Create attack results with NO labels
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
    attack_results = [
        create_attack_result(
            "conv_1", 1, AttackOutcome.SUCCESS, labels={"operation": "op_exists", "researcher": "roakey"}
        ),
        create_attack_result(
            "conv_2", 2, AttackOutcome.SUCCESS, labels={"operation": "another_op", "researcher": "roakey"}
        ),
        create_attack_result("conv_3", 3, AttackOutcome.FAILURE, labels={"operation": "test_op"}),
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
# targeted_harm_categories tests
# ---------------------------------------------------------------------------


def test_attack_result_targeted_harm_categories_round_trip(sqlite_instance: MemoryInterface):
    """targeted_harm_categories persists onto AttackResultEntry and round-trips back."""
    attack_result = create_attack_result(
        "conv_1", 1, AttackOutcome.SUCCESS, targeted_harm_categories=["violence", "hate"]
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    stored = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(stored) == 1
    assert sorted(stored[0].targeted_harm_categories) == ["hate", "violence"]


def test_attack_result_targeted_harm_categories_defaults_empty(sqlite_instance: MemoryInterface):
    """An AttackResult with no harm categories round-trips to an empty list."""
    attack_result = create_attack_result("conv_1", 1, AttackOutcome.SUCCESS)
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result])

    stored = sqlite_instance.get_attack_results(conversation_id="conv_1")
    assert len(stored) == 1
    assert stored[0].targeted_harm_categories == []


def test_get_attack_results_by_targeted_harm_categories(sqlite_instance: MemoryInterface):
    """Filtering by targeted_harm_categories matches attacks targeting ANY listed category."""
    attack_results = [
        create_attack_result("conv_1", 1, AttackOutcome.SUCCESS, targeted_harm_categories=["violence"]),
        create_attack_result("conv_2", 2, AttackOutcome.FAILURE, targeted_harm_categories=["hate", "violence"]),
        create_attack_result("conv_3", 3, AttackOutcome.SUCCESS, targeted_harm_categories=["self_harm"]),
        create_attack_result("conv_4", 4, AttackOutcome.SUCCESS),
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    violence = sqlite_instance.get_attack_results(targeted_harm_categories=["violence"])
    assert {r.conversation_id for r in violence} == {"conv_1", "conv_2"}

    # OR across multiple requested categories.
    multi = sqlite_instance.get_attack_results(targeted_harm_categories=["self_harm", "hate"])
    assert {r.conversation_id for r in multi} == {"conv_2", "conv_3"}

    # Case-insensitive match.
    case = sqlite_instance.get_attack_results(targeted_harm_categories=["VIOLENCE"])
    assert {r.conversation_id for r in case} == {"conv_1", "conv_2"}

    # No match.
    assert sqlite_instance.get_attack_results(targeted_harm_categories=["nonexistent"]) == []

    # Empty sequence applies no filter.
    none_filter = sqlite_instance.get_attack_results(targeted_harm_categories=[])
    assert {r.conversation_id for r in none_filter} == {"conv_1", "conv_2", "conv_3", "conv_4"}


# ---------------------------------------------------------------------------
# get_unique_attack_labels tests
# ---------------------------------------------------------------------------


def test_get_unique_attack_labels_empty(sqlite_instance: MemoryInterface):
    """Returns empty dict when there are no attack results."""
    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_single(sqlite_instance: MemoryInterface):
    """Returns labels from a single attack result."""
    ar = create_attack_result("conv_1", 1, labels={"env": "prod", "team": "red"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod"], "team": ["red"]}


def test_get_unique_attack_labels_multiple_attacks_merges_values(sqlite_instance: MemoryInterface):
    """Values from different attacks are merged and sorted."""
    ar1 = create_attack_result("conv_1", 1, labels={"env": "prod", "team": "red"})
    ar2 = create_attack_result("conv_2", 2, labels={"env": "staging", "team": "red"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod", "staging"], "team": ["red"]}


def test_get_unique_attack_labels_no_labels(sqlite_instance: MemoryInterface):
    """Attack results without labels return an empty dict."""
    ar = create_attack_result("conv_1", 1)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {}


def test_get_unique_attack_labels_non_string_values_skipped(sqlite_instance: MemoryInterface):
    """Non-string label values are ignored."""
    from contextlib import closing

    from sqlalchemy import text

    ar = create_attack_result("conv_1", 1, labels={"env": "prod"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])
    with closing(sqlite_instance.get_session()) as session:
        session.execute(
            text('UPDATE "AttackResultEntries" SET labels = :labels'),
            {"labels": '{"env":"prod","count":42}'},
        )
        session.commit()

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod"]}


def test_get_unique_attack_labels_keys_sorted(sqlite_instance: MemoryInterface):
    """Returned keys and values are sorted alphabetically."""
    ar1 = create_attack_result("conv_1", 1, labels={"zoo": "z_val", "alpha": "a"})
    ar2 = create_attack_result("conv_2", 2, labels={"alpha": "b"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_attack_labels()
    assert list(result.keys()) == ["alpha", "zoo"]
    assert result["alpha"] == ["a", "b"]
    assert result["zoo"] == ["z_val"]


def test_get_unique_attack_labels_non_dict_labels_skipped(sqlite_instance: MemoryInterface):
    """Labels stored as a non-dict JSON value (e.g. a string) are skipped."""
    from contextlib import closing

    from sqlalchemy import text

    ar1 = create_attack_result("conv_1", 1, labels={"env": "prod"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    ar2 = create_attack_result("conv_2", 2, labels={"placeholder": "x"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar2])
    with closing(sqlite_instance.get_session()) as session:
        session.execute(
            text('UPDATE "AttackResultEntries" SET labels = :labels WHERE conversation_id = :cid'),
            {"labels": '"just_a_string"', "cid": "conv_2"},
        )
        session.commit()

    result = sqlite_instance.get_unique_attack_labels()
    # Only the dict labels from conv_1 should appear
    assert result == {"env": ["prod"]}


def test_get_unique_attack_labels_from_attack_result_entry(sqlite_instance: MemoryInterface):
    """Labels stored directly on AttackResultEntry are included."""
    ar = create_attack_result("conv_1", 1, labels={"source": "are_only"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"source": ["are_only"]}


def test_get_unique_attack_labels_deduplicates_across_attacks(sqlite_instance: MemoryInterface):
    """Identical key-value pairs from different attacks are not duplicated."""
    ar1 = create_attack_result("conv_1", 1, labels={"env": "prod"})
    ar2 = create_attack_result("conv_2", 2, labels={"env": "prod"})
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    result = sqlite_instance.get_unique_attack_labels()
    assert result == {"env": ["prod"]}


# ============================================================================
# Attack class and converter class filtering tests
# ============================================================================


def _make_attack_result_with_identifier(
    conversation_id: str,
    class_name: str,
    converter_class_names: list[str] | None = None,
) -> AttackResult:
    """Helper to create an AttackResult with a ComponentIdentifier containing converters."""
    children: dict = {}
    if converter_class_names is not None:
        children["request_converters"] = [
            ComponentIdentifier(
                class_name=name,
                class_module="pyrit.converter",
            )
            for name in converter_class_names
        ]

    return AttackResult(
        conversation_id=conversation_id,
        objective=f"Objective for {conversation_id}",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=class_name,
                class_module="pyrit.attacks",
                children=children,
            ),
        ),
    )


def test_get_attack_results_by_attack_classes(sqlite_instance: MemoryInterface):
    """Test filtering attack results by attack_classes matches class_name in JSON."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_classes=["CrescendoAttack"])
    assert len(results) == 2
    assert {r.conversation_id for r in results} == {"conv_1", "conv_3"}


def test_get_attack_results_by_attack_classes_no_match(sqlite_instance: MemoryInterface):
    """Test that attack_classes filter returns empty when nothing matches."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(attack_classes=["NonExistentAttack"])
    assert len(results) == 0


def test_get_attack_results_by_attack_classes_case_insensitive(sqlite_instance: MemoryInterface):
    """attack_classes is case-insensitive (mirrors converter_classes; forgives REST/CLI casing)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    # Lowercase, uppercase, and mixed-case all match.
    assert len(sqlite_instance.get_attack_results(attack_classes=["crescendoattack"])) == 1
    assert len(sqlite_instance.get_attack_results(attack_classes=["CRESCENDOATTACK"])) == 1
    assert len(sqlite_instance.get_attack_results(attack_classes=["CresCendoATtack"])) == 1


def test_get_attack_results_by_attack_classes_no_identifier(sqlite_instance: MemoryInterface):
    """Test that attacks with no attack_identifier (empty JSON) are excluded by attack_classes filter."""
    ar1 = create_attack_result("conv_1", 1)  # No attack_identifier → stored as {}
    ar2 = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(attack_classes=["CrescendoAttack"])
    assert len(results) == 1
    assert results[0].conversation_id == "conv_2"


def test_get_attack_results_by_attack_classes_multi(sqlite_instance: MemoryInterface):
    """Test that multiple attack_classes use OR logic — matches any of the listed class names."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "TreeOfAttacksAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_classes=["CrescendoAttack", "ManualAttack"])
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2"}


def test_get_attack_results_attack_classes_empty_returns_all(sqlite_instance: MemoryInterface):
    """Test that attack_classes=[] behaves like None (no filter applied)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(attack_classes=[])
    assert len(results) == 2


def _eval_hash_for(class_name: str) -> str:
    from pyrit.models.identifiers.evaluation_identifier import AtomicAttackEvaluationIdentifier

    return AtomicAttackEvaluationIdentifier(
        AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=class_name,
                class_module="pyrit.attacks",
            ),
        ),
    ).eval_hash


def test_get_attack_results_by_atomic_attack_eval_hashes_single(sqlite_instance: MemoryInterface):
    """Filter by a single eval_hash; only matching rows are returned."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    target_hash = _eval_hash_for("CrescendoAttack")
    results = sqlite_instance.get_attack_results(atomic_attack_eval_hashes=[target_hash])
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


def test_get_attack_results_by_atomic_attack_eval_hashes_multi_uses_or(sqlite_instance: MemoryInterface):
    """Multiple eval_hashes OR-combine — matches any of the listed hashes."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "TreeOfAttacksAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    hashes = [_eval_hash_for("CrescendoAttack"), _eval_hash_for("ManualAttack")]
    results = sqlite_instance.get_attack_results(atomic_attack_eval_hashes=hashes)
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2"}


def test_get_attack_results_atomic_attack_eval_hashes_empty_returns_all(sqlite_instance: MemoryInterface):
    """atomic_attack_eval_hashes=[] behaves like None (no filter applied)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(atomic_attack_eval_hashes=[])
    assert len(results) == 2


def test_get_attack_results_atomic_attack_eval_hashes_no_match(sqlite_instance: MemoryInterface):
    """A non-matching eval_hash returns no rows."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(atomic_attack_eval_hashes=["deadbeef" * 8])
    assert len(results) == 0


def test_get_attack_results_converter_classes_none_returns_all(sqlite_instance: MemoryInterface):
    """Test that converter_classes=None (omitted) returns all attacks unfiltered."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack")  # No converters (None)
    ar3 = create_attack_result("conv_3", 3)  # No identifier at all
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(converter_classes=None)
    assert len(results) == 3


def test_get_attack_results_converter_classes_empty_matches_no_converters(sqlite_instance: MemoryInterface):
    """Test that converter_classes=[] returns only attacks with no converters (back-compat)."""
    ar_with_conv = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar_no_conv_none = _make_attack_result_with_identifier("conv_2", "Attack")  # converter_ids=None
    ar_no_conv_empty = _make_attack_result_with_identifier("conv_3", "Attack", [])  # converter_ids=[]
    ar_no_identifier = create_attack_result("conv_4", 4)  # No identifier → stored as {}
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[ar_with_conv, ar_no_conv_none, ar_no_conv_empty, ar_no_identifier]
    )

    results = sqlite_instance.get_attack_results(converter_classes=[])
    conv_ids = {r.conversation_id for r in results}
    assert conv_ids == {"conv_2", "conv_3", "conv_4"}


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


def test_get_attack_results_converter_classes_any_logic(sqlite_instance: MemoryInterface):
    """converter_classes_match='any' returns rows that match at least one listed converter."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["ROT13Converter"])
    ar3 = _make_attack_result_with_identifier("conv_3", "Attack", ["Base64Converter", "ROT13Converter"])
    ar4 = _make_attack_result_with_identifier("conv_4", "Attack", ["UrlConverter"])
    ar5 = _make_attack_result_with_identifier("conv_5", "Attack")  # No converters
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3, ar4, ar5])

    results = sqlite_instance.get_attack_results(
        converter_classes=["Base64Converter", "ROT13Converter"],
        converter_classes_match="any",
    )
    conv_ids = {r.conversation_id for r in results}
    # conv_1, conv_2, conv_3 all have at least one of the listed converters; conv_4 and conv_5 don't
    assert conv_ids == {"conv_1", "conv_2", "conv_3"}


def test_get_attack_results_converter_classes_any_logic_case_insensitive(sqlite_instance: MemoryInterface):
    """converter_classes_match='any' preserves case-insensitive matching (parity with 'all' mode)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["ROT13Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(
        converter_classes=["base64converter", "ROT13CONVERTER"],
        converter_classes_match="any",
    )
    assert {r.conversation_id for r in results} == {"conv_1", "conv_2"}


def test_get_attack_results_converter_classes_any_logic_single_entry_degenerate(sqlite_instance: MemoryInterface):
    """converter_classes_match='any' with a single entry is equivalent to 'all' with that entry."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack", ["ROT13Converter"])
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results_any = sqlite_instance.get_attack_results(
        converter_classes=["Base64Converter"], converter_classes_match="any"
    )
    results_all = sqlite_instance.get_attack_results(
        converter_classes=["Base64Converter"], converter_classes_match="all"
    )
    assert {r.conversation_id for r in results_any} == {"conv_1"}
    assert {r.conversation_id for r in results_any} == {r.conversation_id for r in results_all}


def test_get_attack_results_converter_classes_any_logic_empty_preserves_absence_overload(
    sqlite_instance: MemoryInterface,
):
    """converter_classes=[] with match_mode='any' still means 'no converters' (overload is mode-agnostic)."""
    ar1 = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "Attack")  # No converters
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    results = sqlite_instance.get_attack_results(converter_classes=[], converter_classes_match="any")
    assert {r.conversation_id for r in results} == {"conv_2"}


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


def test_get_attack_results_attack_classes_and_converter_classes_combined(sqlite_instance: MemoryInterface):
    """Test combining attack_classes and converter_classes filters."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack", ["Base64Converter"])
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack", ["ROT13Converter"])
    ar4 = _make_attack_result_with_identifier("conv_4", "CrescendoAttack")  # No converters
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3, ar4])

    results = sqlite_instance.get_attack_results(
        attack_classes=["CrescendoAttack"], converter_classes=["Base64Converter"]
    )
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


def test_get_attack_results_attack_classes_converter_classes_empty_matches_no_converters(
    sqlite_instance: MemoryInterface,
):
    """Combining attack_classes with converter_classes=[] restricts to the class with no converters."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")  # No converters
    ar3 = _make_attack_result_with_identifier("conv_3", "ManualAttack")  # Different class
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_classes=["CrescendoAttack"], converter_classes=[])
    assert {r.conversation_id for r in results} == {"conv_2"}


def test_get_attack_results_has_converters_true(sqlite_instance: MemoryInterface):
    """has_converters=True returns only attacks with at least one converter."""
    ar_with_conv = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar_no_conv_none = _make_attack_result_with_identifier("conv_2", "Attack")  # converter_ids=None
    ar_no_conv_empty = _make_attack_result_with_identifier("conv_3", "Attack", [])  # converter_ids=[]
    ar_no_identifier = create_attack_result("conv_4", 4)  # No identifier → stored as {}
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[ar_with_conv, ar_no_conv_none, ar_no_conv_empty, ar_no_identifier]
    )

    results = sqlite_instance.get_attack_results(has_converters=True)
    assert {r.conversation_id for r in results} == {"conv_1"}


def test_get_attack_results_has_converters_false(sqlite_instance: MemoryInterface):
    """has_converters=False returns only attacks with zero converters."""
    ar_with_conv = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar_no_conv_none = _make_attack_result_with_identifier("conv_2", "Attack")
    ar_no_conv_empty = _make_attack_result_with_identifier("conv_3", "Attack", [])
    ar_no_identifier = create_attack_result("conv_4", 4)
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[ar_with_conv, ar_no_conv_none, ar_no_conv_empty, ar_no_identifier]
    )

    results = sqlite_instance.get_attack_results(has_converters=False)
    assert {r.conversation_id for r in results} == {"conv_2", "conv_3", "conv_4"}


def test_get_attack_results_has_converters_none_returns_all(sqlite_instance: MemoryInterface):
    """has_converters=None applies no filter."""
    ar_with_conv = _make_attack_result_with_identifier("conv_1", "Attack", ["Base64Converter"])
    ar_no_conv = _make_attack_result_with_identifier("conv_2", "Attack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar_with_conv, ar_no_conv])

    results = sqlite_instance.get_attack_results(has_converters=None)
    assert len(results) == 2


def test_get_attack_results_has_converters_false_combined_with_attack_classes(sqlite_instance: MemoryInterface):
    """has_converters=False composes (AND) with attack_classes."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack", ["Base64Converter"])
    ar2 = _make_attack_result_with_identifier("conv_2", "CrescendoAttack")  # No converters
    ar3 = _make_attack_result_with_identifier("conv_3", "ManualAttack")  # No converters, different class
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    results = sqlite_instance.get_attack_results(attack_classes=["CrescendoAttack"], has_converters=False)
    assert {r.conversation_id for r in results} == {"conv_2"}


def test_get_attack_results_can_exclude_scenario_attacks(sqlite_instance: MemoryInterface) -> None:
    """Manual-only queries exclude attacks carrying scenario attribution."""
    scenario = make_scenario_result(
        id=uuid.uuid4(),
        scenario_name="Scenario",
        scenario_run_state=ScenarioRunState.COMPLETED,
        labels={},
        metadata={},
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
    )
    manual_attack = create_attack_result("manual", 1)
    scenario_attack = create_attack_result("scenario", 2)
    scenario_attack.attribution_parent_id = str(scenario.id)
    scenario_attack.attribution_data = {"parent_collection": "attack"}
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario])
    sqlite_instance.add_attack_results_to_memory(attack_results=[manual_attack, scenario_attack])

    all_results = sqlite_instance.get_attack_results(include_scenario_attacks=True)
    manual_results = sqlite_instance.get_attack_results(include_scenario_attacks=False)

    assert {result.conversation_id for result in all_results} == {"manual", "scenario"}
    assert [result.conversation_id for result in manual_results] == ["manual"]


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


def test_get_attack_results_by_attack_identifier_filter_hash(sqlite_instance: MemoryInterface):
    """Test filtering attack results by AttackIdentifierFilter with hash."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2])

    # Filter by hash of ar1's attack identifier
    results = sqlite_instance.get_attack_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.ATTACK,
                property_path="$.hash",
                value=ar1.atomic_attack_identifier.hash,
                partial_match=False,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].conversation_id == "conv_1"


def test_get_attack_results_by_attack_identifier_filter_class_name(sqlite_instance: MemoryInterface):
    """Test filtering attack results by AttackIdentifierFilter with class_name."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    ar2 = _make_attack_result_with_identifier("conv_2", "ManualAttack")
    ar3 = _make_attack_result_with_identifier("conv_3", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    # Filter by partial attack class name
    results = sqlite_instance.get_attack_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.ATTACK,
                property_path="$.children.attack_technique.children.attack.class_name",
                value="Crescendo",
                partial_match=True,
            )
        ],
    )
    assert len(results) == 2
    assert {r.conversation_id for r in results} == {"conv_1", "conv_3"}


def test_get_attack_results_by_attack_identifier_filter_no_match(sqlite_instance: MemoryInterface):
    """Test that AttackIdentifierFilter returns empty when nothing matches."""
    ar1 = _make_attack_result_with_identifier("conv_1", "CrescendoAttack")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1])

    results = sqlite_instance.get_attack_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.ATTACK,
                property_path="$.hash",
                value="nonexistent_hash",
                partial_match=False,
            )
        ],
    )
    assert len(results) == 0


def test_get_attack_results_pagination_returns_recency_ordered_page(sqlite_instance: MemoryInterface):
    """Keyset pagination returns the recency-ordered slice (newest updated_at first)."""
    attack_results = [
        _make_attack_result(f"conv-{i}", ts_offset=i, updated_at_offset=100 + i, created_at_offset=i) for i in range(10)
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    page1 = sqlite_instance.get_attack_results(limit=3)
    assert [r.conversation_id for r in page1] == ["conv-9", "conv-8", "conv-7"]

    page2 = sqlite_instance.get_attack_results(limit=3, after=_after(page1))
    assert [r.conversation_id for r in page2] == ["conv-6", "conv-5", "conv-4"]


def test_get_attack_results_pagination_disjoint_and_complete(sqlite_instance: MemoryInterface):
    """Concatenated keyset pages equal the full recency-ordered set with no gaps or duplicates."""
    attack_results = [_make_attack_result(f"conv-{i}", ts_offset=i, updated_at_offset=100 + i) for i in range(25)]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    full = [r.conversation_id for r in sqlite_instance.get_attack_results(limit=100)]
    paged = [r.conversation_id for r in _drain_keyset(sqlite_instance, page_size=7)]
    assert paged == full
    assert len(set(paged)) == 25


def test_get_attack_results_pagination_duplicate_and_tie_heavy_pages_disjoint_and_complete(
    sqlite_instance: MemoryInterface,
):
    """Duplicate- and tie-heavy data still paginates as disjoint, complete, newest-per-conversation winners."""
    num_convs = 12
    attack_results: list[AttackResult] = []
    for i in range(num_convs):
        # Older row for the conversation — must lose dedup to the newer row below.
        attack_results.append(
            _make_attack_result(
                f"conv-{i}",
                outcome=AttackOutcome.SUCCESS,
                ts_offset=i,
                updated_at_offset=i,
                attack_result_id=f"00000000-0000-4000-8000-{i:012d}",
            )
        )
        # Newer (winning) row. updated_at ties within pairs, broken by timestamp then id.
        attack_results.append(
            _make_attack_result(
                f"conv-{i}",
                outcome=AttackOutcome.FAILURE,
                ts_offset=100 + i,
                updated_at_offset=100 + (i // 2),
                attack_result_id=f"00000000-0000-4000-8000-1{i:011d}",
            )
        )
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    full = list(sqlite_instance.get_attack_results(limit=100))
    # Each duplicate pair collapses to exactly one winner, and the winner is the newer (FAILURE) row.
    assert len(full) == num_convs
    assert all(r.outcome == AttackOutcome.FAILURE for r in full)
    assert len({r.conversation_id for r in full}) == num_convs

    paged = [r.attack_result_id for r in _drain_keyset(sqlite_instance, page_size=5)]
    assert paged == [r.attack_result_id for r in full]
    assert len(set(paged)) == num_convs


def test_get_attack_results_pagination_order_parity_with_timestamp_sort(sqlite_instance: MemoryInterface):
    """Paginated SQL ordering matches a Python sort on the timestamp recency column."""
    attack_results = [_make_attack_result(f"conv-{i}", ts_offset=(i * 7) % 50) for i in range(15)]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    new_order = [r.conversation_id for r in sqlite_instance.get_attack_results(limit=100)]
    all_unpaged = list(sqlite_instance.get_attack_results())
    expected = sorted(
        all_unpaged,
        key=lambda ar: (ar.timestamp, ar.attack_result_id),
        reverse=True,
    )
    assert new_order == [r.conversation_id for r in expected]


def test_get_attack_results_paginated_dedup_is_filter_aware(sqlite_instance: MemoryInterface):
    """When the newest row fails the filter, the older matching row still survives dedup."""
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result(
                "conv-1", outcome=AttackOutcome.SUCCESS, ts_offset=1, updated_at_offset=1, attack_result_id=older_id
            ),
            _make_attack_result(
                "conv-1", outcome=AttackOutcome.FAILURE, ts_offset=2, updated_at_offset=2, attack_result_id=newer_id
            ),
        ]
    )
    page = sqlite_instance.get_attack_results(outcome=AttackOutcome.SUCCESS.value, limit=50)
    assert len(page) == 1
    assert page[0].attack_result_id == older_id


def test_get_attack_results_paginated_turns_filter_after_dedup(sqlite_instance: MemoryInterface):
    """min/max_turns apply to the surviving winner, never resurrecting an older duplicate."""
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result(
                "conv-1", executed_turns=2, ts_offset=1, updated_at_offset=1, attack_result_id=older_id
            ),
            _make_attack_result(
                "conv-1", executed_turns=9, ts_offset=2, updated_at_offset=2, attack_result_id=newer_id
            ),
        ]
    )
    # Winner (turns=9) is out of range; the older turns=2 row must NOT resurface.
    assert list(sqlite_instance.get_attack_results(max_turns=5, limit=50)) == []
    # Winner in range -> returned.
    page = sqlite_instance.get_attack_results(min_turns=5, limit=50)
    assert len(page) == 1
    assert page[0].attack_result_id == newer_id


def test_get_attack_results_paginated_same_timestamp_tie_keeps_max_id(sqlite_instance: MemoryInterface):
    """On identical timestamps, dedup deterministically keeps the max id."""
    id_lo = "00000000-0000-4000-8000-000000000001"
    id_hi = "00000000-0000-4000-8000-0000000000ff"
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result("conv-1", ts_offset=5, updated_at_offset=5, attack_result_id=id_hi),
            _make_attack_result("conv-1", ts_offset=5, updated_at_offset=5, attack_result_id=id_lo),
        ]
    )
    page = sqlite_instance.get_attack_results(limit=50)
    assert len(page) == 1
    assert page[0].attack_result_id == id_hi


def test_get_attack_results_paginated_empty_metadata_orders_newest_first(sqlite_instance: MemoryInterface):
    """Rows without recency metadata fall back to timestamp DESC (newest first)."""
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result("conv-a", ts_offset=1),
            _make_attack_result("conv-b", ts_offset=2),
            _make_attack_result("conv-c", ts_offset=3),
        ]
    )
    page = [r.conversation_id for r in sqlite_instance.get_attack_results(limit=50)]
    assert page == ["conv-c", "conv-b", "conv-a"]


def test_get_attack_results_pagination_with_ids_raises(sqlite_instance: MemoryInterface):
    """limit/keyset pagination cannot be combined with id-batched lookups."""
    anchor = AttackResultKeysetCursor(timestamp=_BASE_TS, attack_result_id=str(uuid.uuid4()))
    with pytest.raises(ValueError, match="pagination cannot be combined"):
        sqlite_instance.get_attack_results(attack_result_ids=[str(uuid.uuid4())], limit=10)
    with pytest.raises(ValueError, match="pagination cannot be combined"):
        sqlite_instance.get_attack_results(objective_sha256=["abc"], after=anchor)


def test_get_attack_results_unpaginated_turns_filter(sqlite_instance: MemoryInterface):
    """The unpaginated path also honors min/max_turns (applied after dedup)."""
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            _make_attack_result("conv-x", executed_turns=2, ts_offset=1, updated_at_offset=1),
            _make_attack_result("conv-y", executed_turns=8, ts_offset=2, updated_at_offset=2),
        ]
    )
    filtered = sqlite_instance.get_attack_results(min_turns=5)
    assert len(filtered) == 1
    assert filtered[0].conversation_id == "conv-y"
    # No bounds -> unchanged behavior (all winners).
    assert len(sqlite_instance.get_attack_results()) == 2


def test_get_attack_results_paginated_hydrates_scores_under_limit(sqlite_instance: MemoryInterface):
    """The paginated joinedload path still hydrates last_response and last_score."""
    message_piece = MessagePiece(
        role="user",
        original_value="Test prompt",
        converted_value="Test prompt",
        conversation_id="conv-scored",
    )
    score = Score(
        score_value="1.0",
        score_type="float_scale",
        score_category=["test_category"],
        message_piece_id=message_piece.id,
        score_rationale="Test score rationale",
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[message_piece])
    sqlite_instance.add_scores_to_memory(scores=[score])

    scored = AttackResult(
        conversation_id="conv-scored",
        objective="scored objective",
        last_response=message_piece,
        last_score=score,
        executed_turns=5,
        execution_time_ms=0,
        outcome=AttackOutcome.SUCCESS,
        metadata={"updated_at": (_BASE_TS + timedelta(seconds=99)).isoformat()},
        timestamp=_BASE_TS + timedelta(seconds=99),
    )
    others = [_make_attack_result(f"conv-{i}", ts_offset=i, updated_at_offset=i) for i in range(5)]
    sqlite_instance.add_attack_results_to_memory(attack_results=[scored, *others])

    page = sqlite_instance.get_attack_results(limit=1)
    assert len(page) == 1
    assert page[0].conversation_id == "conv-scored"
    assert page[0].last_response is not None
    assert page[0].last_response.id == message_piece.id
    assert page[0].last_score is not None
    assert page[0].last_score.id == score.id


def test_get_attack_results_keyset_pagination_stable_under_concurrent_insert(sqlite_instance: MemoryInterface):
    """A row inserted at the top between page loads must not duplicate or skip a result.

    This is the regression guard for the offset-drift bug: with a numeric offset, a new
    top-sorting row shifts every row down one slot, so page 2 re-shows page 1's last row.
    A keyset cursor seeks strictly past the previous page's last row, so it is immune.
    """
    seeded = [_make_attack_result(f"conv-{i}", ts_offset=i, updated_at_offset=i) for i in range(6)]
    sqlite_instance.add_attack_results_to_memory(attack_results=seeded)

    page1 = sqlite_instance.get_attack_results(limit=3)
    assert [r.conversation_id for r in page1] == ["conv-5", "conv-4", "conv-3"]

    # A concurrent attack finishes between page loads and sorts to the very top.
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[_make_attack_result("conv-new", ts_offset=999, updated_at_offset=999)]
    )

    page2 = sqlite_instance.get_attack_results(limit=3, after=_after(page1))
    page2_ids = [r.conversation_id for r in page2]

    # The pre-existing rows below the anchor are returned exactly once, in order, with no
    # duplicate of the page-1 boundary ("conv-3") and no skipped row.
    assert page2_ids == ["conv-2", "conv-1", "conv-0"]
    assert {p.conversation_id for p in page1}.isdisjoint(page2_ids)
    # The newly inserted top row sorts above the anchor, so it is correctly not shown on page 2.
    assert "conv-new" not in page2_ids


def test_get_attack_results_keyset_pagination_stable_under_delete(sqlite_instance: MemoryInterface):
    """Deleting an already-seen row between pages must not skip the next unseen row."""
    seeded = [_make_attack_result(f"conv-{i}", ts_offset=i, updated_at_offset=i) for i in range(6)]
    sqlite_instance.add_attack_results_to_memory(attack_results=seeded)

    page1 = sqlite_instance.get_attack_results(limit=3)
    assert [r.conversation_id for r in page1] == ["conv-5", "conv-4", "conv-3"]

    # Delete a row that was already returned on page 1 (above the anchor). With an offset this
    # shifts the window and skips "conv-2"; the keyset anchor is unaffected.
    with sqlite_instance.get_session() as session:
        session.query(AttackResultEntry).filter(AttackResultEntry.conversation_id == "conv-4").delete()
        session.commit()

    page2 = [r.conversation_id for r in sqlite_instance.get_attack_results(limit=3, after=_after(page1))]
    assert page2 == ["conv-2", "conv-1", "conv-0"]


def test_get_attack_results_keyset_paginates_empty_recency_tie_block(sqlite_instance: MemoryInterface):
    """Rows that all share an empty recency key still paginate disjoint and complete.

    Metadata-less rows all coalesce to recency "", so the seek must fall through to the
    timestamp and id tie-breaks. A recency-only seek would loop or skip within this block.
    """
    seeded = [_make_attack_result(f"conv-{i}", ts_offset=i) for i in range(9)]  # no updated_at/created_at
    sqlite_instance.add_attack_results_to_memory(attack_results=seeded)

    full = [r.conversation_id for r in sqlite_instance.get_attack_results(limit=100)]
    assert full == [f"conv-{i}" for i in range(8, -1, -1)]  # newest timestamp first

    paged = [r.conversation_id for r in _drain_keyset(sqlite_instance, page_size=4)]
    assert paged == full
    assert len(set(paged)) == 9


def test_get_attack_results_keyset_paginates_recency_and_timestamp_ties(sqlite_instance: MemoryInterface):
    """With shared recency and timestamp, the seek falls through to the unique id tie-break."""
    ids = [f"00000000-0000-4000-8000-{i:012d}" for i in range(8)]
    seeded = [
        # All rows share updated_at and timestamp; only the id differs.
        _make_attack_result(f"conv-{i}", ts_offset=5, updated_at_offset=5, attack_result_id=ids[i])
        for i in range(8)
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=seeded)

    full = [r.attack_result_id for r in sqlite_instance.get_attack_results(limit=100)]
    paged = [r.attack_result_id for r in _drain_keyset(sqlite_instance, page_size=3)]
    assert paged == full
    assert len(set(paged)) == 8


def test_attack_result_keyset_order_matches_sql_order(sqlite_instance: MemoryInterface):
    """The keyset anchor ordering (timestamp, id) reproduces the DB recency ordering."""
    seeded = [
        _make_attack_result("conv-a", ts_offset=1, created_at_offset=1),
        _make_attack_result("conv-b", ts_offset=2, created_at_offset=40),
        _make_attack_result("conv-c", ts_offset=3),
        _make_attack_result("conv-d", ts_offset=4),
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=seeded)

    sql_order = list(sqlite_instance.get_attack_results(limit=100))
    python_order = sorted(
        sqlite_instance.get_attack_results(),
        key=lambda ar: (
            AttackResultKeysetCursor.from_attack_result(ar).timestamp,
            ar.attack_result_id,
        ),
        reverse=True,
    )
    assert [r.conversation_id for r in sql_order] == [r.conversation_id for r in python_order]
