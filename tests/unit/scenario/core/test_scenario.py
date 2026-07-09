# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenarios.Scenario class."""

import asyncio
from typing import ClassVar
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, patch

import pytest

try:
    from builtins import ExceptionGroup  # type: ignore[attr-defined,ty:unresolved-import]
except ImportError:  # pragma: no cover - 3.10 only
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef,ty:unresolved-import]

from pyrit.executor.attack.core import AttackExecutorResult
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier
from pyrit.scenario import (
    DatasetAttackConfiguration,
    DatasetConfiguration,
    ScenarioIdentifier,
    ScenarioResult,
)
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioStrategy
from pyrit.score import Scorer
from tests.unit.mocks import make_scenario_identifier, make_scenario_result

# Reusable test scorer identifier
_TEST_SCORER_ID = ComponentIdentifier(
    class_name="MockScorer",
    class_module="tests.unit.scenarios",
)


def save_attack_results_to_memory(attack_results):
    """Helper function to save attack results to memory (mimics what real attacks do)."""
    memory = CentralMemory.get_memory_instance()
    memory.add_attack_results_to_memory(attack_results=attack_results)


def _stamp_scenario_linkage(*, attack_results, atomic_attack):
    """
    Stamp attribution_parent_id + attribution_data on each AttackResult the
    same way the real attack persistence path does. Mirrors what
    ``_DefaultAttackStrategyEventHandler._apply_attribution`` does at runtime
    so test fixtures that mock out the executor still produce DB rows the new
    foreign-key-based hydration can find.
    """
    sid = getattr(atomic_attack, "_scenario_result_id", None)
    name = getattr(atomic_attack, "atomic_attack_name", None)
    if not sid or not name:
        return
    for r in attack_results:
        r.attribution_parent_id = sid
        r.attribution_data = {"parent_collection": name}


def create_mock_run_async(attack_results, *, atomic_attack=None):
    """
    Create a mock ``run_async`` that stamps + saves results to memory.

    Pass ``atomic_attack`` (the AtomicAttack MagicMock) so the helper can copy
    its ``_scenario_result_id`` (set by ``Scenario._execute_scenario_async``)
    and ``atomic_attack_name`` onto each result. Without those the foreign-key-
    based hydration in ``get_scenario_results`` won't see the rows.
    """

    async def mock_run_async(*args, **kwargs):
        if atomic_attack is not None:
            _stamp_scenario_linkage(attack_results=attack_results, atomic_attack=atomic_attack)
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
    run1.display_group = "attack_run_1"
    run1._attack = mock_attack
    run1._scenario_result_id = None
    run1.set_scenario_result_id = MagicMock(side_effect=lambda sid: setattr(run1, "_scenario_result_id", sid))
    type(run1).objectives = PropertyMock(return_value=["objective1"])

    run2 = MagicMock(spec=AtomicAttack)
    run2.atomic_attack_name = "attack_run_2"
    run2.display_group = "attack_run_2"
    run2._attack = mock_attack
    run2._scenario_result_id = None
    run2.set_scenario_result_id = MagicMock(side_effect=lambda sid: setattr(run2, "_scenario_result_id", sid))
    type(run2).objectives = PropertyMock(return_value=["objective2"])

    run3 = MagicMock(spec=AtomicAttack)
    run3.atomic_attack_name = "attack_run_3"
    run3.display_group = "attack_run_3"
    run3._attack = mock_attack
    run3._scenario_result_id = None
    run3.set_scenario_result_id = MagicMock(side_effect=lambda sid: setattr(run3, "_scenario_result_id", sid))
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
            labels={"test_label": f"value{i}"},
        )
        for i in range(5)
    ]


