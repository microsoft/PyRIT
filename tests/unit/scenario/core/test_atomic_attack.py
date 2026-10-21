# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenarios.AtomicAttack class."""

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import AttackExecutor, AttackStrategy, PromptSendingAttack
from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    ComponentIdentifier,
    ScoringExpectation,
    SeedGroup,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
    TargetIdentifier,
)
from pyrit.prompt_target import CapabilityName, TargetRequirements
from pyrit.scenario import AtomicAttack, IncompatibleTechniqueError, TechniqueRequirements
from pyrit.scenario.core.attack_technique import AttackTechnique
from tests.unit.mocks import MockPromptTarget


@pytest.fixture
def mock_attack():
    """Create a mock AttackStrategy for testing."""
    attack = MagicMock(spec=AttackStrategy)
    attack.get_identifier.return_value = ComponentIdentifier(class_name="MockAttack", class_module="pyrit.test")
    return attack


@pytest.fixture
def sample_seed_groups():
    """Create sample seed groups with objectives for testing."""
    return [
        AttackSeedGroup(
            seeds=[
                SeedObjective(value="objective1"),
                SeedPrompt(value="prompt1"),
            ]
        ),
        AttackSeedGroup(
            seeds=[
                SeedObjective(value="objective2"),
                SeedPrompt(value="prompt2"),
            ]
        ),
        AttackSeedGroup(
            seeds=[
                SeedObjective(value="objective3"),
                SeedPrompt(value="prompt3"),
            ]
        ),
    ]


