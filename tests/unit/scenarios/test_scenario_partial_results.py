# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Additional tests for Scenario retry with AttackExecutorResult functionality."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult
from pyrit.scenario import DatasetConfiguration, ScenarioResult
from pyrit.scenario.core import AtomicAttack, Scenario, ScenarioStrategy


def _mock_scorer_id(name: str = "MockScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test",
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    target = MagicMock()
    target.get_identifier.return_value = {"__type__": "MockTarget", "__module__": "test"}
    return target


def save_attack_results_to_memory(attack_results):
    """Helper function to save attack results to memory."""
    memory = CentralMemory.get_memory_instance()
    memory.add_attack_results_to_memory(attack_results=attack_results)


def create_mock_atomic_attack(name: str, objectives: list[str]) -> MagicMock:
    """Create a mock AtomicAttack with required attributes for baseline creation.

    The mock tracks its objectives and properly updates when filter_seed_groups_by_objectives is called.
    """
    mock_attack_strategy = MagicMock()
    mock_attack_strategy.get_objective_target.return_value = MagicMock()
    mock_attack_strategy.get_attack_scoring_config.return_value = MagicMock()

    attack = MagicMock(spec=AtomicAttack)
    attack.atomic_attack_name = name
    attack._attack = mock_attack_strategy

    # Track current objectives in a mutable container so it can be updated
    current_objectives = {"value": list(objectives)}

    # Configure objectives property to return current objectives
    type(attack).objectives = PropertyMock(side_effect=lambda: current_objectives["value"])

    # Configure filter_seed_groups_by_objectives to update the tracked objectives
    def filter_objectives(*, remaining_objectives):
        remaining_set = set(remaining_objectives)
        current_objectives["value"] = [obj for obj in current_objectives["value"] if obj in remaining_set]

    attack.filter_seed_groups_by_objectives = MagicMock(side_effect=filter_objectives)

    return attack


class ConcreteScenario(Scenario):
    """Concrete implementation of Scenario for testing."""

    def __init__(self, *, atomic_attacks_to_return=None, objective_scorer=None, **kwargs):
        # Default include_default_baseline=False for tests unless explicitly specified
        kwargs.setdefault("include_default_baseline", False)

        # Get strategy_class from kwargs or use default
        strategy_class = kwargs.pop("strategy_class", None) or self.get_strategy_class()

        # Create a default mock scorer if not provided
        if objective_scorer is None:
            objective_scorer = MagicMock()
            objective_scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")

        super().__init__(strategy_class=strategy_class, objective_scorer=objective_scorer, **kwargs)
        self._test_atomic_attacks = atomic_attacks_to_return or []

    async def _get_atomic_attacks_async(self):
        return self._test_atomic_attacks

    @classmethod
    def get_strategy_class(cls):
        class TestStrategy(ScenarioStrategy):
            CONCRETE = ("concrete", {"concrete"})
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        return TestStrategy

    @classmethod
    def get_default_strategy(cls):
        return cls.get_strategy_class().ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """Return the default dataset configuration for testing."""
        return DatasetConfiguration()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.asyncio
class TestScenarioPartialAttackCompletion:
    """Tests for Scenario handling AttackExecutorResult from atomic attacks."""

    async def test_atomic_attack_returns_partial_result_with_incomplete_objectives(self, mock_objective_target):
        """Test that scenario handles AttackExecutorResult with incomplete objectives properly."""
        # Create atomic attack that returns partial results
        atomic_attack = create_mock_atomic_attack("partial_attack", ["obj1", "obj2", "obj3"])

        # First call returns partial results (2 completed, 1 incomplete)
        # Second call completes the remaining objective
        call_count = [0]

        async def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: complete 2, fail 1
                completed = [
                    AttackResult(
                        conversation_id=f"conv-{i}",
                        objective=f"obj{i}",
                        attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": str(i)},
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for i in [1, 2]
                ]
                incomplete = [("obj3", ValueError("Failed to complete obj3"))]

                # Save completed results to memory
                save_attack_results_to_memory(completed)

                return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
            else:
                # Retry: complete the remaining objective
                completed = [
                    AttackResult(
                        conversation_id="conv-3",
                        objective="obj3",
                        attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": "3"},
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                ]
                save_attack_results_to_memory(completed)
                return AttackExecutorResult(completed_results=completed, incomplete_objectives=[])

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=1,
        )

        result = await scenario.run_async()

        # Verify scenario succeeded after retry
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2  # Called twice

        # All 3 results should be saved
        assert len(result.attack_results["partial_attack"]) == 3
        objectives_completed = [r.objective for r in result.attack_results["partial_attack"]]
        assert "obj1" in objectives_completed
        assert "obj2" in objectives_completed
        assert "obj3" in objectives_completed

    async def test_scenario_saves_partial_results_before_failure(self, mock_objective_target):
        """Test that scenario saves partial results even when attack fails."""
        atomic_attack = create_mock_atomic_attack("partial_save_attack", ["obj1", "obj2", "obj3", "obj4"])

        async def mock_run(*args, **kwargs):
            # Return partial results with incomplete objectives
            completed = [
                AttackResult(
                    conversation_id=f"conv-{i}",
                    objective=f"obj{i}",
                    attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": str(i)},
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=1,
                )
                for i in [1, 2]
            ]
            incomplete = [("obj3", RuntimeError("Failed obj3")), ("obj4", RuntimeError("Failed obj4"))]

            # Save completed results to memory
            save_attack_results_to_memory(completed)

            return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=0,  # No retries
        )

        # Should raise error because of incomplete objectives
        with pytest.raises(ValueError, match="incomplete"):
            await scenario.run_async()

        # But the 2 completed results should still be saved
        scenario_results = CentralMemory.get_memory_instance().get_scenario_results(
            scenario_result_ids=[scenario._scenario_result_id]
        )
        assert len(scenario_results) == 1
        saved_results = scenario_results[0].attack_results["partial_save_attack"]
        assert len(saved_results) == 2
        assert saved_results[0].objective == "obj1"
        assert saved_results[1].objective == "obj2"

    async def test_scenario_resumes_with_only_incomplete_objectives(self, mock_objective_target):
        """Test that on retry, scenario only passes incomplete objectives to atomic attack."""
        atomic_attack = create_mock_atomic_attack("resume_attack", ["obj1", "obj2", "obj3", "obj4", "obj5"])

        executed_objectives = []
        call_count = [0]

        async def mock_run(*args, **kwargs):
            call_count[0] += 1

            # Track which objectives are being executed
            current_objectives = atomic_attack.objectives.copy()
            executed_objectives.append(current_objectives)

            if call_count[0] == 1:
                # First attempt: complete first 3, fail last 2
                completed = [
                    AttackResult(
                        conversation_id=f"conv-{i}",
                        objective=f"obj{i}",
                        attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": str(i)},
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for i in [1, 2, 3]
                ]
                incomplete = [("obj4", Exception("Failed obj4")), ("obj5", Exception("Failed obj5"))]

                save_attack_results_to_memory(completed)

                return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
            else:
                # Retry: complete remaining objectives
                completed = [
                    AttackResult(
                        conversation_id=f"conv-{i}",
                        objective=f"obj{i}",
                        attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": str(i)},
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for i in [4, 5]
                ]

                save_attack_results_to_memory(completed)

                return AttackExecutorResult(completed_results=completed, incomplete_objectives=[])

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=1,
        )

        result = await scenario.run_async()

        # Verify scenario succeeded
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2

        # Verify first attempt had all 5 objectives
        assert len(executed_objectives[0]) == 5

        # Verify retry only had the 2 incomplete objectives
        assert len(executed_objectives[1]) == 2
        assert "obj4" in executed_objectives[1]
        assert "obj5" in executed_objectives[1]
        assert "obj1" not in executed_objectives[1]  # Should not retry completed ones

        # All 5 results should be in final scenario result
        assert len(result.attack_results["resume_attack"]) == 5

    async def test_multiple_atomic_attacks_with_partial_results(self, mock_objective_target):
        """Test scenario with multiple atomic attacks that return partial results."""
        # Create 3 atomic attacks
        attack1 = create_mock_atomic_attack("attack_1", ["a1_obj1", "a1_obj2"])
        attack2 = create_mock_atomic_attack("attack_2", ["a2_obj1", "a2_obj2", "a2_obj3"])
        attack3 = create_mock_atomic_attack("attack_3", ["a3_obj1"])

        call_counts = {"attack_1": 0, "attack_2": 0, "attack_3": 0}

        async def make_mock_run(attack_name, objectives):
            async def mock_run(*args, **kwargs):
                call_counts[attack_name] += 1

                if attack_name == "attack_2" and call_counts[attack_name] == 1:
                    # Attack 2 fails partially on first attempt
                    completed = [
                        AttackResult(
                            conversation_id="conv-a2-1",
                            objective="a2_obj1",
                            attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": "a2_1"},
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                        )
                    ]
                    incomplete = [("a2_obj2", Exception("Failed a2_obj2")), ("a2_obj3", Exception("Failed a2_obj3"))]

                    save_attack_results_to_memory(completed)

                    return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
                else:
                    # All other attempts succeed fully
                    completed = [
                        AttackResult(
                            conversation_id=f"conv-{obj}",
                            objective=obj,
                            attack_identifier={"__type__": "TestAttack", "__module__": "test", "id": obj},
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                        )
                        for obj in (
                            attack1
                            if attack_name == "attack_1"
                            else (attack2 if attack_name == "attack_2" else attack3)
                        ).objectives
                    ]

                    save_attack_results_to_memory(completed)

                    return AttackExecutorResult(completed_results=completed, incomplete_objectives=[])

            return mock_run

        attack1.run_async = await make_mock_run("attack_1", attack1.objectives)
        attack2.run_async = await make_mock_run("attack_2", attack2.objectives)
        attack3.run_async = await make_mock_run("attack_3", attack3.objectives)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[attack1, attack2, attack3],
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=1,
        )

        result = await scenario.run_async()

        # Verify scenario succeeded after retry
        assert isinstance(result, ScenarioResult)

        # Attack 1 should run once (succeeds)
        assert call_counts["attack_1"] == 1
        # Attack 2 should run twice (fails partially, then succeeds)
        assert call_counts["attack_2"] == 2
        # Attack 3 should run once (after attack 2 succeeds on retry)
        assert call_counts["attack_3"] == 1

        # All results should be present
        assert len(result.attack_results["attack_1"]) == 2
        assert len(result.attack_results["attack_2"]) == 3
        assert len(result.attack_results["attack_3"]) == 1
