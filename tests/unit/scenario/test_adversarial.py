# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AdversarialBenchmark scenario."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack import AttackAdversarialConfig
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ScenarioIdentifier,
    ScenarioResult,
    SeedAttackGroup,
    SeedObjective,
    SeedPrompt,
)
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories


def _build_benchmarkable_factories_snapshot() -> list:
    """Build the benchmarkable-factory snapshot used by module-level test constants.

    Sets up a mock ``adversarial_chat`` in ``TargetRegistry`` so factory
    construction does not depend on environment variables, then filters the
    canonical scenario factories by the same predicate used by
    ``AdversarialBenchmark._get_benchmarkable_factories``.
    """
    TargetRegistry.reset_instance()
    adv = MagicMock(spec=PromptTarget)
    adv.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv, name="adversarial_chat")
    try:
        factories = build_scenario_technique_factories()
    finally:
        TargetRegistry.reset_instance()
    return [f for f in factories if f.uses_adversarial and "core" in f.strategy_tags]


# Self-pinned: any change to ``_get_benchmarkable_factories`` (or to the ``light`` tag
# membership in the canonical factory catalog) is reflected automatically — no magic numbers.
_BENCHMARKABLE_FACTORIES = _build_benchmarkable_factories_snapshot()
_NUM_ADVERSARIAL_TECHNIQUES = len(_BENCHMARKABLE_FACTORIES)
_BENCHMARKABLE_TECHNIQUE_NAMES = {f.name for f in _BENCHMARKABLE_FACTORIES}
_BENCHMARKABLE_ATTACK_CLASSES = {f.attack_class for f in _BENCHMARKABLE_FACTORIES}

_LIGHT_BENCHMARKABLE_FACTORIES = [f for f in _BENCHMARKABLE_FACTORIES if "light" in f.strategy_tags]
_NUM_LIGHT_BENCHMARKABLE = len(_LIGHT_BENCHMARKABLE_FACTORIES)

# ---------------------------------------------------------------------------
# Synthetic many-shot examples — prevents reading the real JSON during tests
# ---------------------------------------------------------------------------
_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"test question {i}", "answer": f"test answer {i}"} for i in range(100)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_id(name: str, *, params: dict | None = None) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test", params=params or {})


_CHAT_TARGET_CONFIGURATION = TargetConfiguration(
    capabilities=TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        supports_system_prompt=True,
        supports_editable_history=True,
    ),
)


def _make_adversarial_target(name: str, *, params: dict | None = None) -> MagicMock:
    """Create a mock adversarial PromptTarget with a given model name and optional identifier params.

    By default, ``model_name`` is stamped into the identifier params so the
    inferred label produced by ``_infer_labels`` matches ``name``.  Pass an
    explicit ``params`` dict to override (e.g. to omit the key for collision
    testing or to add ``underlying_model_name`` / ``endpoint``).

    The mock exposes a real ``TargetConfiguration`` declaring multi-turn and
    editable history so the target satisfies ``CHAT_TARGET_REQUIREMENTS`` at
    construction time.
    """
    mock = MagicMock(spec=PromptTarget)
    mock._model_name = name
    mock.get_identifier.return_value = _mock_id(name, params=params if params is not None else {"model_name": name})
    mock.configuration = _CHAT_TARGET_CONFIGURATION
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
    """Two mock adversarial models for benchmark permutation."""
    return [_make_adversarial_target("model_a"), _make_adversarial_target("model_b")]


@pytest.fixture
def single_adversarial_model():
    """Single mock adversarial model."""
    return [_make_adversarial_target("model_a")]


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries, populate scenario factories, and clear cached strategy class.

    Registers a mock adversarial target under ``adversarial_chat`` in
    ``TargetRegistry`` so ``build_scenario_technique_factories`` resolves
    without falling back to ``OpenAIChatTarget``.
    """
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    AdversarialBenchmark._cached_strategy_class = None

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

    technique_registry = AttackTechniqueRegistry.get_registry_singleton()
    technique_registry.register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    AdversarialBenchmark._cached_strategy_class = None


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

    def test_empty_list_adversarial_models_raises(self):
        """Passing an empty list must raise ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            AdversarialBenchmark(adversarial_models=[])

    def test_unsupported_type_adversarial_models_raises(self):
        """Passing a non-list type must raise ValueError."""
        with pytest.raises(ValueError, match="non-empty list|list of PromptTarget"):
            AdversarialBenchmark(adversarial_models="not-a-list")  # type: ignore[arg-type]

    def test_adversarial_model_missing_chat_capabilities_raises(self):
        """A target that does not satisfy CHAT_TARGET_REQUIREMENTS must be rejected at construction."""
        non_chat_target = MagicMock(spec=PromptTarget)
        non_chat_target.get_identifier.return_value = _mock_id("NonChatTarget")
        non_chat_target.configuration = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_editable_history=False,
            ),
        )

        with pytest.raises(ValueError, match="chat-target capability requirements"):
            AdversarialBenchmark(adversarial_models=[non_chat_target])

    def test_version_is_1(self):
        assert AdversarialBenchmark.VERSION == 1

    def test_default_dataset_config_uses_harmbench(self):
        config = AdversarialBenchmark.default_dataset_config()
        assert isinstance(config, DatasetConfiguration)
        names = config.get_default_dataset_names()
        assert "harmbench" in names

    def test_default_dataset_config_max_size_is_8(self):
        config = AdversarialBenchmark.default_dataset_config()
        assert config.max_dataset_size == 8