@pytest.fixture
def sample_seed_groups_without_objectives():
    """Create sample seed groups without objectives for testing.

    Note: AttackSeedGroup now validates exactly one objective at construction,
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
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._attack_technique.attack == mock_attack
        assert atomic_attack._seed_groups == sample_seed_groups
        assert atomic_attack._memory_labels == {}
        assert atomic_attack._attack_execute_params == {}

    def test_direct_atomic_rejects_dataset_and_full_merge_mismatches(self) -> None:
        attack = PromptSendingAttack(objective_target=MockPromptTarget())
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])
        strict = AttackTechnique(
            attack=attack,
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True)),
        )
        with pytest.raises(IncompatibleTechniqueError, match="only an objective"):
            AtomicAttack(atomic_attack_name="strict", attack_technique=strict, seed_groups=[source])
        conflict = AttackTechnique(
            attack=attack,
            seed_technique=AttackTechniqueSeedGroup(
                seeds=[SeedPrompt(value="system", role="system", is_general_technique=True)]
            ),
        )
        with pytest.raises(IncompatibleTechniqueError, match="cannot be composed"):
            AtomicAttack(atomic_attack_name="conflict", attack_technique=conflict, seed_groups=[source])

    def test_direct_atomic_checks_declared_target_requirements(self) -> None:
        technique = AttackTechnique(
            attack=PromptSendingAttack(objective_target=MockPromptTarget()),
            requirements=TechniqueRequirements(
                objective_target=TargetRequirements(native_required=frozenset({CapabilityName.JSON_OUTPUT}))
            ),
        )
        with pytest.raises(IncompatibleTechniqueError, match="json_output"):
            AtomicAttack(
                atomic_attack_name="native_json",
                attack_technique=technique,
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="objective")])],
            )

    def test_init_with_memory_labels(self, mock_attack, sample_seed_groups):
        """Test initialization with memory labels."""
        memory_labels = {"test": "label", "category": "attack"}

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._memory_labels == memory_labels

    def test_init_with_attack_execute_params(self, mock_attack, sample_seed_groups):
        """Test initialization with additional attack execute parameters."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            memory_labels=memory_labels,
            batch_size=10,
            timeout=30,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack._attack_technique.attack == mock_attack
        assert atomic_attack._seed_groups == sample_seed_groups
        assert atomic_attack._memory_labels == memory_labels
        assert atomic_attack._attack_execute_params["batch_size"] == 10
        assert atomic_attack._attack_execute_params["timeout"] == 30

    def test_init_fails_with_empty_seed_groups(self, mock_attack):
        """Test that initialization fails when seed_groups list is empty."""
        with pytest.raises(ValueError, match="seed_groups list cannot be empty"):
            AtomicAttack(
                attack_technique=AttackTechnique(attack=mock_attack),
                seed_groups=[],
                atomic_attack_name="Test Attack Run",
            )

    def test_init_fails_with_seed_group_missing_objective(self, mock_attack):
        """Test that AttackSeedGroup without objective cannot be created.

        AttackSeedGroup now validates exactly one objective at construction time,
        so we can't even create one without an objective.
        """
        # AttackSeedGroup now validates exactly one objective at construction
        with pytest.raises(ValueError, match="must have exactly one objective"):
            AttackSeedGroup(seeds=[SeedPrompt(value="prompt1")])

    def test_objectives_property_returns_values_from_seed_groups(self, mock_attack, sample_seed_groups):
        """Test that the objectives property returns values from seed groups."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        assert atomic_attack.objectives == ["objective1", "objective2", "objective3"]

    def test_seed_groups_property_returns_copy(self, mock_attack, sample_seed_groups):
        """Test that the seed_groups property returns a copy."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        returned_groups = atomic_attack.seed_groups
        assert returned_groups == sample_seed_groups
        assert returned_groups is not atomic_attack._seed_groups


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackExecution:
    """Tests for AtomicAttack execution methods."""

    async def test_run_async_preserves_incomplete_result_ids(
        self, mock_attack: AttackStrategy, sample_seed_groups: list[AttackSeedGroup]
    ) -> None:
        error = RuntimeError("execution failed")
        executor_result = AttackExecutorResult(
            completed_results=[],
            incomplete_objectives=[("objective1", error), ("objective2", error)],
            incomplete_result_ids=["confirmed-result-id", None],
        )
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )

        with patch.object(
            AttackExecutor,
            "execute_attack_from_seed_groups_async",
            new_callable=AsyncMock,
            return_value=executor_result,
        ):
            result = await atomic_attack.run_async()

        assert result.incomplete_objectives == executor_result.incomplete_objectives
        assert result.incomplete_result_ids == executor_result.incomplete_result_ids

    async def test_run_async_with_valid_atomic_attack(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test successful execution of an atomic attack."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_with_default_concurrency(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that default concurrency (1) is used when not specified."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_with_injected_executor_reuses_it(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """When an executor is passed, AtomicAttack must reuse it rather than build a new one."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        injected = AttackExecutor(max_concurrency=7)
        with (
            patch.object(AttackExecutor, "__init__", return_value=None) as mock_init,
            patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async(executor=injected)

            # __init__ must not be called again — the injected executor is reused as-is.
            mock_init.assert_not_called()

    async def test_run_async_passes_memory_labels(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that memory labels are passed to the executor."""
        memory_labels = {"test": "attack_run", "category": "attack"}

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_passes_seed_groups(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that seed_groups are passed to the executor."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert "seed_groups" in call_kwargs
            assert call_kwargs["seed_groups"] == sample_seed_groups

    async def test_run_async_raises_when_executor_returns_non_attack_result(self, mock_attack, sample_seed_groups):
        """Test that a non-AttackResult item from the executor raises ValueError."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(["not-an-attack-result"])

            with pytest.raises(ValueError, match="unsupported result type"):
                await atomic_attack.run_async()

    async def test_run_async_passes_attack_execute_params(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that attack execute parameters are passed to the executor."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    @pytest.mark.parametrize("override", [ScoringExpectation(objective="execution criterion"), None])
    async def test_run_async_overrides_expectation_without_changing_defaults(
        self, mock_attack, sample_seed_groups, sample_attack_results, override
    ):
        default = ScoringExpectation(objective="default criterion")
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            expectation=default,
            max_retries=3,
            atomic_attack_name="expectation transport",
        )
        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as execute:
            execute.return_value = wrap_results(sample_attack_results)
            await atomic.run_async(expectation=override, max_retries=7)
            assert execute.call_args.kwargs["expectation"] is override
            assert execute.call_args.kwargs["max_retries"] == 7
            await atomic.run_async()
            assert execute.call_args.kwargs["expectation"] is default
            assert execute.call_args.kwargs["max_retries"] == 3
        assert atomic._attack_execute_params == {"expectation": default, "max_retries": 3}

    @pytest.mark.parametrize(
        "labels", [None, {}, {"new": "run", "shared": "override"}], ids=["none", "empty", "override"]
    )
    async def test_run_async_merges_labels_without_changing_defaults(
        self, mock_attack, sample_seed_groups, sample_attack_results, labels
    ):
        defaults = {"scenario": "campaign", "shared": "default"}
        original_labels = dict(labels) if labels is not None else None
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            memory_labels=defaults,
            atomic_attack_name="label merge",
        )
        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as execute:
            execute.return_value = wrap_results(sample_attack_results)
            await atomic.run_async(memory_labels=labels)
            merged = execute.call_args.kwargs["memory_labels"]
            assert merged == {**defaults, **(labels or {})}
            assert merged is not defaults and merged is not labels
            await atomic.run_async()
            assert execute.call_args.kwargs["memory_labels"] == defaults
        assert defaults == atomic._memory_labels == {"scenario": "campaign", "shared": "default"}
        assert labels == original_labels

    @pytest.mark.parametrize(
        "reserved", ["attack", "seed_groups", "adversarial_chat", "objective_scorer", "attribution", "attributions"]
    )
    async def test_run_async_rejects_owned_executor_arguments(self, mock_attack, sample_seed_groups, reserved):
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="reserved arguments",
        )
        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as execute:
            with pytest.raises(ValueError, match="owns these executor arguments"):
                await atomic.run_async(**{reserved: None})
            execute.assert_not_called()

    async def test_run_async_merges_all_parameters(self, mock_attack, sample_seed_groups, sample_attack_results):
        """Test that all parameters are merged and passed correctly."""
        memory_labels = {"test": "merge"}

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_handles_execution_failure(self, mock_attack, sample_seed_groups):
        """Test that execution failures are properly handled and raised."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = Exception("Execution error")

            with pytest.raises(ValueError, match="Failed to execute atomic attack 'Test Attack Run'"):
                await atomic_attack.run_async()

    async def test_run_async_passes_return_partial_on_failure_true_by_default(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """Test that atomic attack passes return_partial_on_failure=True by default."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="Test Attack Run",
        )

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)

            await atomic_attack.run_async()

            call_kwargs = mock_exec.call_args.kwargs
            assert "return_partial_on_failure" in call_kwargs
            assert call_kwargs["return_partial_on_failure"] is True

    async def test_run_async_respects_explicit_return_partial_on_failure(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """Test that explicit return_partial_on_failure parameter is passed through."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_full_attack_run_execution_flow(self, mock_attack, sample_seed_groups):
        """Test the complete attack run execution flow end-to-end."""
        memory_labels = {"test": "integration", "attack_run": "full"}

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

            attack_run_result = await atomic_attack.run_async()

            assert len(attack_run_result.completed_results) == 3
            for i, result in enumerate(attack_run_result.completed_results):
                assert result.objective == f"objective{i + 1}"
                assert result.outcome == AttackOutcome.SUCCESS

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["attack"] == mock_attack
            assert call_kwargs["seed_groups"] == sample_seed_groups
            assert call_kwargs["memory_labels"] == memory_labels
            assert call_kwargs["batch_size"] == 2

    async def test_atomic_attack_with_single_seed_group(self, mock_attack):
        """Test atomic attack with a single seed group."""
        single_seed_group = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value="single_objective"),
                    SeedPrompt(value="single_prompt"),
                ]
            )
        ]

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_atomic_attack_with_many_seed_groups(self, mock_attack):
        """Test atomic attack with many seed groups."""
        many_seed_groups = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value=f"objective_{i}"),
                    SeedPrompt(value=f"prompt_{i}"),
                ]
            )
            for i in range(20)
        ]

        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_only_passes_valid_executor_params(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """
        Test that run_async doesn't pass parameters that the executor doesn't accept.
        The executor has strict_param_matching so invalid params would cause failures.
        """
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value="multi_turn_objective_1"),
                    SeedPrompt(value="First message", data_type="text", sequence=0, role="user"),
                    SeedPrompt(value="Second message", data_type="text", sequence=1, role="user"),
                    SeedPrompt(value="Third message", data_type="text", sequence=2, role="user"),
                ]
            ),
            AttackSeedGroup(
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
            AttackSeedGroup(seeds=[SeedObjective(value="simple_objective")]),
            # With messages - roles required for multi-sequence
            AttackSeedGroup(
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
            attack_technique=AttackTechnique(attack=mock_attack),
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
            attack_technique=AttackTechnique(attack=mock_attack),
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

    async def test_run_async_passes_seed_groups_with_messages(self, mock_attack, seed_groups_with_messages):
        """Test that run_async correctly passes seed groups with messages to executor."""
        atomic_attack = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
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
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=mixed_seed_groups,
            atomic_attack_name="Mixed Attack",
        )

        assert len(atomic_attack.seed_groups) == 2

        # First has no user_messages (empty list)
        assert len(atomic_attack.seed_groups[0].user_messages) == 0

        # Second has user_messages
        assert len(atomic_attack.seed_groups[1].user_messages) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackResultRecording:
    async def test_injected_executor_receives_complete_source_identifiers_async(
        self, *, mock_attack: MagicMock
    ) -> None:
        sources = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value=value),
                    SeedPrompt(value="technique", is_general_technique=True, value_sha256=f"general-{value}"),
                    SeedPrompt(value="context", value_sha256=f"context-{value}"),
                ]
            )
            for value in ["first", "second"]
        ]
        atomic = AtomicAttack(
            atomic_attack_name="recording",
            attack_technique=AttackTechnique(
                attack=mock_attack,
                seed_technique=AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format."),
            ),
            seed_groups=sources,
        )
        executor = MagicMock(spec=AttackExecutor)
        executor.execute_attack_from_seed_groups_async.return_value = AttackExecutorResult(
            completed_results=[], incomplete_objectives=[]
        )

        await atomic.run_async(executor=executor)

        kwargs = executor.execute_attack_from_seed_groups_async.call_args.kwargs
        identifiers = kwargs["atomic_attack_identifiers"]
        assert len(identifiers) == len(sources)
        assert kwargs["result_metadata"] == [{}, {}]
        for identifier, source in zip(identifiers, sources, strict=True):
            assert identifier.logical_seed_group_id == source.logical_id
            assert identifier.eval_hash == atomic.technique_eval_hash
            assert identifier.attack_technique is not None
            assert len(identifier.seed_identifiers) == 3
            hashes = {seed.params.get("value_sha256") for seed in identifier.seed_identifiers}
            assert f"general-{source.objective.value}" in hashes
            assert f"context-{source.objective.value}" in hashes

    async def test_source_identifiers_are_recorded_before_the_only_write_async(
        self, *, sqlite_instance: SQLiteMemory
    ) -> None:
        target = MockPromptTarget()
        sources = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value=value),
                    SeedPrompt(value="technique", is_general_technique=True, value_sha256=f"general-{value}"),
                    SeedPrompt(value="context", value_sha256=f"context-{value}"),
                ]
            )
            for value in ["first", "second"]
        ]
        atomic = AtomicAttack(
            atomic_attack_name="recording",
            attack_technique=AttackTechnique(
                attack=PromptSendingAttack(objective_target=target),
                seed_technique=AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format."),
            ),
            seed_groups=sources,
        )
        with (
            patch.object(
                sqlite_instance,
                "add_attack_results_to_memory_async",
                wraps=sqlite_instance.add_attack_results_to_memory_async,
            ) as add_results,
            patch.object(sqlite_instance, "update_attack_result_by_id_async", new_callable=AsyncMock) as update_result,
        ):
            returned = await atomic.run_async()
            assert not returned.has_incomplete
            assert add_results.await_count == len(sources)
            update_result.assert_not_awaited()
            for call in add_results.call_args_list:
                identifier = AtomicAttackIdentifier.from_component_identifier(
                    call.kwargs["attack_results"][0].atomic_attack_identifier
                )
                assert len(identifier.seed_identifiers) == 3

        stored = await sqlite_instance.get_attack_results_async()
        assert len(stored) == len(sources)
        source_by_objective = {source.objective.value: source for source in sources}
        for result in [*returned.completed_results, *stored]:
            source = source_by_objective[result.objective]
            identifier = AtomicAttackIdentifier.from_component_identifier(result.atomic_attack_identifier)
            assert identifier.logical_seed_group_id == source.logical_id
            assert identifier.eval_hash == atomic.technique_eval_hash
            assert len(identifier.seed_identifiers) == 3


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackFilterSeedGroupsByCompletedHashes:
    """Tests for ``drop_seed_groups_with_hashes`` — the hash-based
    resume filter."""

    def test_filters_out_completed_hashes(self, mock_attack, sample_seed_groups):
        from pyrit.common.utils import to_sha256

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )
        completed = {to_sha256("objective1"), to_sha256("objective3")}
        atomic.drop_seed_groups_with_hashes(hashes=completed)

        assert atomic.seed_groups == [sample_seed_groups[1]]

    def test_empty_completed_hashes_is_noop(self, mock_attack, sample_seed_groups):
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )

        atomic.drop_seed_groups_with_hashes(hashes=set())

        assert atomic.seed_groups == sample_seed_groups

    def test_all_hashes_completed_clears_seed_groups(self, mock_attack, sample_seed_groups):
        from pyrit.common.utils import to_sha256

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )

        atomic.drop_seed_groups_with_hashes(hashes={to_sha256(f"objective{i}") for i in range(1, 4)})

        assert atomic.seed_groups == []

    def test_filter_is_stable_across_resampling(self, mock_attack, sample_seed_groups):
        """Identity is content-derived, so reordering ``_seed_groups`` between
        two calls (e.g. a fresh ``random.sample``) doesn't break the filter."""
        from pyrit.common.utils import to_sha256

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )
        # Simulate a re-sample by reversing the internal list.
        atomic._seed_groups = list(reversed(atomic._seed_groups))

        atomic.drop_seed_groups_with_hashes(hashes={to_sha256("objective1")})
        kept_objectives = [sg.objective.value for sg in atomic.seed_groups]
        assert "objective1" not in kept_objectives
        assert set(kept_objectives) == {"objective2", "objective3"}


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackRestrictSeedGroupsToHashes:
    """Tests for ``keep_seed_groups_with_hashes`` — the keep-set inverse used
    on resume to replay the originally-sampled subset."""

    def test_keeps_only_listed_hashes(self, mock_attack, sample_seed_groups):
        from pyrit.common.utils import to_sha256

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )
        keep = {to_sha256("objective1"), to_sha256("objective3")}
        retained = atomic.keep_seed_groups_with_hashes(hashes=keep)

        assert {sg.objective.value for sg in atomic.seed_groups} == {"objective1", "objective3"}
        assert retained == keep

    def test_retained_set_excludes_missing_hashes(self, mock_attack, sample_seed_groups):
        from pyrit.common.utils import to_sha256

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )
        keep = {to_sha256("objective1"), to_sha256("not-in-dataset")}
        retained = atomic.keep_seed_groups_with_hashes(hashes=keep)

        assert {sg.objective.value for sg in atomic.seed_groups} == {"objective1"}
        assert retained == {to_sha256("objective1")}


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackDuplicateObjectiveValidation:
    """``AtomicAttack.__init__`` enforces objective-hash uniqueness within a
    single atomic attack so resume can use the hash as a stable identity."""

    def test_constructing_with_duplicate_objective_raises(self, mock_attack):
        duplicate_groups = [
            AttackSeedGroup(seeds=[SeedObjective(value="same-objective")]),
            AttackSeedGroup(seeds=[SeedObjective(value="same-objective")]),
        ]
        with pytest.raises(ValueError, match="duplicate objective hash"):
            AtomicAttack(
                attack_technique=AttackTechnique(attack=mock_attack),
                seed_groups=duplicate_groups,
                atomic_attack_name="dup",
            )

    def test_constructing_with_unique_objectives_succeeds(self, mock_attack, sample_seed_groups):
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="ok",
        )
        assert len(atomic.seed_groups) == 3


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackAttributionStamping:
    """Tests for how ``run_async`` builds the ``AttackResultAttribution`` it
    passes to the executor."""

    async def test_no_attribution_when_scenario_result_id_unset(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """Outside a Scenario, ``_scenario_result_id`` is None and the
        executor must receive ``attributions=None``."""
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="test",
        )
        assert atomic._scenario_result_id is None

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)
            await atomic.run_async()

        assert mock_exec.call_args.kwargs["attributions"] is None

    async def test_attribution_built_when_scenario_result_id_set(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """When the Scenario stamps ``_scenario_result_id`` onto the atomic
        attack, ``run_async`` must build and pass per-seed-group attribution."""
        from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution

        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="MyAtomicAttack",
        )
        atomic._scenario_result_id = "00000000-0000-0000-0000-000000000abc"

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)
            await atomic.run_async()

        attributions = mock_exec.call_args.kwargs["attributions"]
        assert len(attributions) == len(sample_seed_groups)
        assert all(isinstance(attribution, AttackResultAttribution) for attribution in attributions)
        assert all(attribution.parent_id == "00000000-0000-0000-0000-000000000abc" for attribution in attributions)
        assert all(attribution.parent_collection == "MyAtomicAttack" for attribution in attributions)
        assert [attribution.seed_group_id for attribution in attributions] == [
            seed_group.logical_id for seed_group in sample_seed_groups
        ]

    async def test_attribution_includes_technique_eval_hash(
        self, mock_attack, sample_seed_groups, sample_attack_results
    ):
        """The stamped attribution must carry ``parent_eval_hash`` equal to
        ``technique_eval_hash`` so resume disambiguates between two atomic
        attacks that share a name but use different techniques."""
        atomic = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="MyAtomicAttack",
        )
        atomic._scenario_result_id = "00000000-0000-0000-0000-000000000abc"

        with patch.object(AttackExecutor, "execute_attack_from_seed_groups_async", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = wrap_results(sample_attack_results)
            await atomic.run_async()

        attributions = mock_exec.call_args.kwargs["attributions"]
        assert all(attribution.parent_eval_hash is not None for attribution in attributions)
        assert all(attribution.parent_eval_hash == atomic.technique_eval_hash for attribution in attributions)


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackTechniqueEvalHash:
    """``technique_eval_hash`` must be stable across seed groups and differ
    between distinct technique configurations — it's the resume bucket key."""

    def test_hash_is_independent_of_seed_groups(self, mock_attack, sample_seed_groups):
        a1 = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=sample_seed_groups,
            atomic_attack_name="same",
        )
        a2 = AtomicAttack(
            attack_technique=AttackTechnique(attack=mock_attack),
            seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="different-objective")])],
            atomic_attack_name="same",
        )
        assert a1.technique_eval_hash == a2.technique_eval_hash

    def test_hash_differs_for_different_attacks(self, sample_seed_groups):
        attack_a = MagicMock(spec=AttackStrategy)
        attack_a.get_identifier.return_value = ComponentIdentifier(class_name="AttackA", class_module="pyrit.test")
        attack_b = MagicMock(spec=AttackStrategy)
        attack_b.get_identifier.return_value = ComponentIdentifier(class_name="AttackB", class_module="pyrit.test")

        a1 = AtomicAttack(
            attack_technique=AttackTechnique(attack=attack_a),
            seed_groups=sample_seed_groups,
            atomic_attack_name="same",
        )
        a2 = AtomicAttack(
            attack_technique=AttackTechnique(attack=attack_b),
            seed_groups=sample_seed_groups,
            atomic_attack_name="same",
        )
        assert a1.technique_eval_hash != a2.technique_eval_hash

    def test_hash_differs_for_different_adversarial_prompt_template(self, sample_seed_groups):
        """Two otherwise-identical adversarial attacks that differ only in their resolved
        per-turn adversarial_prompt_template must land in different resume buckets --
        otherwise resuming a scenario after only the follow-up prompt changed would
        silently reuse results generated under the old template."""
        adv_target = TargetIdentifier(class_name="AdvChat", class_module="pyrit.test")

        attack_a = MagicMock(spec=AttackStrategy)
        attack_a.get_identifier.return_value = AttackIdentifier(
            class_name="RedTeamingAttack",
            class_module="pyrit.test",
            adversarial_chat=adv_target,
            adversarial_prompt_template="A: {{ feedback_text }}",
        )
        attack_b = MagicMock(spec=AttackStrategy)
        attack_b.get_identifier.return_value = AttackIdentifier(
            class_name="RedTeamingAttack",
            class_module="pyrit.test",
            adversarial_chat=adv_target,
            adversarial_prompt_template="B: {{ feedback_text }}",
        )

        a1 = AtomicAttack(
            attack_technique=AttackTechnique(attack=attack_a),
            seed_groups=sample_seed_groups,
            atomic_attack_name="same",
        )
        a2 = AtomicAttack(
            attack_technique=AttackTechnique(attack=attack_b),
            seed_groups=sample_seed_groups,
            atomic_attack_name="same",
        )
        assert a1.technique_eval_hash != a2.technique_eval_hash
        assert a1.logical_group_id != a2.logical_group_id


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicAttackAdaptation:
    async def test_execution_copies_follow_resume_order_without_mutation_async(self, mock_attack: MagicMock) -> None:
        sources = [
            AttackSeedGroup(seeds=[SeedObjective(value=value), SeedPrompt(value=f"context-{value}")])
            for value in ["first", "second", "third"]
        ]
        originals = [group.model_dump() for group in sources]
        technique = AttackTechnique(
            attack=mock_attack,
            seed_technique=AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format."),
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)),
        )
        atomic = AtomicAttack(atomic_attack_name="adapted", attack_technique=technique, seed_groups=sources)
        atomic._seed_groups = [sources[2], sources[0]]
        executor = MagicMock(spec=AttackExecutor)
        seen: list[list[str]] = []

        async def execute_async(*, seed_groups: list[AttackSeedGroup], **_: Any) -> AttackExecutorResult[AttackResult]:
            seen.append([group.objective.value for group in seed_groups])
            assert [group.prompts[0].value for group in seed_groups] == [
                "Use the supplied format.",
                "Use the supplied format.",
            ]
            seed_groups[0].prompts[0].value = "execution mutation"
            return AttackExecutorResult(completed_results=[], incomplete_objectives=[])

        executor.execute_attack_from_seed_groups_async = AsyncMock(side_effect=execute_async)
        await atomic.run_async(executor=executor)
        await atomic.run_async(executor=executor)

        assert seen == [["third", "first"], ["third", "first"]]
        assert [group.model_dump() for group in sources] == originals
        assert atomic.seed_group_adaptations == {
            sources[2].logical_id: "objective_only",
            sources[0].logical_id: "objective_only",
        }
        assert [
            identifier.logical_seed_group_id
            for identifier in executor.execute_attack_from_seed_groups_async.call_args.kwargs[
                "atomic_attack_identifiers"
            ]
        ] == [sources[2].logical_id, sources[0].logical_id]

    @pytest.mark.parametrize("failure_mode", ["success", "partial", "strict"])
    async def test_adaptation_and_source_identity_persist_for_all_outcomes_async(
        self, *, failure_mode: str, sqlite_instance: SQLiteMemory
    ) -> None:
        target = MockPromptTarget()
        source = AttackSeedGroup(
            seeds=[SeedObjective(value="say hello"), SeedPrompt(value="dataset context", harm_categories=["context"])]
        )
        original = source.model_dump()
        atomic = AtomicAttack(
            atomic_attack_name="adapted",
            attack_technique=AttackTechnique(
                attack=PromptSendingAttack(objective_target=target),
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                ),
            ),
            seed_groups=[source],
        )
        with patch.object(sqlite_instance, "update_attack_result_by_id_async", new_callable=AsyncMock) as update_result:
            if failure_mode == "success":
                await atomic.run_async()
                assert target.prompt_sent == ["say hello"]
            else:
                with patch.object(
                    target, "_send_prompt_to_target_async", side_effect=RuntimeError("synthetic failure")
                ):
                    if failure_mode == "strict":
                        with pytest.raises(ValueError, match="synthetic failure"):
                            await atomic.run_async(return_partial_on_failure=False)
                    else:
                        results = await atomic.run_async()
                        assert results.has_incomplete
            update_result.assert_not_awaited()

        stored_results = await sqlite_instance.get_attack_results_async()
        assert len(stored_results) == 1
        result = stored_results[0]
        identifier = AtomicAttackIdentifier.from_component_identifier(result.atomic_attack_identifier)
        assert identifier.logical_seed_group_id == source.logical_id
        assert len(identifier.seed_identifiers) == 2
        assert identifier.eval_hash == atomic.technique_eval_hash
        assert result.metadata["seed_group_adaptation"] == {
            "source_seed_group_id": source.logical_id,
            "technique_eval_hash": atomic.technique_eval_hash,
            "adaptation": "objective_only",
        }
        assert result.targeted_harm_categories == ["context"]
        assert (result.outcome is AttackOutcome.ERROR) is (failure_mode != "success")
        assert source.model_dump() == original