class ConcreteScenario(Scenario):
    """Concrete implementation of Scenario for testing."""

    # Tests using this fixture should default to no baseline; set the class policy to Forbidden
    # so we don't have to thread include_baseline=False through every initialize_async call.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(self, *, atomic_attacks_to_return=None, **kwargs):
        # Add required strategy_class if not provided

        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})  # Tagged as concrete, not aggregate
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        kwargs.setdefault("strategy_class", TestStrategy)
        kwargs.setdefault("default_strategy", kwargs["strategy_class"].ALL)
        kwargs.setdefault("default_dataset_config", DatasetConfiguration())

        # Add a mock scorer if not provided
        if "objective_scorer" not in kwargs:
            mock_scorer = MagicMock(spec=Scorer)
            mock_scorer.get_identifier.return_value = _TEST_SCORER_ID
            mock_scorer.get_scorer_metrics.return_value = None
            kwargs["objective_scorer"] = mock_scorer

        super().__init__(**kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    async def _resolve_seed_groups_by_dataset_async(self):
        return {}

    async def _build_atomic_attacks_async(self, *, context):
        return self._atomic_attacks_to_return


def test_scenario_base_class_is_abstract():
    """The base ``Scenario`` declares ``_build_atomic_attacks_async`` abstract and can't be instantiated directly."""
    assert "_build_atomic_attacks_async" in Scenario.__abstractmethods__
    with pytest.raises(TypeError, match="_build_atomic_attacks_async"):
        Scenario()  # type: ignore[abstract]


def test_subclass_without_build_atomic_attacks_async_is_abstract():
    """A subclass that omits ``_build_atomic_attacks_async`` stays abstract and fails at instantiation."""

    class IncompleteScenario(Scenario):
        """Subclass that forgets to implement the required extension point."""

    assert "_build_atomic_attacks_async" in IncompleteScenario.__abstractmethods__
    with pytest.raises(TypeError, match="_build_atomic_attacks_async"):
        IncompleteScenario()  # type: ignore[abstract]


def test_subclass_implementing_build_atomic_attacks_async_is_concrete():
    """Implementing ``_build_atomic_attacks_async`` clears the abstract marker so the subclass is instantiable."""
    assert not ConcreteScenario.__abstractmethods__


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
        assert scenario._version == 1
        assert scenario._description == "Concrete implementation of Scenario for testing."
        assert scenario._memory_labels == {}
        assert scenario._max_concurrency is None
        assert scenario._max_retries == 0  # Default value
        assert scenario.atomic_attack_count == 0  # Not initialized yet

    def test_init_stores_scenario_version_and_description(self, mock_objective_target):
        """Test that initialization stores run metadata used by ScenarioResult."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=3,
        )

        assert scenario._version == 3
        assert scenario._description == "Concrete implementation of Scenario for testing."

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

    async def test_initialize_async_populates_atomic_attacks(self, mock_atomic_attacks, mock_objective_target):
        """Test that initialize_async populates atomic attacks."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )

        assert scenario.atomic_attack_count == 0

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert scenario.atomic_attack_count == len(mock_atomic_attacks)
        assert scenario._atomic_attacks == mock_atomic_attacks

    async def test_initialize_async_sets_objective_target(self, mock_objective_target):
        """Test that initialize_async sets objective_target properly."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert scenario._objective_target == mock_objective_target
        # Verify it's a ComponentIdentifier with the expected class_name
        assert scenario._objective_target_identifier.class_name == "MockTarget"
        assert scenario._objective_target_identifier.class_module == "test"

    async def test_initialize_async_requires_objective_target(self):
        """Test that initialize_async raises ValueError when objective_target is None."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        with pytest.raises(ValueError, match="objective_target is required"):
            await scenario.initialize_async()

    async def test_initialize_async_sets_max_retries(self, mock_objective_target):
        """Test that initialize_async sets max_retries."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_retries": 3,
            }
        )
        await scenario.initialize_async()

        assert scenario._max_retries == 3

    async def test_initialize_async_sets_max_concurrency(self, mock_objective_target):
        """Test that initialize_async sets max_concurrency."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 5,
            }
        )
        await scenario.initialize_async()

        assert scenario._max_concurrency == 5

    async def test_initialize_async_sets_memory_labels(self, mock_objective_target):
        """Test that initialize_async sets memory_labels."""
        labels = {"test": "scenario", "category": "encoding"}
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "memory_labels": labels,
            }
        )
        await scenario.initialize_async()

        assert scenario._memory_labels == labels

    async def test_initialize_async_uses_default_values(self, mock_objective_target):
        """Test that initialize_async uses default values when not provided."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert scenario._max_retries == 0
        assert scenario._max_concurrency == 4
        assert scenario._memory_labels == {}

    @pytest.mark.asyncio
    async def test_initialize_async_validates_target_requirements(self, mock_objective_target):
        """Test that initialize_async validates objective_target against TARGET_REQUIREMENTS."""
        scenario = ConcreteScenario(name="Test Scenario", version=1)

        with patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate") as mock_validate:
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()

        mock_validate.assert_called_once_with(target=mock_objective_target)

    @pytest.mark.asyncio
    async def test_initialize_async_propagates_target_requirements_error(self, mock_objective_target):
        """Test that initialize_async surfaces errors from TARGET_REQUIREMENTS.validate."""
        scenario = ConcreteScenario(name="Test Scenario", version=1)

        with patch(
            "pyrit.prompt_target.common.target_requirements.TargetRequirements.validate",
            side_effect=ValueError("Target must natively support 'editable_history'"),
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(ValueError, match="editable_history"):
                await scenario.initialize_async()

    def test_scenario_base_target_requirements_is_empty(self):
        """Base Scenario declares an empty TargetRequirements so it accepts any target by default."""
        from pyrit.prompt_target.common.target_requirements import TargetRequirements

        assert isinstance(Scenario.TARGET_REQUIREMENTS, TargetRequirements)
        assert Scenario.TARGET_REQUIREMENTS.required == frozenset()
        assert Scenario.TARGET_REQUIREMENTS.native_required == frozenset()


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioExecution:
    """Tests for Scenario execution methods."""

    async def test_run_async_executes_all_runs(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """Test that run_async executes all atomic attacks."""
        # Configure each run to return different results
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]], atomic_attack=run)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        result = await scenario.run_async()

        # Verify return type is ScenarioResult
        assert isinstance(result, ScenarioResult)

        # Verify all runs were executed. Default max_concurrency=4 with 3 atomic attacks
        # means parallel path: each atomic attack receives the shared executor whose
        # internal semaphore caps total in-flight objectives at 4.
        assert len(result.attack_results) == 3
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once_with(executor=ANY, return_partial_on_failure=True)

        # Verify results are aggregated correctly by atomic attack name
        assert "attack_run_1" in result.attack_results
        assert "attack_run_2" in result.attack_results
        assert "attack_run_3" in result.attack_results
        assert result.attack_results["attack_run_1"][0] == sample_attack_results[0]
        assert result.attack_results["attack_run_2"][0] == sample_attack_results[1]
        assert result.attack_results["attack_run_3"][0] == sample_attack_results[2]

    async def test_run_async_with_custom_concurrency(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that max_concurrency from init is split across atomic attacks."""
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
                "max_concurrency": 5,
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        # 3 atomic attacks, max_concurrency=5 -> parallel path with a shared AttackExecutor.
        # Each atomic attack receives the same executor instance.
        for run in mock_atomic_attacks:
            run.run_async.assert_called_once_with(executor=ANY, return_partial_on_failure=True)

        # Verify result structure
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 3

    async def test_run_async_aggregates_multiple_results(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that results from multiple atomic attacks are properly aggregated."""
        # Configure runs to return different numbers of results
        mock_atomic_attacks[0].run_async = create_mock_run_async(
            sample_attack_results[0:2], atomic_attack=mock_atomic_attacks[0]
        )
        mock_atomic_attacks[1].run_async = create_mock_run_async(
            sample_attack_results[2:4], atomic_attack=mock_atomic_attacks[1]
        )
        mock_atomic_attacks[2].run_async = create_mock_run_async(
            sample_attack_results[4:5], atomic_attack=mock_atomic_attacks[2]
        )

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        result = await scenario.run_async()

        # Should have 3 atomic attacks with results (2 + 2 + 1)
        assert isinstance(result, ScenarioResult)
        assert len(result.attack_results) == 3
        assert len(result.attack_results["attack_run_1"]) == 2
        assert len(result.attack_results["attack_run_2"]) == 2
        assert len(result.attack_results["attack_run_3"]) == 1

    async def test_run_async_stops_on_error(self, mock_atomic_attacks, sample_attack_results, mock_objective_target):
        """With max_concurrency=1 the single worker pulls one attack at a time and stops on first failure."""
        mock_atomic_attacks[0].run_async = create_mock_run_async([sample_attack_results[0]])
        mock_atomic_attacks[1].run_async = AsyncMock(side_effect=Exception("Test error"))
        mock_atomic_attacks[2].run_async = create_mock_run_async([sample_attack_results[2]])

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        # Single worker so abort-on-first-failure is deterministic.
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 1,
            }
        )
        await scenario.initialize_async()

        with pytest.raises(Exception, match="Test error"):
            await scenario.run_async()

        # First run should have been executed
        mock_atomic_attacks[0].run_async.assert_called_once()
        # Second run should have been attempted
        mock_atomic_attacks[1].run_async.assert_called_once()
        # Third run should not have been executed (worker stops pulling after failure)
        mock_atomic_attacks[2].run_async.assert_not_called()

    async def test_run_async_fails_without_initialization(self, mock_objective_target):
        """Test that run_async fails if initialize_async was not called."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
        )

        with pytest.raises(ValueError, match="Cannot run scenario with no atomic attacks"):
            await scenario.run_async()

    async def test_run_async_returns_scenario_result_with_identifier(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Test that run_async returns ScenarioResult with proper identifier."""
        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = create_mock_run_async([sample_attack_results[i]], atomic_attack=run)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=5,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        result = await scenario.run_async()

        assert isinstance(result, ScenarioResult)
        assert result.scenario_name == "ConcreteScenario"
        assert result.scenario_version == 5
        assert result.pyrit_version is not None
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

    async def test_atomic_attack_count_property(self, mock_atomic_attacks, mock_objective_target):
        """Test that atomic_attack_count returns the correct count."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )

        assert scenario.atomic_attack_count == 0

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert scenario.atomic_attack_count == 3

    async def test_atomic_attack_count_with_different_sizes(self, mock_objective_target):
        """Test atomic_attack_count with different numbers of atomic attacks."""
        # Create mock attack strategy
        mock_attack = MagicMock()
        mock_attack.get_objective_target.return_value = mock_objective_target
        mock_attack.get_attack_scoring_config.return_value = MagicMock()

        single_run_mock = MagicMock(spec=AtomicAttack)
        single_run_mock.atomic_attack_name = "attack_1"
        single_run_mock.display_group = "attack_1"
        single_run_mock._attack = mock_attack
        single_run_mock._scenario_result_id = None
        single_run_mock.set_scenario_result_id = MagicMock(
            side_effect=lambda sid: setattr(single_run_mock, "_scenario_result_id", sid)
        )
        type(single_run_mock).objectives = PropertyMock(return_value=["obj1"])
        single_run = [single_run_mock]

        scenario1 = ConcreteScenario(
            name="Single",
            version=1,
            atomic_attacks_to_return=single_run,
        )
        scenario1.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario1.initialize_async()
        assert scenario1.atomic_attack_count == 1

        many_runs = []
        for i in range(10):
            run = MagicMock(spec=AtomicAttack)
            run.atomic_attack_name = f"attack_{i}"
            run.display_group = f"attack_{i}"
            run._attack = mock_attack
            run._scenario_result_id = None
            # Capture run by default arg to avoid late-binding in the closure.
            run.set_scenario_result_id = MagicMock(
                side_effect=lambda sid, _run=run: setattr(_run, "_scenario_result_id", sid)
            )
            type(run).objectives = PropertyMock(return_value=[f"obj{i}"])
            many_runs.append(run)

        scenario2 = ConcreteScenario(
            name="Many",
            version=1,
            atomic_attacks_to_return=many_runs,
        )
        scenario2.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario2.initialize_async()
        assert scenario2.atomic_attack_count == 10


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResult:
    """Tests for ScenarioResult class."""

    def test_scenario_result_initialization(self, sample_attack_results):
        """Test ScenarioResult initialization."""
        result = make_scenario_result(
            scenario_name="Test",
            scenario_version=1,
            objective_target_identifier=ComponentIdentifier(class_name="TestTarget", class_module="test"),
            attack_results={
                "base64": sample_attack_results[:3],
                "rot13": sample_attack_results[3:],
            },
            objective_scorer_identifier=_TEST_SCORER_ID,
        )

        assert result.scenario_name == "Test"
        assert result.scenario_version == 1
        assert result.get_strategies_used() == ["base64", "rot13"]
        assert len(result.attack_results) == 2
        assert len(result.attack_results["base64"]) == 3
        assert len(result.attack_results["rot13"]) == 2

    def test_scenario_result_with_empty_results(self):
        """Test ScenarioResult with empty attack results."""
        result = make_scenario_result(
            scenario_name="TestScenario",
            scenario_version=1,
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
        # All successful
        result = make_scenario_result(
            scenario_name="Test",
            scenario_version=1,
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
        result2 = make_scenario_result(
            scenario_name="Test",
            scenario_version=1,
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
    """Tests for ScenarioIdentifier registry projection."""

    def test_scenario_identifier_initialization(self):
        """Test ScenarioIdentifier projection initialization."""
        identifier = ScenarioIdentifier(
            class_name="TestScenario",
            class_module="tests.unit.scenario.core.test_scenario",
            version=2,
        )

        assert identifier.class_name == "TestScenario"
        assert identifier.class_module == "tests.unit.scenario.core.test_scenario"

    def test_scenario_identifier_accepts_registry_projection_fields(self):
        """Test ScenarioIdentifier stores registry projection metadata."""
        identifier = ScenarioIdentifier(
            class_name="TestScenario",
            class_module="tests.unit.scenario.core.test_scenario",
            techniques=["baseline"],
            datasets=["harmful_content"],
        )

        assert identifier.techniques == ["baseline"]
        assert identifier.datasets == ["harmful_content"]


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

    def __init__(self, *, atomic_attacks_to_return=None, **kwargs):
        # Add required strategy_class if not provided

        class TestStrategy(ScenarioStrategy):
            TEST = ("test", {"concrete"})
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        kwargs.setdefault("strategy_class", TestStrategy)
        kwargs.setdefault("default_strategy", kwargs["strategy_class"].ALL)
        kwargs.setdefault("default_dataset_config", DatasetConfiguration())

        # Use TrueFalseScorer mock if not provided
        if "objective_scorer" not in kwargs:
            kwargs["objective_scorer"] = create_mock_truefalse_scorer()

        super().__init__(**kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    async def _resolve_seed_groups_by_dataset_async(self):
        return await self._dataset_config.get_attack_groups_by_dataset_async()

    async def _build_atomic_attacks_async(self, *, context):
        return list(self._atomic_attacks_to_return)


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioBaselineOnlyExecution:
    """Tests for baseline-only execution (empty strategies with include_baseline=True)."""

    async def test_initialize_async_with_empty_strategies_and_baseline(self, mock_objective_target):
        """Test that baseline is included when include_baseline=True, regardless of strategies."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        # Create a scenario with TrueFalseScorer; baseline is included by default
        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Only Test",
            version=1,
        )

        # Create a mock dataset config with seed groups
        mock_dataset_config = MagicMock(spec=DatasetAttackConfiguration)
        mock_dataset_config.get_attack_groups_by_dataset_async.return_value = {
            "default": [
                SeedAttackGroup(seeds=[SeedObjective(value="test objective 1")]),
                SeedAttackGroup(seeds=[SeedObjective(value="test objective 2")]),
            ]
        }

        # Initialize with None (default strategy) — [] also works, both expand defaults
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_strategies": None,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()

        # Should have exactly one attack - the baseline
        assert scenario.atomic_attack_count == 1
        assert scenario._atomic_attacks[0].atomic_attack_name == "baseline"

    async def test_baseline_only_execution_runs_successfully(self, mock_objective_target, sample_attack_results):
        """Test that baseline-only scenario can run successfully."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        # Create a scenario with TrueFalseScorer; baseline is included by default
        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Only Test",
            version=1,
        )

        # Create a mock dataset config with seed groups
        mock_dataset_config = MagicMock(spec=DatasetAttackConfiguration)
        mock_dataset_config.get_attack_groups_by_dataset_async.return_value = {
            "default": [SeedAttackGroup(seeds=[SeedObjective(value="test objective 1")])]
        }

        # Initialize with None — [] also expands defaults now, both are equivalent
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_strategies": None,  # same as [] now
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()

        # Mock the baseline attack's run_async
        scenario._atomic_attacks[0].run_async = create_mock_run_async(
            [sample_attack_results[0]], atomic_attack=scenario._atomic_attacks[0]
        )

        # Run the scenario
        result = await scenario.run_async()

        # Verify the result
        assert isinstance(result, ScenarioResult)
        assert "baseline" in result.attack_results
        assert len(result.attack_results["baseline"]) == 1

    async def test_empty_strategies_without_baseline_allows_initialization(self, mock_objective_target):
        """Test that no strategies + no baseline allows initialization but fails at run time."""
        scenario = ConcreteScenario(
            name="No Baseline Test",
            version=1,
        )

        mock_dataset_config = MagicMock(spec=DatasetConfiguration)

        # None strategies with no baseline: _get_atomic_attacks_async returns []
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_strategies": None,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()

        # But running should fail because there are no atomic attacks
        with pytest.raises(ValueError, match="Cannot run scenario with no atomic attacks"):
            await scenario.run_async()

    async def test_standalone_baseline_uses_dataset_config_seeds(self, mock_objective_target):
        """Test that standalone baseline uses seed groups from dataset_config."""
        from pyrit.models import SeedAttackGroup, SeedObjective

        scenario = ConcreteScenarioWithTrueFalseScorer(
            name="Baseline Seeds Test",
            version=1,
        )

        # Create specific seed groups to verify they're used
        expected_seeds = [
            SeedAttackGroup(seeds=[SeedObjective(value="objective_a")]),
            SeedAttackGroup(seeds=[SeedObjective(value="objective_b")]),
            SeedAttackGroup(seeds=[SeedObjective(value="objective_c")]),
        ]

        mock_dataset_config = MagicMock(spec=DatasetAttackConfiguration)
        mock_dataset_config.get_attack_groups_by_dataset_async.return_value = {"default": expected_seeds}

        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_strategies": None,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()

        # Verify the baseline attack has the expected seed groups
        baseline_attack = scenario._atomic_attacks[0]
        assert baseline_attack.atomic_attack_name == "baseline"
        assert baseline_attack.seed_groups == expected_seeds

    def test_empty_list_strategies_expands_defaults_same_as_none(self):
        """Test that [] and None both expand to the default strategy set."""
        scenario = ConcreteScenario(name="Test", version=1)
        strategy_class = scenario._strategy_class
        default = scenario._default_strategy

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
        mock_registry.instances.get_by_tag.return_value = [mock_entry]
        mock_registry_cls.get_registry_singleton.return_value = mock_registry

        # Mock self with _get_additional_scoring_questions returning empty sequence
        mock_self = MagicMock()
        type(mock_self)._get_additional_scoring_questions = classmethod(lambda cls: [])

        result = Scenario._get_default_objective_scorer(mock_self)
        assert result is mock_scorer

    @patch("pyrit.scenario.core.scenario.get_default_scorer_target")
    @patch("pyrit.scenario.core.scenario.ScorerRegistry")
    def test_returns_fallback_when_registry_empty(self, mock_registry_cls, mock_get_scorer_target) -> None:
        """Test fallback to TrueFalseInverterScorer when no tagged scorer exists."""
        from pyrit.score import TrueFalseInverterScorer

        mock_registry = MagicMock()
        mock_registry.instances.get_by_tag.return_value = []
        mock_registry_cls.get_registry_singleton.return_value = mock_registry

        # Mock self with _get_additional_scoring_questions returning empty sequence
        mock_self = MagicMock()
        type(mock_self)._get_additional_scoring_questions = classmethod(lambda cls: [])

        result = Scenario._get_default_objective_scorer(mock_self)
        assert isinstance(result, TrueFalseInverterScorer)


@pytest.mark.usefixtures("patch_central_database")
async def test_execute_scenario_raises_when_scenario_result_id_is_none():
    """Test that _execute_scenario_async raises ValueError when _scenario_result_id is None."""
    scenario = ConcreteScenario.__new__(ConcreteScenario)
    scenario._scenario_result_id = None
    scenario._name = "test_scenario"
    scenario._atomic_attacks = []
    scenario._memory = MagicMock()

    with pytest.raises(ValueError, match="self._scenario_result_id is not initialized"):
        await scenario._execute_scenario_async()


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioBaselineUniformObjectives:
    """ADO 9012 regression: baseline and strategy share objectives under max_dataset_size.

    The structural fix collapses to a single seed-group resolution call per scenario
    run. Both the strategy atomic attacks and the baseline use the same sampled
    population, so ``random.sample`` runs once and the two groups match.
    """

    async def test_baseline_objectives_match_atomic_attacks_under_max_dataset_size(
        self,
        mock_objective_target,
    ):
        from pyrit.models import SeedGroup, SeedObjective
        from pyrit.scenario.core.attack_technique import AttackTechnique

        seed_groups = [SeedGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(10)]
        config = DatasetAttackConfiguration(seed_groups=seed_groups, max_dataset_size=3)

        class StrategyScenario(ConcreteScenarioWithTrueFalseScorer):
            async def _build_atomic_attacks_async(self, *, context):
                return [
                    AtomicAttack(
                        atomic_attack_name="strategy",
                        attack_technique=AttackTechnique(attack=MagicMock()),
                        seed_groups=list(context.seed_groups),
                    )
                ]

        # A single deterministic resolution: random.sample must be called exactly once,
        # so baseline and strategy draw from the same sampled population and share objectives.
        def _sample_first_k(population, k):
            return list(population)[:k]

        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample",
            side_effect=_sample_first_k,
        ) as mock_sample:
            scenario = StrategyScenario(name="ADO 9012 regression", version=1)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_strategies": None,
                    "dataset_config": config,
                }
            )
            await scenario.initialize_async()

        assert mock_sample.call_count == 1

        baseline, strategy = scenario._atomic_attacks
        assert baseline.atomic_attack_name == "baseline"
        assert strategy.atomic_attack_name == "strategy"
        assert set(baseline.objectives) == set(strategy.objectives)
        assert len(baseline.objectives) == 3


@pytest.mark.usefixtures("patch_central_database")
class TestBuildBaselineAtomicAttack:
    """Unit tests for Scenario._build_baseline_atomic_attack."""

    def _seed_groups(self):
        from pyrit.models import SeedAttackGroup, SeedObjective

        return [SeedAttackGroup(seeds=[SeedObjective(value="x")])]

    def test_returns_baseline_atomic_attack(self, mock_objective_target):
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack

        seed_groups = self._seed_groups()
        scenario = ConcreteScenarioWithTrueFalseScorer(name="T", version=1)
        scenario._objective_target = mock_objective_target

        atomic = scenario._build_baseline_atomic_attack(seed_groups=seed_groups)

        assert atomic.atomic_attack_name == "baseline"
        assert atomic.seed_groups == seed_groups
        assert isinstance(atomic.attack_technique.attack, PromptSendingAttack)

    def test_raises_when_target_is_none(self):
        scenario = ConcreteScenarioWithTrueFalseScorer(name="T", version=1)
        # _objective_target is None pre-initialize_async

        with pytest.raises(ValueError, match="Objective target is required"):
            scenario._build_baseline_atomic_attack(seed_groups=self._seed_groups())

    def test_raises_when_scorer_is_none(self, mock_objective_target):
        scenario = ConcreteScenarioWithTrueFalseScorer(name="T", version=1)
        scenario._objective_target = mock_objective_target
        scenario._objective_scorer = None  # type: ignore[assignment]

        with pytest.raises(ValueError, match="Objective scorer is required"):
            scenario._build_baseline_atomic_attack(seed_groups=self._seed_groups())


@pytest.mark.usefixtures("patch_central_database")
class TestValidateStoredScenario:
    """Tests for Scenario._validate_stored_scenario."""

    def _make_scenario(self, *, name: str = "TestScenario", version: int = 1) -> ConcreteScenario:
        scenario = ConcreteScenario(name=name, version=version)
        scenario._scenario_result_id = "test-result-id"
        scenario.params = {}
        return scenario

    def test_passes_when_name_and_version_match(self):
        """Valid match (identical eval hash) does not raise."""
        scenario = self._make_scenario(name="TestScenario", version=2)

        current = make_scenario_identifier(scenario_name="ConcreteScenario", version=2)
        stored_result = make_scenario_result(
            scenario_name="ConcreteScenario", scenario_version=2, scenario_run_state="CREATED", attack_results={}
        )

        # Should not raise
        scenario._validate_stored_scenario(stored_result=stored_result, current_identifier=current)

    def test_raises_when_name_mismatches(self):
        """Mismatched name raises ValueError."""
        scenario = self._make_scenario(name="TestScenario", version=1)

        current = make_scenario_identifier(scenario_name="ConcreteScenario", version=1)
        stored_result = make_scenario_result(scenario_name="DifferentScenario", scenario_version=1, attack_results={})

        with pytest.raises(ValueError, match="does not match the current"):
            scenario._validate_stored_scenario(stored_result=stored_result, current_identifier=current)

    def test_raises_when_version_mismatches(self):
        """Mismatched version changes the eval hash and raises ValueError."""
        scenario = self._make_scenario(name="TestScenario", version=2)

        current = make_scenario_identifier(scenario_name="ConcreteScenario", version=2)
        stored_result = make_scenario_result(scenario_name="ConcreteScenario", scenario_version=99, attack_results={})

        with pytest.raises(ValueError, match="does not match the current"):
            scenario._validate_stored_scenario(stored_result=stored_result, current_identifier=current)


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResumption:
    """Tests for scenario resumption logic in initialize_async."""

    async def test_resume_succeeds_when_stored_result_matches(self, mock_objective_target, mock_atomic_attacks):
        """When scenario_result_id finds a matching result, no new result is created."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        # Capture the created scenario_result_id
        original_id = scenario._scenario_result_id
        assert original_id is not None

        # Now create a second scenario that reuses the same result id
        scenario2 = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
            scenario_result_id=original_id,
        )

        scenario2.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario2.initialize_async()

        # Should reuse the same ID (no new creation)
        assert scenario2._scenario_result_id == original_id

    async def test_resume_raises_when_id_not_found(self, mock_objective_target, mock_atomic_attacks):
        """When scenario_result_id doesn't exist in memory, ValueError is raised."""
        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
            scenario_result_id="nonexistent-id",
        )

        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        with pytest.raises(ValueError, match="not found in memory"):
            await scenario.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioParallelExecution:
    """Tests for parallel atomic-attack execution sharing a single max_concurrency budget."""

    async def test_atomic_attacks_share_one_executor(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """All atomic attacks in parallel mode receive the same shared AttackExecutor instance."""
        from pyrit.executor.attack import AttackExecutor

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
                "max_concurrency": 4,
            }
        )
        await scenario.initialize_async()

        await scenario.run_async()

        # Each atomic attack got an executor kwarg, and it's the SAME AttackExecutor instance,
        # sized to max_concurrency=4.
        executors_seen = []
        for run in mock_atomic_attacks:
            assert run.run_async.call_count == 1
            kwargs = run.run_async.call_args.kwargs
            assert kwargs["return_partial_on_failure"] is True
            assert isinstance(kwargs["executor"], AttackExecutor)
            executors_seen.append(kwargs["executor"])
        assert executors_seen[0] is executors_seen[1] is executors_seen[2]
        assert executors_seen[0]._max_concurrency == 4

    async def test_shared_executor_bounds_global_concurrency(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """Total in-flight objectives across all atomic attacks never exceeds max_concurrency.

        Simulates each atomic attack 'using' the executor's internal semaphore for two
        objectives. With max_concurrency=2 and 3 atomic attacks (= 6 objectives total),
        peak in-flight objective count must stay <= 2 even though all three atomic
        attacks are launched.
        """
        peak = [0]
        in_flight = [0]
        lock = asyncio.Lock()

        def make_run_async(idx):
            async def run_async(*, executor, **kwargs):
                # Simulate two objectives per atomic attack, each acquiring the shared
                # executor's semaphore. Use the public-ish accessor so the executor can
                # rebind the semaphore to the currently running event loop on demand.
                semaphore = executor._get_semaphore()
                for _ in range(2):
                    async with semaphore:
                        async with lock:
                            in_flight[0] += 1
                            peak[0] = max(peak[0], in_flight[0])
                        # Yield so other tasks contending for the semaphore can enter.
                        await asyncio.sleep(0)
                        async with lock:
                            in_flight[0] -= 1
                _stamp_scenario_linkage(
                    attack_results=[sample_attack_results[idx]],
                    atomic_attack=mock_atomic_attacks[idx],
                )
                save_attack_results_to_memory([sample_attack_results[idx]])
                return AttackExecutorResult(
                    completed_results=[sample_attack_results[idx]],
                    incomplete_objectives=[],
                )

            return AsyncMock(side_effect=run_async)

        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = make_run_async(i)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 2,
            }
        )
        await scenario.initialize_async()

        await scenario.run_async()

        assert peak[0] <= 2, f"shared executor budget violated: peak in-flight was {peak[0]}"
        assert peak[0] == 2, f"expected to saturate budget of 2, peaked at {peak[0]}"

    async def test_atomic_attacks_run_concurrently(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """When max_concurrency permits, multiple atomic attacks are in-flight simultaneously."""
        started = asyncio.Event()
        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        def make_run_async(idx):
            async def run_async(*args, **kwargs):
                nonlocal in_flight, max_in_flight
                async with lock:
                    in_flight += 1
                    max_in_flight = max(max_in_flight, in_flight)
                if in_flight >= 3:
                    started.set()
                try:
                    await asyncio.wait_for(started.wait(), timeout=2.0)
                finally:
                    async with lock:
                        in_flight -= 1
                _stamp_scenario_linkage(
                    attack_results=[sample_attack_results[idx]],
                    atomic_attack=mock_atomic_attacks[idx],
                )
                save_attack_results_to_memory([sample_attack_results[idx]])
                return AttackExecutorResult(
                    completed_results=[sample_attack_results[idx]],
                    incomplete_objectives=[],
                )

            return AsyncMock(side_effect=run_async)

        for i, run in enumerate(mock_atomic_attacks):
            run.run_async = make_run_async(i)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 6,
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        assert max_in_flight == 3, f"expected all 3 atomic attacks in flight, peaked at {max_in_flight}"
        assert len(result.attack_results) == 3

    async def test_failure_lets_inflight_siblings_finish_but_skips_queued(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """In-flight siblings finish so partial work persists; queued siblings don't start.

        Uses max_concurrency=2 with 3 atomic attacks so the third is unambiguously queued
        rather than already-started. attack[0] takes a slot and sleeps; attack[1] takes
        the second slot and fails. attack[2] is queued behind them — once attack[1]'s
        worker observes the failure and stops pulling, attack[2] must never start.
        """
        started_calls: list[str] = []
        completed_calls: list[str] = []
        bad_started = asyncio.Event()

        async def ok_run(idx, name):
            started_calls.append(name)
            # Wait for the bad task to fail before this one completes, so the
            # failure is observed mid-flight (no wall-clock dependency).
            await bad_started.wait()
            completed_calls.append(name)
            _stamp_scenario_linkage(
                attack_results=[sample_attack_results[idx]],
                atomic_attack=mock_atomic_attacks[idx],
            )
            save_attack_results_to_memory([sample_attack_results[idx]])
            return AttackExecutorResult(completed_results=[sample_attack_results[idx]], incomplete_objectives=[])

        async def bad_run(*args, **kwargs):
            started_calls.append("attack_run_2")
            bad_started.set()
            raise RuntimeError("boom")

        async def side_run_0(*a, **k):
            return await ok_run(0, "attack_run_1")

        async def side_run_2(*a, **k):
            return await ok_run(2, "attack_run_3")

        mock_atomic_attacks[0].run_async = AsyncMock(side_effect=side_run_0)
        mock_atomic_attacks[1].run_async = AsyncMock(side_effect=bad_run)
        mock_atomic_attacks[2].run_async = AsyncMock(side_effect=side_run_2)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 2,
            }
        )
        await scenario.initialize_async()

        with pytest.raises(RuntimeError, match="boom"):
            await scenario.run_async()

        # attack[0] was in-flight when attack[1] failed and must complete cleanly.
        assert "attack_run_1" in completed_calls
        # attack[2] was queued behind the failed one and must never have started.
        assert "attack_run_3" not in started_calls
        assert "attack_run_3" not in completed_calls
        # Sanity check: the failure actually happened.
        assert bad_started.is_set()

    async def test_multiple_inflight_failures_are_grouped_into_exception_group(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """When multiple in-flight atomic attacks fail, all failures are surfaced via ExceptionGroup."""

        # All three workers fail concurrently, so all three are in-flight when failure is
        # observed (no queueing) and every failure should propagate.
        def make_fail_run(name: str):
            async def _run(*args, **kwargs):
                # Yield so all three workers are in-flight before any fails.
                await asyncio.sleep(0)
                raise RuntimeError(f"{name} boom")

            return AsyncMock(side_effect=_run)

        mock_atomic_attacks[0].run_async = make_fail_run("a")
        mock_atomic_attacks[1].run_async = make_fail_run("b")
        mock_atomic_attacks[2].run_async = make_fail_run("c")

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 3,
            }
        )
        await scenario.initialize_async()

        with pytest.raises(ExceptionGroup) as exc_info:
            await scenario.run_async()

        # All three failures must be present in the group.
        messages = sorted(str(e) for e in exc_info.value.exceptions)
        assert messages == ["a boom", "b boom", "c boom"]
        assert all(isinstance(e, RuntimeError) for e in exc_info.value.exceptions)

    async def test_single_failure_is_raised_directly_not_wrapped(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """A lone failure is re-raised as-is (no ExceptionGroup wrapping for the common case)."""
        for i in [0, 2]:
            mock_atomic_attacks[i].run_async = create_mock_run_async(
                [sample_attack_results[i]], atomic_attack=mock_atomic_attacks[i]
            )

        async def bad_run(*a, **k):
            raise RuntimeError("solo boom")

        mock_atomic_attacks[1].run_async = AsyncMock(side_effect=bad_run)

        scenario = ConcreteScenario(
            name="Test Scenario",
            version=1,
            atomic_attacks_to_return=mock_atomic_attacks,
        )
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 3,
            }
        )
        await scenario.initialize_async()

        # Bare RuntimeError, not ExceptionGroup.
        with pytest.raises(RuntimeError, match="solo boom"):
            await scenario.run_async()

    async def test_max_concurrency_one_serializes_via_single_worker(
        self, mock_atomic_attacks, sample_attack_results, mock_objective_target
    ):
        """max_concurrency=1 reduces the worker pool to one worker; attacks still get the shared executor."""
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
                "max_concurrency": 1,
            }
        )
        await scenario.initialize_async()

        await scenario.run_async()

        for run in mock_atomic_attacks:
            run.run_async.assert_called_once_with(executor=ANY, return_partial_on_failure=True)
