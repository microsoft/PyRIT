# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenarios.AtomicAttack class."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import AttackExecutor, AttackStrategy
from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    SeedAttackGroup,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
)
from pyrit.scenario import AtomicAttack


@pytest.fixture
def mock_attack():
    """Create a mock AttackStrategy for testing."""
    return MagicMock(spec=AttackStrategy)


@pytest.fixture
def sample_seed_groups():
    """Create sample seed groups with objectives for testing."""
    return [
        SeedAttackGroup(
            seeds=[
                SeedObjective(value="objective1"),
                SeedPrompt(value="prompt1"),
            ]
        ),
        SeedAttackGroup(
            seeds=[
                SeedObjective(value="objective2"),
                SeedPrompt(value="prompt2"),
            ]
        ),
        SeedAttackGroup(
            seeds=[
                SeedObjective(value="objective3"),
                SeedPrompt(value="prompt3"),
            ]
        ),
    ]


@pytest.fixture
def sample_seed_groups_without_objectives():
    """Create sample seed groups without objectives for testing.

    Note: SeedAttackGroup now validates exactly one objective at construction,
    so we use SeedGroup here which doesn't have that requirement.
    """
    return [
        SeedGroup(
            seeds=[
                SeedPrompt(value="prompt1"),
            ]
        ),
    ]


@pytest.fixture
def sample_attack_results():
    """Create sample attack results for testing."""
    return [
        AttackResult(
            conversation_id="conv-1",
            objective="objective1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
        ),
        AttackResult(
            conversation_id="conv-2",
            objective="objective2",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
        ),
        AttackResult(
            conversation_id="conv-3",
            objective="objective3",
            outcome=AttackOutcome.FAILURE,
            executed_turns=1,
        ),
    ]


