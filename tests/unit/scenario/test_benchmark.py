# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Benchmark scenario."""

import copy
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES
from pyrit.scenario.scenarios.benchmark.benchmark import Benchmark
from pyrit.score import TrueFalseScorer

# Pin the technique count to whatever production currently considers benchmarkable.
# Self-pinning: any change to ``_get_benchmarkable_specs`` is reflected here, but
# count-based assertions stay correct without hard-coding a magic number.
_NUM_ADVERSARIAL_TECHNIQUES = len(Benchmark._get_benchmarkable_specs())
_BENCHMARKABLE_TECHNIQUE_NAMES = {spec.name for spec in Benchmark._get_benchmarkable_specs()}
_BENCHMARKABLE_ATTACK_CLASSES = {spec.attack_class for spec in Benchmark._get_benchmarkable_specs()}

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
def all_supported_attacks():
    """All attacks that currently support adversarial models (computed from production)."""
    return _BENCHMARKABLE_TECHNIQUE_NAMES


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def two_adversarial_models():
    """Two mock adversarial models for benchmark permutation"""
    return {"model_a": _make_adversarial_target("model_a"), "model_b": _make_adversarial_target("model_b")}


@pytest.fixture
def single_adversarial_model():
    """Single mock adversarial model."""
    return {"model_a": _make_adversarial_target("model_a")}


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
        """Passing an empty dict must raise ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            Benchmark(adversarial_models={})

    def test_non_dict_adversarial_models_raises(self):
        """Passing a list (legacy 1662 shape) must raise ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            Benchmark(adversarial_models=[MagicMock(spec=PromptChatTarget)])  # type: ignore[arg-type]

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
            spec.name = "mutated"  # type: ignore[misc]


# ===========================================================================
# Strategy construction tests
# ===========================================================================


def _make_benchmark(adversarial_models):
    """Helper to create a Benchmark with mocked default scorer."""
    with patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer:
        mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
        return Benchmark(adversarial_models=adversarial_models)


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkStrategy:
    """Tests for the (static) BenchmarkStrategy enum and instance-level wiring."""

    def test_strategy_includes_all_adversarial_techniques(self, all_supported_attacks):
        """get_strategy_class() concrete members match the adversarial-capable spec set."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert values == all_supported_attacks

    def test_strategy_has_no_permuted_members(self):
        """No ``__model`` suffixes — models are a runtime parameter, not a strategy axis."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert not any("__" in v for v in values)

    def test_strategy_excludes_non_adversarial_techniques(self):
        """prompt_sending and many_shot don't accept an adversarial chat and must be excluded."""
        strat = Benchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "prompt_sending" not in values
        assert "many_shot" not in values

    def test_strategy_class_is_static(self, single_adversarial_model, two_adversarial_models):
        """All instances share the same strategy class — no per-instance permutation."""
        s1 = _make_benchmark(single_adversarial_model)
        s2 = _make_benchmark(two_adversarial_models)
        assert s1._strategy_class is s2._strategy_class
        assert s1._strategy_class is Benchmark.get_strategy_class()

    def test_default_strategy_is_all(self):
        """Default expands to every benchmarkable technique via the ``all`` aggregate."""
        default = Benchmark.get_default_strategy()
        assert default.value == "all"

    def test_benchmarkable_specs_have_no_adversarial_chat(self):
        """Filtered specs must leave adversarial_chat unset — the scenario injects its own."""
        for spec in Benchmark._get_benchmarkable_specs():
            assert spec.adversarial_chat is None

    def test_benchmarkable_specs_accept_adversarial(self):
        """All filtered specs must accept attack_adversarial_config."""
        for spec in Benchmark._get_benchmarkable_specs():
            assert AttackTechniqueRegistry._accepts_adversarial(spec.attack_class)

    def test_original_scenario_techniques_unmodified(self, two_adversarial_models):
        """SCENARIO_TECHNIQUES global must not be mutated by spec filtering."""
        original = copy.deepcopy([(s.name, s.attack_class) for s in SCENARIO_TECHNIQUES])
        _make_benchmark(two_adversarial_models)
        current = [(s.name, s.attack_class) for s in SCENARIO_TECHNIQUES]
        assert current == original

    def test_singleton_registry_not_polluted(self, two_adversarial_models):
        """Building atomic attacks must not register anything in the global singleton."""
        _make_benchmark(two_adversarial_models)
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert not any("__" in name for name in factories)

    def test_empty_label_in_dict_raises(self):
        """An empty user-chosen label must raise ValueError."""
        model = MagicMock(spec=PromptChatTarget)
        model.get_identifier.return_value = _mock_id("AnyTarget")
        with pytest.raises(ValueError, match="Empty user-chosen label"):
            _make_benchmark({"": model})

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
    async def test_default_strategy_runs_all_techniques(self, mock_objective_target, two_adversarial_models):
        """With no strategies passed, default ``all`` produces N_techniques x N_models attacks."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_adversarial_models,
        )
        assert len(attacks) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    @pytest.mark.asyncio
    async def test_all_strategy_produces_full_cross_product(self, mock_objective_target, two_adversarial_models):
        """ALL strategy: N_techniques x 2 models x 1 dataset attacks."""
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
        """1 model x N_techniques x 2 datasets = 2 * N_techniques atomic attacks."""
        two_datasets = {
            "harmbench": _make_seed_groups("harmbench"),
            "extra": _make_seed_groups("extra"),
        }
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
            seed_groups=two_datasets,
        )
        assert len(attacks) == _NUM_ADVERSARIAL_TECHNIQUES * 2

    @pytest.mark.asyncio
    async def test_attacks_use_all_benchmarkable_attack_classes(self, mock_objective_target, single_adversarial_model):
        """Atomic attacks must cover every adversarial-capable attack class."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
        )
        technique_classes = {type(a.attack_technique.attack) for a in attacks}
        assert technique_classes == _BENCHMARKABLE_ATTACK_CLASSES

    @pytest.mark.asyncio
    async def test_attacks_carry_seed_groups(self, mock_objective_target, single_adversarial_model):
        """Each atomic attack should have non-empty objectives from the seed groups."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
        )
        for a in attacks:
            assert len(a.objectives) > 0
