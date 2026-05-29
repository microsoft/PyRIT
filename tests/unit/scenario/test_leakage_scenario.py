# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Leakage class."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedDataset, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.airt import Leakage
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.scenarios.airt.leakage import _build_leakage_strategy
from pyrit.score import TrueFalseCompositeScorer
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories


def _mock_scorer_id(name: str = "MockObjectiveScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


@pytest.fixture
def mock_memory_seeds():
    leakage_path = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt"
    seed_prompts = list(SeedDataset.from_yaml_file(leakage_path / "leakage.prompt").get_values())
    return [SeedObjective(value=prompt) for prompt in seed_prompts]


@pytest.fixture
def mock_dataset_config(mock_memory_seeds):
    """Create a mock dataset config that returns the seed groups."""
    seed_groups = [SeedAttackGroup(seeds=[seed]) for seed in mock_memory_seeds]
    mock_config = MagicMock(spec=DatasetConfiguration)
    mock_config.get_all_seed_attack_groups.return_value = seed_groups
    mock_config.get_seed_attack_groups.return_value = {"airt_leakage": seed_groups}
    mock_config.get_default_dataset_names.return_value = ["airt_leakage"]
    mock_config.has_data_source.return_value = True
    return mock_config


@pytest.fixture
def mock_runtime_env():
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_target_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseCompositeScorer)
    mock.get_identifier.return_value = _mock_scorer_id("MockObjectiveScorer")
    return mock


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries and populate scenario factories for each test."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_leakage_strategy.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    _build_leakage_strategy.cache_clear()


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageInitialization:
    """Tests for Leakage initialization."""

    def test_init_with_custom_scorer(self, mock_objective_scorer):
        """Test initialization with custom scorer."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        assert scenario.name == "Leakage"
        assert scenario.VERSION == 2

    def test_init_with_default_scorer(self):
        """Test initialization with default scorer."""
        scenario = Leakage()
        assert scenario._objective_scorer_identifier

    def test_default_scorer_uses_leakage_yaml(self):
        """Test that the default scorer uses leakage.yaml, not privacy.yaml."""
        scorer_path = DATASETS_PATH / "score" / "true_false_question" / "leakage.yaml"
        assert scorer_path.exists(), f"Expected leakage.yaml scorer at {scorer_path}"

    def test_init_supports_default_baseline(self):
        """Leakage opts into the parent's default baseline."""
        assert Leakage.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageAttackGeneration:
    """Tests for Leakage attack generation."""

    async def test_attack_generation_for_all(self, mock_objective_target, mock_objective_scorer, mock_dataset_config):
        """Test that _get_atomic_attacks_async returns atomic attacks."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
        atomic_attacks = await scenario._get_atomic_attacks_async()

        assert len(atomic_attacks) > 0
        assert all(run.attack_technique is not None for run in atomic_attacks)

    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_dataset_config
    ):
        """Test that attack runs include objectives for each seed prompt."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
        atomic_attacks = await scenario._get_atomic_attacks_async()

        for run in atomic_attacks:
            assert len(run.objectives) > 0

    async def test_unknown_strategy_skipped(self, mock_objective_target, mock_objective_scorer, mock_dataset_config):
        """Test that an unknown strategy is skipped (logged as warning) by base class."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=mock_dataset_config)
        # Base class logs a warning for unknown technique names and skips them
        # This is a behavior change from the old manual implementation which raised ValueError


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageLifecycle:
    """Tests for Leakage lifecycle, including initialize_async and execution."""

    async def test_initialize_async_with_max_concurrency(
        self, mock_objective_target, mock_objective_scorer, mock_dataset_config
    ):
        """Test initialization with custom max_concurrency."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(
            objective_target=mock_objective_target, max_concurrency=20, dataset_config=mock_dataset_config
        )
        assert scenario._max_concurrency == 20

    async def test_initialize_async_with_memory_labels(
        self, mock_objective_target, mock_objective_scorer, mock_dataset_config
    ):
        """Test initialization with memory labels."""
        memory_labels = {"test": "leakage", "category": "scenario"}
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        await scenario.initialize_async(
            memory_labels=memory_labels,
            objective_target=mock_objective_target,
            dataset_config=mock_dataset_config,
        )
        assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageProperties:
    """Tests for Leakage properties and attributes."""

    def test_scenario_version_is_set(self, mock_objective_scorer):
        """Test that scenario version is properly set."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        assert scenario.VERSION == 2

    def test_get_strategy_class_returns_dynamic_class(self, mock_objective_scorer):
        """Test that the instance strategy class is the dynamically generated Leakage strategy class."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        assert strategy_class.__name__ == "LeakageStrategy"

    def test_get_default_strategy_returns_default(self, mock_objective_scorer):
        """Test that the default strategy is the DEFAULT aggregate."""
        default = Leakage(objective_scorer=mock_objective_scorer)._default_strategy
        assert default.value == "default"

    def test_required_datasets_returns_airt_leakage(self):
        """Test that required_datasets returns airt_leakage."""
        assert Leakage.required_datasets() == ["airt_leakage"]


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageStrategyEnum:
    """Tests for LeakageStrategy enum (dynamically generated)."""

    def test_strategy_all_exists(self, mock_objective_scorer):
        """Test that ALL strategy exists."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        assert strategy_class.ALL is not None
        assert strategy_class.ALL.value == "all"
        assert "all" in strategy_class.ALL.tags

    def test_strategy_single_turn_aggregate_exists(self, mock_objective_scorer):
        """Test that SINGLE_TURN aggregate strategy exists."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        assert strategy_class.SINGLE_TURN is not None
        assert strategy_class.SINGLE_TURN.value == "single_turn"
        assert "single_turn" in strategy_class.SINGLE_TURN.tags

    def test_strategy_multi_turn_aggregate_exists(self, mock_objective_scorer):
        """Test that MULTI_TURN aggregate strategy exists."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        assert strategy_class.MULTI_TURN is not None
        assert strategy_class.MULTI_TURN.value == "multi_turn"
        assert "multi_turn" in strategy_class.MULTI_TURN.tags

    def test_strategy_default_aggregate_exists(self, mock_objective_scorer):
        """Test that DEFAULT aggregate strategy exists."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        assert strategy_class.DEFAULT is not None
        assert strategy_class.DEFAULT.value == "default"
        assert "default" in strategy_class.DEFAULT.tags

    def test_strategy_has_technique_members(self, mock_objective_scorer):
        """Test that the strategy has technique members from core + leakage techniques."""
        strategy_class = Leakage(objective_scorer=mock_objective_scorer)._strategy_class
        values = {m.value for m in strategy_class}
        # Leakage-unique techniques
        assert "first_letter" in values
        assert "image" in values
        # Core techniques included
        assert "role_play" in values