def wrap_results(results):
    """Helper to wrap attack results in AttackExecutorResult."""
    return AttackExecutorResult(
        completed_results=results,
        incomplete_objectives=[],
        input_indices=list(range(len(results))),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackInitialization:
    """Tests for AtomicAttack class initialization."""

    def test_init_with_valid_params(self, mock_attack, sample_seed_groups):
        """Test successful initialization with valid parameters."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._attack == mock_attack
        assert atomic_attack._seed_groups == sample_seed_groups
        assert atomic_attack._memory_labels == {}
        assert atomic_attack._attack_execute_params == {}

    def test_init_with_memory_labels(self, mock_attack, sample_seed_groups):
        """Test initialization with memory labels."""
        memory_labels = {"test": "label", "category": "attack"}

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._memory_labels == memory_labels

    def test_init_with_attack_execute_params(self, mock_attack, sample_seed_groups):
        """Test initialization with additional attack execute parameters."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            max_retries=5,
            custom_param="value",
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._attack_execute_params["max_retries"] == 5
        assert atomic_attack._attack_execute_params["custom_param"] == "value"

    def test_init_with_all_parameters(self, mock_attack, sample_seed_groups):
        """Test initialization with all parameters."""
        memory_labels = {"test": "comprehensive"}

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            batch_size=10,
            timeout=30,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._attack == mock_attack
        assert atomic_attack._seed_groups == sample_seed_groups
        assert atomic_attack._memory_labels == memory_labels
        assert atomic_attack._attack_execute_params["batch_size"] == 10
        assert atomic_attack._attack_execute_params["timeout"] == 30

    def test_init_fails_with_empty_seed_groups(self, mock_attack):
        """Test that initialization fails when seed_groups list is empty."""
        with pytest.raises(ValueError, match="seed_groups list cannot be empty"):
            AtomicAttack(
                attack=mock_attack,
                seed_groups=[],
                atomic_attack_name="Test Attack Run",
            )

    def test_init_fails_with_seed_group_missing_objective(self, mock_attack):
        """Test that SeedAttackGroup without objective cannot be created.

        SeedAttackGroup now validates exactly one objective at construction time,
        so we can't even create one without an objective.
        """
        # SeedAttackGroup now validates exactly one objective at construction
        with pytest.raises(ValueError, match="must have exactly one objective"):
            SeedAttackGroup(seeds=[SeedPrompt(value="prompt1")])

    def test_objectives_property_returns_values_from_seed_groups(self, mock_attack, sample_seed_groups):
        """Test that the objectives property returns values from seed groups."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack.objectives == ["objective1", "objective2", "objective3"]

    def test_seed_groups_property_returns_copy(self, mock_attack, sample_seed_groups):
        """Test that the seed_groups property returns a copy."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        returned_groups = atomic_attack.seed_groups
        assert returned_groups == sample_seed_groups
        assert returned_groups is not atomic_attack._seed_groups


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackExecution:
    """Tests for AtomicAttack execution methods."""

    @pytest.mark.asyncio
    async def test_run_async_with_valid_atomic_attack(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test successful execution of an atomic attack."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            result = await atomic_attack.run_async()

            assert len(result.completed_results) == 3
            assert result.completed_results == sample_attack_results
            assert len(result.incomplete_objectives) == 0
            mock_exec.assert_called_once()

            # Verify the attack was passed correctly
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["attack"] == mock_attack

    @pytest.mark.asyncio
    async def test_run_async_with_custom_concurrency(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test execution with custom max_concurrency for atomic attack."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with (
            patch.object(AttackExecutor, "__init__", return_value=None) as mock_init,
            patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = wrap_results(sample_attack_results)

            result = await atomic_attack.run_async(max_concurrency=5)

            mock_init.assert_called_once_with(max_concurrency=5)
            assert len(result.completed_results) == 3

    @pytest.mark.asyncio
    async def test_run_async_with_default_concurrency(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that default concurrency (1) is used when not specified."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with (
            patch.object(AttackExecutor, "__init__", return_value=None) as mock_init,
            patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            mock_init.assert_called_once_with(max_concurrency=1)

    @pytest.mark.asyncio
    async def test_run_async_passes_memory_labels(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that memory labels are passed to the executor."""
        memory_labels = {"test": "attack_run", "category": "attack"}

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert "memory_labels" in call_kwargs
            assert call_kwargs["memory_labels"] == memory_labels

    @pytest.mark.asyncio
    async def test_run_async_passes_seed_groups(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that seed_groups are passed to the executor."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert "seed_groups" in call_kwargs
            assert call_kwargs["seed_groups"] == sample_seed_groups

    @pytest.mark.asyncio
    async def test_run_async_passes_attack_execute_params(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that attack execute parameters are passed to the executor."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            custom_param="value",
            max_retries=3,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["custom_param"] == "value"
            assert call_kwargs["max_retries"] == 3

    @pytest.mark.asyncio
    async def test_run_async_merges_all_parameters(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that all parameters are merged and passed correctly."""
        memory_labels = {"test": "merge"}

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            batch_size=5,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["attack"] == mock_attack
            assert call_kwargs["seed_groups"] == sample_seed_groups
            assert call_kwargs["memory_labels"] == memory_labels
            assert call_kwargs["batch_size"] == 5

    @pytest.mark.asyncio
    async def test_run_async_handles_execution_failure(self, mock_attack, sample_seed_groups):
        """Test that execution failures are properly handled and raised."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = Exception("Execution error")

            with pytest.raises(ValueError, match="Failed to execute atomic attack"):
                await atomic_attack.run_async()

    @pytest.mark.asyncio
    async def test_run_async_passes_return_partial_on_failure_true_by_default(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """Test that atomic attack passes return_partial_on_failure=True by default."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert "return_partial_on_failure" in call_kwargs
            assert call_kwargs["return_partial_on_failure"] is True

    @pytest.mark.asyncio
    async def test_run_async_respects_explicit_return_partial_on_failure(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """Test that explicit return_partial_on_failure parameter is passed through."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async(return_partial_on_failure=False)

            call_kwargs = mock_exec.call_args.kwargs
            assert "return_partial_on_failure" in call_kwargs
            assert call_kwargs["return_partial_on_failure"] is False


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackIntegration:
    """Integration Tests for AtomicAttack."""

    @pytest.mark.asyncio
    async def test_full_attack_run_execution_flow(self, mock_attack, sample_seed_groups):
        """Test the complete attack run execution flow end-to-end."""
        memory_labels = {"test": "integration", "attack_run": "full"}

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            batch_size=2,
            atomic_attack_name="Test Attack Run",
        )

        mock_results = [
            AttackResult(
                conversation_id=f"conv-{i}",
                objective=f"objective{i + 1}",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            )
            for i in range(3)
        ]

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(mock_results)

            attack_run_result = await atomic_attack.run_async(max_concurrency=3)

            assert len(attack_run_result.completed_results) == 3
            for i, result in enumerate(attack_run_result.completed_results):
                assert result.objective == f"objective{i + 1}"
                assert result.outcome == AttackOutcome.SUCCESS

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["attack"] == mock_attack
            assert call_kwargs["seed_groups"] == sample_seed_groups
            assert call_kwargs["memory_labels"] == memory_labels
            assert call_kwargs["batch_size"] == 2

    @pytest.mark.asyncio
    async def test_atomic_attack_with_single_seed_group(self, mock_attack):
        """Test atomic attack with a single seed group."""
        single_seed_group = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="single_objective"),
                    SeedPrompt(value="single_prompt"),
                ]
            )
        ]

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=single_seed_group,
            atomic_attack_name="Test Attack Run",
        )

        mock_result = [
            AttackResult(
                conversation_id="conv-1",
                objective="single_objective",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            )
        ]

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(mock_result)

            attack_run_result = await atomic_attack.run_async()

            assert len(attack_run_result.completed_results) == 1
            assert attack_run_result.completed_results[0].objective == "single_objective"

    @pytest.mark.asyncio
    async def test_atomic_attack_with_many_seed_groups(self, mock_attack):
        """Test atomic attack with many seed groups."""
        many_seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value=f"objective_{i}"),
                    SeedPrompt(value=f"prompt_{i}"),
                ]
            )
            for i in range(20)
        ]

        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=many_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        mock_results = [
            AttackResult(
                conversation_id=f"conv-{i}",
                objective=f"objective_{i}",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            )
            for i in range(20)
        ]

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(mock_results)

            attack_run_result = await atomic_attack.run_async()

            assert len(attack_run_result.completed_results) == 20

            call_kwargs = mock_exec.call_args.kwargs
            assert len(call_kwargs["seed_groups"]) == 20


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackExecutorParamCompatibility:
    """Tests to verify AtomicAttack passes parameters compatible with AttackExecutor."""

    def test_atomic_attack_passes_expected_executor_params(self, mock_attack, sample_seed_groups):
        """
        Test that AtomicAttack.run_async passes all expected parameters
        to execute_attack_from_seed_groups_async.
        """
        # Get the signature of execute_attack_from_seed_groups_async
        executor_method = AttackExecutor.execute_attack_from_seed_groups_async
        sig = inspect.signature(executor_method)

        # These are the parameters that execute_attack_from_seed_groups_async accepts
        expected_params = set(sig.parameters.keys()) - {"self"}

        # Verify the explicit parameters we know AtomicAttack should pass
        # Note: memory_labels is passed via **broadcast_fields, not as an explicit parameter
        required_from_atomic_attack = {
            "attack",
            "seed_groups",
            "return_partial_on_failure",
        }

        # All required params should be in the executor method signature
        assert required_from_atomic_attack.issubset(expected_params), (
            f"Missing expected params in executor: {required_from_atomic_attack - expected_params}"
        )

        # Verify that the executor accepts **broadcast_fields (e.g., for memory_labels)
        assert "broadcast_fields" in expected_params, "Executor should accept **broadcast_fields for dynamic params"

    @pytest.mark.asyncio
    async def test_run_async_only_passes_valid_executor_params(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """
        Test that run_async doesn't pass parameters that the executor doesn't accept.
        The executor has strict_param_matching so invalid params would cause failures.
        """
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs

            # Verify essential params are present
            assert "attack" in call_kwargs
            assert "seed_groups" in call_kwargs
            assert "memory_labels" in call_kwargs
            assert "return_partial_on_failure" in call_kwargs


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackWithMessages:
    """Tests for AtomicAttack with seed groups containing multi-turn messages."""

    @pytest.fixture
    def seed_groups_with_messages(self):
        """Create seed groups with multi-turn message sequences for testing."""
        return [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="multi_turn_objective_1"),
                    SeedPrompt(value="First message", data_type="text", sequence=0, role="user"),
                    SeedPrompt(value="Second message", data_type="text", sequence=1, role="user"),
                    SeedPrompt(value="Third message", data_type="text", sequence=2, role="user"),
                ]
            ),
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="multi_turn_objective_2"),
                    SeedPrompt(value="Message A", data_type="text", sequence=0, role="user"),
                    SeedPrompt(value="Message B", data_type="text", sequence=1, role="user"),
                ]
            ),
        ]

    @pytest.fixture
    def mixed_seed_groups(self):
        """Create seed groups where some have messages and some don't."""
        return [
            # No messages (just objective)
            SeedAttackGroup(seeds=[SeedObjective(value="simple_objective")]),
            # With messages - roles required for multi-sequence
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="objective_with_messages"),
                    SeedPrompt(value="Message 1", data_type="text", sequence=0, role="user"),
                    SeedPrompt(value="Message 2", data_type="text", sequence=1, role="user"),
                ]
            ),
        ]

    def test_init_with_seed_groups_with_messages(self, mock_attack, seed_groups_with_messages):
        """Test that AtomicAttack initializes correctly with seed groups containing messages."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=seed_groups_with_messages,
            atomic_attack_name="Multi-turn Attack",
        )

        assert len(atomic_attack.seed_groups) == 2
        assert atomic_attack.objectives == ["multi_turn_objective_1", "multi_turn_objective_2"]

        # Verify seed groups have user messages
        for sg in atomic_attack.seed_groups:
            assert len(sg.user_messages) > 0

    def test_seed_groups_user_messages_property(self, mock_attack, seed_groups_with_messages):
        """Test that seed group user_messages are accessible and have correct content."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=seed_groups_with_messages,
            atomic_attack_name="Multi-turn Attack",
        )

        sg1 = atomic_attack.seed_groups[0]
        sg2 = atomic_attack.seed_groups[1]

        # First seed group has 3 user messages
        assert len(sg1.user_messages) == 3
        assert sg1.user_messages[0].message_pieces[0].original_value == "First message"
        assert sg1.user_messages[1].message_pieces[0].original_value == "Second message"
        assert sg1.user_messages[2].message_pieces[0].original_value == "Third message"

        # Second seed group has 2 user messages
        assert len(sg2.user_messages) == 2
        assert sg2.user_messages[0].message_pieces[0].original_value == "Message A"
        assert sg2.user_messages[1].message_pieces[0].original_value == "Message B"

    @pytest.mark.asyncio
    async def test_run_async_passes_seed_groups_with_messages(self, mock_attack, seed_groups_with_messages):
        """Test that run_async correctly passes seed groups with messages to executor."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=seed_groups_with_messages,
            atomic_attack_name="Multi-turn Attack",
        )

        mock_results = [
            AttackResult(
                conversation_id=f"conv-{i}",
                objective=seed_groups_with_messages[i].objective.value,
                outcome=AttackOutcome.SUCCESS,
                executed_turns=len(seed_groups_with_messages[i].user_messages),
            )
            for i in range(2)
        ]

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(mock_results)

            result = await atomic_attack.run_async()

            assert len(result.completed_results) == 2

            # Verify seed groups were passed correctly
            call_kwargs = mock_exec.call_args.kwargs
            passed_seed_groups = call_kwargs["seed_groups"]
            assert len(passed_seed_groups) == 2

            # Verify user messages are preserved in passed seed groups
            assert len(passed_seed_groups[0].user_messages) == 3
            assert len(passed_seed_groups[1].user_messages) == 2

    def test_init_with_mixed_seed_groups(self, mock_attack, mixed_seed_groups):
        """Test that AtomicAttack handles mixed seed groups (some with user_messages, some without)."""
        atomic_attack = AtomicAttack(
            attack=mock_attack,
            seed_groups=mixed_seed_groups,
            atomic_attack_name="Mixed Attack",
        )

        assert len(atomic_attack.seed_groups) == 2

        # First has no user_messages (empty list)
        assert len(atomic_attack.seed_groups[0].user_messages) == 0

        # Second has user_messages
        assert len(atomic_attack.seed_groups[1].user_messages) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestEnrichAtomicAttackIdentifiers:
    """Tests for _enrich_atomic_attack_identifiers in AtomicAttack."""

    @pytest.mark.asyncio
    async def test_enrichment_populates_atomic_attack_identifier(self, mock_attack):
        """Test that run_async enriches results with atomic_attack_identifier."""
        seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj1"),
                    SeedPrompt(value="technique1", is_general_technique=True),
                ]
            ),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results([attack_result])
            result = await atomic.run_async()

        enriched = result.completed_results[0]
        assert enriched.atomic_attack_identifier is not None
        assert enriched.atomic_attack_identifier.class_name == "AtomicAttack"
        assert "attack" in enriched.atomic_attack_identifier.children
        assert "seeds" in enriched.atomic_attack_identifier.children

    @pytest.mark.asyncio
    async def test_enrichment_skips_results_without_attack_identifier(self, mock_attack):
        """Test that enrichment is skipped when result has no attack_identifier."""
        seed_groups = [
            SeedAttackGroup(seeds=[SeedObjective(value="obj1"), SeedPrompt(value="p1")]),
        ]
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            atomic_attack_identifier=None,
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results([attack_result])
            result = await atomic.run_async()

        # Should not be enriched (no attack_identifier to build from)
        assert result.completed_results[0].atomic_attack_identifier is None

    @pytest.mark.asyncio
    async def test_enrichment_skips_out_of_range_index(self, mock_attack):
        """Test that enrichment is skipped when input_indices has an out-of-range value."""
        seed_groups = [
            SeedAttackGroup(seeds=[SeedObjective(value="obj1"), SeedPrompt(value="p1")]),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            # Index 99 is out of range for seed_groups (only 1 element)
            mock_exec.return_value = AttackExecutorResult(
                completed_results=[attack_result],
                incomplete_objectives=[],
                input_indices=[99],
            )
            result = await atomic.run_async()

        # Should not be enriched (index out of range), so the identifier
        # should still lack seed info (seeds remains empty)
        enriched = result.completed_results[0]
        assert enriched.atomic_attack_identifier is not None
        seeds = enriched.atomic_attack_identifier.children.get("seeds", [])
        assert seeds == [], "Expected no seeds since index was out of range"

    @pytest.mark.asyncio
    async def test_enrichment_includes_all_seeds(self, mock_attack):
        """Test that all seeds (general and non-general) appear in the enriched identifier."""
        seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj1"),
                    SeedPrompt(value="technique", is_general_technique=True, value_sha256="tech_hash"),
                    SeedPrompt(value="non_technique", is_general_technique=False, value_sha256="other_hash"),
                ]
            ),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results([attack_result])
            result = await atomic.run_async()

        enriched = result.completed_results[0].atomic_attack_identifier
        assert enriched is not None
        seed_ids = enriched.children["seeds"]
        # All three seeds (objective + technique + non_technique) should be present
        assert len(seed_ids) == 3
        sha_values = [s.params.get("value_sha256") for s in seed_ids]
        assert "tech_hash" in sha_values
        assert "other_hash" in sha_values

    @pytest.mark.asyncio
    async def test_enrichment_maps_multiple_results_to_correct_seed_groups(self, mock_attack):
        """Test that multiple results are correctly mapped to their corresponding seed groups."""
        seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj1"),
                    SeedPrompt(value="tech_a", is_general_technique=True, value_sha256="hash_a"),
                ]
            ),
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj2"),
                    SeedPrompt(value="tech_b", is_general_technique=True, value_sha256="hash_b"),
                ]
            ),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        results = [
            AttackResult(
                conversation_id="c1",
                objective="obj1",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
                atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
            ),
            AttackResult(
                conversation_id="c2",
                objective="obj2",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
                atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
            ),
        ]

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(results)
            result = await atomic.run_async()

        # First result should have hash_a seed
        enriched_0 = result.completed_results[0].atomic_attack_identifier
        seed_sha_values_0 = [s.params.get("value_sha256") for s in enriched_0.children["seeds"]]
        assert "hash_a" in seed_sha_values_0

        # Second result should have hash_b seed
        enriched_1 = result.completed_results[1].atomic_attack_identifier
        seed_sha_values_1 = [s.params.get("value_sha256") for s in enriched_1.children["seeds"]]
        assert "hash_b" in seed_sha_values_1

    @pytest.mark.asyncio
    async def test_enrichment_persists_to_db(self, mock_attack):
        """Test that enrichment persists the updated atomic_attack_identifier to the database."""
        seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj1"),
                    SeedPrompt(value="technique1", is_general_technique=True),
                ]
            ),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            attack_result_id="00000000-0000-0000-0000-000000000001",
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results([attack_result])

            mock_memory = MagicMock()
            mock_memory.update_attack_result_by_id.return_value = True
            with patch("pyrit.scenario.core.atomic_attack.CentralMemory") as mock_cm:
                mock_cm.get_memory_instance.return_value = mock_memory
                await atomic.run_async()

        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args.kwargs
        assert call_kwargs["attack_result_id"] == "00000000-0000-0000-0000-000000000001"
        assert "atomic_attack_identifier" in call_kwargs["update_fields"]
        # The persisted dict should have the AtomicAttack shape
        persisted = call_kwargs["update_fields"]["atomic_attack_identifier"]
        assert persisted["class_name"] == "AtomicAttack"

    @pytest.mark.asyncio
    async def test_enrichment_skips_db_update_when_no_attack_result_id(self, mock_attack):
        """Test that enrichment does not attempt a DB update when attack_result_id is None."""
        seed_groups = [
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="obj1"),
                    SeedPrompt(value="technique1", is_general_technique=True),
                ]
            ),
        ]
        attack_id = ComponentIdentifier(class_name="MockAttack", class_module="test.mock")
        attack_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
            attack_result_id=None,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=attack_id),
        )

        atomic = AtomicAttack(attack=mock_attack, seed_groups=seed_groups, atomic_attack_name="test")

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results([attack_result])

            mock_memory = MagicMock()
            with patch("pyrit.scenario.core.atomic_attack.CentralMemory") as mock_cm:
                mock_cm.get_memory_instance.return_value = mock_memory
                await atomic.run_async()

        mock_memory.update_attack_result_by_id.assert_not_called()
