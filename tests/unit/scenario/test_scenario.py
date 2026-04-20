# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenarios.Scenario class."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.identifiers import ComponentIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult
from pyrit.scenario import DatasetConfiguration, ScenarioIdentifier, ScenarioResult
from pyrit.scenario.core import AtomicAttack, Scenario, ScenarioStrategy
from pyrit.score import Scorer

# Reusable test scorer identifier
_TEST_SCORER_ID = ComponentIdentifier(
    class_name="MockScorer",
    class_module="tests.unit.scenarios",
)


def save_attack_results_to_memory(attack_results):
    """Helper function to save attack results to memory (mimics what real attacks do)."""
    memory = CentralMemory.get_memory_instance()
    memory.add_attack_results_to_memory(attack_results=attack_results)


def create_mock_run_async(attack_results):
    """Create a mock run_async that saves results to memory before returning."""

    async def mock_run_async(*args, **kwargs):
        # Save results to memory (mimics what real attacks do)
        save_attack_results_to_memory(attack_results)
        return AttackExecutorResult(completed_results=attack_results, incomplete_objectives=[])

    return AsyncMock(side_effect=mock_run_async)


@pytest.fixture
def mock_atomic_attacks():
    """Create mock AtomicAttack instances for testing."""
    # Create a mock attack strategy
    mock_attack = MagicMock()
    mock_attack.get_objective_target.return_value = MagicMock()
    mock_attack.get_attack_scoring_config.return_value = MagicMock()

    run1 = MagicMock(spec=AtomicAttack)
    run1.atomic_attack_name = "attack_run_1"
    run1._attack = mock_attack
    type(run1).objectives = PropertyMock(return_value=["objective1"])

    run2 = MagicMock(spec=AtomicAttack)
    run2.atomic_attack_name = "attack_run_2"
    run2._attack = mock_attack
    type(run2).objectives = PropertyMock(return_value=["objective2"])

    run3 = MagicMock(spec=AtomicAttack)
    run3.atomic_attack_name = "attack_run_3"
    run3._attack = mock_attack
    type(run3).objectives = PropertyMock(return_value=["objective3"])

    return [run1, run2, run3]


@pytest.fixture
def mock_objective_target():
    """Create a mock objective target for testing."""
    target = MagicMock()
    target.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTarget",
        class_module="test",
    )
    return target


@pytest.fixture
def sample_attack_results():
    """Create sample attack results for testing."""
    return [
        AttackResult(
            conversation_id=f"conv-{i}",
            objective=f"objective{i}",
            outcome=AttackOutcome.SUCCESS,
            executed_turns=1,
        )
        for i in range(5)
    ]