# ===========================================================================
# Strategy construction tests
# ===========================================================================


def _make_benchmark(adversarial_models):
    """Helper to create a AdversarialBenchmark with mocked default scorer."""
    with patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer:
        mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
        return AdversarialBenchmark(adversarial_models=adversarial_models)


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkStrategy:
    """Tests for the (static) BenchmarkStrategy enum and instance-level wiring."""

    def test_strategy_includes_all_adversarial_techniques(self, all_supported_attacks):
        """get_strategy_class() concrete members match the adversarial-capable spec set."""
        strat = AdversarialBenchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert values == all_supported_attacks

    def test_strategy_has_no_permuted_members(self):
        """No ``__model`` suffixes — models are a runtime parameter, not a strategy axis."""
        strat = AdversarialBenchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert not any("__" in v for v in values)

    def test_strategy_excludes_non_adversarial_techniques(self):
        """many_shot doesn't accept an adversarial chat and must be excluded."""
        strat = AdversarialBenchmark.get_strategy_class()
        values = {s.value for s in strat.get_all_strategies()}
        assert "many_shot" not in values

    def test_strategy_class_is_static(self, single_adversarial_model, two_adversarial_models):
        """All instances share the same strategy class — no per-instance permutation."""
        s1 = _make_benchmark(single_adversarial_model)
        s2 = _make_benchmark(two_adversarial_models)
        assert s1._strategy_class is s2._strategy_class
        assert s1._strategy_class is AdversarialBenchmark.get_strategy_class()

    def test_default_strategy_is_light(self):
        """Default expands to every benchmarkable technique via the ``all`` aggregate."""
        default = AdversarialBenchmark.get_default_strategy()
        assert default.value == "light"

    def test_benchmarkable_specs_have_no_adversarial_chat(self):
        """Benchmarkable factories must be tagged ``core`` (excludes persona variants)."""
        for factory in AdversarialBenchmark._get_benchmarkable_factories():
            assert "core" in factory.strategy_tags

    def test_benchmarkable_specs_accept_adversarial(self):
        """All filtered factories drive an adversarial chat."""
        for factory in AdversarialBenchmark._get_benchmarkable_factories():
            assert factory.uses_adversarial is True

    def test_original_scenario_techniques_unmodified(self, two_adversarial_models):
        """The benchmark's factory filter must not mutate the registry."""
        registry = AttackTechniqueRegistry.get_registry_singleton()
        before = sorted(registry.get_names())
        _make_benchmark(two_adversarial_models)
        after = sorted(registry.get_names())
        assert before == after

    def test_singleton_registry_not_polluted(self, two_adversarial_models):
        """Building atomic attacks must not register anything in the global singleton."""
        _make_benchmark(two_adversarial_models)
        registry = AttackTechniqueRegistry.get_registry_singleton()
        factories = registry.get_factories()
        assert not any("__" in name for name in factories)

    def test_scenario_name(self, single_adversarial_model):
        """Scenario name should be 'AdversarialBenchmark'."""
        scenario = _make_benchmark(single_adversarial_model)
        assert scenario.name == "AdversarialBenchmark"


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
    ) -> tuple[AdversarialBenchmark, list[AtomicAttack]]:
        """Helper: create AdversarialBenchmark, initialize, return (scenario, attacks)."""
        groups = seed_groups or {"harmbench": _make_seed_groups("harmbench")}
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = AdversarialBenchmark(adversarial_models=adversarial_models)
            init_kwargs: dict = {"objective_target": mock_objective_target}
            if strategies:
                init_kwargs["scenario_strategies"] = strategies
            await scenario.initialize_async(**init_kwargs)
            attacks = await scenario._get_atomic_attacks_async()
            return scenario, attacks

    @pytest.mark.asyncio
    async def test_default_strategy_runs_light_techniques(self, mock_objective_target, two_adversarial_models):
        """With no strategies passed, default ``light`` produces N_light x N_models attacks."""
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=two_adversarial_models,
        )
        assert len(attacks) == _NUM_LIGHT_BENCHMARKABLE * 2

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
            scenario = AdversarialBenchmark(adversarial_models=two_adversarial_models)
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
            scenario = AdversarialBenchmark(adversarial_models=two_adversarial_models)
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
            scenario = AdversarialBenchmark(adversarial_models=single_adversarial_model)
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
            scenario = AdversarialBenchmark(adversarial_models=two_adversarial_models)
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
        """1 model x N_light_techniques x 2 datasets = 2 * N_light atomic attacks (default ``light``)."""
        two_datasets = {
            "harmbench": _make_seed_groups("harmbench"),
            "extra": _make_seed_groups("extra"),
        }
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
            seed_groups=two_datasets,
        )
        assert len(attacks) == _NUM_LIGHT_BENCHMARKABLE * 2

    @pytest.mark.asyncio
    async def test_attacks_use_all_benchmarkable_attack_classes(self, mock_objective_target, single_adversarial_model):
        """Under the ``all`` strategy, atomic attacks must cover every adversarial-capable attack class."""
        scenario_class_strategies = AdversarialBenchmark.get_strategy_class()
        _, attacks = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
            strategies=[scenario_class_strategies("all")],
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

    async def test_baseline_excluded(self, mock_objective_target, single_adversarial_model):
        """AdversarialBenchmark must opt out of the parent's default baseline.

        Verifies both the class-level capability flag and the observable property
        (no atomic attack is named ``"baseline"``).
        """
        scenario, _ = await self._init_and_get_attacks(
            mock_objective_target=mock_objective_target,
            adversarial_models=single_adversarial_model,
        )
        assert type(scenario).BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden
        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)

    async def test_baseline_explicit_true_raises(self, mock_objective_target, single_adversarial_model):
        """Explicitly passing include_baseline=True to a forbidden scenario raises ValueError."""
        scenario = AdversarialBenchmark(adversarial_models=single_adversarial_model)
        with pytest.raises(ValueError, match="does not support a default baseline"):
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                include_baseline=True,
            )

    async def test_baseline_explicit_false_succeeds(self, mock_objective_target, single_adversarial_model):
        """Explicit include_baseline=False on a forbidden scenario is accepted (matches the default)."""
        groups = {"harmbench": _make_seed_groups("harmbench")}
        with (
            patch.object(DatasetConfiguration, "get_seed_attack_groups", return_value=groups),
            patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer") as mock_scorer,
        ):
            mock_scorer.return_value = MagicMock(spec=TrueFalseScorer, get_identifier=lambda: _mock_id("scorer"))
            scenario = AdversarialBenchmark(adversarial_models=single_adversarial_model)
            await scenario.initialize_async(
                objective_target=mock_objective_target,
                include_baseline=False,
            )
        assert not any(a.atomic_attack_name == "baseline" for a in scenario._atomic_attacks)


