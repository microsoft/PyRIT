# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Scenario retry functionality."""

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier
from pyrit.scenario import DatasetConfiguration, ScenarioResult
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique

# Test constants
TEST_ATTACK_TYPE = "TestAttack"
TEST_MODULE = "test"
CONV_ID_PREFIX = "conv-"
OBJECTIVE_PREFIX = "objective"
ATTACK_NAME_PREFIX = "attack_"


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module=TEST_MODULE,
    )


@pytest.fixture
def mock_objective_scorer():
    """Create a mock objective scorer for testing."""
    scorer = MagicMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


# Helper functions
def save_attack_results_to_memory(attack_results, *, atomic_attack=None):
    """Helper function to save attack results to memory.

    When ``atomic_attack`` is provided, stamps ``attribution_parent_id`` and
    ``attribution_data`` onto each result (mirrors the real attack persistence
    path so foreign-key-based hydration sees the rows).
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


def create_attack_result(
    index: int,
    objective: str | None = None,
    conversation_id: str | None = None,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    executed_turns: int = 1,
) -> AttackResult:
    """Factory function to create AttackResult objects with consistent defaults.

    Args:
        index: Numeric identifier for the attack result
        objective: Objective text (defaults to "objectiveN")
        conversation_id: Conversation ID (defaults to "conv-N")
        outcome: Attack outcome (defaults to SUCCESS)
        executed_turns: Number of executed turns (defaults to 1)

    Returns:
        AttackResult instance
    """
    return AttackResult(
        conversation_id=conversation_id or f"{CONV_ID_PREFIX}{index}",
        objective=objective or f"{OBJECTIVE_PREFIX}{index}",
        outcome=outcome,
        executed_turns=executed_turns,
    )


def create_attack_results_list(count: int, start_index: int = 1) -> list[AttackResult]:
    """Create a list of AttackResult objects.

    Args:
        count: Number of results to create
        start_index: Starting index for numbering (defaults to 1)

    Returns:
        List of AttackResult instances
    """
    return [create_attack_result(i) for i in range(start_index, start_index + count)]


def create_mock_run_async(attack_results, *, atomic_attack=None):
    """Create a mock run_async that stamps + saves results to memory before returning.

    Args:
        attack_results: List of AttackResult objects to return
        atomic_attack: Optional AtomicAttack mock. When provided, results are
            stamped with attribution_parent_id and attribution_data so
            foreign-key-based hydration finds them.

    Returns:
        AsyncMock configured to return the results
    """

    async def mock_run_async(*args, **kwargs):
        save_attack_results_to_memory(attack_results, atomic_attack=atomic_attack)
        return AttackExecutorResult(completed_results=attack_results, incomplete_objectives=[])

    return AsyncMock(side_effect=mock_run_async)


def create_mock_atomic_attack(name: str, objectives: list[str], run_async_mock: AsyncMock | None = None) -> MagicMock:
    """Factory function to create mock AtomicAttack instances.

    Args:
        name: Name for the atomic attack
        objectives: List of objectives for the attack
        run_async_mock: Optional pre-configured run_async mock (if None, must be set separately)

    Returns:
        MagicMock configured as an AtomicAttack
    """
    # Create a mock attack technique
    mock_attack_strategy = MagicMock()
    mock_attack_strategy.get_objective_target.return_value = MagicMock()
    mock_attack_strategy.get_attack_scoring_config.return_value = MagicMock()

    attack = MagicMock(spec=AtomicAttack)
    attack.atomic_attack_name = name
    attack.display_group = name
    attack._attack = mock_attack_strategy
    attack._scenario_result_id = None

    def _set_scenario_result_id(scenario_result_id):
        attack._scenario_result_id = scenario_result_id

    attack.set_scenario_result_id = MagicMock(side_effect=_set_scenario_result_id)

    # Track objectives + objective-hash mapping so the hash-based filter
    # behaves correctly in resume tests.
    from pyrit.common.utils import to_sha256

    current_objectives = {"value": list(objectives)}
    type(attack).objectives = PropertyMock(side_effect=lambda: current_objectives["value"])
    type(attack).seed_groups = PropertyMock(side_effect=lambda: current_objectives["value"])

    def drop_hashes(*, hashes):
        current_objectives["value"] = [o for o in current_objectives["value"] if to_sha256(o) not in hashes]

    attack.drop_seed_groups_with_hashes = MagicMock(side_effect=drop_hashes)

    if run_async_mock:
        attack.run_async = run_async_mock
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

        kwargs.setdefault("default_technique", technique_class.ALL)
        kwargs.setdefault("default_dataset_config", DatasetConfiguration())
        super().__init__(technique_class=technique_class, objective_scorer=objective_scorer, **kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    async def _resolve_seed_groups_by_dataset_async(self, *, apply_sampling: bool = True):
        return {}

    async def _build_atomic_attacks_async(self, *, context):
        return self._atomic_attacks_to_return


def _build_test_technique():
    class TestTechnique(ScenarioTechnique):
        CONCRETE = ("concrete", {"concrete"})
        ALL = ("all", {"all"})

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    return TestTechnique


@pytest.fixture
def mock_atomic_attacks():
    """Create mock AtomicAttack instances for testing."""
    return [
        create_mock_atomic_attack("attack_run_1", ["objective1"]),
        create_mock_atomic_attack("attack_run_2", ["objective2"]),
    ]


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    target = MagicMock()
    target.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTarget",
        class_module=TEST_MODULE,
    )
    return target


@pytest.fixture
def sample_attack_results():
    """Create sample attack results for testing."""
    return create_attack_results_list(count=3, start_index=0)


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioRetry:
    """Tests for Scenario retry functionality."""

    async def test_no_retry_on_success(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that scenario doesn't retry when execution succeeds."""
        # Configure successful execution
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]], atomic_attack=run)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 3,  # Set retries but shouldn't use them on success
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        # Verify each atomic attack was called exactly once (no retries needed)
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once()

        # Verify result is successful
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 2

    async def test_retry_on_failure(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that scenario retries on failure up to max_retries."""
        # Configure first run to fail, second to succeed
        call_count = [0]

        async def mock_run_with_retry(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Test failure")
            # Retry succeeds
            results = [sample_attack_results[0]]
            save_attack_results_to_memory(results, atomic_attack=mock_atomic_attacks[0])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_retry
        mock_atomic_attacks[1].run_async = create_mock_run_async(
            [sample_attack_results[1]], atomic_attack=mock_atomic_attacks[1]
        )

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 1,
                "max_retries": 2,
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        # Verify scenario succeeded on retry
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2  # Initial attempt + 1 retry

    async def test_exhausts_retries_and_fails(self, mock_atomic_attacks, mock_objective_target):
        """Test that scenario fails after exhausting all retries."""
        # Configure all attempts to fail
        mock_atomic_attacks[0].run_async = AsyncMock(side_effect=Exception("Persistent failure"))
        mock_atomic_attacks[1].run_async = AsyncMock(side_effect=Exception("Should not be called"))

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 2,  # Allow 2 retries (3 total attempts)
            }
        )
        await scenario.initialize_async()

        # Verify that scenario raises exception after exhausting retries
        with pytest.raises(Exception, match="Persistent failure"):
            await scenario.run_async()

        # Verify it attempted max_retries + 1 times (initial + retries)
        assert mock_atomic_attacks[0].run_async.call_count == 3

    async def test_no_retry_when_max_retries_zero(self, mock_atomic_attacks, mock_objective_target):
        """Test that scenario doesn't retry when max_retries is 0 (default)."""
        # Configure to fail
        mock_atomic_attacks[0].run_async = AsyncMock(side_effect=Exception("Test failure"))

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 0,  # No retries
            }
        )
        await scenario.initialize_async()

        # Verify that scenario raises exception immediately without retry
        with pytest.raises(Exception, match="Test failure"):
            await scenario.run_async()

        # Verify it was only called once (no retries)
        mock_atomic_attacks[0].run_async.assert_called_once()

    async def test_number_tries_increments_on_retry(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that number_tries field increments with each retry attempt."""
        call_count = [0]

        async def mock_run_with_multiple_retries(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("Test failure")
            # Third attempt succeeds
            results = [sample_attack_results[0]]
            save_attack_results_to_memory(results, atomic_attack=mock_atomic_attacks[0])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_multiple_retries
        mock_atomic_attacks[1].run_async = create_mock_run_async(
            [sample_attack_results[1]], atomic_attack=mock_atomic_attacks[1]
        )

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 1,
                "max_retries": 3,
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        # Verify scenario succeeded after retries
        assert isinstance(result, ScenarioResult)
        assert result.number_tries == 3  # Failed twice, succeeded on third

    async def test_retry_logs_error_with_exception(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target, caplog
    ):
        """Test that retry failures are logged with exception details."""
        call_count = [0]

        async def mock_run_with_logged_failure(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("First failure")
            # Retry succeeds
            results = [sample_attack_results[0]]
            save_attack_results_to_memory(results, atomic_attack=mock_atomic_attacks[0])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_logged_failure
        mock_atomic_attacks[1].run_async = create_mock_run_async(
            [sample_attack_results[1]], atomic_attack=mock_atomic_attacks[1]
        )

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 1,
                "max_retries": 1,
            }
        )
        await scenario.initialize_async()

        with caplog.at_level("ERROR"):
            result = await scenario.run_async()

        # Verify error was logged
        assert "failed on attempt" in caplog.text.lower()
        assert "First failure" in caplog.text or "ValueError" in caplog.text
        assert "retrying" in caplog.text.lower()

        # Verify scenario eventually succeeded
        assert isinstance(result, ScenarioResult)


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResumption:
    """Tests for Scenario resumption after partial failure."""

    async def test_resumes_from_partial_completion_single_attack(self, mock_objective_target):
        """Test that scenario resumes from where it left off when an atomic attack partially completes."""
        objectives = ["obj1", "obj2", "obj3", "obj4"]
        atomic_attack = create_mock_atomic_attack("multi_objective_attack", objectives)

        # Track which objectives have been executed
        executed_objectives = []
        call_count = [0]

        async def mock_run_with_partial_completion(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: complete 2 objectives, then fail
                executed_objectives.extend(["obj1", "obj2"])
                results = [create_attack_result(i, objective=f"obj{i}") for i in [1, 2]]
                save_attack_results_to_memory(results, atomic_attack=atomic_attack)
                raise Exception("Failed after 2 objectives")
            # Retry: should only execute remaining objectives (obj3, obj4)
            executed_objectives.extend(["obj3", "obj4"])
            results = [create_attack_result(i, objective=f"obj{i}") for i in [3, 4]]
            save_attack_results_to_memory(results, atomic_attack=atomic_attack)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        atomic_attack.run_async = mock_run_with_partial_completion

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

        # Verify scenario succeeded after retry
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2  # Initial attempt + 1 retry
        # All objectives should be executed across both attempts
        assert "obj1" in executed_objectives or "obj3" in executed_objectives

    async def test_resumes_skipping_completed_atomic_attacks(self, mock_objective_target):
        """Test that scenario skips completed atomic attacks on retry."""
        # Create 3 atomic attacks
        attack1 = create_mock_atomic_attack("attack_1", ["objective1"])
        attack2 = create_mock_atomic_attack("attack_2", ["objective2"])
        attack3 = create_mock_atomic_attack("attack_3", ["objective3"])

        call_count = {"attack_1": 0, "attack_2": 0, "attack_3": 0}

        # Attack 1: Succeeds immediately
        async def mock_run_attack1(*args, **kwargs):
            call_count["attack_1"] += 1
            results = [create_attack_result(1, objective="objective1")]
            save_attack_results_to_memory(results, atomic_attack=attack1)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 2: Succeeds on first attempt, should not be retried
        async def mock_run_attack2(*args, **kwargs):
            call_count["attack_2"] += 1
            if call_count["attack_2"] == 1:
                results = [create_attack_result(2, objective="objective2")]
                save_attack_results_to_memory(results, atomic_attack=attack2)
                return AttackExecutorResult(completed_results=results, incomplete_objectives=[])
            raise AssertionError("Attack 2 should not be retried after completion")

        # Attack 3: Fails on first attempt, succeeds on retry
        async def mock_run_attack3(*args, **kwargs):
            call_count["attack_3"] += 1
            if call_count["attack_3"] == 1:
                raise Exception("Attack 3 failed on first attempt")
            results = [create_attack_result(3, objective="objective3")]
            save_attack_results_to_memory(results, atomic_attack=attack3)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        attack1.run_async = mock_run_attack1
        attack2.run_async = mock_run_attack2
        attack3.run_async = mock_run_attack3

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

        # Verify scenario succeeded
        assert isinstance(result, ScenarioResult)
        # Attack 1 and 2 should be called once each (completed on first attempt)
        assert call_count["attack_1"] == 1
        assert call_count["attack_2"] == 1
        # Attack 3 should be called twice (failed first, succeeded on retry)
        assert call_count["attack_3"] == 2
        # All three attacks should be in results
        assert len(result.attack_results) == 3
        assert "attack_1" in result.attack_results
        assert "attack_2" in result.attack_results
        assert "attack_3" in result.attack_results

    async def test_resumes_with_multiple_failures_across_attacks(self, mock_objective_target):
        """Test resumption when multiple atomic attacks fail at different stages."""
        # Create 4 atomic attacks
        attacks = [create_mock_atomic_attack(f"attack_{i}", [f"objective{i}"]) for i in range(1, 5)]

        call_count = {f"attack_{i}": 0 for i in range(1, 5)}

        # Attack 1: Succeeds immediately
        async def mock_run_attack1(*args, **kwargs):
            call_count["attack_1"] += 1
            results = [create_attack_result(1, objective="objective1")]
            save_attack_results_to_memory(results, atomic_attack=attacks[0])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 2: Fails on first attempt, succeeds on retry
        async def mock_run_attack2(*args, **kwargs):
            call_count["attack_2"] += 1
            if call_count["attack_2"] == 1:
                raise Exception("Attack 2 failed")
            results = [create_attack_result(2, objective="objective2")]
            save_attack_results_to_memory(results, atomic_attack=attacks[1])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 3: Only called on retry (after attack 2 succeeds)
        async def mock_run_attack3(*args, **kwargs):
            call_count["attack_3"] += 1
            results = [create_attack_result(3, objective="objective3")]
            save_attack_results_to_memory(results, atomic_attack=attacks[2])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 4: Only called on retry
        async def mock_run_attack4(*args, **kwargs):
            call_count["attack_4"] += 1
            results = [create_attack_result(4, objective="objective4")]
            save_attack_results_to_memory(results, atomic_attack=attacks[3])
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        attacks[0].run_async = mock_run_attack1
        attacks[1].run_async = mock_run_attack2
        attacks[2].run_async = mock_run_attack3
        attacks[3].run_async = mock_run_attack4

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=attacks,
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
        # Attack 1: Called once (succeeded before failure point)
        assert call_count["attack_1"] == 1
        # Attack 2: Called twice (failed first, succeeded on retry)
        assert call_count["attack_2"] == 2
        # Attack 3: Called once (only on retry, after attack 2 succeeded)
        assert call_count["attack_3"] == 1
        # Attack 4: Called once (only on retry)
        assert call_count["attack_4"] == 1
        # All four attacks should be in results
        assert len(result.attack_results) == 4


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioForeignKeyResumeRegression:
    """Regression tests for the foreign-key-based scenario linkage resume path.

    The bug being regression-tested: when a Scenario is interrupted mid-
    AtomicAttack (Ctrl-C, OOM, crash), AttackResults already persisted to the
    DB used to be invisible to the scenario because the scenario→attack-result
    link only lived in a JSON manifest written after the whole AtomicAttack
    returned. On resume, those objectives were re-executed (wasted compute).

    After the refactor, ``attribution_parent_id`` is stamped on each
    ``AttackResultEntry`` at write time, so resume reads them directly and
    skips the already-done work even when the manifest was never updated.
    """

    async def test_resume_skips_objectives_persisted_before_interruption(self, mock_objective_target):
        """Simulate Ctrl-C after some objectives in an atomic attack persisted
        results but before the manifest was bulk-written. On resume, only the
        missing objectives are re-executed."""
        atomic_attack = create_mock_atomic_attack("partial", ["o1", "o2", "o3", "o4"])

        async def first_run(*args, **kwargs):
            partials = [
                create_attack_result(0, conversation_id="c1", objective="o1"),
                create_attack_result(1, conversation_id="c2", objective="o2"),
            ]
            save_attack_results_to_memory(partials, atomic_attack=atomic_attack)
            raise Exception("simulated crash after partial persistence")

        atomic_attack.run_async = first_run

        scenario = ConcreteScenario(
            name="Interrupted Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack],
        )
        scenario.set_params_from_args(args={"objective_target": mock_objective_target, "max_retries": 0})
        await scenario.initialize_async()

        with pytest.raises(Exception, match="simulated crash"):
            await scenario.run_async()

        scenario_result_id = scenario._scenario_result_id
        assert scenario_result_id is not None

        # === Resume by scenario_result_id ===
        atomic_attack_resume = create_mock_atomic_attack("partial", ["o1", "o2", "o3", "o4"])
        executed: list[str] = []

        async def second_run(*args, **kwargs):
            executed.extend(atomic_attack_resume.objectives)
            results = [
                create_attack_result(i, conversation_id=f"c{i + 1}", objective=obj)
                for i, obj in enumerate(atomic_attack_resume.objectives, start=2)
            ]
            save_attack_results_to_memory(results, atomic_attack=atomic_attack_resume)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        atomic_attack_resume.run_async = second_run

        scenario_resumed = ConcreteScenario(
            name="Interrupted Scenario",
            version=1,
            atomic_attacks_to_return=[atomic_attack_resume],
            scenario_result_id=scenario_result_id,
        )
        scenario_resumed.set_params_from_args(args={"objective_target": mock_objective_target, "max_retries": 0})
        await scenario_resumed.initialize_async()
        await scenario_resumed.run_async()

        # Resume executed only the missing objectives — the core fix.
        assert executed == ["o3", "o4"]

    async def test_duplicate_objective_text_in_atomic_attack_is_rejected(self, mock_objective_target):
        """Resume identity is the objective sha256 within an AtomicAttack, so
        the real ``AtomicAttack.__init__`` refuses to construct with duplicate
        objective text. We exercise the production constructor here to lock
        that contract in (the resume mocks bypass it intentionally)."""
        from pyrit.executor.attack import AttackStrategy
        from pyrit.models import AttackSeedGroup, SeedObjective
        from pyrit.scenario import AtomicAttack
        from pyrit.scenario.core.attack_technique import AttackTechnique

        mock_attack = MagicMock(spec=AttackStrategy)
        duplicate_groups = [
            AttackSeedGroup(seeds=[SeedObjective(value="dup-obj")]),
            AttackSeedGroup(seeds=[SeedObjective(value="dup-obj")]),
        ]
        with pytest.raises(ValueError, match="duplicate objective hash"):
            AtomicAttack(
                attack_technique=AttackTechnique(attack=mock_attack),
                seed_groups=duplicate_groups,
                atomic_attack_name="dup_attack",
            )

    async def test_duplicate_atomic_attack_name_does_not_warn(self, mock_objective_target, caplog):
        """Duplicate ``atomic_attack_name`` is supported: resume disambiguates
        rows by ``(parent_collection, parent_eval_hash)``, so two atomic
        attacks sharing a name with different techniques don't cross-pollinate
        their completed-hash sets. No warning is emitted."""
        dup1 = create_mock_atomic_attack("dup_name", ["objA"])
        dup2 = create_mock_atomic_attack("dup_name", ["objB"])

        async def noop_run(*args, **kwargs):
            return AttackExecutorResult(completed_results=[], incomplete_objectives=[])

        dup1.run_async = noop_run
        dup2.run_async = noop_run

        scenario = ConcreteScenario(
            name="Dup Name Scenario",
            version=1,
            atomic_attacks_to_return=[dup1, dup2],
        )

        with caplog.at_level("WARNING"):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()

        assert not any("duplicate atomic_attack_name" in record.message for record in caplog.records), (
            "Duplicate atomic_attack_name should be supported without warning"
        )


@pytest.mark.usefixtures("patch_central_database")
class TestGetCompletedObjectiveHashesForAttack:
    """Direct tests for ``Scenario._get_completed_objective_hashes_for_attack``
    — the filter that excludes already-completed objectives on resume.

    Covers the row-filtering branches: outcome=ERROR rows, rows without
    attribution_data, and the technique-disambiguation branch where two
    atomic attacks share a name but differ in technique eval hash.
    """

    def _make_scenario(self, scenario_result_id="scn-1"):
        scenario = ConcreteScenario(name="S", version=1, atomic_attacks_to_return=[])
        scenario._scenario_result_id = scenario_result_id
        scenario._memory = MagicMock()
        return scenario

    def _make_atomic(self, name, eval_hash="hash-A"):
        atomic = MagicMock(spec=AtomicAttack)
        atomic.atomic_attack_name = name
        type(atomic).technique_eval_hash = PropertyMock(return_value=eval_hash)
        return atomic

    def _row(self, *, objective, outcome=AttackOutcome.SUCCESS, attribution_data=None):
        row = MagicMock()
        row.outcome = outcome
        row.attribution_data = attribution_data
        row.objective = objective
        return row

    def test_returns_empty_when_scenario_result_id_unset(self):
        scenario = ConcreteScenario(name="S", version=1, atomic_attacks_to_return=[])
        scenario._scenario_result_id = None
        result = scenario._get_completed_objective_hashes_for_attack(
            atomic_attack=self._make_atomic("a"),
        )
        assert result == set()

    def test_skips_error_rows(self):
        from pyrit.common.utils import to_sha256

        scenario = self._make_scenario()
        scenario._memory.get_attack_results.return_value = [
            self._row(
                objective="ok",
                outcome=AttackOutcome.SUCCESS,
                attribution_data={"parent_collection": "a", "parent_eval_hash": "hash-A"},
            ),
            self._row(
                objective="failed",
                outcome=AttackOutcome.ERROR,
                attribution_data={"parent_collection": "a", "parent_eval_hash": "hash-A"},
            ),
        ]
        result = scenario._get_completed_objective_hashes_for_attack(
            atomic_attack=self._make_atomic("a"),
        )
        assert result == {to_sha256("ok")}

    def test_skips_rows_without_attribution_data(self):
        from pyrit.common.utils import to_sha256

        scenario = self._make_scenario()
        scenario._memory.get_attack_results.return_value = [
            self._row(objective="legacy", attribution_data=None),
            self._row(
                objective="new",
                attribution_data={"parent_collection": "a", "parent_eval_hash": "hash-A"},
            ),
        ]
        result = scenario._get_completed_objective_hashes_for_attack(
            atomic_attack=self._make_atomic("a"),
        )
        assert result == {to_sha256("new")}

    def test_skips_rows_with_mismatched_eval_hash(self):
        """Two atomic attacks with the same name but different techniques
        must not cross-pollinate completed hashes. This is the core Option-B
        guarantee."""
        from pyrit.common.utils import to_sha256

        scenario = self._make_scenario()
        scenario._memory.get_attack_results.return_value = [
            self._row(
                objective="mine",
                attribution_data={"parent_collection": "encoding", "parent_eval_hash": "hash-base64"},
            ),
            self._row(
                objective="theirs",
                attribution_data={"parent_collection": "encoding", "parent_eval_hash": "hash-hex"},
            ),
        ]
        result = scenario._get_completed_objective_hashes_for_attack(
            atomic_attack=self._make_atomic("encoding", eval_hash="hash-base64"),
        )
        assert result == {to_sha256("mine")}

    def test_backward_compat_matches_name_only_when_eval_hash_missing(self):
        """Rows persisted before ``parent_eval_hash`` shipped match name-only
        so pre-existing resume runs aren't stranded."""
        from pyrit.common.utils import to_sha256

        scenario = self._make_scenario()
        scenario._memory.get_attack_results.return_value = [
            self._row(
                objective="old",
                attribution_data={"parent_collection": "a"},  # no parent_eval_hash
            ),
        ]
        result = scenario._get_completed_objective_hashes_for_attack(
            atomic_attack=self._make_atomic("a", eval_hash="hash-A"),
        )
        assert result == {to_sha256("old")}


@pytest.mark.usefixtures("patch_central_database")
class TestApplyPersistedObjectives:
    """Direct tests for ``Scenario._apply_persisted_objectives`` — the
    resume-time replay that locks subsequent runs to the originally-sampled
    objective subset."""

    def _make_scenario_with_atomics(self, atomics):
        scenario = ConcreteScenario(name="S", version=1, atomic_attacks_to_return=[])
        scenario._scenario_result_id = "scn-1"
        scenario._atomic_attacks = atomics
        return scenario

    def test_noop_when_metadata_has_no_persisted_hashes(self):
        atomic = MagicMock(spec=AtomicAttack)
        scenario = self._make_scenario_with_atomics([atomic])
        stored = MagicMock()
        stored.metadata = {}
        scenario._apply_persisted_objectives(stored_result=stored)
        atomic.keep_seed_groups_with_hashes.assert_not_called()

    def test_replays_persisted_subset_across_atomics(self):
        atomic_a = MagicMock(spec=AtomicAttack)
        atomic_a.keep_seed_groups_with_hashes.return_value = {"h1", "h2"}
        atomic_b = MagicMock(spec=AtomicAttack)
        atomic_b.keep_seed_groups_with_hashes.return_value = {"h3"}
        scenario = self._make_scenario_with_atomics([atomic_a, atomic_b])

        stored = MagicMock()
        stored.metadata = {"objective_hashes": ["h1", "h2", "h3"]}
        scenario._apply_persisted_objectives(stored_result=stored)

        atomic_a.keep_seed_groups_with_hashes.assert_called_once_with(hashes={"h1", "h2", "h3"})
        atomic_b.keep_seed_groups_with_hashes.assert_called_once_with(hashes={"h1", "h2", "h3"})

    def test_raises_when_persisted_hash_is_missing(self):
        atomic = MagicMock(spec=AtomicAttack)
        atomic.keep_seed_groups_with_hashes.return_value = {"h1"}  # h2 missing
        scenario = self._make_scenario_with_atomics([atomic])

        stored = MagicMock()
        stored.metadata = {"objective_hashes": ["h1", "h2"]}
        with pytest.raises(ValueError, match="cannot resume"):
            scenario._apply_persisted_objectives(stored_result=stored)
