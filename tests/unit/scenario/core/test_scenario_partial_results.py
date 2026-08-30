# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Additional tests for Scenario retry with AttackExecutorResult functionality."""

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

try:
    from builtins import ExceptionGroup  # type: ignore[attr-defined,ty:unresolved-import]
except ImportError:  # pragma: no cover - 3.10 only
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef,ty:unresolved-import]

from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioRunState,
    SeedObjective,
    config_hash,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import DatasetConfiguration, ScenarioResult
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTarget",
        class_module="test",
    )
    return target


def save_attack_results_to_memory(attack_results, *, atomic_attack=None):
    """
    Helper function to save attack results to memory. When ``atomic_attack`` is
    provided, also stamps ``attribution_parent_id`` and ``attribution_data`` on
    each result the same way the real attack persistence path does — so
    foreign-key-based
    hydration in ``get_scenario_results`` finds them.
    """
    if atomic_attack is not None:
        sid = getattr(atomic_attack, "_scenario_result_id", None)
        name = getattr(atomic_attack, "atomic_attack_name", None)
        if sid and name:
            for r in attack_results:
                r.attribution_parent_id = sid
                r.attribution_data = {"parent_collection": name}
    memory = CentralMemory.get_memory_instance()
    memory.add_attack_results_to_memory(attack_results=attack_results)


def create_mock_atomic_attack(name: str, objectives: list[str]) -> MagicMock:
    """Create a mock AtomicAttack with required attributes for baseline creation.

    The mock tracks its objectives and properly updates when
    drop_seed_groups_with_hashes is called.
    """
    from pyrit.common.utils import to_sha256

    mock_attack_strategy = MagicMock()
    mock_attack_strategy.get_objective_target.return_value = MagicMock()
    mock_attack_strategy.get_attack_scoring_config.return_value = MagicMock()

    attack = MagicMock(spec=AtomicAttack)
    attack.atomic_attack_name = name
    attack.display_group = name
    attack.technique_eval_hash = config_hash({"name": name, "objectives": objectives})
    attack._attack = mock_attack_strategy
    attack._scenario_result_id = None

    def _set_scenario_result_id(scenario_result_id):
        attack._scenario_result_id = scenario_result_id

    attack.set_scenario_result_id = MagicMock(side_effect=_set_scenario_result_id)

    original_objectives = list(objectives)
    current_seed_groups = {
        "value": [AttackSeedGroup(seeds=[SeedObjective(value=objective)]) for objective in objectives]
    }

    type(attack).objectives = PropertyMock(
        side_effect=lambda: [seed_group.objective.value for seed_group in current_seed_groups["value"]]
    )
    type(attack).seed_groups = PropertyMock(side_effect=lambda: current_seed_groups["value"])

    def drop_hashes(*, hashes):
        current_seed_groups["value"] = [
            seed_group
            for seed_group in current_seed_groups["value"]
            if to_sha256(seed_group.objective.value) not in hashes
        ]

    attack.drop_seed_groups_with_hashes = MagicMock(side_effect=drop_hashes)
    attack._original_objectives = original_objectives

    return attack


class ConcreteScenario(Scenario):
    """Concrete implementation of Scenario for testing."""

    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(self, *, atomic_attacks_to_return=None, objective_scorer=None, **kwargs):
        technique_class = kwargs.pop("technique_class", None) or _build_test_technique()

        # Create a default mock scorer if not provided
        if objective_scorer is None:
            objective_scorer = MagicMock()
            objective_scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")

        kwargs.setdefault("default_dataset_config", DatasetConfiguration())
        super().__init__(technique_class=technique_class, objective_scorer=objective_scorer, **kwargs)
        self._test_atomic_attacks = atomic_attacks_to_return or []

    async def _resolve_seed_groups_by_dataset_async(self, *, apply_sampling: bool = True):
        return {}

    async def _build_atomic_attacks_async(self, *, context):
        return self._test_atomic_attacks


def _build_test_technique():
    class TestTechnique(ScenarioTechnique):
        CONCRETE = ("concrete", {"concrete"})
        ALL = ("all", {"all"})

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    return TestTechnique


