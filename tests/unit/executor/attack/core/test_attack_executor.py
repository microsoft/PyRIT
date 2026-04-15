# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the simplified AttackExecutor.

These tests verify the new API that uses AttackParameters and params_type.
"""

import asyncio
import dataclasses
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack import (
    AttackExecutor,
    AttackParameters,
    AttackStrategy,
    SingleTurnAttackContext,
)
from pyrit.executor.attack.core.attack_executor import AttackExecutorResult
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    SeedAttackGroup,
    SeedObjective,
    SeedPrompt,
)


# Helper to create a properly configured mock attack
def create_mock_attack(params_type=AttackParameters, context_type=SingleTurnAttackContext):
    """Create a mock attack with required attributes for the new executor."""
    attack = MagicMock(spec=AttackStrategy)
    attack.params_type = params_type
    attack._context_type = context_type
    attack.execute_with_context_async = AsyncMock()
    return attack


def create_attack_result(objective: str) -> AttackResult:
    """Create a sample attack result."""
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=AttackOutcome.SUCCESS,
        executed_turns=1,
    )


def create_seed_group(objective: str) -> SeedAttackGroup:
    """Create a seed attack group with an objective."""
    return SeedAttackGroup(
        seeds=[
            SeedObjective(value=objective),
            SeedPrompt(value=objective, data_type="text"),
        ]
    )


@pytest.mark.usefixtures("patch_central_database")
class TestAttackExecutorInitialization:
    """Tests for AttackExecutor initialization."""

    def test_init_with_default_max_concurrency(self):
        executor = AttackExecutor()
        assert executor._max_concurrency == 1

    def test_init_with_custom_max_concurrency(self):
        executor = AttackExecutor(max_concurrency=10)
        assert executor._max_concurrency == 10

    @pytest.mark.parametrize("invalid_concurrency", [0, -1, -10])
    def test_init_raises_error_for_invalid_concurrency(self, invalid_concurrency):
        with pytest.raises(ValueError, match="max_concurrency must be a positive integer"):
            AttackExecutor(max_concurrency=invalid_concurrency)


@pytest.mark.usefixtures("patch_central_database")
class TestExecuteAttackAsync:
    """Tests for execute_attack_async method."""

    @pytest.mark.asyncio
    async def test_execute_single_objective(self):
        """Test executing with a single objective."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()
        results = await executor.execute_attack_async(
            attack=attack,
            objectives=["Test objective"],
        )

        assert len(results) == 1
        attack.execute_with_context_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_multiple_objectives(self):
        """Test executing with multiple objectives."""
        attack = create_mock_attack()
        attack.execute_with_context_async.side_effect = [create_attack_result(f"Obj{i}") for i in range(3)]

        executor = AttackExecutor(max_concurrency=5)
        results = await executor.execute_attack_async(
            attack=attack,
            objectives=["Obj1", "Obj2", "Obj3"],
        )

        assert len(results) == 3
        assert attack.execute_with_context_async.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_with_broadcast_memory_labels(self):
        """Test memory_labels broadcast to all objectives."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()
        await executor.execute_attack_async(
            attack=attack,
            objectives=["Obj1", "Obj2"],
            memory_labels={"test": "value"},
        )

        # Check that contexts were created with memory_labels
        calls = attack.execute_with_context_async.call_args_list
        for call in calls:
            context = call.kwargs["context"]
            assert context.params.memory_labels == {"test": "value"}

    @pytest.mark.asyncio
    async def test_execute_with_field_overrides(self):
        """Test field_overrides provides per-objective values."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()
        await executor.execute_attack_async(
            attack=attack,
            objectives=["Obj1", "Obj2"],
            field_overrides=[
                {"memory_labels": {"id": "1"}},
                {"memory_labels": {"id": "2"}},
            ],
        )

        calls = attack.execute_with_context_async.call_args_list
        assert calls[0].kwargs["context"].params.memory_labels == {"id": "1"}
        assert calls[1].kwargs["context"].params.memory_labels == {"id": "2"}

    @pytest.mark.asyncio
    async def test_validates_empty_objectives(self):
        """Test that empty objectives raises ValueError."""
        attack = create_mock_attack()
        executor = AttackExecutor()

        with pytest.raises(ValueError, match="At least one objective must be provided"):
            await executor.execute_attack_async(attack=attack, objectives=[])

    @pytest.mark.asyncio
    async def test_validates_field_overrides_length(self):
        """Test validation of field_overrides length."""
        attack = create_mock_attack()
        executor = AttackExecutor()

        with pytest.raises(ValueError, match="field_overrides length .* must match"):
            await executor.execute_attack_async(
                attack=attack,
                objectives=["Obj1", "Obj2"],
                field_overrides=[{}],  # Wrong length
            )

    @pytest.mark.asyncio
    async def test_concurrency_control(self):
        """Test that concurrency is properly limited."""
        attack = create_mock_attack()
        max_concurrency = 2
        executor = AttackExecutor(max_concurrency=max_concurrency)

        concurrent_count = 0
        max_concurrent = 0

        async def mock_execute(*, context):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return create_attack_result(context.params.objective)

        attack.execute_with_context_async.side_effect = mock_execute

        await executor.execute_attack_async(
            attack=attack,
            objectives=[f"Obj{i}" for i in range(10)],
        )

        assert max_concurrent <= max_concurrency

    @pytest.mark.asyncio
    async def test_single_concurrency_serializes_execution(self):
        """Test that max_concurrency=1 truly serializes execution."""
        attack = create_mock_attack()
        executor = AttackExecutor(max_concurrency=1)

        execution_order = []

        async def mock_execute(*, context):
            objective = context.params.objective
            execution_order.append(f"start_{objective}")
            await asyncio.sleep(0.01)
            execution_order.append(f"end_{objective}")
            return create_attack_result(objective)

        attack.execute_with_context_async.side_effect = mock_execute

        await executor.execute_attack_async(
            attack=attack,
            objectives=["A", "B", "C"],
        )

        # With max_concurrency=1, executions should not overlap
        expected_order = ["start_A", "end_A", "start_B", "end_B", "start_C", "end_C"]
        assert execution_order == expected_order


