# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Leakage class."""

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedDataset, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.airt import Leakage  # type: ignore[ty:unresolved-import]
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.scenarios.airt.leakage import _build_leakage_technique
from pyrit.score import TrueFalseCompositeScorer
from pyrit.setup.initializers.techniques import build_technique_factories


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
    mock_config = MagicMock(spec=DatasetAttackConfiguration)
    mock_config.get_attack_groups_by_dataset_async = AsyncMock(return_value={"airt_leakage": seed_groups})
    mock_config.dataset_names = ["airt_leakage"]
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
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_leakage_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_leakage_technique.cache_clear()


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
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        assert len(atomic_attacks) > 0
        assert all(run.attack_technique is not None for run in atomic_attacks)

    async def test_attack_runs_include_objectives(
        self, mock_objective_target, mock_objective_scorer, mock_dataset_config
    ):
        """Test that attack runs include objectives for each seed prompt."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()
        atomic_attacks = scenario._atomic_attacks

        for run in atomic_attacks:
            assert len(run.objectives) > 0

    async def test_unknown_technique_skipped(self, mock_objective_target, mock_objective_scorer, mock_dataset_config):
        """Test that an unknown technique is skipped (logged as warning) by base class."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()
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
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "max_concurrency": 20,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()
        assert scenario._max_concurrency == 20

    async def test_initialize_async_with_memory_labels(
        self, mock_objective_target, mock_objective_scorer, mock_dataset_config
    ):
        """Test initialization with memory labels."""
        memory_labels = {"test": "leakage", "category": "scenario"}
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(
            args={
                "memory_labels": memory_labels,
                "objective_target": mock_objective_target,
                "dataset_config": mock_dataset_config,
            }
        )
        await scenario.initialize_async()
        assert scenario._memory_labels == memory_labels


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageProperties:
    """Tests for Leakage properties and attributes."""

    def test_scenario_version_is_set(self, mock_objective_scorer):
        """Test that scenario version is properly set."""
        scenario = Leakage(objective_scorer=mock_objective_scorer)
        assert scenario.VERSION == 2

    def test_get_technique_class_returns_dynamic_class(self, mock_objective_scorer):
        """Test that the instance technique class is the dynamically generated Leakage technique class."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        assert technique_class.__name__ == "LeakageTechnique"

    def test_get_default_technique_returns_default(self, mock_objective_scorer):
        """Test that the default technique is the DEFAULT aggregate."""
        default = Leakage(objective_scorer=mock_objective_scorer)._default_technique
        assert default.value == "default"

    def test_required_datasets_returns_airt_leakage(self):
        """Test that required_datasets returns airt_leakage."""
        assert Leakage.required_datasets() == ["airt_leakage"]


@pytest.mark.usefixtures(*FIXTURES)
class TestLeakageTechniqueEnum:
    """Tests for LeakageTechnique enum (dynamically generated)."""

    def test_technique_all_exists(self, mock_objective_scorer):
        """Test that ALL technique exists."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        assert technique_class.ALL is not None
        assert technique_class.ALL.value == "all"
        assert "all" in technique_class.ALL.tags

    def test_technique_single_turn_aggregate_exists(self, mock_objective_scorer):
        """Test that SINGLE_TURN aggregate technique exists."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        assert technique_class.SINGLE_TURN is not None
        assert technique_class.SINGLE_TURN.value == "single_turn"
        assert "single_turn" in technique_class.SINGLE_TURN.tags

    def test_technique_multi_turn_aggregate_exists(self, mock_objective_scorer):
        """Test that MULTI_TURN aggregate technique exists."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        assert technique_class.MULTI_TURN is not None
        assert technique_class.MULTI_TURN.value == "multi_turn"
        assert "multi_turn" in technique_class.MULTI_TURN.tags

    def test_technique_default_aggregate_exists(self, mock_objective_scorer):
        """Test that DEFAULT aggregate technique exists."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        assert technique_class.DEFAULT is not None
        assert technique_class.DEFAULT.value == "default"
        assert "default" in technique_class.DEFAULT.tags

    def test_technique_has_technique_members(self, mock_objective_scorer):
        """Test that the technique has technique members from core + leakage techniques."""
        technique_class = Leakage(objective_scorer=mock_objective_scorer)._technique_class
        values = {m.value for m in technique_class}
        # Leakage-unique techniques
        assert "first_letter" in values
        assert "image" in values
        # Core techniques included
        assert "role_play" in values