# ===========================================================================
# adversarial_models normalization tests (label inference / dedupe / collision)
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestBenchmarkAdversarialModelsNormalization:
    """Tests for the list → ``dict[str, AttackAdversarialConfig]`` normalization in __init__.

    Labels are inferred from each target's identifier; identical targets dedupe
    silently, distinct targets whose inferred names collide get suffixed with
    a warning.
    """

    def test_list_of_targets_infers_labels_from_model_name(self):
        """A list of bare targets is normalized to {model_name: AttackAdversarialConfig}."""
        t1 = _make_adversarial_target("t1", params={"model_name": "alpha"})
        t2 = _make_adversarial_target("t2", params={"model_name": "beta"})
        scenario = _make_benchmark([t1, t2])
        assert set(scenario._adversarial_configs.keys()) == {"alpha", "beta"}
        assert all(isinstance(v, AttackAdversarialConfig) for v in scenario._adversarial_configs.values())
        assert scenario._adversarial_configs["alpha"].target is t1
        assert scenario._adversarial_configs["beta"].target is t2

    def test_list_falls_back_to_underlying_model_name(self):
        """``underlying_model_name`` is preferred over ``model_name`` when present."""
        t = _make_adversarial_target("t", params={"underlying_model_name": "gpt-4o", "model_name": "wrapper"})
        scenario = _make_benchmark([t])
        assert "gpt-4o" in scenario._adversarial_configs

    def test_list_dedupe_silent_for_identical_target(self, caplog):
        """The same target instance passed twice in a list collapses to one entry, silently."""
        t = _make_adversarial_target("t", params={"model_name": "alpha"})
        with caplog.at_level("WARNING"):
            scenario = _make_benchmark([t, t])
        assert list(scenario._adversarial_configs.keys()) == ["alpha"]
        assert "collided" not in caplog.text

    def test_list_collision_suffixes_distinct_targets_and_warns(self, caplog):
        """Two distinct targets that infer the same name get suffixed and a warning is logged."""
        t1 = _make_adversarial_target("t1", params={"model_name": "alpha", "endpoint": "ep1"})
        t2 = _make_adversarial_target("t2", params={"model_name": "alpha", "endpoint": "ep2"})
        with caplog.at_level("WARNING"):
            scenario = _make_benchmark([t1, t2])
        assert set(scenario._adversarial_configs.keys()) == {"alpha", "alpha_2"}
        assert "collided" in caplog.text