@pytest.mark.usefixtures("patch_central_database")
class TestExecuteAttackFromSeedGroupsAsync:
    """Tests for execute_attack_from_seed_groups_async method."""

    @pytest.mark.asyncio
    async def test_extracts_objectives_from_seed_groups(self):
        """Test that objectives are extracted from seed groups."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()
        sg1 = create_seed_group("Objective 1")
        sg2 = create_seed_group("Objective 2")

        await executor.execute_attack_from_seed_groups_async(
            attack=attack,
            seed_groups=[sg1, sg2],
        )

        calls = attack.execute_with_context_async.call_args_list
        assert calls[0].kwargs["context"].params.objective == "Objective 1"
        assert calls[1].kwargs["context"].params.objective == "Objective 2"

    @pytest.mark.asyncio
    async def test_validates_empty_seed_groups(self):
        """Test that empty seed_groups raises ValueError."""
        attack = create_mock_attack()
        executor = AttackExecutor()

        with pytest.raises(ValueError, match="At least one seed_group must be provided"):
            await executor.execute_attack_from_seed_groups_async(
                attack=attack,
                seed_groups=[],
            )

    @pytest.mark.asyncio
    async def test_validates_seed_group_has_objective(self):
        """Test that seed groups without objectives raise ValueError at construction."""
        # SeedAttackGroup now validates exactly one objective at construction
        with pytest.raises(ValueError, match="must have exactly one objective"):
            SeedAttackGroup(seeds=[SeedPrompt(value="test", data_type="text")])

    @pytest.mark.asyncio
    async def test_passes_broadcast_fields(self):
        """Test that broadcast fields are passed to all seed groups."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()
        sg = create_seed_group("Test objective")

        await executor.execute_attack_from_seed_groups_async(
            attack=attack,
            seed_groups=[sg],
            memory_labels={"broadcast": "value"},
        )

        context = attack.execute_with_context_async.call_args.kwargs["context"]
        assert context.params.memory_labels == {"broadcast": "value"}

    @pytest.mark.asyncio
    async def test_passes_adversarial_chat_and_objective_scorer(self):
        """Test that adversarial_chat and objective_scorer are passed to from_seed_group_async."""
        attack = create_mock_attack()
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        mock_adversarial_chat = MagicMock()
        mock_objective_scorer = MagicMock()
        captured_kwargs = {}

        original_from_seed_group_async = attack.params_type.from_seed_group_async

        async def capture_from_seed_group_async(*, seed_group, **kwargs):
            captured_kwargs.update(kwargs)
            return await original_from_seed_group_async(seed_group=seed_group, **kwargs)

        attack.params_type.from_seed_group_async = capture_from_seed_group_async

        try:
            executor = AttackExecutor()
            sg = create_seed_group("Test objective")

            await executor.execute_attack_from_seed_groups_async(
                attack=attack,
                seed_groups=[sg],
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
            )

            assert captured_kwargs.get("adversarial_chat") is mock_adversarial_chat
            assert captured_kwargs.get("objective_scorer") is mock_objective_scorer
        finally:
            # Restore the original to prevent test pollution in parallel test runs
            attack.params_type.from_seed_group_async = original_from_seed_group_async


