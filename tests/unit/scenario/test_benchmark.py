# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Benchmark scenario (factory-override design)."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import RolePlayAttack, TreeOfAttacksWithPruningAttack
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.scenarios.benchmark.benchmark import Benchmark, _get_benchmarkable_specs
from pyrit.score import TrueFalseScorer


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


def _make_models_dict(*names: str) -> dict[str, MagicMock]:
    """Create a dict of label → mock PromptChatTarget."""
    return {name: _make_adversarial_target(name) for name in names}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def two_models():
    return _make_models_dict("model_a", "model_b")


@pytest.fixture
def single_model():
    return _make_models_dict("model_a")


@pytest.fixture(autouse=True)
def reset_cached_strategy():
    """Reset the cached strategy class between tests."""
    Benchmark._cached_strategy_class = None
    yield
    Benchmark._cached_strategy_class = None


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset the AttackTechniqueRegistry between tests."""
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


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


def _make_benchmark(adversarial_models: dict[str, PromptChatTarget]) -> Benchmark:
    """Helper to create a Benchmark with mocked default scorer."""
    with patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer:
        mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
        return Benchmark(adversarial_models=adversarial_models)


# ===========================================================================
# Type and validation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkValidation:
    """Constructor validation and basic properties."""

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _make_benchmark({})

    def test_version_is_1(self):
        assert Benchmark.VERSION == 1

    def test_default_dataset_uses_harmbench(self):
        config = Benchmark.default_dataset_config()
        assert isinstance(config, DatasetConfiguration)
        assert "harmbench" in config.get_default_dataset_names()

    def test_default_dataset_max_size_is_8(self):
        assert Benchmark.default_dataset_config().max_dataset_size == 8

    def test_scenario_name(self, single_model):
        scenario = _make_benchmark(single_model)
        assert scenario.name == "Benchmark"


# ===========================================================================
# Strategy tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkStrategy:
    """Strategy class is static (no permutation) and adversarial-only."""

    def test_strategy_has_role_play_and_tap(self):
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "role_play" in values
        assert "tap" in values

    def test_strategy_excludes_non_adversarial(self):
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "prompt_sending" not in values
        assert "many_shot" not in values

    def test_strategy_has_no_permuted_members(self):
        """No __model suffix — models are not in the strategy axis."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert not any("__" in v for v in values)

    def test_default_strategy_is_all(self):
        default = Benchmark.get_default_strategy()
        assert default.value == "all"

    def test_strategy_class_is_same_across_instances(self, single_model, two_models):
        """Strategy class is static — identical for all instances."""
        s1 = _make_benchmark(single_model)
        s2 = _make_benchmark(two_models)
        assert s1._strategy_class is s2._strategy_class

    def test_benchmarkable_specs_have_no_adversarial_chat(self):
        """Filtered specs must not have adversarial_chat set — we inject our own."""
        for spec in _get_benchmarkable_specs():
            assert spec.adversarial_chat is None

    def test_benchmarkable_specs_are_adversarial_capable(self):
        """All filtered specs must accept attack_adversarial_config."""
        for spec in _get_benchmarkable_specs():
            assert AttackTechniqueRegistry._accepts_adversarial(spec.attack_class)


# ===========================================================================
# Runtime / attack generation tests
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkRuntime:
    """Tests for _get_atomic_attacks_async."""

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
    async def test_all_strategy_full_cross_product(self, mock_objective_target, two_models):
        """ALL: 2 techniques × 2 models × 1 dataset = 4 attacks."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_models,
        )
        assert len(attacks) == 4  # 2 techniques * 2 models * 1 dataset

    @pytest.mark.asyncio
    async def test_atomic_attack_names_are_unique(self, mock_objective_target, two_models):
        """All names must be unique for resume correctness."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_models,
        )
        names = [a.atomic_attack_name for a in attacks]
        assert len(names) == len(set(names))

    @pytest.mark.asyncio
    async def test_atomic_attack_names_contain_model_label(self, mock_objective_target, single_model):
        """Names should follow pattern: technique__model_dataset."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_model,
        )
        for a in attacks:
            assert "__model_a_" in a.atomic_attack_name

    @pytest.mark.asyncio
    async def test_display_groups_are_model_labels(self, mock_objective_target, two_models):
        """display_group should be the model label."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_models,
        )
        display_groups = {a.display_group for a in attacks}
        assert display_groups == {"model_a", "model_b"}

    @pytest.mark.asyncio
    async def test_adversarial_chat_matches_model(self, mock_objective_target, two_models):
        """Each attack's adversarial_chat should be the model target, not the factory default."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_models,
        )
        for a in attacks:
            assert a._adversarial_chat in two_models.values()

    @pytest.mark.asyncio
    async def test_technique_types_correct(self, mock_objective_target, single_model):
        """Attacks should use RolePlayAttack and TreeOfAttacksWithPruningAttack."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_model,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == {RolePlayAttack, TreeOfAttacksWithPruningAttack}

    @pytest.mark.asyncio
    async def test_multiple_datasets_multiplies_attacks(self, mock_objective_target, single_model):
        """2 techniques × 1 model × 2 datasets = 4 attacks."""
        two_datasets = {
            "harmbench": _make_seed_groups("harmbench"),
            "extra": _make_seed_groups("extra"),
        }
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_model,
            seed_groups=two_datasets,
        )
        assert len(attacks) == 4  # 2 techniques * 1 model * 2 datasets

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self, single_model):
        """_get_atomic_attacks_async must raise if initialize_async was not called."""
        scenario = _make_benchmark(single_model)
        with pytest.raises(ValueError, match="Scenario not properly initialized"):
            await scenario._get_atomic_attacks_async()

    @pytest.mark.asyncio
    async def test_attacks_have_seed_groups(self, mock_objective_target, single_model):
        """Each attack should have non-empty objectives."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_model,
        )
        for a in attacks:
            assert len(a.objectives) > 0

    @pytest.mark.asyncio
    async def test_registry_singleton_not_polluted(self, mock_objective_target, two_models):
        """Creating and running Benchmark must not register anything in the global singleton."""
        _, _ = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_models,
        )
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert not any("__" in name for name in factories)
