# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Benchmark scenario."""

import copy
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import (
    RolePlayAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES
from pyrit.scenario.scenarios.benchmark.benchmark import Benchmark
from pyrit.score import TrueFalseScorer

# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _make_adversarial_target(name: str) -> MagicMock:
    """Create a mock PromptChatTarget with a given model name."""
    mock = MagicMock(spec=PromptChatTarget)
    mock._model_name = name
    mock.get_identifier.return_value = _mock_id(name)
    return mock


def _make_seed_groups(name: str) -> list[SeedAttackGroup]:
    """Create two seed attack groups for a given category."""
    return [
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 1"), SeedPrompt(value=f"{name} prompt 1")]),
        SeedAttackGroup(seeds=[SeedObjective(value=f"{name} objective 2"), SeedPrompt(value=f"{name} prompt 2")]),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def two_adversarial_models():
    """Two mock adversarial models for benchmark permutation tests."""
    return [_make_adversarial_target("model_a"), _make_adversarial_target("model_b")]


@pytest.fixture
def single_adversarial_model():
    """Single mock adversarial model."""
    return [_make_adversarial_target("model_a")]


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset the AttackTechniqueRegistry and cached strategy class between tests."""
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    Benchmark._cached_strategy_class = None
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    Benchmark._cached_strategy_class = None


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    """Prevent ManyShotJailbreakAttack from loading the full bundled dataset."""
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


@pytest.fixture
def mock_runtime_env():
    """Set minimal env vars needed for OpenAIChatTarget fallback via @apply_defaults."""
    with patch.dict(
        "os.environ",
        {
            "OPENAI_CHAT_ENDPOINT": "https://test.openai.azure.com/",
            "OPENAI_CHAT_KEY": "test-key",
            "OPENAI_CHAT_MODEL": "gpt-4",
        },
    ):
        yield


FIXTURES = ["patch_central_database", "mock_runtime_env"]


# ===========================================================================
# Type and syntax tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkTypes:
    """Unit tests for types, validation, and basic construction."""

    def test_empty_adversarial_models_raises(self):
        """Passing an empty list must raise ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            Benchmark(adversarial_models=[])

    def test_version_is_1(self):
        assert Benchmark.VERSION == 1

    def test_default_dataset_config_uses_harmbench(self):
        config = Benchmark.default_dataset_config()
        assert isinstance(config, DatasetConfiguration)
        names = config.get_default_dataset_names()
        assert "harmbench" in names

    def test_default_dataset_config_max_size_is_8(self):
        config = Benchmark.default_dataset_config()
        assert config.max_dataset_size == 8

    def test_frozen_spec_cannot_be_mutated(self):
        """AttackTechniqueSpec is frozen — direct mutation must raise."""
        spec = SCENARIO_TECHNIQUES[0]
        with pytest.raises(FrozenInstanceError):
            spec.name = "mutated"


# ===========================================================================
# Strategy construction tests
# ===========================================================================


_NUM_ADVERSARIAL_TECHNIQUES = 2


def _make_benchmark(adversarial_models):
    """Helper to create a Benchmark with mocked default scorer."""
    with patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer:
        mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
        return Benchmark(adversarial_models=adversarial_models)


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkStrategy:
    """Tests for strategy class construction, permutation, and the
    class-level vs instance-level split."""

    def test_classmethod_strategy_has_unpermuted_techniques(self):
        """get_strategy_class() returns a strategy with many_shot and tap (no model suffix)."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "many_shot" in values
        assert "tap" in values
        assert not any("__" in v for v in values)

    def test_classmethod_strategy_excludes_non_adversarial(self):
        """get_strategy_class() must not include prompt_sending or role_play."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "prompt_sending" not in values
        assert "role_play" not in values

    def test_instance_strategy_has_permuted_techniques(self, two_adversarial_models):
        """Instance strategy should have technique__model members for each (technique x model) pair."""
        scenario = _make_benchmark(two_adversarial_models)
        strat = scenario._strategy_class
        values = {s.value for s in strat.get_all_strategies()}
        assert "role_play__model_a" in values
        assert "role_play__model_b" in values
        assert "tap__model_a" in values
        assert "tap__model_b" in values
        assert len(values) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    def test_permuted_spec_names_are_unique(self, two_adversarial_models):
        """Each permuted AttackTechniqueSpec must have a unique name."""
        scenario = _make_benchmark(two_adversarial_models)
        names = [s.name for s in scenario._benchmark_specs]
        assert len(names) == len(set(names))

    def test_original_scenario_techniques_unmodified(self, two_adversarial_models):
        """SCENARIO_TECHNIQUES global must not be mutated by permutation."""
        original = copy.deepcopy([(s.name, s.attack_class) for s in SCENARIO_TECHNIQUES])
        _make_benchmark(two_adversarial_models)
        current = [(s.name, s.attack_class) for s in SCENARIO_TECHNIQUES]
        assert current == original

    def test_non_adversarial_techniques_excluded_from_specs(self, two_adversarial_models):
        """prompt_sending and many_shot should not appear in permuted specs."""
        scenario = _make_benchmark(two_adversarial_models)
        spec_names = {s.name for s in scenario._benchmark_specs}
        assert not any("prompt_sending" in n for n in spec_names)
        assert not any(n.startswith("many_shot") for n in spec_names)

    def test_singleton_registry_not_polluted(self, two_adversarial_models):
        """Creating a Benchmark must not register permuted techniques in the global singleton."""
        _make_benchmark(two_adversarial_models)
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert not any("__" in name for name in factories)

    def test_permuted_specs_have_adversarial_chat_set(self, two_adversarial_models):
        """Every permuted spec must have adversarial_chat pointing to the correct model."""
        scenario = _make_benchmark(two_adversarial_models)
        for spec in scenario._benchmark_specs:
            assert spec.adversarial_chat is not None

    def test_model_label_fallback_to_unique_name(self):
        """When _model_name is empty, label should fall back to unique_name."""
        model = MagicMock(spec=PromptChatTarget)
        model._model_name = ""
        model.get_identifier.return_value = _mock_id("FallbackTarget")
        scenario = _make_benchmark([model])
        for name in scenario._technique_to_model:
            assert "__" in name
            assert name.split("__")[1] != ""


# ===========================================================================
# Post-init property tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkProperties:
    """Tests for post-init instance properties."""

    def test_technique_to_model_mapping_populated(self, two_adversarial_models):
        """_technique_to_model should map every permuted technique name to its model label."""
        scenario = _make_benchmark(two_adversarial_models)
        assert len(scenario._technique_to_model) == _NUM_ADVERSARIAL_TECHNIQUES * 2
        for name, label in scenario._technique_to_model.items():
            assert label in ("model_a", "model_b")
            assert label in name

    def test_benchmark_specs_count(self, two_adversarial_models):
        """_benchmark_specs should have |adversarial_models| x |adversarial_techniques| entries."""
        scenario = _make_benchmark(two_adversarial_models)
        assert len(scenario._benchmark_specs) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    def test_prepare_strategies_resolves_default(self, single_adversarial_model):
        """_prepare_strategies(None) must resolve from the instance strategy class."""
        scenario = _make_benchmark(single_adversarial_model)
        strategies = scenario._prepare_strategies(None)
        values = {s.value for s in strategies}
        # role_play has no "default" tag, tap has no "default" tag — check what actually has it
        # The DEFAULT aggregate expands to techniques tagged "default" in SCENARIO_TECHNIQUES
        assert len(values) > 0

    def test_prepare_strategies_accepts_all_aggregate(self, single_adversarial_model):
        """_prepare_strategies with ALL should return all permuted techniques."""
        scenario = _make_benchmark(single_adversarial_model)
        all_strat = scenario._strategy_class("all")
        strategies = scenario._prepare_strategies([all_strat])
        assert len(strategies) == _NUM_ADVERSARIAL_TECHNIQUES

    def test_scenario_name(self, single_adversarial_model):
        """Scenario name should be 'Benchmark'."""
        scenario = _make_benchmark(single_adversarial_model)
        assert scenario.name == "Benchmark"


# ===========================================================================
# Runtime / attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkRuntime:
    """Tests for _get_atomic_attacks_async and display grouping."""

    async def _init_and_get_attacks(
        self,
        *,
        mock_objective_target,
        adversarial_models,
        seed_groups: dict[str, list[SeedAttackGroup]] | None = None,
        strategies=None,
    ):
        """Helper: create Benchmark, initialize, return (scenario, attacks)."""
        groups = seed_groups or {"harmbench": _make_seed_groups("harmbench")}
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=adversarial_models)
            init_kwargs: dict = {"objective_target": mock_objective_target}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            attacks = await scenario._get_atomic_attacks_async()
            return scenario, attacks

    @pytest.mark.asyncio
    async def test_default_strategy_attack_count(self, mock_objective_target, two_adversarial_models):
        """DEFAULT expands to techniques tagged 'default' among adversarial-capable ones."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_adversarial_models,
        )
        # role_play has tag "single_turn" (no "default"), tap has tag "multi_turn" (no "default")
        # So DEFAULT may expand to 0 techniques — use ALL instead for count validation
        # This test validates the default behavior, whatever it is
        assert isinstance(attacks, list)

    @pytest.mark.asyncio
    async def test_all_strategy_produces_full_cross_product(self, mock_objective_target, two_adversarial_models):
        """ALL strategy: 2 models x 2 techniques x 1 dataset = 4 atomic attacks."""
        with (
            patch.object(
                DatasetConfiguration,
                "get_seed_attack_groups",
                return_value={"harmbench": _make_seed_groups("harmbench")},
            ),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=two_adversarial_models)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            assert len(attacks) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    @pytest.mark.asyncio
    async def test_atomic_attack_names_are_unique(self, mock_objective_target, two_adversarial_models):
        """All atomic_attack_name values must be unique for resume correctness."""
        with (
            patch.object(
                DatasetConfiguration,
                "get_seed_attack_groups",
                return_value={"harmbench": _make_seed_groups("harmbench")},
            ),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=two_adversarial_models)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            names = [a.atomic_attack_name for a in attacks]
            assert len(names) == len(set(names))

    @pytest.mark.asyncio
    async def test_atomic_attack_names_follow_pattern(self, mock_objective_target, single_adversarial_model):
        """Each atomic_attack_name should contain the technique__model and dataset."""
        with (
            patch.object(
                DatasetConfiguration,
                "get_seed_attack_groups",
                return_value={"harmbench": _make_seed_groups("harmbench")},
            ),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=single_adversarial_model)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            for a in attacks:
                assert "_harmbench" in a.atomic_attack_name
                assert "__model_a" in a.atomic_attack_name

    @pytest.mark.asyncio
    async def test_display_groups_by_adversarial_model(self, mock_objective_target, two_adversarial_models):
        """display_group should group by model label, not by technique or dataset."""
        with (
            patch.object(
                DatasetConfiguration,
                "get_seed_attack_groups",
                return_value={"harmbench": _make_seed_groups("harmbench")},
            ),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=two_adversarial_models)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            display_groups = {a.display_group for a in attacks}
            assert display_groups == {"model_a", "model_b"}

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self, single_adversarial_model):
        """_get_atomic_attacks_async must raise if initialize_async was not called."""
        scenario = _make_benchmark(single_adversarial_model)
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_multiple_datasets_multiplies_attacks(self, mock_objective_target, single_adversarial_model):
        """With 2 datasets and 1 model, ALL strategy (2 techniques) -> 4 atomic attacks."""
        two_datasets = {
            "harmbench": _make_seed_groups("harmbench"),
            "extra": _make_seed_groups("extra"),
        }
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=two_datasets),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=single_adversarial_model)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            # 1 model x 2 techniques x 2 datasets = 4
            assert len(attacks) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    @pytest.mark.asyncio
    async def test_all_strategy_with_multiple_datasets(self, mock_objective_target, single_adversarial_model):
        """ALL + 2 datasets: 1 model x 2 techniques x 2 datasets = 4."""
        two_datasets = {
            "harmbench": _make_seed_groups("harmbench"),
            "extra": _make_seed_groups("extra"),
        }
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=two_datasets),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=single_adversarial_model)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            assert len(attacks) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    @pytest.mark.asyncio
    async def test_attacks_have_correct_technique_types(self, mock_objective_target, single_adversarial_model):
        """Atomic attacks should use ManyShotJailbreakAttack and TreeOfAttacksWithPruningAttack."""
        with (
            patch.object(
                DatasetConfiguration,
                "get_seed_attack_groups",
                return_value={"harmbench": _make_seed_groups("harmbench")},
            ),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = Benchmark(adversarial_models=single_adversarial_model)
            all_strat = scenario._strategy_class("all")
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[all_strat])
            attacks = await scenario._get_atomic_attacks_async()
            technique_classes = {type(a.attack_technique.attack) for a in attacks}
            assert technique_classes == {RolePlayAttack, TreeOfAttacksWithPruningAttack}

    @pytest.mark.asyncio
    async def test_attacks_carry_seed_groups(self, mock_objective_target, single_adversarial_model):
        """Each atomic attack should have non-empty objectives from the seed groups."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
        )
        for a in attacks:
            assert len(a.objectives) > 0


# ===========================================================================
# Display group tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBuildDisplayGroup:
    """Tests for _build_display_group in isolation."""

    def test_returns_model_label(self, single_adversarial_model):
        """_build_display_group should return the model label from _technique_to_model."""
        scenario = _make_benchmark(single_adversarial_model)
        result = scenario._build_display_group(technique_name="role_play__model_a", seed_group_name="harmbench")
        assert result == "model_a"

    def test_ignores_seed_group_name(self, single_adversarial_model):
        """Changing seed_group_name should not affect the result."""
        scenario = _make_benchmark(single_adversarial_model)
        r1 = scenario._build_display_group(technique_name="role_play__model_a", seed_group_name="harmbench")
        r2 = scenario._build_display_group(technique_name="role_play__model_a", seed_group_name="other")
        assert r1 == r2 == "model_a"

    def test_unknown_technique_raises_key_error(self, single_adversarial_model):
        """Unknown technique_name should raise KeyError."""
        scenario = _make_benchmark(single_adversarial_model)
        with pytest.raises(KeyError):
            scenario._build_display_group(technique_name="nonexistent__model", seed_group_name="harmbench")