@pytest.mark.usefixtures("patch_central_database")
class TestPartialFailureHandling:
    """Tests for partial failure handling."""

    @pytest.mark.asyncio
    async def test_partial_failure_preserves_input_indices(self):
        """Test that input_indices correctly maps completed results when some fail."""
        attack = create_mock_attack()

        async def mock_execute(*, context):
            if "fail" in context.params.objective:
                raise RuntimeError("Execution failed")
            return create_attack_result(context.params.objective)

        attack.execute_with_context_async.side_effect = mock_execute

        executor = AttackExecutor()
        result = await executor.execute_attack_async(
            attack=attack,
            objectives=["success0", "fail1", "success2"],
            return_partial_on_failure=True,
        )

        # success0 is input index 0, fail1 is index 1 (excluded), success2 is index 2
        assert len(result.completed_results) == 2
        assert result.input_indices == [0, 2]

    @pytest.mark.asyncio
    async def test_all_succeed_input_indices_sequential(self):
        """Test that input_indices is [0, 1, 2, ...] when all succeed."""
        attack = create_mock_attack()
        attack.execute_with_context_async.side_effect = lambda *, context: create_attack_result(
            context.params.objective
        )

        executor = AttackExecutor()
        result = await executor.execute_attack_async(
            attack=attack,
            objectives=["obj0", "obj1", "obj2"],
        )

        assert result.input_indices == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_partial_failure_with_return_partial(self):
        """Test return_partial_on_failure=True returns partial results."""
        attack = create_mock_attack()

        async def mock_execute(*, context):
            if "fail" in context.params.objective:
                raise RuntimeError("Execution failed")
            return create_attack_result(context.params.objective)

        attack.execute_with_context_async.side_effect = mock_execute

        executor = AttackExecutor()
        result = await executor.execute_attack_async(
            attack=attack,
            objectives=["success1", "fail", "success2"],
            return_partial_on_failure=True,
        )

        assert len(result.completed_results) == 2
        assert len(result.incomplete_objectives) == 1
        assert result.has_incomplete

    @pytest.mark.asyncio
    async def test_partial_failure_raises_by_default(self):
        """Test that failures raise exception by default."""
        attack = create_mock_attack()

        async def mock_execute(*, context):
            raise RuntimeError("Execution failed")

        attack.execute_with_context_async.side_effect = mock_execute

        executor = AttackExecutor()
        with pytest.raises(RuntimeError, match="Execution failed"):
            await executor.execute_attack_async(
                attack=attack,
                objectives=["Test"],
            )