class ConcreteScenario(Scenario):
    """Concrete implementation of Scenario for testing."""

    def __init__(self, atomic_attacks_to_return=None, **kwargs):
        # Default include_default_baseline=False for tests unless explicitly specified
        kwargs.setdefault("include_default_baseline", False)

        # Add required strategy_class if not provided

        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})  # Tagged as concrete, not aggregate
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        kwargs.setdefault("strategy_class", TestStrategy)

        # Add a mock scorer if not provided
        if "objective_scorer" not in kwargs:
            mock_scorer = MagicMock(spec=Scorer)
            mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
            mock_scorer.get_scorer_metrics.return_value = None
            kwargs["objective_scorer"] = mock_scorer

        super().__init__(**kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    @classmethod
    def get_strategy_class(cls):
        """Return a mock strategy class for testing."""

        from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

        # Return a simple mock strategy class for testing
        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})  # Tagged as concrete, not aggregate
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


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioInitialization:
    """Tests for Scenario class initialization."""

    def test_init_with_valid_params(self, mock_objective_target):
        """Test successful initialization with valid parameters."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        assert scenario.name == "Test Scenario"
        assert scenario._identifier.name == "ConcreteScenario"
        assert scenario._identifier.version == 1
        assert scenario._memory_labels == {}
        assert scenario._max_concurrency == 1
        assert scenario._max_retries == 0  # Default value
        assert scenario.atomic_attack_count == 0  # Not initialized yet

    def test_init_creates_scenario_identifier(self, mock_objective_target):
        """Test that initialization creates a proper ScenarioIdentifier."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=3,
        )

        assert isinstance(scenario._identifier, ScenarioIdentifier)
        assert scenario._identifier.name == "ConcreteScenario"
        assert scenario._identifier.version == 3
        assert scenario._identifier.pyrit_version is not None

    def test_init_with_empty_attack_strategies(self, mock_objective_target):
        """Test that initialization works without attack_strategies."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        # Test that scenario initializes correctly without attack_strategies
        assert scenario.atomic_attack_count == 0


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioInitialization2:
    """Tests for Scenario initialize_async method."""

    @pytest.mark.asyncio
    async def test_initialize_async_populates_atomic_attacks(self, mock_atomic_attacks, mock_objective_target):
        """Test that initialize_async populates atomic attacks."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )

        assert scenario.atomic_attack_count == 0

        await scenario.initialize_async(objective_target=mock_objective_target)

        assert scenario.atomic_attack_count == len(mock_atomic_attacks)
        assert scenario._atomic_attacks == mock_atomic_attacks

    @pytest.mark.asyncio
    async def test_initialize_async_sets_objective_target(self, mock_objective_target):
        """Test that initialize_async sets objective_target properly."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        await scenario.initialize_async(objective_target=mock_objective_target)

        assert scenario._objective_target == mock_objective_target
        # Verify it's a ComponentIdentifier with the expected class_name
        assert scenario._objective_target_identifier.class_name == "MockTarget"
        assert scenario._objective_target_identifier.class_module == "test"

    @pytest.mark.asyncio
    async def test_initialize_async_requires_objective_target(self):
        """Test that initialize_async raises ValueError when objective_target is None."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        with pytest.raises(ValueError, match="objective_target is required"):
            await scenario.initialize_async()

    @pytest.mark.asyncio
    async def test_initialize_async_sets_max_retries(self, mock_objective_target):
        """Test that initialize_async sets max_retries."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        await scenario.initialize_async(objective_target=mock_objective_target, max_retries=3)

        assert scenario._max_retries == 3

    @pytest.mark.asyncio
    async def test_initialize_async_sets_max_concurrency(self, mock_objective_target):
        """Test that initialize_async sets max_concurrency."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=5)

        assert scenario._max_concurrency == 5

    @pytest.mark.asyncio
    async def test_initialize_async_sets_memory_labels(self, mock_objective_target):
        """Test that initialize_async sets memory_labels."""
        labels = {"test": "scenario", "category": "encoding"}
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        await scenario.initialize_async(objective_target=mock_objective_target, memory_labels=labels)

        assert scenario._memory_labels == labels

    @pytest.mark.asyncio
    async def test_initialize_async_uses_default_values(self, mock_objective_target):
        """Test that initialize_async uses default values when not provided."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        await scenario.initialize_async(objective_target=mock_objective_target)

        assert scenario._max_retries == 0
        assert scenario._max_concurrency == 10
        assert scenario._memory_labels == {}


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioExecution:
    """Tests for Scenario execution methods."""

    @pytest.mark.asyncio
    async def test_run_async_executes_all_runs(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that run_async executes all atomic attacks sequentially."""
        # Configure each run to return different results
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(objective_target=mock_objective_target)

        result = await scenario.run_async()

        # Verify return type is ScenarioResult
        assert isinstance(result, ScenarioResult)

        # Verify all runs were executed with correct concurrency
        assert len(result.attack_results) == 3
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once_with(max_concurrency=10, return_partial_on_failure=True)

        # Verify results are aggregated correctly by atomic attack name
        assert "attack_run_1" in result.attack_results
        assert "attack_run_2" in result.attack_results
        assert "attack_run_3" in result.attack_results
        assert result.attack_results["attack_run_1"][0] == sample_attack_results[0]
        assert result.attack_results["attack_run_2"][0] == sample_attack_results[1]
        assert result.attack_results["attack_run_3"][0] == sample_attack_results[2]

    @pytest.mark.asyncio
    async def test_run_async_with_custom_concurrency(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that max_concurrency from init is passed to each atomic attack."""
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(objective_target=mock_objective_target, max_concurrency=5)

        result = await scenario.run_async()

        # Verify max_concurrency was passed to each run
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once_with(max_concurrency=5, return_partial_on_failure=True)

        # Verify result structure
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 3

    @pytest.mark.asyncio
    async def test_run_async_aggregates_multiple_results(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that results from multiple atomic attacks are properly aggregated."""
        # Configure runs to return different numbers of results
        mock_atomic_attacks[0].run_async = create_mock_run_async(sample_attack_results[0:2])
        mock_atomic_attacks[1].run_async = create_mock_run_async(sample_attack_results[2:4])
        mock_atomic_attacks[2].run_async = create_mock_run_async(sample_attack_results[4:5])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(objective_target=mock_objective_target)

        result = await scenario.run_async()

        # Should have 3 atomic attacks with results (2 + 2 + 1)
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 3
        assert len(result.attack_results["attack_run_1"]) == 2
        assert len(result.attack_results["attack_run_2"]) == 2
        assert len(result.attack_results["attack_run_3"]) == 1

    @pytest.mark.asyncio
    async def test_run_async_stops_on_error(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that execution stops when an atomic attack fails."""
        mock_atomic_attacks[0].run_async = create_mock_run_async([sample_attack_results[0]])
        mock_atomic_attacks[1].run_async = AsyncMock(side_effect=Exception("Test error"))
        mock_atomic_attacks[2].run_async = create_mock_run_async([sample_attack_results[2]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(objective_target=mock_objective_target)

        with pytest.raises(Exception, match="Test error"):
            await scenario.run_async()

        # First run should have been executed
        mock_atomic_attacks[0].run_async.assert_called_once()
        # Second run should have been attempted
        mock_atomic_attacks[1].run_async.assert_called_once()
        # Third run should not have been executed
        mock_atomic_attacks[2].run_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_async_fails_without_initialization(self, mock_objective_target):
        """Test that run_async fails if initialize_async was not called."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        with pytest.raises(ValueError, match="Cannot run scenario with no atomic attacks"):
            await scenario.run_async()

    @pytest.mark.asyncio
    async def test_run_async_returns_scenario_result_with_identifier(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that run_async returns ScenarioResult with proper identifier."""
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=5,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        await scenario.initialize_async(objective_target=mock_objective_target)

        result = await scenario.run_async()

        assert isinstance(result, ScenarioResult)
        assert isinstance(result.scenario_identifier, ScenarioIdentifier)
        assert result.scenario_identifier.name == "ConcreteScenario"
        assert result.scenario_identifier.version == 5
        assert result.scenario_identifier.pyrit_version is not None
        assert result.get_strategies_used() == [
            "attack_run_1",
            "attack_run_2",
            "attack_run_3",
        ]


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioProperties:
    """Tests for Scenario property methods."""

    def test_name_property(self, mock_objective_target):
        """Test that name property returns the scenario name."""
        scenario = ConcreteScenario(
            name="My Test Scenario",
            version=1,
        )

        assert scenario.name == "My Test Scenario"

    @pytest.mark.asyncio
    async def test_atomic_attack_count_property(self, mock_atomic_attacks, mock_objective_target):
        """Test that atomic_attack_count returns the correct count."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )

        assert scenario.atomic_attack_count == 0

        await scenario.initialize_async(objective_target=mock_objective_target)

        assert scenario.atomic_attack_count == 3

    @pytest.mark.asyncio
    async def test_atomic_attack_count_with_different_sizes(self, mock_objective_target):
        """Test atomic_attack_count with different numbers of atomic attacks."""
        # Create mock attack strategy
        mock_attack = MagicMock()
        mock_attack.get_objective_target.return_value = mock_objective_target
        mock_attack.get_attack_scoring_config.return_value = MagicMock()

        single_run_mock = MagicMock(spec=AtomicAttack)
        single_run_mock.atomic_attack_name = "attack_1"
        single_run_mock._attack = mock_attack
        type(single_run_mock).objectives = PropertyMock(return_value=["obj1"])
        single_run = [single_run_mock]

        scenario1 = ConcreteScenario(
            name="Single",
            version=1,
            atomic_attacks_to_return=single_run,
        )
        await scenario1.initialize_async(objective_target=mock_objective_target)
        assert scenario1.atomic_attack_count == 1

        many_runs = []
        for i in range(10):
            run = MagicMock(spec=AtomicAttack)
            run.atomic_attack_name = f"attack_{i}"
            run._attack = mock_attack
            type(run).objectives = PropertyMock(return_value=[f"obj{i}"])
            many_runs.append(run)

        scenario2 = ConcreteScenario(
            name="Many",
            version=1,
            atomic_attacks_to_return=many_runs,
        )
        await scenario2.initialize_async(objective_target=mock_objective_target)
        assert scenario2.atomic_attack_count == 10


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResult:
    """Tests for ScenarioResult class."""

    def test_scenario_result_initialization(self, sample_attack_results):
        """Test ScenarioResult initialization."""
        identifier = ScenarioIdentifier(name="Test", scenario_version=1)
        result = ScenarioResult(
            scenario_identifier=identifier,
            objective_target_identifier=ComponentIdentifier(class_name="TestTarget", class_module="test"),
            attack_results={"base64": sample_attack_results[:3], "rot13": sample_attack_results[3:]},
            objective_scorer_identifier=_TEST_SCORER_ID,
        )

        assert result.scenario_identifier == identifier
        assert result.get_strategies_used() == ["base64", "rot13"]
        assert len(result.attack_results) == 2
        assert len(result.attack_results["base64"]) == 3
        assert len(result.attack_results["rot13"]) == 2

    def test_scenario_result_with_empty_results(self):
        """Test ScenarioResult with empty attack results."""
        identifier = ScenarioIdentifier(name="TestScenario", scenario_version=1)
        result = ScenarioResult(
            scenario_identifier=identifier,
            objective_target_identifier=ComponentIdentifier(
                class_name="TestTarget",
                class_module="test",
            ),
            attack_results={"base64": []},
            objective_scorer_identifier=_TEST_SCORER_ID,
        )

        assert len(result.attack_results["base64"]) == 0
        assert result.objective_achieved_rate() == 0

    def test_scenario_result_objective_achieved_rate(self, sample_attack_results):
        """Test objective_achieved_rate calculation."""
        identifier = ScenarioIdentifier(name="Test", scenario_version=1)

        # All successful
        result = ScenarioResult(
            scenario_identifier=identifier,
            objective_target_identifier=ComponentIdentifier(
                class_name="TestTarget",
                class_module="test",
            ),
            attack_results={"base64": sample_attack_results},
            objective_scorer_identifier=_TEST_SCORER_ID,
        )
        assert result.objective_achieved_rate() == 100

        # Mixed outcomes
        mixed_results = sample_attack_results[:3] + [
            AttackResult(
                conversation_id="conv-fail",
                objective="objective",
                outcome=AttackOutcome.FAILURE,
                executed_turns=1,
            ),
            AttackResult(
                conversation_id="conv-fail2",
                objective="objective",
                outcome=AttackOutcome.FAILURE,
                executed_turns=1,
            ),
        ]
        result2 = ScenarioResult(
            scenario_identifier=identifier,
            objective_target_identifier=ComponentIdentifier(
                class_name="TestTarget",
                class_module="test",
            ),
            attack_results={"base64": mixed_results},
            objective_scorer_identifier=_TEST_SCORER_ID,
        )
        assert result2.objective_achieved_rate() == 60  # 3 out of 5


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioIdentifier:
    """Tests for ScenarioIdentifier class."""

    def test_scenario_identifier_initialization(self):
        """Test ScenarioIdentifier initialization."""
        identifier = ScenarioIdentifier(name="TestScenario", scenario_version=2)

        assert identifier.name == "TestScenario"
        assert identifier.version == 2
        assert identifier.pyrit_version is not None

    def test_scenario_identifier_with_custom_pyrit_version(self):
        """Test ScenarioIdentifier initialization sets pyrit version automatically."""
        identifier = ScenarioIdentifier(name="TestScenario", scenario_version=1)

        assert identifier.pyrit_version is not None
        assert identifier.name == "TestScenario"

    def test_scenario_identifier_with_init_data(self):
        """Test ScenarioIdentifier with init_data."""
        init_data = {"param1": "value1", "param2": 42}
        identifier = ScenarioIdentifier(name="TestScenario", scenario_version=1, init_data=init_data)

        assert identifier.init_data == init_data


def create_mock_truefalse_scorer():
    """Create a mock TrueFalseScorer for testing baseline-only execution."""
    from pyrit.score import TrueFalseScorer

    mock_scorer = MagicMock(spec=TrueFalseScorer)
    mock_scorer.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTrueFalseScorer",
        class_module="test",
    )
    mock_scorer.get_scorer_metrics.return_value = None
    # Make isinstance check work
    mock_scorer.__class__ = TrueFalseScorer
    return mock_scorer


class ConcreteScenarioWithTrueFalseScorer(Scenario):
    """Concrete implementation of Scenario for testing baseline-only execution."""

    def __init__(self, atomic_attacks_to_return=None, **kwargs):
        # Add required strategy_class if not provided

        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        kwargs.setdefault("strategy_class", TestStrategy)

        # Use TrueFalseScorer mock if not provided
        if "objective_scorer" not in kwargs:
            kwargs["objective_scorer"] = create_mock_truefalse_scorer()

        super().__init__(**kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    @classmethod
    def get_strategy_class(cls):
        """Return a mock strategy class for testing."""

        from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})
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


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioBaselineOnlyExecution:
    """Tests for baseline-only execution (empty strategies with include_baseline=True)."""

    @pytest.mark.asyncio
    async def test_initialize_async_with_empty_strategies_and_baseline(self, mock_objective_target):
        """Test that baseline is included when include_baseline=True, regardless of strategies."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        # Create a scenario with include_default_baseline=True and TrueFalseScorer
        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Only Test",
            version=1,
            include_default_baseline=True,
        )

        # Create a mock dataset config with seed groups
        mock_dataset_config = MagicMock(spec=DatasetConfiguration)
        mock_dataset_config.get_all_seed_attack_groups.return_value = [
            SeedAttackGroup(seeds=[SeedObjective(value="test objective 1")]),
            SeedAttackGroup(seeds=[SeedObjective(value="test objective 2")]),
        ]

        # Initialize with None (default strategy) — [] also works, both expand defaults
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=None,
            dataset_config=mock_dataset_config,
        )

        # Should have exactly one attack - the baseline
        assert scenario.atomic_attack_count == 1
        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"

    @pytest.mark.asyncio
    async def test_baseline_only_execution_runs_successfully(self, mock_objective_target, sample_attack_results):
        """Test that baseline-only scenario can run successfully."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        # Create a scenario with include_default_baseline=True and TrueFalseScorer
        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Only Test",
            version=1,
            include_default_baseline=True,
        )

        # Create a mock dataset config with seed groups
        mock_dataset_config = MagicMock(spec=DatasetConfiguration)
        mock_dataset_config.get_all_seed_attack_groups.return_value = [
            SeedAttackGroup(seeds=[SeedObjective(value="test objective 1")]),
        ]

        # Initialize with None — [] also expands defaults now, both are equivalent
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=None,  # same as [] now
            dataset_config=mock_dataset_config,
        )

        # Mock the baseline attack's run_async
        scenario._atomic_attacks[0].run_async = create_mock_run_async([sample_attack_results[0]])

        # Run the scenario
        result = await scenario.run_async()

        # Verify the result
        assert isinstance(result, ScenarioResult)
        assert "baseline" in result.attack_results
        assert len(result.attack_results["baseline"]) == 1

    @pytest.mark.asyncio
    async def test_empty_strategies_without_baseline_allows_initialization(self, mock_objective_target):
        """Test that no strategies + no baseline allows initialization but fails at run time."""
        scenario = ConcreteScenario(
            name="No Baseline Test",
            version=1,
            include_default_baseline=False,  # No baseline
        )

        mock_dataset_config = MagicMock(spec=DatasetConfiguration)

        # None strategies with no baseline: _get_atomic_attacks_async returns []
        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=None,
            dataset_config=mock_dataset_config,
        )

        # But running should fail because there are no atomic attacks
        with pytest.raises(ValueError, match="Cannot run scenario with no atomic attacks"):
            await scenario.run_async()

    @pytest.mark.asyncio
    async def test_standalone_baseline_uses_dataset_config_seeds(self, mock_objective_target):
        """Test that standalone baseline uses seed groups from dataset_config."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Seeds Test",
            version=1,
            include_default_baseline=True,
        )

        # Create specific seed groups to verify they're used
        expected_seeds = [
            SeedAttackGroup(seeds=[SeedObjective(value="objective_a")]),
            SeedAttackGroup(seeds=[SeedObjective(value="objective_b")]),
            SeedAttackGroup(seeds=[SeedObjective(value="objective_c")]),
        ]

        mock_dataset_config = MagicMock(spec=DatasetConfiguration)
        mock_dataset_config.get_all_seed_attack_groups.return_value = expected_seeds

        await scenario.initialize_async(
            objective_target=mock_objective_target,
            scenario_strategies=None,
            dataset_config=mock_dataset_config,
        )

        # Verify the baseline attack has the expected seed groups
        baseline_attack = scenario._atomic_attacks[0]
        assert baseline_attack.atomic_attack_name == "baseline"
        assert baseline_attack.seed_groups == expected_seeds

    def test_empty_list_strategies_expands_defaults_same_as_none(self):
        """Test that [] and None both expand to the default strategy set."""
        scenario = ConcreteScenario(name="Test", version=1)
        strategy_class = scenario.get_strategy_class()
        default = scenario.get_default_strategy()

        resolved_none = strategy_class.resolve(None, default=default)
        resolved_empty = strategy_class.resolve([], default=default)

        assert resolved_none == resolved_empty
        assert len(resolved_none) > 0


class TestGetDefaultObjectiveScorer:
    """Tests for Scenario._get_default_objective_scorer method."""

    @patch("pyrit.scenario.core.scenario.ScorerRegistry")
    def test_returns_registry_scorer_when_tagged(self, mock_registry_cls) -> None:
        """Test that a tagged scorer from the registry is returned."""
        from pyrit.score import TrueFalseScorer

        mock_scorer = MagicMock(spec=TrueFalseScorer)
        mock_scorer.__class__ = TrueFalseScorer

        mock_entry = MagicMock()
        mock_entry.instance = mock_scorer

        mock_registry = MagicMock()
        mock_registry.get_by_tag.return_value = [mock_entry]
        mock_registry_cls.get_registry_singleton.return_value = mock_registry

        result = Scenario._get_default_objective_scorer(MagicMock())
        assert result is mock_scorer

    @patch("pyrit.scenario.core.scenario.OpenAIChatTarget")
    @patch("pyrit.scenario.core.scenario.ScorerRegistry")
    def test_returns_fallback_when_registry_empty(self, mock_registry_cls, mock_oai_target) -> None:
        """Test fallback to TrueFalseInverterScorer when no tagged scorer exists."""
        from pyrit.score import TrueFalseInverterScorer

        mock_registry = MagicMock()
        mock_registry.get_by_tag.return_value = []
        mock_registry_cls.get_registry_singleton.return_value = mock_registry

        result = Scenario._get_default_objective_scorer(MagicMock())
        assert isinstance(result, TrueFalseInverterScorer)
