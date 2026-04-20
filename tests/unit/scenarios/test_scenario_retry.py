# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for Scenario retry functionality."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult
from pyrit.scenario import DatasetConfiguration, ScenarioResult
from pyrit.scenario.core import AtomicAttack, Scenario, ScenarioStrategy

# Test constants
TEST_ATTACK_TYPE = "TestAttack"
TEST_MODULE = "test"
CONV_ID_PREFIX = "conv-"
OBJECTIVE_PREFIX = "objective"
ATTACK_NAME_PREFIX = "attack_"


def _mock_scorer_id(name: str = "MockScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module=TEST_MODULE,
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_objective_scorer():
    """Create a mock objective scorer for testing."""
    scorer = MagicMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


# Helper functions
def save_attack_results_to_memory(attack_results):
    """Helper function to save attack results to memory (mimics what real attacks do)."""
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
        attack_identifier={"__type__": TEST_ATTACK_TYPE, "__module__": TEST_MODULE, "id": str(index)},
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


def create_mock_run_async(attack_results):
    """Create a mock run_async that saves results to memory before returning.

    Args:
        attack_results: List of AttackResult objects to return

    Returns:
        AsyncMock configured to return the results
    """

    async def mock_run_async(*args, **kwargs):
        # Save results to memory (mimics what real attacks do)
        save_attack_results_to_memory(attack_results)
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
    # Create a mock attack strategy
    mock_attack_strategy = MagicMock()
    mock_attack_strategy.get_objective_target.return_value = MagicMock()
    mock_attack_strategy.get_attack_scoring_config.return_value = MagicMock()

    attack = MagicMock(spec=AtomicAttack)
    attack.atomic_attack_name = name
    attack._attack = mock_attack_strategy
    type(attack).objectives = PropertyMock(return_value=objectives)

    # Configure filter_seed_groups_by_objectives - needed for scenario retry filtering
    attack.filter_seed_groups_by_objectives = MagicMock()

    if run_async_mock:
        attack.run_async = run_async_mock
    return attack


class ConcreteScenario(Scenario):
    """Concrete implementation of Scenario for testing."""

    def __init__(self, atomic_attacks_to_return=None, objective_scorer=None, **kwargs):
        # Default include_default_baseline=False for tests unless explicitly specified
        kwargs.setdefault("include_default_baseline", False)

        # Get strategy_class from kwargs or use default
        strategy_class = kwargs.pop("strategy_class", None) or self.get_strategy_class()

        # Create a default mock scorer if not provided
        if objective_scorer is None:
            objective_scorer = MagicMock()
            objective_scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")

        super().__init__(strategy_class=strategy_class, objective_scorer=objective_scorer, **kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    @classmethod
    def get_strategy_class(cls):
        """Return a mock strategy class for testing."""

        # Return a simple mock strategy class for testing
        class TestStrategy(ScenarioStrategy):
            CONCRETE = ("concrete", {"concrete"})
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        return TestStrategy

    @classmethod
    def get_default_strategy(cls):
        """Return the default strategy for testing."""
        return cls.get_strategy_class().ALL

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """Return the default dataset configuration for testing."""
        return DatasetConfiguration()

    async def _get_atomic_attacks_async(self):
        return self._atomic_attacks_to_return


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
    target.get_identifier.return_value = {"__type__": "MockTarget", "__module__": TEST_MODULE}
    return target


@pytest.fixture
def sample_attack_results():
    """Create sample attack results for testing."""
    return create_attack_results_list(count=3, start_index=0)


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioRetry:
    """Tests for Scenario retry functionality."""

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that scenario doesn't retry when execution succeeds."""
        # Configure successful execution
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=3,  # Set retries but shouldn't use them on success
        )

        result = await scenario.run_async()

        # Verify each atomic attack was called exactly once (no retries needed)
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once()

        # Verify result is successful
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 2

    @pytest.mark.asyncio
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
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_retry
        mock_atomic_attacks[1].run_async = create_mock_run_async([sample_attack_results[1]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=2,
        )

        result = await scenario.run_async()

        # Verify scenario succeeded on retry
        assert isinstance(result, ScenarioResult)
        assert call_count[0] == 2  # Initial attempt + 1 retry

    @pytest.mark.asyncio
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
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=2,  # Allow 2 retries (3 total attempts)
        )

        # Verify that scenario raises exception after exhausting retries
        with pytest.raises(Exception, match="Persistent failure"):
            await scenario.run_async()

        # Verify it attempted max_retries + 1 times (initial + retries)
        assert mock_atomic_attacks[0].run_async.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_when_max_retries_zero(self, mock_atomic_attacks, mock_objective_target):
        """Test that scenario doesn't retry when max_retries is 0 (default)."""
        # Configure to fail
        mock_atomic_attacks[0].run_async = AsyncMock(side_effect=Exception("Test failure"))

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=0,  # No retries
        )

        # Verify that scenario raises exception immediately without retry
        with pytest.raises(Exception, match="Test failure"):
            await scenario.run_async()

        # Verify it was only called once (no retries)
        mock_atomic_attacks[0].run_async.assert_called_once()

    @pytest.mark.asyncio
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
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_multiple_retries
        mock_atomic_attacks[1].run_async = create_mock_run_async([sample_attack_results[1]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=3,
        )

        result = await scenario.run_async()

        # Verify scenario succeeded after retries
        assert isinstance(result, ScenarioResult)
        assert result.number_tries == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
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
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        mock_atomic_attacks[0].run_async = mock_run_with_logged_failure
        mock_atomic_attacks[1].run_async = create_mock_run_async([sample_attack_results[1]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=1,
        )

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

    @pytest.mark.asyncio
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
                save_attack_results_to_memory(results)
                raise Exception("Failed after 2 objectives")
            else:
                # Retry: should only execute remaining objectives (obj3, obj4)
                executed_objectives.extend(["obj3", "obj4"])
                results = [create_attack_result(i, objective=f"obj{i}") for i in [3, 4]]
                save_attack_results_to_memory(results)
                return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        atomic_attack.run_async = mock_run_with_partial_completion

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
        assert call_count[0] == 2  # Initial attempt + 1 retry
        # All objectives should be executed across both attempts
        assert "obj1" in executed_objectives or "obj3" in executed_objectives

    @pytest.mark.asyncio
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
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 2: Succeeds on first attempt, should not be retried
        async def mock_run_attack2(*args, **kwargs):
            call_count["attack_2"] += 1
            if call_count["attack_2"] == 1:
                results = [create_attack_result(2, objective="objective2")]
                save_attack_results_to_memory(results)
                return AttackExecutorResult(completed_results=results, incomplete_objectives=[])
            else:
                raise AssertionError("Attack 2 should not be retried after completion")

        # Attack 3: Fails on first attempt, succeeds on retry
        async def mock_run_attack3(*args, **kwargs):
            call_count["attack_3"] += 1
            if call_count["attack_3"] == 1:
                raise Exception("Attack 3 failed on first attempt")
            else:
                results = [create_attack_result(3, objective="objective3")]
                save_attack_results_to_memory(results)
                return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        attack1.run_async = mock_run_attack1
        attack2.run_async = mock_run_attack2
        attack3.run_async = mock_run_attack3

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

    @pytest.mark.asyncio
    async def test_resumes_with_multiple_failures_across_attacks(self, mock_objective_target):
        """Test resumption when multiple atomic attacks fail at different stages."""
        # Create 4 atomic attacks
        attacks = [create_mock_atomic_attack(f"attack_{i}", [f"objective{i}"]) for i in range(1, 5)]

        call_count = {f"attack_{i}": 0 for i in range(1, 5)}

        # Attack 1: Succeeds immediately
        async def mock_run_attack1(*args, **kwargs):
            call_count["attack_1"] += 1
            results = [create_attack_result(1, objective="objective1")]
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 2: Fails on first attempt, succeeds on retry
        async def mock_run_attack2(*args, **kwargs):
            call_count["attack_2"] += 1
            if call_count["attack_2"] == 1:
                raise Exception("Attack 2 failed")
            results = [create_attack_result(2, objective="objective2")]
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 3: Only called on retry (after attack 2 succeeds)
        async def mock_run_attack3(*args, **kwargs):
            call_count["attack_3"] += 1
            results = [create_attack_result(3, objective="objective3")]
            save_attack_results_to_memory(results)
            return AttackExecutorResult(completed_results=results, incomplete_objectives=[])

        # Attack 4: Only called on retry
        async def mock_run_attack4(*args, **kwargs):
            call_count["attack_4"] += 1
            results = [create_attack_result(4, objective="objective4")]
            save_attack_results_to_memory(results)
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
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            max_retries=1,
        )

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