@pytest.mark.usefixtures("patch_central_database")
class TestAttackExecutorResult:
    """Tests for AttackExecutorResult dataclass."""

    def test_iteration(self):
        """Test that result is iterable."""
        results = [create_attack_result(f"Obj{i}") for i in range(3)]
        executor_result = AttackExecutorResult(
            completed_results=results,
            incomplete_objectives=[],
        )

        assert list(executor_result) == results

    def test_len(self):
        """Test len() on result."""
        results = [create_attack_result(f"Obj{i}") for i in range(3)]
        executor_result = AttackExecutorResult(
            completed_results=results,
            incomplete_objectives=[],
        )

        assert len(executor_result) == 3

    def test_indexing(self):
        """Test indexing into result."""
        results = [create_attack_result(f"Obj{i}") for i in range(3)]
        executor_result = AttackExecutorResult(
            completed_results=results,
            incomplete_objectives=[],
        )

        assert executor_result[0] == results[0]
        assert executor_result[2] == results[2]

    def test_has_incomplete_true(self):
        """Test has_incomplete when there are incomplete objectives."""
        executor_result = AttackExecutorResult(
            completed_results=[],
            incomplete_objectives=[("Obj1", RuntimeError("Failed"))],
        )

        assert executor_result.has_incomplete is True

    def test_has_incomplete_false(self):
        """Test has_incomplete when all complete."""
        executor_result = AttackExecutorResult(
            completed_results=[create_attack_result("Test")],
            incomplete_objectives=[],
        )

        assert executor_result.has_incomplete is False

    def test_raise_if_incomplete(self):
        """Test raise_if_incomplete raises first exception."""
        error = RuntimeError("First error")
        executor_result = AttackExecutorResult(
            completed_results=[],
            incomplete_objectives=[("Obj1", error)],
        )

        with pytest.raises(RuntimeError, match="First error"):
            executor_result.raise_if_incomplete()

    def test_get_results_raises_when_incomplete(self):
        """Test get_results raises when incomplete."""
        executor_result = AttackExecutorResult(
            completed_results=[create_attack_result("Test")],
            incomplete_objectives=[("Obj1", RuntimeError("Failed"))],
        )

        with pytest.raises(RuntimeError):
            executor_result.get_results()

    def test_get_results_returns_when_complete(self):
        """Test get_results returns results when all complete."""
        results = [create_attack_result("Test")]
        executor_result = AttackExecutorResult(
            completed_results=results,
            incomplete_objectives=[],
        )

        assert executor_result.get_results() == results

    def test_input_indices_default_empty(self):
        """Test that input_indices defaults to empty list."""
        executor_result = AttackExecutorResult(
            completed_results=[create_attack_result("Test")],
            incomplete_objectives=[],
        )

        assert executor_result.input_indices == []

    def test_input_indices_preserved(self):
        """Test that input_indices are preserved when set."""
        executor_result = AttackExecutorResult(
            completed_results=[create_attack_result("Test")],
            incomplete_objectives=[],
            input_indices=[2],
        )

        assert executor_result.input_indices == [2]


@pytest.mark.usefixtures("patch_central_database")
class TestParamsTypeIntegration:
    """Tests for params_type integration with executor."""

    @pytest.mark.asyncio
    async def test_excluded_params_type_rejects_excluded_fields(self):
        """Test that params_type.excluding() properly rejects fields."""
        # Create a params type that excludes next_message
        LimitedParams = AttackParameters.excluding("next_message", "prepended_conversation")  # noqa: N806

        attack = create_mock_attack(params_type=LimitedParams)
        attack.execute_with_context_async.return_value = create_attack_result("Test")

        executor = AttackExecutor()

        # This should work - only passing valid fields
        await executor.execute_attack_async(
            attack=attack,
            objectives=["Test"],
            memory_labels={"test": "value"},
        )

        # Verify context was created with correct params type
        context = attack.execute_with_context_async.call_args.kwargs["context"]
        fields = {f.name for f in dataclasses.fields(context.params)}
        assert "next_message" not in fields
        assert "prepended_conversation" not in fields
        assert "objective" in fields
        assert "memory_labels" in fields