@pytest.mark.usefixtures("patch_central_database")
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
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for i in [1, 2]
                ]
                incomplete = [("obj3", ValueError("Failed to complete obj3"))]

                # Save completed results to memory
                save_attack_results_to_memory(completed, atomic_attack=atomic_attack)

                return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
            # Retry: complete the remaining objective
            completed = [
                AttackResult(
                    conversation_id="conv-3",
                    objective="obj3",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=1,
                )
            ]
            save_attack_results_to_memory(completed, atomic_attack=atomic_attack)
            return AttackExecutorResult(completed_results=completed, incomplete_objectives=[])

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 1,
            }
        )
        await scenario.initialize_async()

        with patch.object(
            scenario._memory,
            "update_scenario_run_state",
            wraps=scenario._memory.update_scenario_run_state,
        ) as update_state:
            result = await scenario.run_async()

        # Verify scenario succeeded after retry
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2  # Called twice
        assert result.scenario_run_state == ScenarioRunState.COMPLETED
        assert result.error_message is None
        assert result.error_type is None
        observed_states = [call.kwargs["scenario_run_state"] for call in update_state.call_args_list]
        assert observed_states == [
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.COMPLETED,
        ]

        # All 3 results should be saved
        assert len(result.attack_results["partial_attack"]) == 3
        objectives_completed = [r.objective for r in result.attack_results["partial_attack"]]
        assert "obj1" in objectives_completed
        assert "obj2" in objectives_completed
        assert "obj3" in objectives_completed

    async def test_scenario_saves_partial_results_before_failure(self, mock_objective_target):
        """Test that scenario saves partial results even when attack fails."""
        atomic_attack = create_mock_atomic_attack("partial_save_attack", ["obj1", "obj2", "obj3", "obj4"])
        first_error = RuntimeError("Failed obj3")
        second_error = RuntimeError("Failed obj4")

        async def mock_run(*args, **kwargs):
            # Return partial results with incomplete objectives
            completed = [
                AttackResult(
                    conversation_id=f"conv-{i}",
                    objective=f"obj{i}",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=1,
                )
                for i in [1, 2]
            ]
            incomplete = [("obj3", first_error), ("obj4", second_error)]

            # Save completed results to memory
            save_attack_results_to_memory(completed, atomic_attack=atomic_attack)

            return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 0,  # No retries
            }
        )
        await scenario.initialize_async()

        # Should raise error because of incomplete objectives
        with pytest.raises(ScenarioPartialFailureException, match="incomplete") as exc_info:
            await scenario.run_async()

        error = exc_info.value
        assert error.atomic_attack_name == "partial_save_attack"
        assert error.completed_count == 2
        assert error.incomplete_count == 2
        assert error.total_count == 4
        assert error.incomplete_objectives == (("obj3", first_error), ("obj4", second_error))
        assert error.__cause__ is first_error
        assert type(error) is ScenarioPartialFailureException
        assert isinstance(error, ValueError)

        # But the 2 completed results should still be saved
        scenario_results = CentralMemory.get_memory_instance().get_scenario_results(
            scenario_result_ids=[scenario._scenario_result_id]
        )
        assert len(scenario_results) == 1
        assert scenario_results[0].scenario_run_state == ScenarioRunState.FAILED
        assert scenario_results[0].error_type == "ScenarioPartialFailureException"
        assert scenario_results[0].error_message.endswith("Caused by RuntimeError: Failed obj3")
        saved_results = scenario_results[0].attack_results["partial_save_attack"]
        assert len(saved_results) == 2
        assert saved_results[0].objective == "obj1"
        assert saved_results[1].objective == "obj2"

    async def test_failure_before_worker_retries_before_marking_failed(self, mock_objective_target):
        atomic_attack = create_mock_atomic_attack("never_started", ["obj1"])
        failure = RuntimeError("Failed before worker execution")
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 1,
            }
        )
        await scenario.initialize_async()

        with (
            patch.object(
                scenario,
                "_get_remaining_atomic_attacks_async",
                new=AsyncMock(side_effect=failure),
            ),
            patch.object(
                scenario._memory,
                "update_scenario_run_state",
                wraps=scenario._memory.update_scenario_run_state,
            ) as update_state,
        ):
            with pytest.raises(RuntimeError, match="before worker execution"):
                await scenario.run_async()

        observed_states = [call.kwargs["scenario_run_state"] for call in update_state.call_args_list]
        assert observed_states == [
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.FAILED,
        ]
        atomic_attack.run_async.assert_not_called()

        scenario_results = CentralMemory.get_memory_instance().get_scenario_results(
            scenario_result_ids=[scenario._scenario_result_id]
        )
        assert scenario_results[0].scenario_run_state == ScenarioRunState.FAILED
        assert scenario_results[0].error_message == str(failure)
        assert scenario_results[0].error_type == "RuntimeError"

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
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for i in [1, 2, 3]
                ]
                incomplete = [("obj4", Exception("Failed obj4")), ("obj5", Exception("Failed obj5"))]

                save_attack_results_to_memory(completed, atomic_attack=atomic_attack)

                return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
            # Retry: complete remaining objectives
            completed = [
                AttackResult(
                    conversation_id=f"conv-{i}",
                    objective=f"obj{i}",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=1,
                )
                for i in [4, 5]
            ]

            save_attack_results_to_memory(completed, atomic_attack=atomic_attack)

            return AttackExecutorResult(completed_results=completed, incomplete_objectives=[])

        atomic_attack.run_async = mock_run

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 1,
            }
        )
        await scenario.initialize_async()

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

    async def test_run_async_cancellation_persists_progress_cleans_workers_and_resumes(self, mock_objective_target):
        completed_attack = create_mock_atomic_attack("completed_attack", ["obj1"])
        in_flight_attack = create_mock_atomic_attack("in_flight_attack", ["obj2"])
        queued_attack = create_mock_atomic_attack("queued_attack", ["obj3"])

        completed_result = AttackResult(
            conversation_id="conv-1",
            objective="obj1",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
        )
        resumed_results = {
            "in_flight_attack": AttackResult(
                conversation_id="conv-2",
                objective="obj2",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            ),
            "queued_attack": AttackResult(
                conversation_id="conv-3",
                objective="obj3",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            ),
        }

        completed_persisted = asyncio.Event()
        in_flight_started = asyncio.Event()
        completed_worker_exited = asyncio.Event()
        in_flight_worker_exited = asyncio.Event()
        block_until_cancelled = asyncio.Event()
        persisted_objectives: list[str] = []

        async def run_completed_attack(*args, **kwargs):
            save_attack_results_to_memory([completed_result], atomic_attack=completed_attack)
            persisted_objectives.append(completed_result.objective)
            completed_persisted.set()
            try:
                await block_until_cancelled.wait()
            finally:
                completed_worker_exited.set()

        async def run_in_flight_attack(*args, **kwargs):
            if in_flight_attack.run_async.call_count == 1:
                in_flight_started.set()
                try:
                    await block_until_cancelled.wait()
                finally:
                    in_flight_worker_exited.set()

            result = resumed_results["in_flight_attack"]
            save_attack_results_to_memory([result], atomic_attack=in_flight_attack)
            persisted_objectives.append(result.objective)
            return AttackExecutorResult(completed_results=[result], incomplete_objectives=[])

        async def run_queued_attack(*args, **kwargs):
            result = resumed_results["queued_attack"]
            save_attack_results_to_memory([result], atomic_attack=queued_attack)
            persisted_objectives.append(result.objective)
            return AttackExecutorResult(completed_results=[result], incomplete_objectives=[])

        completed_attack.run_async = AsyncMock(side_effect=run_completed_attack)
        in_flight_attack.run_async = AsyncMock(side_effect=run_in_flight_attack)
        queued_attack.run_async = AsyncMock(side_effect=run_queued_attack)

        scenario = ConcreteScenario(
            name="Cancellation Test Scenario",
            version=1,
            atomic_attacks_to_return=[completed_attack, in_flight_attack, queued_attack],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 2,
                "max_retries": 3,
            }
        )
        await scenario.initialize_async()

        scenario_task = asyncio.create_task(scenario.run_async())
        await asyncio.wait_for(completed_persisted.wait(), timeout=5.0)
        await asyncio.wait_for(in_flight_started.wait(), timeout=5.0)
        scenario_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await scenario_task

        assert completed_worker_exited.is_set()
        assert in_flight_worker_exited.is_set()
        queued_attack.run_async.assert_not_called()
        assert persisted_objectives == ["obj1"]

        [cancelled_result] = CentralMemory.get_memory_instance().get_scenario_results(
            scenario_result_ids=[scenario._scenario_result_id]
        )
        assert cancelled_result.scenario_run_state == ScenarioRunState.CANCELLED
        assert cancelled_result.error_type == "CancelledError"
        assert cancelled_result.number_tries == 1
        assert [result.objective for result in cancelled_result.attack_results["completed_attack"]] == ["obj1"]

        await asyncio.sleep(0)
        assert persisted_objectives == ["obj1"]

        resumed_result = await scenario.run_async()

        assert resumed_result.scenario_run_state == ScenarioRunState.COMPLETED
        assert resumed_result.number_tries == 2
        assert completed_attack.run_async.call_count == 1
        assert in_flight_attack.run_async.call_count == 2
        assert queued_attack.run_async.call_count == 1
        assert persisted_objectives == ["obj1", "obj2", "obj3"]
        assert sorted(resumed_result.get_objectives()) == ["obj1", "obj2", "obj3"]
        assert all(len(results) == 1 for results in resumed_result.attack_results.values())

    async def test_run_async_cancellation_is_not_masked_by_persistence_failure(
        self, mock_objective_target: MagicMock
    ) -> None:
        atomic_attack = create_mock_atomic_attack("cancelled_attack", ["obj1"])
        scenario = ConcreteScenario(
            name="Cancellation Persistence Failure Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        with (
            patch.object(
                scenario,
                "_execute_scenario_async",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
            patch.object(
                scenario._memory,
                "update_scenario_run_state",
                side_effect=RuntimeError("database unavailable"),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await scenario.run_async()

    async def test_multiple_atomic_attacks_with_partial_results(self, mock_objective_target):
        """Test scenario with multiple atomic attacks that return partial results."""
        # Create 3 atomic attacks
        attack1 = create_mock_atomic_attack("attack_1", ["a1_obj1", "a1_obj2"])
        attack2 = create_mock_atomic_attack("attack_2", ["a2_obj1", "a2_obj2", "a2_obj3"])
        attack3 = create_mock_atomic_attack("attack_3", ["a3_obj1"])

        call_counts = {"attack_1": 0, "attack_2": 0, "attack_3": 0}
        attacks_by_name = {"attack_1": attack1, "attack_2": attack2, "attack_3": attack3}

        async def make_mock_run(attack_name, objectives):
            async def mock_run(*args, **kwargs):
                call_counts[attack_name] += 1
                this_attack = attacks_by_name[attack_name]

                if attack_name == "attack_2" and call_counts[attack_name] == 1:
                    # Attack 2 fails partially on first attempt
                    completed = [
                        AttackResult(
                            conversation_id="conv-a2-1",
                            objective="a2_obj1",
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                        )
                    ]
                    incomplete = [("a2_obj2", Exception("Failed a2_obj2")), ("a2_obj3", Exception("Failed a2_obj3"))]

                    save_attack_results_to_memory(completed, atomic_attack=this_attack)

                    return AttackExecutorResult(completed_results=completed, incomplete_objectives=incomplete)
                # All other attempts succeed fully
                completed = [
                    AttackResult(
                        conversation_id=f"conv-{obj}",
                        objective=obj,
                        outcome=AttackOutcome.SUCCESS,
                        executed_turns=1,
                    )
                    for obj in this_attack.objectives
                ]

                save_attack_results_to_memory(completed, atomic_attack=this_attack)

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
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 1,
            }
        )
        await scenario.initialize_async()

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

    async def test_concurrent_partial_failures_resume_without_duplicate_results(self, mock_objective_target):
        """
        Concurrent partial failures should surface together and resume only unfinished objectives.

        Three attacks start together. Two persist one result each before failing, while the
        third succeeds. A second run must execute only the two unfinished objectives.
        """
        attack_a = create_mock_atomic_attack("attack-a", ["a-complete", "a-retry"])
        attack_b = create_mock_atomic_attack("attack-b", ["b-complete", "b-retry"])
        attack_c = create_mock_atomic_attack("attack-c", ["c-complete"])

        all_started = asyncio.Event()
        attack_a_finished = asyncio.Event()
        attack_b_finished = asyncio.Event()
        started_attacks: set[str] = set()
        objective_batches: dict[str, list[list[str]]] = {"attack-a": [], "attack-b": [], "attack-c": []}

        async def wait_until_all_started_async(*, attack_name: str) -> None:
            started_attacks.add(attack_name)
            if len(started_attacks) == 3:
                all_started.set()
            await all_started.wait()

        def save_result(*, objective: str, attack: MagicMock) -> AttackResult:
            result = AttackResult(
                conversation_id=f"conv-{objective}",
                objective=objective,
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
            )
            save_attack_results_to_memory([result], atomic_attack=attack)
            return result

        async def run_attack_a_async(*args, **kwargs) -> AttackExecutorResult[AttackResult]:
            objectives = list(attack_a.objectives)
            objective_batches["attack-a"].append(objectives)
            if len(objective_batches["attack-a"]) == 1:
                await wait_until_all_started_async(attack_name="attack-a")
                completed = save_result(objective="a-complete", attack=attack_a)
                attack_a_finished.set()
                return AttackExecutorResult(
                    completed_results=[completed],
                    incomplete_objectives=[("a-retry", RuntimeError("attack-a interrupted"))],
                )
            completed = save_result(objective="a-retry", attack=attack_a)
            return AttackExecutorResult(completed_results=[completed], incomplete_objectives=[])

        async def run_attack_b_async(*args, **kwargs) -> AttackExecutorResult[AttackResult]:
            objectives = list(attack_b.objectives)
            objective_batches["attack-b"].append(objectives)
            if len(objective_batches["attack-b"]) == 1:
                await wait_until_all_started_async(attack_name="attack-b")
                await attack_a_finished.wait()
                completed = save_result(objective="b-complete", attack=attack_b)
                attack_b_finished.set()
                return AttackExecutorResult(
                    completed_results=[completed],
                    incomplete_objectives=[("b-retry", TimeoutError("attack-b timed out"))],
                )
            completed = save_result(objective="b-retry", attack=attack_b)
            return AttackExecutorResult(completed_results=[completed], incomplete_objectives=[])

        async def run_attack_c_async(*args, **kwargs) -> AttackExecutorResult[AttackResult]:
            objectives = list(attack_c.objectives)
            objective_batches["attack-c"].append(objectives)
            await wait_until_all_started_async(attack_name="attack-c")
            await attack_b_finished.wait()
            completed = save_result(objective="c-complete", attack=attack_c)
            return AttackExecutorResult(completed_results=[completed], incomplete_objectives=[])

        attack_a.run_async = AsyncMock(side_effect=run_attack_a_async)
        attack_b.run_async = AsyncMock(side_effect=run_attack_b_async)
        attack_c.run_async = AsyncMock(side_effect=run_attack_c_async)

        scenario = ConcreteScenario(
            name="Concurrent partial failure scenario",
            version=1,
            atomic_attacks_to_return=[attack_a, attack_b, attack_c],
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 3,
                "max_retries": 0,
            }
        )
        await scenario.initialize_async()

        with patch.object(
            scenario._memory,
            "update_scenario_run_state",
            wraps=scenario._memory.update_scenario_run_state,
        ) as update_state:
            with pytest.raises(ExceptionGroup) as exc_info:
                await asyncio.wait_for(scenario.run_async(), timeout=10)

            assert all(isinstance(error, ScenarioPartialFailureException) for error in exc_info.value.exceptions)
            partial_failures = {
                error.atomic_attack_name: error
                for error in exc_info.value.exceptions
                if isinstance(error, ScenarioPartialFailureException)
            }
            assert set(partial_failures) == {"attack-a", "attack-b"}
            assert isinstance(partial_failures["attack-a"].incomplete_objectives[0][1], RuntimeError)
            assert isinstance(partial_failures["attack-b"].incomplete_objectives[0][1], TimeoutError)

            failed_result = scenario._memory.get_scenario_results(scenario_result_ids=[scenario._scenario_result_id])[0]
            assert failed_result.scenario_run_state == ScenarioRunState.FAILED
            assert failed_result.number_tries == 1

            final_result = await asyncio.wait_for(scenario.run_async(), timeout=10)

        assert final_result.scenario_run_state == ScenarioRunState.COMPLETED
        assert final_result.number_tries == 2
        assert objective_batches == {
            "attack-a": [["a-complete", "a-retry"], ["a-retry"]],
            "attack-b": [["b-complete", "b-retry"], ["b-retry"]],
            "attack-c": [["c-complete"]],
        }
        assert attack_a.run_async.await_count == 2
        assert attack_b.run_async.await_count == 2
        assert attack_c.run_async.await_count == 1

        stored_results = [
            result for attack_results in final_result.attack_results.values() for result in attack_results
        ]
        assert sorted(result.objective for result in stored_results) == [
            "a-complete",
            "a-retry",
            "b-complete",
            "b-retry",
            "c-complete",
        ]
        assert len({result.attack_result_id for result in stored_results}) == 5
        assert [call.kwargs["scenario_run_state"] for call in update_state.call_args_list] == [
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.FAILED,
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.COMPLETED,
        ]