# ===========================================================================
# ASR-sensibility tests (per-model breakdown math)
# ===========================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestBenchmarkASRBreakdown:
    """Verify the per-display-group ASR math the notebook sanity check relies on.

    A higher per-group success rate must correspond to more ``AttackOutcome.SUCCESS``
    results in that group.  This test pins the invariant that lets reviewers trust
    the printed breakdown when comparing adversarial models or system prompts.
    """

    @staticmethod
    def _result(*, conv_id: str, outcome: AttackOutcome) -> AttackResult:
        return AttackResult(
            conversation_id=conv_id,
            objective="objective",
            outcome=outcome,
            executed_turns=1,
        )

    def test_per_model_breakdown_reflects_outcome_counts(self):
        """High-success model > low-success model in per-group ASR; math invariants hold."""
        # Two techniques × two models, mirroring how AdversarialBenchmark keys atomic_attack_name
        # ("{technique}__{model_label}__{dataset}") and folds them into model_label.
        attack_results: dict[str, list[AttackResult]] = {
            "role_play__model_high__hb": [
                self._result(conv_id=f"high-rp-{i}", outcome=AttackOutcome.SUCCESS) for i in range(3)
            ],
            "context_compliance__model_high__hb": [
                self._result(conv_id=f"high-cc-{i}", outcome=AttackOutcome.SUCCESS) for i in range(3)
            ],
            "role_play__model_low__hb": [
                self._result(conv_id=f"low-rp-{i}", outcome=AttackOutcome.FAILURE) for i in range(3)
            ],
            "context_compliance__model_low__hb": [
                self._result(conv_id=f"low-cc-{i}", outcome=AttackOutcome.FAILURE) for i in range(3)
            ],
        }
        display_group_map = {
            "role_play__model_high__hb": "model_high",
            "context_compliance__model_high__hb": "model_high",
            "role_play__model_low__hb": "model_low",
            "context_compliance__model_low__hb": "model_low",
        }
        result = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name="AdversarialBenchmark", scenario_version=1),
            objective_target_identifier=ComponentIdentifier(class_name="MockTarget", class_module="test"),
            attack_results=attack_results,
            objective_scorer_identifier=ComponentIdentifier(class_name="MockScorer", class_module="test"),
            display_group_map=display_group_map,
        )

        groups = result.get_display_groups()
        assert set(groups.keys()) == {"model_high", "model_low"}

        per_group = {
            label: int(sum(1 for r in rs if r.outcome == AttackOutcome.SUCCESS) / max(len(rs), 1) * 100)
            for label, rs in groups.items()
        }

        # The whole point of the sanity check: more SUCCESSes ⇒ higher rate.
        assert per_group["model_high"] == 100
        assert per_group["model_low"] == 0
        assert per_group["model_high"] > per_group["model_low"]
        # Bounds invariant the notebook asserts.
        assert all(0 <= rate <= 100 for rate in per_group.values())

        # Overall rate matches the weighted average (6 SUCCESS / 12 total = 50%).
        assert result.objective_achieved_rate() == 50

        # Display grouping must not lose results.
        assert sum(len(rs) for rs in groups.values()) == sum(len(rs) for rs in attack_results.values())
