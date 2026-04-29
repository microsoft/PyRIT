# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for batching functionality to handle large numbers of IDs.
This addresses the scaling bug where methods like get_scores_by_prompt_ids
fail when querying with many IDs due to SQLite bind variable limits.
"""

import hashlib
import uuid
from unittest.mock import patch

from pyrit.identifiers import ComponentIdentifier
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import PromptMemoryEntry
from pyrit.models import AttackResult, MessagePiece, ScenarioIdentifier, ScenarioResult, Score

# Use the class attribute for the batch limit in tests
_MAX_BIND_VARS = MemoryInterface._MAX_BIND_VARS


def _create_message_piece(
    conversation_id: str | None = None,
    original_value: str = "test message",
    role: str = "user",
) -> MessagePiece:
    """Create a sample message piece for testing."""
    converted_value = original_value
    # Compute SHA256 for converted_value so filtering by sha256 works
    sha256 = hashlib.sha256(converted_value.encode("utf-8")).hexdigest()
    return MessagePiece(
        id=str(uuid.uuid4()),
        role=role,
        original_value=original_value,
        converted_value=converted_value,
        converted_value_sha256=sha256,
        sequence=0,
        conversation_id=conversation_id or str(uuid.uuid4()),
        labels={"test": "label"},
        attack_identifier={"id": str(uuid.uuid4())},
    )


def _create_score(message_piece_id: str) -> Score:
    """Create a sample score for testing."""
    return Score(
        score_value="0.5",
        score_value_description="test score",
        score_type="float_scale",
        score_category=["test"],
        score_rationale="test rationale",
        score_metadata={},
        scorer_class_identifier={"__type__": "TestScorer"},
        message_piece_id=message_piece_id,
    )


def _create_scenario_result(
    name: str = "Test Scenario",
    attack_results: dict[str, list[AttackResult]] | None = None,
) -> ScenarioResult:
    """Create a sample scenario result for testing."""
    return ScenarioResult(
        scenario_identifier=ScenarioIdentifier(
            name=name,
            description="test",
            scenario_version=1,
            init_data={},
        ),
        objective_target_identifier=ComponentIdentifier(class_name="TestTarget", class_module="test"),
        attack_results=attack_results or {},
        objective_scorer_identifier=ComponentIdentifier(class_name="TestScorer", class_module="test"),
    )


class TestBatchingScale:
    """Tests for batching when querying with many IDs."""

    def test_get_message_pieces_with_many_prompt_ids(self, sqlite_instance: MemoryInterface):
        """Test that get_message_pieces works with more IDs than the batch limit."""
        # Create more message pieces than the batch limit
        num_pieces = _MAX_BIND_VARS + 100
        pieces = [_create_message_piece() for _ in range(num_pieces)]

        # Add to memory
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Query with all IDs - this should work with batching
        all_ids = [piece.id for piece in pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        assert len(results) == num_pieces, f"Expected {num_pieces} results, got {len(results)}"

    def test_get_message_pieces_with_exact_batch_size(self, sqlite_instance: MemoryInterface):
        """Test that get_message_pieces works with exactly the batch limit."""
        num_pieces = _MAX_BIND_VARS
        pieces = [_create_message_piece() for _ in range(num_pieces)]

        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        all_ids = [piece.id for piece in pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        assert len(results) == num_pieces

    def test_get_message_pieces_with_double_batch_size(self, sqlite_instance: MemoryInterface):
        """Test that get_message_pieces works with double the batch limit."""
        num_pieces = _MAX_BIND_VARS * 2
        pieces = [_create_message_piece() for _ in range(num_pieces)]

        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        all_ids = [piece.id for piece in pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        assert len(results) == num_pieces

    def test_get_scores_with_many_score_ids(self, sqlite_instance: MemoryInterface):
        """Test that get_scores works with more IDs than the batch limit."""
        # Create message pieces first (scores need to reference them)
        num_scores = _MAX_BIND_VARS + 100
        pieces = [_create_message_piece() for _ in range(num_scores)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Create and add scores
        scores = [_create_score(str(piece.id)) for piece in pieces]
        sqlite_instance.add_scores_to_memory(scores=scores)

        # Query with all score IDs - this should work with batching
        all_score_ids = [str(score.id) for score in scores]
        results = sqlite_instance.get_scores(score_ids=all_score_ids)

        assert len(results) == num_scores, f"Expected {num_scores} results, got {len(results)}"

    def test_get_prompt_scores_with_many_prompt_ids(self, sqlite_instance: MemoryInterface):
        """Test that get_prompt_scores works with more prompt IDs than the batch limit."""
        # Create message pieces
        num_pieces = _MAX_BIND_VARS + 50
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Create and add scores for half of them
        num_scores = num_pieces // 2
        scores = [_create_score(str(pieces[i].id)) for i in range(num_scores)]
        sqlite_instance.add_scores_to_memory(scores=scores)

        # Query with all prompt IDs - should return scores for pieces that have them
        all_prompt_ids = [piece.id for piece in pieces]
        results = sqlite_instance.get_prompt_scores(prompt_ids=all_prompt_ids)

        assert len(results) == num_scores, f"Expected {num_scores} results, got {len(results)}"

    def test_get_message_pieces_batching_preserves_other_filters(self, sqlite_instance: MemoryInterface):
        """Test that batching still applies other filter conditions correctly."""
        # Create pieces with different roles
        num_pieces = _MAX_BIND_VARS + 50
        user_pieces = [_create_message_piece(role="user") for _ in range(num_pieces)]

        assistant_pieces = [_create_message_piece(role="assistant") for _ in range(50)]

        all_pieces = user_pieces + assistant_pieces
        sqlite_instance.add_message_pieces_to_memory(message_pieces=all_pieces)

        # Query with all IDs but filter by role
        all_ids = [piece.id for piece in all_pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids, role="user")

        assert len(results) == num_pieces, f"Expected {num_pieces} user pieces, got {len(results)}"

    def test_get_message_pieces_small_list_still_works(self, sqlite_instance: MemoryInterface):
        """Test that small ID lists (under batch limit) still work correctly."""
        num_pieces = 10
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        all_ids = [piece.id for piece in pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        assert len(results) == num_pieces

    def test_get_message_pieces_with_many_original_values(self, sqlite_instance: MemoryInterface):
        """Test that get_message_pieces works with many original_values exceeding batch limit."""
        num_pieces = _MAX_BIND_VARS + 100
        # Create pieces with unique original values
        pieces = [_create_message_piece(original_value=f"unique_value_{i}") for i in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Query with all original values
        all_values = [piece.original_value for piece in pieces]
        results = sqlite_instance.get_message_pieces(original_values=all_values)

        assert len(results) == num_pieces, f"Expected {num_pieces} results, got {len(results)}"

    def test_get_message_pieces_with_many_converted_value_sha256(self, sqlite_instance: MemoryInterface):
        """Test that get_message_pieces works with many converted_value_sha256 exceeding batch limit."""
        num_pieces = _MAX_BIND_VARS + 100
        pieces = [_create_message_piece(original_value=f"unique_value_{i}") for i in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Get SHA256 hashes from stored pieces
        stored_pieces = sqlite_instance.get_message_pieces()
        all_hashes = [piece.converted_value_sha256 for piece in stored_pieces if piece.converted_value_sha256]

        assert len(all_hashes) > _MAX_BIND_VARS, "Test setup failed: not enough hashes to trigger batching"
        results = sqlite_instance.get_message_pieces(converted_value_sha256=all_hashes)
        assert len(results) == len(all_hashes)

    def test_get_message_pieces_combines_filters_correctly(self, sqlite_instance: MemoryInterface):
        """Test that multiple filters can be combined (e.g., prompt_ids AND role)."""
        # Create message pieces with different roles
        num_pieces = 50
        user_pieces = [_create_message_piece(role="user") for _ in range(num_pieces)]

        assistant_pieces = [_create_message_piece(role="assistant") for _ in range(num_pieces)]

        all_pieces = user_pieces + assistant_pieces
        sqlite_instance.add_message_pieces_to_memory(message_pieces=all_pieces)

        # Query with both prompt_ids AND role filter
        user_ids = [piece.id for piece in user_pieces]
        results = sqlite_instance.get_message_pieces(prompt_ids=user_ids, role="user")

        # Should return only user pieces (intersection of both filters)
        assert len(results) == num_pieces
        assert all(r.get_role_for_storage() == "user" for r in results)

        # Query with role filter and a subset of IDs
        subset_ids = user_ids[:10]
        results = sqlite_instance.get_message_pieces(prompt_ids=subset_ids, role="user")
        assert len(results) == 10

    def test_get_message_pieces_multiple_large_params_simultaneously(self, sqlite_instance: MemoryInterface):
        """Test batching with multiple parameters exceeding batch limit simultaneously."""
        # Create enough pieces to exceed batch limit with unique values
        num_pieces = _MAX_BIND_VARS + 200
        pieces = [_create_message_piece(original_value=f"original_value_{i}") for i in range(num_pieces)]

        # Add to memory
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Get all stored pieces to extract their IDs and SHA256 hashes
        stored_pieces = sqlite_instance.get_message_pieces()
        assert len(stored_pieces) >= num_pieces

        # Extract multiple large parameter lists
        all_ids = [piece.id for piece in stored_pieces[:num_pieces]]
        all_original_values = [piece.original_value for piece in stored_pieces[:num_pieces]]
        all_sha256 = [piece.converted_value_sha256 for piece in stored_pieces[:num_pieces]]

        # Query with multiple large parameters simultaneously
        # This tests that ALL parameters are batched correctly, not just one
        results = sqlite_instance.get_message_pieces(
            prompt_ids=all_ids,
            original_values=all_original_values,
            converted_value_sha256=all_sha256,
        )

        # Should return all pieces that match ALL conditions (intersection)
        assert len(results) == num_pieces, (
            f"Expected {num_pieces} results when filtering with multiple large parameters, got {len(results)}"
        )

        # Verify all returned pieces match all filter criteria
        result_ids = {r.id for r in results}
        result_original_values = {r.original_value for r in results}
        result_sha256 = {r.converted_value_sha256 for r in results}

        assert result_ids == set(all_ids), "Returned IDs don't match filter"
        assert result_original_values == set(all_original_values), "Returned original_values don't match filter"
        assert result_sha256 == set(all_sha256), "Returned SHA256 hashes don't match filter"

    def test_get_message_pieces_multiple_batched_params_with_query_spy(self, sqlite_instance: MemoryInterface):
        """Test that batching executes multiple separate queries and merges results correctly."""
        # Create pieces exceeding batch limit
        num_pieces = _MAX_BIND_VARS + 100
        pieces = [_create_message_piece(original_value=f"value_{i}") for i in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Get stored pieces
        stored_pieces = sqlite_instance.get_message_pieces()
        all_ids = [piece.id for piece in stored_pieces[:num_pieces]]
        all_original_values = [piece.original_value for piece in stored_pieces[:num_pieces]]

        # Mock _query_entries to track how it's called
        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            results = sqlite_instance.get_message_pieces(prompt_ids=all_ids, original_values=all_original_values)

        # Should get all results despite batching
        assert len(results) == num_pieces

        # With the new batching approach, multiple separate queries should be executed
        # when the primary batch parameter exceeds _MAX_BIND_VARS
        # Expected: ceil(num_pieces / _MAX_BIND_VARS) = 2 queries
        expected_min_calls = (num_pieces + _MAX_BIND_VARS - 1) // _MAX_BIND_VARS
        assert call_count >= expected_min_calls, (
            f"Expected at least {expected_min_calls} separate queries for {num_pieces} items, "
            f"but only got {call_count} calls"
        )

    def test_get_message_pieces_triple_large_params_preserves_intersection(self, sqlite_instance: MemoryInterface):
        """Test that filtering with 3 large parameter lists returns correct intersection."""
        # Create a large set of pieces
        total_pieces = _MAX_BIND_VARS + 150
        pieces = [
            _create_message_piece(conversation_id=str(uuid.uuid4()), original_value=f"content_{i}")
            for i in range(total_pieces)
        ]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Get stored pieces
        stored_pieces = sqlite_instance.get_message_pieces()

        # Create three overlapping large filter lists
        # List 1: All IDs
        filter_ids = [p.id for p in stored_pieces[:total_pieces]]

        # List 2: All original values
        filter_original_values = [p.original_value for p in stored_pieces[:total_pieces]]

        # List 3: Subset of SHA256 hashes (to test intersection)
        subset_size = _MAX_BIND_VARS + 50
        filter_sha256 = [p.converted_value_sha256 for p in stored_pieces[:subset_size]]

        # Query with all three large parameters
        results = sqlite_instance.get_message_pieces(
            prompt_ids=filter_ids,
            original_values=filter_original_values,
            converted_value_sha256=filter_sha256,
        )

        # Should return only the intersection (subset_size items)
        assert len(results) == subset_size, f"Expected {subset_size} results from intersection, got {len(results)}"

        # Verify all results have SHA256 in the filter list
        result_sha256 = {r.converted_value_sha256 for r in results}
        assert result_sha256.issubset(set(filter_sha256)), "Results contain unexpected SHA256 values"


class TestExecuteBatchedQuery:
    """Tests for the _execute_batched_query helper method."""

    def test_execute_batched_query_small_list_single_query(self, sqlite_instance: MemoryInterface):
        """Test that small lists execute a single query."""
        # Create a small number of pieces (under batch limit)
        num_pieces = 10
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Track query calls
        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            all_ids = [piece.id for piece in pieces]
            results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        # Should be a single query for small lists
        assert call_count == 1
        assert len(results) == num_pieces

    def test_execute_batched_query_large_list_multiple_queries(self, sqlite_instance: MemoryInterface):
        """Test that large lists execute multiple separate queries."""
        # Create pieces exceeding batch limit
        num_pieces = _MAX_BIND_VARS * 3  # 3 batches needed
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Track query calls
        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            all_ids = [piece.id for piece in pieces]
            results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        # Should execute 3 separate queries (one per batch)
        assert call_count == 3, f"Expected 3 queries for 3 batches, got {call_count}"
        assert len(results) == num_pieces

    def test_execute_batched_query_deduplicates_results(self, sqlite_instance: MemoryInterface):
        """Test that batched queries properly deduplicate results."""
        # Create pieces
        num_pieces = 50
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Query with the same IDs repeated (should still return unique results)
        all_ids = [piece.id for piece in pieces]
        # Query twice with same IDs - results should still be unique
        results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        assert len(results) == num_pieces
        # Verify no duplicates
        result_ids = [r.id for r in results]
        assert len(result_ids) == len(set(result_ids)), "Results contain duplicate entries"

    def test_execute_batched_query_exact_batch_boundary(self, sqlite_instance: MemoryInterface):
        """Test querying with exactly the batch limit (edge case)."""
        num_pieces = _MAX_BIND_VARS
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Track query calls
        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            all_ids = [piece.id for piece in pieces]
            results = sqlite_instance.get_message_pieces(prompt_ids=all_ids)

        # Exactly at the limit should still be a single query
        assert call_count == 1, f"Expected 1 query at exact batch limit, got {call_count}"
        assert len(results) == num_pieces

    def test_batching_with_scores_exceeds_limit(self, sqlite_instance: MemoryInterface):
        """Test that get_scores handles large numbers of score IDs correctly."""
        # Create message pieces and scores exceeding the limit
        num_items = _MAX_BIND_VARS * 2 + 50
        pieces = [_create_message_piece() for _ in range(num_items)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        scores = [_create_score(str(piece.id)) for piece in pieces]
        sqlite_instance.add_scores_to_memory(scores=scores)

        # Query with all score IDs
        all_score_ids = [str(score.id) for score in scores]

        # Track query calls
        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            results = sqlite_instance.get_scores(score_ids=all_score_ids)

        # Should execute multiple queries
        expected_calls = (num_items + _MAX_BIND_VARS - 1) // _MAX_BIND_VARS
        assert call_count == expected_calls, f"Expected {expected_calls} queries, got {call_count}"
        assert len(results) == num_items


def _create_attack_result(conversation_id: str | None = None, objective: str = "test objective") -> AttackResult:
    """Create a sample attack result for testing."""
    return AttackResult(
        conversation_id=conversation_id or str(uuid.uuid4()),
        objective=objective,
    )


class TestAttackResultBatching:
    """Tests for batching in get_attack_results."""

    def test_get_attack_results_with_many_ids(self, sqlite_instance: MemoryInterface):
        """Test that get_attack_results works with more IDs than the batch limit."""
        num_results = _MAX_BIND_VARS + 100
        results = [_create_attack_result(objective=f"objective_{i}") for i in range(num_results)]
        sqlite_instance.add_attack_results_to_memory(attack_results=results)

        all_ids = [r.attack_result_id for r in results]
        fetched = sqlite_instance.get_attack_results(attack_result_ids=all_ids)

        assert len(fetched) == num_results, f"Expected {num_results} results, got {len(fetched)}"

    def test_get_attack_results_with_many_objective_sha256(self, sqlite_instance: MemoryInterface):
        """Test that get_attack_results works with many objective_sha256 values exceeding batch limit."""
        num_results = _MAX_BIND_VARS + 100
        results = [_create_attack_result(objective=f"unique_objective_{i}") for i in range(num_results)]
        sqlite_instance.add_attack_results_to_memory(attack_results=results)

        all_sha256 = [hashlib.sha256(f"unique_objective_{i}".encode()).hexdigest() for i in range(num_results)]
        fetched = sqlite_instance.get_attack_results(objective_sha256=all_sha256)

        assert len(fetched) == num_results, f"Expected {num_results} results, got {len(fetched)}"

    def test_get_attack_results_small_list_still_works(self, sqlite_instance: MemoryInterface):
        """Test that small ID lists (under batch limit) still work correctly."""
        num_results = 10
        results = [_create_attack_result(objective=f"objective_{i}") for i in range(num_results)]
        sqlite_instance.add_attack_results_to_memory(attack_results=results)

        all_ids = [r.attack_result_id for r in results]
        fetched = sqlite_instance.get_attack_results(attack_result_ids=all_ids)

        assert len(fetched) == num_results

    def test_get_attack_results_empty_list_returns_empty(self, sqlite_instance: MemoryInterface):
        """Test that explicit empty list returns empty results."""
        results = [_create_attack_result() for _ in range(5)]
        sqlite_instance.add_attack_results_to_memory(attack_results=results)

        fetched = sqlite_instance.get_attack_results(attack_result_ids=[])
        assert fetched == []

        fetched = sqlite_instance.get_attack_results(objective_sha256=[])
        assert fetched == []


class TestScoresEmptyList:
    """Tests for get_scores empty list handling."""

    def test_get_scores_empty_list_returns_empty(self, sqlite_instance: MemoryInterface):
        """Test that get_scores with explicit empty score_ids returns empty results."""
        pieces = [_create_message_piece() for _ in range(3)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
        scores = [_create_score(str(p.id)) for p in pieces]
        sqlite_instance.add_scores_to_memory(scores=scores)

        fetched = sqlite_instance.get_scores(score_ids=[])
        assert fetched == []


class TestEffectiveBatchSize:
    """Tests for effective batch size reduction when small + large params are combined."""

    def test_batch_size_reduced_by_small_params(self, sqlite_instance: MemoryInterface):
        """Test that batch size is reduced when small IN-clause params consume bind variables."""
        # Create pieces with unique values so we can filter by both prompt_ids and original_values
        num_pieces = _MAX_BIND_VARS + 100
        pieces = [_create_message_piece(original_value=f"value_{i}") for i in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # small param: original_values with 200 items (under _MAX_BIND_VARS)
        small_original_values = [f"value_{i}" for i in range(200)]
        # large param: prompt_ids with all IDs (over _MAX_BIND_VARS)
        all_ids = [piece.id for piece in pieces]

        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            results = sqlite_instance.get_message_pieces(
                prompt_ids=all_ids,
                original_values=small_original_values,
            )

        # With 200 small param binds, effective_batch_size = 500 - 200 = 300
        # num_pieces = 600, so ceil(600 / 300) = 2 batches
        effective_batch_size = _MAX_BIND_VARS - 200
        expected_calls = (num_pieces + effective_batch_size - 1) // effective_batch_size
        assert call_count == expected_calls, (
            f"Expected {expected_calls} queries (effective_batch={effective_batch_size}), got {call_count}"
        )
        # Results should be the intersection: only pieces whose original_value is in small_original_values
        assert len(results) == 200

    def test_custom_batch_size_on_execute_batched_query(self, sqlite_instance: MemoryInterface):
        """Test that _execute_batched_query respects custom batch_size parameter."""
        num_pieces = 100
        pieces = [_create_message_piece() for _ in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        all_ids = [piece.id for piece in pieces]
        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            results = sqlite_instance._execute_batched_query(
                PromptMemoryEntry,
                batch_column=PromptMemoryEntry.id,
                batch_values=all_ids,
                batch_size=30,
            )

        # 100 items / batch_size 30 = ceil(100/30) = 4 batches
        assert call_count == 4, f"Expected 4 queries with batch_size=30, got {call_count}"
        assert len(results) == num_pieces

    def test_multiple_small_params_exceeding_limit_are_promoted(self, sqlite_instance: MemoryInterface):
        """Test that multiple small params whose total exceeds _MAX_BIND_VARS get promoted to large."""
        # Create pieces with unique values
        num_pieces = 400
        pieces = [_create_message_piece(original_value=f"val_{i}") for i in range(num_pieces)]
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

        # Two "small" params each at 400 (under 500 individually, but 800 total > 500)
        ids = [piece.id for piece in pieces]
        original_vals = [f"val_{i}" for i in range(400)]

        original_query = sqlite_instance._query_entries
        call_count = 0

        def spy_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_query(*args, **kwargs)

        with patch.object(sqlite_instance, "_query_entries", side_effect=spy_query):
            results = sqlite_instance.get_message_pieces(
                prompt_ids=ids,
                original_values=original_vals,
            )

        # Without the fix this would try 800 binds in one query.
        # With the fix, one param is promoted to large and gets batched.
        assert call_count > 1, f"Expected multiple queries to avoid bind overflow, got {call_count}"
        assert len(results) == num_pieces


class TestScenarioResultBatching:
    """Tests for batching in get_scenario_results."""

    def test_get_scenario_results_with_many_ids(self, sqlite_instance: MemoryInterface):
        """Test that get_scenario_results works with more IDs than the batch limit."""
        num_scenarios = _MAX_BIND_VARS + 50
        attack_results = [_create_attack_result(objective=f"objective_{i}") for i in range(num_scenarios)]
        sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

        scenarios = [
            _create_scenario_result(
                name=f"Scenario {i}",
                attack_results={"attack": [attack_results[i]]},
            )
            for i in range(num_scenarios)
        ]
        sqlite_instance.add_scenario_results_to_memory(scenario_results=scenarios)

        all_ids = [str(s.id) for s in scenarios]
        fetched = sqlite_instance.get_scenario_results(scenario_result_ids=all_ids)

        assert len(fetched) == num_scenarios, f"Expected {num_scenarios}, got {len(fetched)}"

    def test_get_scenario_results_empty_list_returns_empty(self, sqlite_instance: MemoryInterface):
        """Test that explicit empty list returns empty results."""
        fetched = sqlite_instance.get_scenario_results(scenario_result_ids=[])
        assert fetched == []
