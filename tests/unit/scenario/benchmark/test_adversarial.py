# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the post-collapse AdversarialBenchmark scenario.

AdversarialBenchmark now owns its adversarial target axis directly via
the ``adversarial_targets`` parameter declared in
``supported_parameters``. Targets are user-supplied registry names
that resolve to ``PromptTarget`` instances via ``TargetRegistry``. The
``(technique × target × dataset)`` cross-product is built lazily inside
``_build_atomic_attacks_async`` using factory.create() with an
adversarial config override; no global ``AttackTechniqueRegistry``
state is mutated.

These tests cover the new contract:
* Class metadata (VERSION, BASELINE policy, defaults).
* Technique enum is built from registered factories with ``uses_adversarial=True``
  that do not bake their own ``adversarial_chat``; the default expands to the exact
  benchmark set while the ``light`` aggregate remains selectable.
* ``supported_parameters`` declares ``adversarial_targets: list[str]``.
* ``_resolve_adversarial_targets`` raises with available names on typos.
* ``_build_atomic_attacks_async`` produces ``N × M × D`` atomic attacks
  with the expected ``atomic_attack_name`` and ``display_group``.
* ``_collect_cached_completion_pairs`` delegates to
  ``pyrit.analytics.get_cached_results_for_technique`` per unique
  technique hash and returns the set of technique hashes with at least
  one ``SUCCESS`` / ``FAILURE`` match for the scenario's objective target.
* ``use_cached`` filters cached candidates end-to-end.
* Real-memory smoke for ``_collect_cached_completion_pairs`` exercises
  persistence -> SQL filter -> objective-target filter -> outcome filter.
"""

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.common.utils import to_sha256
from pyrit.executor.attack import AttackScoringConfig, TreeOfAttacksWithPruningAttack
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ObjectiveTargetEvaluationIdentifier,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunState,
    Score,
    ScorerEvaluationIdentifier,
    SeedObjective,
    TargetIdentifier,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark, _build_benchmark_technique
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories

# ---------------------------------------------------------------------------
# Module-level constants derived from the canonical factory catalog
# ---------------------------------------------------------------------------


def _build_benchmarkable_factories_snapshot() -> list:
    """Compute benchmarkable-factory counts from the production catalog.

    Sets up a transient mock ``adversarial_chat`` in ``TargetRegistry`` so
    factory construction does not depend on environment variables, then filters
    by the same predicate used in ``AdversarialBenchmark._get_benchmarkable_factories``.
    """
    TargetRegistry.reset_registry_singleton()
    adv = MagicMock(spec=PromptTarget)
    adv.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv, name="adversarial_chat")
    try:
        factories = build_technique_factories()
    finally:
        TargetRegistry.reset_registry_singleton()
    return [f for f in factories if f.uses_adversarial and f.adversarial_chat is None]


_BENCHMARKABLE_FACTORIES = _build_benchmarkable_factories_snapshot()
_BENCHMARKABLE_TECHNIQUE_NAMES = {f.name for f in _BENCHMARKABLE_FACTORIES}
_DEFAULT_BENCHMARK_TECHNIQUE_NAMES = {
    "role_play_video_game",
    "crescendo_simulated",
    "tap",
}

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries, register a mock adversarial target, and populate real factories.

    Registers a mock ``adversarial_chat`` target so ``build_technique_factories``
    resolves without depending on environment variables. Uses ``_build_benchmark_technique.cache_clear()``
    because our implementation uses ``@cache`` (not ``_cached_technique_class``).
    """
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()


def _register_adversarial_target(*, name: str) -> PromptTarget:
    """Register a mock adversarial target in TargetRegistry."""
    target = MagicMock(spec=PromptTarget)
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(target, name=name)
    return target


def _register_mock_factory(*, name: str, tags: list[str] | None = None, seed_technique=None) -> MagicMock:
    """Register a mock AttackTechniqueFactory in AttackTechniqueRegistry."""
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.name = name
    factory.uses_adversarial = True
    factory.adversarial_chat = None
    factory.technique_tags = tags if tags is not None else ["core", "light"]
    factory.seed_technique = seed_technique
    technique_instance = MagicMock(name="AttackTechnique")
    technique_instance.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTechnique", class_module="pyrit.test"
    )
    factory.create.return_value = technique_instance
    factory.attack_class = MagicMock(__name__=name)
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories([factory])
    return factory


async def _build_atomic_attacks(bench: AdversarialBenchmark) -> list:
    """Drive the post-``initialize_async`` build path: resolve seeds, snapshot the
    context, then build atomic attacks — the same sequence ``initialize_async`` runs."""
    seed_groups_by_dataset = await bench._resolve_seed_groups_by_dataset_async(
        apply_sampling=bench._scenario_result_id is None
    )
    context = bench._build_scenario_context(seed_groups_by_dataset=seed_groups_by_dataset)
    return await bench._build_atomic_attacks_async(context=context)


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkMetadata:
    """Tests for class-level metadata that doesn't depend on any runtime state."""

    def test_version_is_5(self):
        """VERSION 5 identifies runs using task-achievement objective scoring."""
        assert AdversarialBenchmark.VERSION == 5

    def test_baseline_attack_policy_is_forbidden(self):
        """A baseline contributes no signal to a model-comparison benchmark, so it is forbidden."""
        assert AdversarialBenchmark.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden

    def test_task_achieved_rubric_is_used(self):
        expected = SCORER_SEED_PROMPT_PATH / "true_false_question" / "task_achieved_refined.yaml"

        assert AdversarialBenchmark._get_additional_scoring_questions() == [expected]


# ---------------------------------------------------------------------------
# supported_parameters
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkSupportedParameters:
    """Tests for the ``adversarial_targets`` parameter declaration."""

    def test_declares_adversarial_targets_param(self):
        params = AdversarialBenchmark.supported_parameters()
        names = [p.name for p in params]
        assert "adversarial_targets" in names

    def test_adversarial_targets_param_is_list_of_str(self):
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}
        param = params["adversarial_targets"]
        assert param.param_type == list[str]

    def test_adversarial_targets_default_is_none(self):
        """``None`` default lets the scenario raise a domain-specific error rather than the framework default."""
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}
        assert params["adversarial_targets"].default is None

    def test_adversarial_targets_description_mentions_cli_flag(self):
        """The description must point users at ``--adversarial-targets`` for discoverability."""
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}
        description = params["adversarial_targets"].description
        assert "--adversarial-targets" in description

    def test_declares_use_cached_false_by_default(self):
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}
        use_cached = params["use_cached"]

        assert use_cached.param_type is bool
        assert use_cached.default is None
        assert "Defaults to false" in use_cached.description

    @pytest.mark.parametrize(("raw_value", "expected"), [("true", True), ("False", False)])
    def test_use_cached_parameter_coerces_cli_boolean(self, raw_value: str, expected: bool) -> None:
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}

        assert params["use_cached"].coerce_value(raw_value) is expected

    @pytest.mark.parametrize(
        "parameter_name",
        ["tap_tree_width", "tap_tree_depth", "tap_branching_factor", "tap_batch_size"],
    )
    def test_declares_optional_tap_override(self, parameter_name: str) -> None:
        params = {p.name: p for p in AdversarialBenchmark.supported_parameters()}

        assert params[parameter_name].param_type is int
        assert params[parameter_name].default is None


# ---------------------------------------------------------------------------
# Technique class construction
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkTechnique:
    """Tests for ``_build_benchmark_technique`` using the registry-based factory API."""

    def test_technique_built_from_registered_adversarial_factories(self):
        """Each registered adversarial factory produces one concrete enum member."""
        technique_cls = _build_benchmark_technique()
        aggregate_names = {"all"} | technique_cls.get_aggregate_tags()
        concrete_members = [m for m in technique_cls if m.value not in aggregate_names]
        concrete_member_values = {m.value for m in concrete_members}
        assert concrete_member_values == _BENCHMARKABLE_TECHNIQUE_NAMES

    def test_technique_excludes_non_adversarial_factories(self):
        """Factories without ``uses_adversarial=True`` must not appear as enum members."""
        # Register a non-adversarial factory directly
        non_adv = MagicMock(spec=AttackTechniqueFactory)
        non_adv.name = "prompt_sending"
        non_adv.uses_adversarial = False
        non_adv.technique_tags = ["core", "light"]
        non_adv.seed_technique = None
        non_adv.attack_class = MagicMock(__name__="prompt_sending")
        non_adv.create.return_value = MagicMock()
        AttackTechniqueRegistry.get_registry_singleton().register_from_factories([non_adv])

        technique_cls = _build_benchmark_technique()
        member_values = {m.value for m in technique_cls}
        assert "prompt_sending" not in member_values

    def test_technique_excludes_factories_with_baked_adversarial_chat(self):
        """Adversarial factories that bake their own ``adversarial_chat`` are not swept."""
        baked = MagicMock(spec=AttackTechniqueFactory)
        baked.name = "pinned_adversary"
        baked.uses_adversarial = True
        baked.technique_tags = ["core", "light"]
        baked.seed_technique = None
        baked.attack_class = MagicMock(__name__="pinned_adversary")
        baked.adversarial_chat = MagicMock()
        baked.create.return_value = MagicMock()
        AttackTechniqueRegistry.get_registry_singleton().register_from_factories([baked])

        technique_cls = _build_benchmark_technique()
        member_values = {m.value for m in technique_cls}
        assert "pinned_adversary" not in member_values

    def test_technique_exposes_tag_aggregates(self):
        """The technique enum exposes ``light``, ``single_turn``, ``multi_turn`` aggregates."""
        technique_cls = _build_benchmark_technique()
        aggregates = technique_cls.get_aggregate_tags()
        assert "light" in aggregates
        assert "single_turn" in aggregates
        assert "multi_turn" in aggregates

    def test_default_expands_to_exact_benchmark_techniques(self):
        """The synthetic default contains only the evidence-backed benchmark techniques."""
        technique_cls = _build_benchmark_technique()
        resolved_values = {child.value for child in technique_cls.expand({technique_cls.default()})}
        assert resolved_values == _DEFAULT_BENCHMARK_TECHNIQUE_NAMES

    def test_light_aggregate_excludes_non_light_techniques(self):
        """Techniques without the ``light`` tag must not appear in the ``light`` aggregate."""
        technique_cls = _build_benchmark_technique()
        light_member = technique_cls("light")
        resolved_values = {child.value for child in technique_cls.expand({light_member})}
        assert "tap" not in resolved_values
        assert "red_teaming" in resolved_values

    def test_light_aggregate_includes_red_teaming(self):
        """Sanity check: ``red_teaming`` tagged ``light`` appears in the ``light`` aggregate."""
        technique_cls = _build_benchmark_technique()
        light_member = technique_cls("light")
        resolved_values = {child.value for child in technique_cls.expand({light_member})}
        assert "red_teaming" in resolved_values


# ---------------------------------------------------------------------------
# Construction (collapsed __init__)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialBenchmarkInit:
    """Tests for the collapsed ``__init__`` surface."""

    def test_construct_with_default_objective_scorer(self):
        """When no scorer is supplied, ``_get_default_objective_scorer`` is consulted."""
        default_scorer = MagicMock(spec=TrueFalseScorer)
        with patch.object(AdversarialBenchmark, "_get_default_objective_scorer", return_value=default_scorer):
            bench = AdversarialBenchmark()
        assert bench._objective_scorer is default_scorer

    def test_construct_with_explicit_objective_scorer(self):
        explicit_scorer = MagicMock(spec=TrueFalseScorer)
        bench = AdversarialBenchmark(objective_scorer=explicit_scorer)
        assert bench._objective_scorer is explicit_scorer

    def test_construct_takes_no_adversarial_models_param(self):
        """Regression: the old ``adversarial_models`` constructor param is removed."""
        with pytest.raises(TypeError):
            AdversarialBenchmark(adversarial_models=[MagicMock(spec=PromptTarget)])  # type: ignore[call-arg]

    def test_construct_takes_no_models_param(self):
        """Regression: the interim ``models`` param (BenchmarkInitializer era) is removed."""
        with pytest.raises(TypeError):
            AdversarialBenchmark(models=[MagicMock(spec=PromptTarget)])  # type: ignore[call-arg]

    def test_skip_cached_defaults_to_false(self):
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        assert bench._use_cached is False

    def test_skip_cached_can_be_set_true(self):
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            use_cached=True,
        )
        assert bench._use_cached is True

    def test_construct_without_named_default_factory_falls_back_to_all(self):
        """A pool with none of the named defaults must still construct, defaulting to ``all``."""
        AttackTechniqueRegistry.reset_registry_singleton()
        _build_benchmark_technique.cache_clear()
        _register_mock_factory(name="custom_technique", tags=["custom", "multi_turn"])

        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))

        assert "light" not in bench._technique_class.get_aggregate_tags()
        assert bench._default_technique.value == "all"

    async def test_initialize_without_selection_resolves_exact_default(self):
        """Omitting ``scenario_techniques`` resolves exactly the approved benchmark defaults."""
        objective_target = MagicMock(spec=PromptTarget)
        objective_target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockObjectiveTarget", class_module="pyrit.test"
        )
        objective_scorer = MagicMock(spec=TrueFalseScorer)
        objective_scorer.get_identifier.return_value = ComponentIdentifier(
            class_name="MockObjectiveScorer", class_module="pyrit.test"
        )
        bench = AdversarialBenchmark(objective_scorer=objective_scorer)
        bench.set_params_from_args(
            args={
                "objective_target": objective_target,
                "adversarial_targets": ["adversarial_chat"],
            }
        )

        with (
            patch.object(bench, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value={}),
            patch.object(bench, "_build_atomic_attacks_async", new_callable=AsyncMock, return_value=[]),
        ):
            await bench.initialize_async()

        assert {technique.value for technique in bench._scenario_techniques} == _DEFAULT_BENCHMARK_TECHNIQUE_NAMES

    def test_tap_constructs_with_benchmark_scorer_policy(self, caplog):
        """The benchmark's generic scorer is skipped under TAP's WARN policy so TAP can use its own scorer."""
        factory = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()["tap"]

        objective_target = MagicMock(spec=PromptTarget)
        objective_target.configuration.capabilities.output_modalities = [{"text"}]
        objective_target.get_identifier.return_value = TargetIdentifier(
            class_name="MockObjectiveTarget",
            class_module="tests.unit.scenario.benchmark.test_adversarial",
        )
        adversarial_target = TargetRegistry.get_registry_singleton().instances.get("adversarial_chat")
        adversarial_target.get_identifier.return_value = TargetIdentifier(
            class_name="MockAdversarialTarget",
            class_module="tests.unit.scenario.benchmark.test_adversarial",
        )
        scoring_config = AttackScoringConfig(objective_scorer=MagicMock(spec=TrueFalseScorer))

        with caplog.at_level(logging.WARNING):
            technique = factory.create(
                objective_target=objective_target,
                attack_scoring_config=scoring_config,
                adversarial_chat=adversarial_target,
            )

        assert isinstance(technique.attack, TreeOfAttacksWithPruningAttack)
        assert any("incompatible" in record.message for record in caplog.records)
        atomic_attack = MagicMock()
        atomic_attack.attack_technique = technique
        expected_scorer_hash = ScorerEvaluationIdentifier(technique.attack._objective_scorer.get_identifier()).eval_hash
        assert AdversarialBenchmark._get_attack_scorer_eval_hash(atomic_attack=atomic_attack) == expected_scorer_hash

    def test_tap_factory_override_applies_search_parameters_and_changes_identity(self) -> None:
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench.params = {
            "tap_tree_width": 2,
            "tap_tree_depth": 3,
            "tap_branching_factor": 2,
            "tap_batch_size": 2,
        }
        override = bench._get_tap_factory_override()
        assert override is not None
        registered_factory = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()["tap"]
        assert override["tap"].description == registered_factory.description
        assert override["tap"].attack_class is registered_factory.attack_class
        assert override["tap"].technique_tags == registered_factory.technique_tags

        objective_target = MagicMock(spec=PromptTarget)
        objective_target.configuration.capabilities.output_modalities = [{"text"}]
        objective_target.get_identifier.return_value = TargetIdentifier(
            class_name="MockObjectiveTarget",
            class_module="tests.unit.scenario.benchmark.test_adversarial",
        )
        adversarial_target = TargetRegistry.get_registry_singleton().instances.get("adversarial_chat")
        adversarial_target.get_identifier.return_value = TargetIdentifier(
            class_name="MockAdversarialTarget",
            class_module="tests.unit.scenario.benchmark.test_adversarial",
        )
        scoring_config = AttackScoringConfig(objective_scorer=MagicMock(spec=TrueFalseScorer))

        default_technique = (
            AttackTechniqueRegistry.get_registry_singleton()
            .get_factories_or_raise()["tap"]
            .create(
                objective_target=objective_target,
                attack_scoring_config=scoring_config,
                adversarial_chat=adversarial_target,
            )
        )
        quick_technique = override["tap"].create(
            objective_target=objective_target,
            attack_scoring_config=scoring_config,
            adversarial_chat=adversarial_target,
        )

        configuration = quick_technique.attack._configuration
        assert configuration.tree_width == 2
        assert configuration.tree_depth == 3
        assert configuration.branching_factor == 2
        assert configuration.batch_size == 2
        default_identity = AtomicAttackEvaluationIdentifier(
            AtomicAttackIdentifier.build(technique_identifier=default_technique.get_identifier())
        )
        quick_identity = AtomicAttackEvaluationIdentifier(
            AtomicAttackIdentifier.build(technique_identifier=quick_technique.get_identifier())
        )
        assert quick_identity.eval_hash != default_identity.eval_hash

    def test_tap_factory_override_is_absent_when_parameters_are_unset(self) -> None:
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench.params = {}

        assert bench._get_tap_factory_override() is None


# ---------------------------------------------------------------------------
# _resolve_adversarial_targets
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestResolveAdversarialTargets:
    """Tests for ``_resolve_adversarial_targets``: registry lookup + actionable errors on miss."""

    def _make_bench(self) -> AdversarialBenchmark:
        return AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))

    def test_resolves_registered_targets(self):
        t_a = _register_adversarial_target(name="adv_a")
        t_b = _register_adversarial_target(name="adv_b")
        bench = self._make_bench()

        resolved = bench._resolve_adversarial_targets(target_names=["adv_a", "adv_b"])

        names = [name for name, _ in resolved]
        instances = [inst for _, inst in resolved]
        assert names == ["adv_a", "adv_b"]
        assert instances == [t_a, t_b]

    def test_unknown_target_raises_with_available_list(self):
        _register_adversarial_target(name="adv_a")
        bench = self._make_bench()

        with pytest.raises(ValueError) as exc_info:
            bench._resolve_adversarial_targets(target_names=["adv_a", "missing"])

        message = str(exc_info.value)
        assert "missing" in message
        assert "adv_a" in message  # available list should include registered targets

    def test_all_unknown_targets_raises(self):
        bench = self._make_bench()

        with pytest.raises(ValueError, match="not found in TargetRegistry"):
            bench._resolve_adversarial_targets(target_names=["nope_1", "nope_2"])

    def test_preserves_caller_order(self):
        _register_adversarial_target(name="adv_b")
        _register_adversarial_target(name="adv_a")
        _register_adversarial_target(name="adv_c")
        bench = self._make_bench()

        resolved = bench._resolve_adversarial_targets(target_names=["adv_c", "adv_a", "adv_b"])
        names = [name for name, _ in resolved]
        assert names == ["adv_c", "adv_a", "adv_b"]


# ---------------------------------------------------------------------------
# _build_atomic_attacks_async — validation and cross-product
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestGetAtomicAttacksValidation:
    """Tests for validation errors raised by ``_build_atomic_attacks_async``."""

    def _make_bench(self) -> AdversarialBenchmark:
        return AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))

    async def test_uninitialized_scenario_raises(self):
        """Building a context before ``initialize_async`` raises a clear error."""
        bench = self._make_bench()
        bench._objective_target = None

        with pytest.raises(ValueError, match="not properly initialized"):
            bench._build_scenario_context(seed_groups_by_dataset={})

    async def test_missing_adversarial_targets_raises_actionable_error(self):
        """Empty/missing ``adversarial_targets`` raises a message pointing at CLI / .pyrit_conf / list-targets."""
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {}

        context = bench._build_scenario_context(seed_groups_by_dataset={})
        with pytest.raises(ValueError) as exc_info:
            await bench._build_atomic_attacks_async(context=context)

        message = str(exc_info.value)
        assert "--adversarial-targets" in message
        assert ".pyrit_conf" in message
        assert "list-targets" in message

    async def test_empty_adversarial_targets_list_raises(self):
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": []}

        context = bench._build_scenario_context(seed_groups_by_dataset={})
        with pytest.raises(ValueError, match="at least one adversarial chat target"):
            await bench._build_atomic_attacks_async(context=context)

    async def test_unknown_target_name_raises_listing_available(self):
        _register_adversarial_target(name="adv_a")
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": ["missing"]}

        context = bench._build_scenario_context(seed_groups_by_dataset={})
        with pytest.raises(ValueError) as exc_info:
            await bench._build_atomic_attacks_async(context=context)

        message = str(exc_info.value)
        assert "missing" in message
        assert "adv_a" in message


@pytest.mark.usefixtures("patch_central_database")
class TestGetAtomicAttacksCrossProduct:
    """Tests for the (technique × target × dataset) cross-product produced by ``_build_atomic_attacks_async``."""

    def _make_bench_with_targets(self, *, target_names: list[str]) -> AdversarialBenchmark:
        for name in target_names:
            _register_adversarial_target(name=name)
        # Reset the technique registry so we can register a controllable mock factory
        # whose create() return value we can inspect.
        AttackTechniqueRegistry.reset_registry_singleton()
        _build_benchmark_technique.cache_clear()
        _register_mock_factory(name="red_teaming", tags=["core", "light"])
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": target_names}

        red_teaming_technique = MagicMock()
        red_teaming_technique.value = "red_teaming"
        bench._scenario_techniques = [red_teaming_technique]

        # Dataset config: one dataset with one real seed group (AtomicAttack hashes objectives).
        seed_group = AttackSeedGroup(seeds=[SeedObjective(value="benchmark_objective_1")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.max_dataset_size = None
        bench._dataset_config.get_attack_groups_by_dataset_async = AsyncMock(return_value={"harmbench": [seed_group]})

        return bench

    async def test_cross_product_count_matches_n_techniques_m_targets_d_datasets(self):
        """1 technique × 2 targets × 1 dataset = 2 atomic attacks."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])
        result = await _build_atomic_attacks(bench)
        assert len(result) == 2

    async def test_atomic_attack_name_format_is_technique__target_dataset(self):
        """Name format: ``{technique}__{target}_{dataset}`` (preserves VERSION=2 cache key shape)."""
        bench = self._make_bench_with_targets(target_names=["adv_a"])
        result = await _build_atomic_attacks(bench)
        names = [a.atomic_attack_name for a in result]
        assert names == ["red_teaming__adv_a_harmbench"]

    async def test_display_group_equals_target_registry_name(self):
        """``display_group`` is the raw target registry name — no string parsing."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])
        result = await _build_atomic_attacks(bench)
        display_groups = sorted({a.display_group for a in result})
        assert display_groups == ["adv_a", "adv_b"]

    async def test_display_group_uses_registry_name_not_target_model_name(self):
        """Regression: ``display_group`` must come from the registry name, not the target's internal fields."""
        target = MagicMock(spec=PromptTarget)
        target._model_name = "totally-different-model-name"
        target._underlying_model = "another-model-identity"
        target._endpoint = "https://hijacked.example.com/openai/v1"
        target.name = "name-attribute-that-must-not-leak"
        TargetRegistry.get_registry_singleton().instances.register(target, name="adv_a")
        # Reset the technique registry to get a controllable mock factory
        AttackTechniqueRegistry.reset_registry_singleton()
        _build_benchmark_technique.cache_clear()
        _register_mock_factory(name="red_teaming", tags=["core", "light"])

        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": ["adv_a"]}

        red_teaming_technique = MagicMock()
        red_teaming_technique.value = "red_teaming"
        bench._scenario_techniques = [red_teaming_technique]

        seed_group = AttackSeedGroup(seeds=[SeedObjective(value="display_group_regression_objective")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.get_attack_groups_by_dataset_async = AsyncMock(return_value={"harmbench": [seed_group]})

        result = await _build_atomic_attacks(bench)

        assert len(result) == 1
        atomic = result[0]
        assert atomic.display_group == "adv_a", (
            f"display_group must equal the registry name 'adv_a', got {atomic.display_group!r}."
        )
        assert atomic.atomic_attack_name == "red_teaming__adv_a_harmbench"

    async def test_factory_create_called_per_target_with_adversarial_chat(self):
        """Each (factory, target) pair calls ``factory.create`` with an ``adversarial_chat`` target."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])
        factory = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()["red_teaming"]

        await _build_atomic_attacks(bench)

        # 1 factory × 2 targets × 1 dataset = 2 create calls
        assert factory.create.call_count == 2
        target_a = TargetRegistry.get_registry_singleton().instances.get("adv_a")
        target_b = TargetRegistry.get_registry_singleton().instances.get("adv_b")
        injected_targets = {call.kwargs["adversarial_chat"] for call in factory.create.call_args_list}
        assert injected_targets == {target_a, target_b}


# ---------------------------------------------------------------------------
# _collect_cached_completion_pairs
# ---------------------------------------------------------------------------


def _make_attack_result_with_outcome(outcome: AttackOutcome) -> MagicMock:
    """Build a minimal ``AttackResult`` stand-in for cache-hit tests.

    The new analytics-backed cache filter only reads ``outcome`` off each
    match — the (technique × objective target) keying is done by the
    analytics lookup parameters, not by introspecting result fields.
    """
    ar = MagicMock()
    ar.outcome = outcome
    return ar


def _make_attack_result_with_attribution(*, outcome: AttackOutcome, parent_collection: str) -> MagicMock:
    """Like ``_make_attack_result_with_outcome`` but with attribution_data for parent-collection filtering."""
    ar = MagicMock()
    ar.outcome = outcome
    ar.objective = "skip_cached_objective"
    ar.attribution_data = {"parent_collection": parent_collection}
    return ar


@pytest.mark.usefixtures("patch_central_database")
class TestCollectCachedCompletionPairs:
    """Tests for ``_collect_cached_completion_pairs`` — now delegates to ``pyrit.analytics``."""

    _ANALYTICS_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.get_cached_results_for_technique"
    _IDENTIFIER_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.ObjectiveTargetEvaluationIdentifier"

    def _make_bench(self, *, with_target_identifier: bool = True) -> AdversarialBenchmark:
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._memory = MagicMock()
        bench._objective_target_identifier = MagicMock() if with_target_identifier else None
        return bench

    def _make_candidate(self, *, technique_eval_hash: str | None, atomic_attack_name: str = "attack_a") -> MagicMock:
        candidate = MagicMock()
        candidate.technique_eval_hash = technique_eval_hash
        candidate.atomic_attack_name = atomic_attack_name
        return candidate

    def _patch_identifier(self, eval_hash: str = "obj_target_hash"):
        """Patch ``ObjectiveTargetEvaluationIdentifier`` so we don't need a real ComponentIdentifier."""
        identifier_instance = MagicMock()
        identifier_instance.eval_hash = eval_hash
        return patch(self._IDENTIFIER_PATH, return_value=identifier_instance)

    def test_returns_empty_when_no_objective_target_identifier(self):
        """Pre-``initialize_async`` state: no identifier means the cache filter is a no-op."""
        bench = self._make_bench(with_target_identifier=False)
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with patch(self._ANALYTICS_PATH) as analytics_mock:
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == set()
        analytics_mock.assert_not_called()

    def test_returns_empty_when_no_atomic_attacks(self):
        """No candidates → no analytics calls and an empty result."""
        bench = self._make_bench()
        with self._patch_identifier(), patch(self._ANALYTICS_PATH) as analytics_mock:
            cached = bench._collect_cached_completion_pairs(atomic_attacks=[])

        assert cached == set()
        analytics_mock.assert_not_called()

    def test_returns_hash_when_success_match_exists(self):
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a")]

        with (
            self._patch_identifier(eval_hash="obj_hash"),
            patch(
                self._ANALYTICS_PATH,
                return_value=[
                    _make_attack_result_with_attribution(outcome=AttackOutcome.SUCCESS, parent_collection="attack_a")
                ],
            ),
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"attack_a"}

    def test_returns_hash_when_failure_match_exists(self):
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a")]

        with (
            self._patch_identifier(),
            patch(
                self._ANALYTICS_PATH,
                return_value=[
                    _make_attack_result_with_attribution(outcome=AttackOutcome.FAILURE, parent_collection="attack_a")
                ],
            ),
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"attack_a"}

    def test_excludes_hash_when_only_error_or_undetermined_matches(self):
        """ERROR / UNDETERMINED outcomes must NOT count as cached so transient failures retry."""
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a")]

        with (
            self._patch_identifier(),
            patch(
                self._ANALYTICS_PATH,
                return_value=[
                    _make_attack_result_with_attribution(outcome=AttackOutcome.ERROR, parent_collection="attack_a"),
                    _make_attack_result_with_attribution(
                        outcome=AttackOutcome.UNDETERMINED, parent_collection="attack_a"
                    ),
                ],
            ),
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == set()

    def test_excludes_hash_when_no_matches(self):
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with self._patch_identifier(), patch(self._ANALYTICS_PATH, return_value=[]):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == set()

    def test_dedupes_unique_technique_hashes_across_candidates(self):
        """Three candidates sharing two unique hashes → analytics called twice, not three times.

        Two candidates share hash_a (attack_a1 and attack_a2); one has hash_b (attack_b1).
        The analytics mock returns results attributed to each name, so all three attacks
        are independently cached. Key assertion: DB is called twice (deduplicated by hash).
        """
        bench = self._make_bench()
        candidates = [
            self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a1"),
            self._make_candidate(technique_eval_hash="hash_b", atomic_attack_name="attack_b1"),
            self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a2"),
        ]

        def _fake_analytics(_memory, *, technique_eval_hash, objective_target_eval_hash):
            if technique_eval_hash == "hash_a":
                return [
                    _make_attack_result_with_attribution(outcome=AttackOutcome.SUCCESS, parent_collection="attack_a1"),
                    _make_attack_result_with_attribution(outcome=AttackOutcome.SUCCESS, parent_collection="attack_a2"),
                ]
            return [_make_attack_result_with_attribution(outcome=AttackOutcome.SUCCESS, parent_collection="attack_b1")]

        with (
            self._patch_identifier(eval_hash="obj_hash"),
            patch(self._ANALYTICS_PATH, side_effect=_fake_analytics) as analytics_mock,
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"attack_a1", "attack_b1", "attack_a2"}
        assert analytics_mock.call_count == 2
        called_technique_hashes = {call.kwargs["technique_eval_hash"] for call in analytics_mock.call_args_list}
        assert called_technique_hashes == {"hash_a", "hash_b"}

    def test_delegates_with_memory_and_objective_target_hash(self):
        """Each analytics call passes the scenario's memory + the computed objective target hash."""
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            self._patch_identifier(eval_hash="my_obj_target_hash"),
            patch(self._ANALYTICS_PATH, return_value=[]) as analytics_mock,
        ):
            bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        analytics_mock.assert_called_once_with(
            bench._memory,
            technique_eval_hash="hash_a",
            objective_target_eval_hash="my_obj_target_hash",
        )

    def test_skips_candidates_with_no_technique_eval_hash(self):
        """A candidate whose ``technique_eval_hash`` is ``None`` is silently ignored."""
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash=None)]

        with self._patch_identifier(), patch(self._ANALYTICS_PATH) as analytics_mock:
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == set()
        analytics_mock.assert_not_called()

    def test_analytics_lookup_exception_is_not_silently_treated_as_cache_miss(self):
        bench = self._make_bench()
        candidates = [
            self._make_candidate(technique_eval_hash="hash_a", atomic_attack_name="attack_a"),
            self._make_candidate(technique_eval_hash="hash_b", atomic_attack_name="attack_b"),
        ]

        with (
            self._patch_identifier(),
            patch(self._ANALYTICS_PATH, side_effect=RuntimeError("analytics blew up")),
            pytest.raises(RuntimeError, match="analytics blew up"),
        ):
            bench._collect_cached_completion_pairs(atomic_attacks=candidates)

    def test_identifier_construction_failure_is_not_silently_treated_as_cache_miss(self):
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            patch(self._IDENTIFIER_PATH, side_effect=RuntimeError("bad identifier")),
            patch(self._ANALYTICS_PATH) as analytics_mock,
            pytest.raises(RuntimeError, match="bad identifier"),
        ):
            bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        analytics_mock.assert_not_called()


# ---------------------------------------------------------------------------
# skip_cached end-to-end through _build_atomic_attacks_async
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestSkipCachedFilter:
    """End-to-end tests for the ``skip_cached`` filter applied in ``_build_atomic_attacks_async``."""

    _ANALYTICS_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.get_cached_results_for_technique"
    _IDENTIFIER_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.ObjectiveTargetEvaluationIdentifier"

    def _make_bench(self, *, use_cached: bool) -> AdversarialBenchmark:
        _register_adversarial_target(name="adv_a")
        # Reset the technique registry to get a controllable mock factory
        AttackTechniqueRegistry.reset_registry_singleton()
        _build_benchmark_technique.cache_clear()
        _register_mock_factory(name="red_teaming", tags=["core", "light"])
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            use_cached=use_cached,
        )
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench._objective_target_identifier = MagicMock()
        bench.params = {"adversarial_targets": ["adv_a"]}

        red_teaming_technique = MagicMock()
        red_teaming_technique.value = "red_teaming"
        bench._scenario_techniques = [red_teaming_technique]

        seed_group = AttackSeedGroup(seeds=[SeedObjective(value="skip_cached_objective")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.max_dataset_size = None
        bench._dataset_config.get_attack_groups_by_dataset_async = AsyncMock(return_value={"harmbench": [seed_group]})

        return bench

    def _patch_identifier(self, eval_hash: str = "obj_hash"):
        identifier_instance = MagicMock()
        identifier_instance.eval_hash = eval_hash
        return patch(self._IDENTIFIER_PATH, return_value=identifier_instance)

    async def test_use_cached_sampling_is_stable_across_fresh_runs(self):
        bench = self._make_bench(use_cached=True)
        bench._dataset_config.max_dataset_size = 1
        group_a = AttackSeedGroup(seeds=[SeedObjective(value="objective a")])
        group_b = AttackSeedGroup(seeds=[SeedObjective(value="objective b")])

        with patch.object(
            Scenario,
            "_resolve_seed_groups_by_dataset_async",
            new=AsyncMock(side_effect=[{"dataset": [group_a, group_b]}, {"dataset": [group_b, group_a]}]),
        ) as resolve_groups:
            first = await bench._resolve_seed_groups_by_dataset_async()
            second = await bench._resolve_seed_groups_by_dataset_async()

        assert first == second
        assert sum(len(groups) for groups in first.values()) == 1
        assert [call.kwargs for call in resolve_groups.call_args_list] == [
            {"apply_sampling": False},
            {"apply_sampling": False},
        ]

    async def test_use_cached_false_preserves_default_sampling(self):
        bench = self._make_bench(use_cached=False)
        expected = {"dataset": [AttackSeedGroup(seeds=[SeedObjective(value="objective")])]}

        with patch.object(
            Scenario,
            "_resolve_seed_groups_by_dataset_async",
            new=AsyncMock(return_value=expected),
        ) as resolve_groups:
            actual = await bench._resolve_seed_groups_by_dataset_async()

        assert actual == expected
        resolve_groups.assert_awaited_once_with(apply_sampling=True)

    async def test_use_cached_false_returns_all_candidates_without_analytics_call(self):
        bench = self._make_bench(use_cached=False)

        with patch(self._ANALYTICS_PATH) as analytics_mock:
            result = await _build_atomic_attacks(bench)

        assert len(result) == 1
        analytics_mock.assert_not_called()

    async def test_runtime_use_cached_true_overrides_constructor_false(self):
        bench = self._make_bench(use_cached=False)
        bench.params["use_cached"] = True
        cached_attack = _make_attack_result_with_attribution(
            outcome=AttackOutcome.SUCCESS,
            parent_collection="red_teaming__adv_a_harmbench",
        )

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={"red_teaming__adv_a_harmbench": [cached_attack]},
        ) as collect_reusable:
            result = await _build_atomic_attacks(bench)

        collect_reusable.assert_called_once()
        assert result[0].seed_groups == []

    async def test_runtime_use_cached_false_overrides_constructor_true(self):
        bench = self._make_bench(use_cached=True)
        bench.params["use_cached"] = False

        with patch.object(bench, "_collect_reusable_cached_results") as collect_reusable:
            result = await _build_atomic_attacks(bench)

        collect_reusable.assert_not_called()
        assert len(result[0].seed_groups) == 1

    async def test_use_cached_true_filters_matching_candidates(self):
        bench = self._make_bench(use_cached=True)
        cached_attack = _make_attack_result_with_attribution(
            outcome=AttackOutcome.SUCCESS,
            parent_collection="red_teaming__adv_a_harmbench",
        )

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={"red_teaming__adv_a_harmbench": [cached_attack]},
        ):
            result = await _build_atomic_attacks(bench)

        assert len(result) == 1
        assert result[0].seed_groups == []

    async def test_use_cached_true_keeps_unmatched_candidates(self):
        bench = self._make_bench(use_cached=True)

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={},
        ):
            result = await _build_atomic_attacks(bench)

        assert len(result) == 1

    async def test_use_cached_true_stages_results_for_persistence(self):
        """Full pipeline: a cache hit stages its prior result for persistence."""
        bench = self._make_bench(use_cached=True)
        cached_attack = _make_attack_result_with_attribution(
            outcome=AttackOutcome.SUCCESS,
            parent_collection="red_teaming__adv_a_harmbench",
        )

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={"red_teaming__adv_a_harmbench": [cached_attack]},
        ):
            result = await _build_atomic_attacks(bench)

        assert len(result) == 1
        assert result[0].seed_groups == []
        assert bench._precomputed_cached_results == {"red_teaming__adv_a_harmbench": [cached_attack]}

    async def test_use_cached_true_stages_only_exact_reusable_results(self):
        """The exact-match selector controls which prior rows are staged."""
        bench = self._make_bench(use_cached=True)
        matching = _make_attack_result_with_attribution(
            outcome=AttackOutcome.SUCCESS,
            parent_collection="red_teaming__adv_a_harmbench",
        )

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={"red_teaming__adv_a_harmbench": [matching]},
        ):
            await _build_atomic_attacks(bench)

        assert bench._precomputed_cached_results == {"red_teaming__adv_a_harmbench": [matching]}

    async def test_cache_pruning_preserves_complete_sampled_objective_metadata(self):
        bench = self._make_bench(use_cached=True)
        bench._dataset_config.max_dataset_size = 1
        cached_attack = _make_attack_result_with_attribution(
            outcome=AttackOutcome.SUCCESS,
            parent_collection="red_teaming__adv_a_harmbench",
        )

        with patch.object(
            bench,
            "_collect_reusable_cached_results",
            return_value={"red_teaming__adv_a_harmbench": [cached_attack]},
        ):
            await _build_atomic_attacks(bench)

        assert bench._build_initial_scenario_metadata()["objective_hashes"] == [to_sha256("skip_cached_objective")]

    async def test_resume_uses_scenario_results_instead_of_cross_run_cache(self):
        bench = self._make_bench(use_cached=True)
        bench._scenario_result_id = str(uuid.uuid4())

        with patch.object(bench, "_collect_reusable_cached_results") as collect_reusable:
            atomic_attacks = await _build_atomic_attacks(bench)

        collect_reusable.assert_not_called()
        assert len(atomic_attacks[0].seed_groups) == 1


# ---------------------------------------------------------------------------
# Real-memory coverage for _collect_cached_completion_pairs
# ---------------------------------------------------------------------------
#
# The mocked TestCollectCachedCompletionPairs class above exercises the
# scenario-layer wiring (delegation, dedup, outcome filter, identifier
# construction). The tests in this section exercise the *full* path through
# real SQLite memory: AttackResult persistence (which auto-stamps
# ``atomic_attack_identifier.eval_hash``), the
# ``get_cached_results_for_technique`` SQL filter on ``$.eval_hash``, and
# the python-side ``ObjectiveTargetEvaluationIdentifier`` filter inside
# the analytics helper. They catch wiring regressions (e.g. a future
# refactor that stops stamping ``eval_hash`` at write time) that the
# mocked tests cannot.


def _make_objective_target_component(
    *,
    model_name: str = "gpt-4o",
    temperature: float = 0.7,
    top_p: float = 1.0,
) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={
            "underlying_model_name": model_name,
            "temperature": temperature,
            "top_p": top_p,
        },
    )


def _make_atomic_attack_identifier(target: ComponentIdentifier) -> ComponentIdentifier:
    """Build the nested identifier tree the persistence layer expects."""
    technique = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack.single_turn.prompt_sending",
        children={"objective_target": target},
    )
    return ComponentIdentifier(
        class_name="AtomicAttack",
        class_module="pyrit.scenario.core.atomic_attack",
        children={"attack_technique": technique},
    )


def _technique_eval_hash_for(target: ComponentIdentifier) -> str:
    atomic = _make_atomic_attack_identifier(target)
    return AtomicAttackEvaluationIdentifier(atomic).eval_hash


def _persist_attack_result(
    memory: MemoryInterface,
    target: ComponentIdentifier,
    *,
    outcome: AttackOutcome,
    objective: str = "probe target",
    atomic_attack_name: str | None = None,
) -> AttackResult:
    """Persist a real AttackResult with a well-formed identifier tree.

    When ``atomic_attack_name`` is provided, ``attribution_data`` is stamped
    with ``{"parent_collection": atomic_attack_name}`` so dataset-level cache
    scoping tests can verify the attribution filter in
    ``_collect_cached_completion_pairs``.
    """
    attack_result = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        atomic_attack_identifier=_make_atomic_attack_identifier(target),
        outcome=outcome,
        timestamp=datetime.now(UTC),
        attribution_data={"parent_collection": atomic_attack_name} if atomic_attack_name else None,
    )
    memory.add_attack_results_to_memory(attack_results=[attack_result])
    return attack_result


def _make_bench_with_real_memory(
    memory: MemoryInterface,
    objective_target: ComponentIdentifier,
) -> AdversarialBenchmark:
    """Build a minimal benchmark wired to a real memory backend.

    Uses ``__new__`` to bypass the full ``__init__`` so we don't have to
    register a target or build a technique enum just to exercise the cache
    helper. The helper only reads ``_memory`` and
    ``_objective_target_identifier``.
    """
    bench = AdversarialBenchmark.__new__(AdversarialBenchmark)
    bench._memory = memory
    bench._objective_target_identifier = objective_target
    return bench


def _make_candidate(*, technique_eval_hash: str, atomic_attack_name: str = "attack_a") -> MagicMock:
    candidate = MagicMock()
    candidate.technique_eval_hash = technique_eval_hash
    candidate.atomic_attack_name = atomic_attack_name
    return candidate


@pytest.mark.usefixtures("patch_central_database")
class TestCollectCachedCompletionPairsWithRealMemory:
    """End-to-end cache coverage through real ``SQLiteMemory``."""

    def test_cold_cache_returns_empty(self, sqlite_instance):
        target = _make_objective_target_component()
        bench = _make_bench_with_real_memory(sqlite_instance, target)
        candidate = _make_candidate(technique_eval_hash=_technique_eval_hash_for(target))

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == set()

    def test_returns_hash_for_success_match_in_real_db(self, sqlite_instance):
        target = _make_objective_target_component()
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.SUCCESS, atomic_attack_name="attack_a")

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        tech_hash = _technique_eval_hash_for(target)
        candidate = _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name="attack_a")

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == {"attack_a"}

    def test_returns_hash_for_failure_match_in_real_db(self, sqlite_instance):
        target = _make_objective_target_component()
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.FAILURE, atomic_attack_name="attack_a")

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        tech_hash = _technique_eval_hash_for(target)
        candidate = _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name="attack_a")

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == {"attack_a"}

    def test_filters_out_persisted_results_with_different_objective_target(self, sqlite_instance):
        """A row with a matching technique hash but a different target hash is rejected."""
        persisted_target = _make_objective_target_component(model_name="gpt-4o", temperature=0.7)
        bench_target = _make_objective_target_component(model_name="gpt-4o-mini", temperature=0.7)
        # AtomicAttackEvaluationIdentifier strips non-temperature target params, so the
        # two targets share a technique hash even though their objective-target eval
        # hashes differ. The SQL filter on $.eval_hash will hit; the python-side target
        # filter inside get_cached_results_for_technique must do the rejection.
        assert _technique_eval_hash_for(persisted_target) == _technique_eval_hash_for(bench_target)
        assert (
            ObjectiveTargetEvaluationIdentifier(persisted_target).eval_hash
            != ObjectiveTargetEvaluationIdentifier(bench_target).eval_hash
        )

        _persist_attack_result(sqlite_instance, persisted_target, outcome=AttackOutcome.SUCCESS)

        bench = _make_bench_with_real_memory(sqlite_instance, bench_target)
        candidate = _make_candidate(technique_eval_hash=_technique_eval_hash_for(bench_target))

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == set()

    def test_filters_out_persisted_results_with_different_technique_hash(self, sqlite_instance):
        """A row whose technique eval hash differs is rejected by the SQL filter."""
        persisted_target = _make_objective_target_component(model_name="gpt-4o", temperature=0.0)
        bench_target = _make_objective_target_component(model_name="gpt-4o", temperature=0.7)
        # Temperature feeds into AtomicAttackEvaluationIdentifier, so the persisted
        # row's stamped $.eval_hash is different from the candidate's technique hash
        # and the SQL filter returns no rows.
        assert _technique_eval_hash_for(persisted_target) != _technique_eval_hash_for(bench_target)

        _persist_attack_result(sqlite_instance, persisted_target, outcome=AttackOutcome.SUCCESS)

        bench = _make_bench_with_real_memory(sqlite_instance, bench_target)
        candidate = _make_candidate(technique_eval_hash=_technique_eval_hash_for(bench_target))

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == set()

    def test_filters_out_error_only_history(self, sqlite_instance):
        """Outcomes other than SUCCESS / FAILURE never count as cached."""
        target = _make_objective_target_component()
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.ERROR)
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.UNDETERMINED)

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        candidate = _make_candidate(technique_eval_hash=_technique_eval_hash_for(target))

        result = bench._collect_cached_completion_pairs(atomic_attacks=[candidate])

        assert result == set()

    def test_dedupes_candidates_with_same_technique_hash(self, sqlite_instance):
        """Two candidates sharing a technique hash are evaluated independently by name."""
        target = _make_objective_target_component()
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.SUCCESS, atomic_attack_name="attack_a")
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.SUCCESS, atomic_attack_name="attack_b")

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        tech_hash = _technique_eval_hash_for(target)
        candidates = [
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name="attack_a"),
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name="attack_b"),
        ]

        result = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert result == {"attack_a", "attack_b"}

    def test_same_technique_hash_only_harmbench_cached_when_only_harmbench_persisted(self, sqlite_instance):
        """Dataset-level scoping: same technique+target hash, only harmbench records in DB.

        Both harmbench and advbench candidates share a technique_eval_hash (same technique,
        same model target). Only harmbench results were persisted. The advbench slot must
        NOT be marked as cached — it should be re-run on the next execution.
        """
        target = _make_objective_target_component()
        harmbench_name = "red_teaming__adv_a_harmbench"
        advbench_name = "red_teaming__adv_a_advbench"

        _persist_attack_result(
            sqlite_instance, target, outcome=AttackOutcome.SUCCESS, atomic_attack_name=harmbench_name
        )

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        tech_hash = _technique_eval_hash_for(target)
        candidates = [
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name=harmbench_name),
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name=advbench_name),
        ]

        result = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert result == {harmbench_name}
        assert advbench_name not in result

    def test_same_technique_hash_both_datasets_cached_when_both_persisted(self, sqlite_instance):
        """Dataset-level scoping: same technique+target, both datasets have prior results → both skipped."""
        target = _make_objective_target_component()
        harmbench_name = "red_teaming__adv_a_harmbench"
        advbench_name = "red_teaming__adv_a_advbench"

        _persist_attack_result(
            sqlite_instance, target, outcome=AttackOutcome.SUCCESS, atomic_attack_name=harmbench_name
        )
        _persist_attack_result(sqlite_instance, target, outcome=AttackOutcome.FAILURE, atomic_attack_name=advbench_name)

        bench = _make_bench_with_real_memory(sqlite_instance, target)
        tech_hash = _technique_eval_hash_for(target)
        candidates = [
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name=harmbench_name),
            _make_candidate(technique_eval_hash=tech_hash, atomic_attack_name=advbench_name),
        ]

        result = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert result == {harmbench_name, advbench_name}


@pytest.mark.usefixtures("patch_central_database")
class TestRunAsyncCachePersistence:
    """Tests that cache persistence happens before normal scenario execution."""

    async def test_precomputed_results_are_persisted_before_base_run(self):
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            use_cached=True,
        )

        result_x = MagicMock(spec=AttackResult)
        result_y = MagicMock(spec=AttackResult)
        result_z = MagicMock(spec=AttackResult)

        # Simulate what _build_atomic_attacks_async populated for two cached attacks.
        bench._precomputed_cached_results = {
            "technique_a__adv_target_harmbench": [result_x],
            "technique_b__adv_target_harmbench": [result_y],
        }

        base_scenario_result = MagicMock()
        base_scenario_result.attack_results = {"technique_c__adv_target_harmbench": [result_z]}
        base_scenario_result.display_group_map = {}

        with (
            patch.object(bench, "_persist_precomputed_cached_results") as persist_cached,
            patch.object(Scenario, "run_async", new=AsyncMock(return_value=base_scenario_result)),
        ):
            result = await bench.run_async()

        persist_cached.assert_called_once_with()
        assert result is base_scenario_result

    async def test_base_result_is_returned_after_cache_persistence(self):
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            use_cached=True,
        )

        bench._precomputed_cached_results = {"technique_a__adv_target_harmbench": [MagicMock(spec=AttackResult)]}

        base_scenario_result = MagicMock()
        base_scenario_result.attack_results = {}
        base_scenario_result.display_group_map = {}

        with (
            patch.object(bench, "_persist_precomputed_cached_results"),
            patch.object(Scenario, "run_async", new=AsyncMock(return_value=base_scenario_result)),
        ):
            result = await bench.run_async()

        assert result is base_scenario_result

    async def test_no_injection_when_no_cached_attacks(self):
        """When all attacks were executed freshly, attack_results is returned unchanged."""
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            use_cached=False,
        )

        result_z = MagicMock(spec=AttackResult)
        base_scenario_result = MagicMock()
        base_scenario_result.attack_results = {"technique_c__adv_target_harmbench": [result_z]}
        base_scenario_result.display_group_map = {}

        with patch.object(Scenario, "run_async", new=AsyncMock(return_value=base_scenario_result)):
            result = await bench.run_async()

        assert set(result.attack_results.keys()) == {"technique_c__adv_target_harmbench"}


def _make_scorer_identifier(*, question: str) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name="SelfAskTrueFalseScorer",
        class_module="pyrit.score.true_false.self_ask_true_false_scorer",
        params={"question": question},
    )


def _make_cache_candidate(
    *,
    scorer_identifier: ComponentIdentifier,
    objectives: list[str],
    atomic_attack_name: str = "attack_a",
) -> MagicMock:
    strategy_identifier = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack.single_turn.prompt_sending",
        children={"objective_scorer": scorer_identifier},
    )
    technique_identifier = ComponentIdentifier(
        class_name="AttackTechnique",
        class_module="pyrit.scenario.core.attack_technique",
        children={"attack": strategy_identifier},
    )
    candidate = MagicMock()
    candidate.atomic_attack_name = atomic_attack_name
    candidate.technique_eval_hash = "technique-hash"
    candidate.objectives = objectives
    candidate.seed_groups = [object() for _ in objectives]
    candidate.attack_technique.get_identifier.return_value = technique_identifier
    return candidate


def _make_exact_cached_result(
    *,
    objective: str,
    scorer_identifier: ComponentIdentifier,
    parent_id: str,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    attack_result_id: str | None = None,
) -> AttackResult:
    score = Score(
        score_value="true",
        score_value_description="",
        score_type="true_false",
        score_category=None,
        score_metadata={},
        score_rationale="",
        scorer_class_identifier=scorer_identifier,
        message_piece_id=uuid.uuid4(),
        objective=objective,
    )
    return AttackResult(
        attack_result_id=attack_result_id or str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        automated_score=score,
        outcome=outcome,
        attribution_parent_id=parent_id,
        attribution_data={"parent_collection": "attack_a", "parent_eval_hash": "technique-hash"},
        labels={"source": "prior"},
    )


def _make_compatible_parent(*, parent_id: str, version: int = AdversarialBenchmark.VERSION) -> MagicMock:
    parent = MagicMock()
    parent.id = uuid.UUID(parent_id)
    parent.scenario_name = "AdversarialBenchmark"
    parent.scenario_version = version
    parent.scenario_identifier.class_module = "pyrit.scenario.scenarios.benchmark.adversarial"
    return parent


@pytest.mark.usefixtures("patch_central_database")
class TestReusableCachedResults:
    def _make_bench_with_candidates(
        self,
        *,
        candidate: MagicMock,
        cached_results: list[AttackResult],
        parent_version: int = AdversarialBenchmark.VERSION,
        parent_module: str = "pyrit.scenario.scenarios.benchmark.adversarial",
    ) -> AdversarialBenchmark:
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._memory = MagicMock(spec=MemoryInterface)
        parent_id = cached_results[0].attribution_parent_id if cached_results else str(uuid.uuid4())
        parent = _make_compatible_parent(parent_id=parent_id, version=parent_version)
        parent.scenario_identifier.class_module = parent_module
        bench._memory.get_scenario_results.return_value = [parent]

        def collect_candidates(*, atomic_attacks):
            assert atomic_attacks == [candidate]
            bench._cached_results_by_name = {candidate.atomic_attack_name: cached_results}
            return {candidate.atomic_attack_name}

        bench._collect_cached_completion_pairs = MagicMock(side_effect=collect_candidates)
        return bench

    def test_reuses_only_matching_objective_from_partial_slot(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["objective one", "objective two"])
        cached = _make_exact_cached_result(
            objective="objective one",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        bench = self._make_bench_with_candidates(candidate=candidate, cached_results=[cached])

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {"attack_a": [cached]}

    def test_does_not_reuse_objective_absent_from_current_dataset(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["current objective"])
        cached = _make_exact_cached_result(
            objective="removed objective",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        bench = self._make_bench_with_candidates(candidate=candidate, cached_results=[cached])

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {}

    def test_does_not_reuse_result_from_different_scorer(self):
        current_scorer = _make_scorer_identifier(question="current rubric")
        prior_scorer = _make_scorer_identifier(question="old rubric")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=current_scorer, objectives=["objective"])
        cached = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=prior_scorer,
            parent_id=parent_id,
        )
        bench = self._make_bench_with_candidates(candidate=candidate, cached_results=[cached])

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {}

    def test_does_not_reuse_result_from_different_benchmark_version(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["objective"])
        cached = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        bench = self._make_bench_with_candidates(
            candidate=candidate,
            cached_results=[cached],
            parent_version=AdversarialBenchmark.VERSION - 1,
        )

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {}

    def test_does_not_reuse_result_from_different_scenario_module(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["objective"])
        cached = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        bench = self._make_bench_with_candidates(
            candidate=candidate,
            cached_results=[cached],
            parent_module="pyrit.scenario.scenarios.other",
        )

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {}

    def test_does_not_reuse_error_or_undetermined_result(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["objective"])
        cached_results = [
            _make_exact_cached_result(
                objective="objective",
                scorer_identifier=scorer,
                parent_id=parent_id,
                outcome=outcome,
            )
            for outcome in (AttackOutcome.ERROR, AttackOutcome.UNDETERMINED)
        ]
        bench = self._make_bench_with_candidates(candidate=candidate, cached_results=cached_results)

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {}

    def test_selects_newest_matching_result_per_objective(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["objective"])
        newest = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        older = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=scorer,
            parent_id=parent_id,
            outcome=AttackOutcome.FAILURE,
        )
        bench = self._make_bench_with_candidates(candidate=candidate, cached_results=[newest, older])

        reusable = bench._collect_reusable_cached_results(atomic_attacks=[candidate])

        assert reusable == {"attack_a": [newest]}

    def test_apply_cache_drops_only_reused_objective(self):
        scorer = _make_scorer_identifier(question="achieved")
        parent_id = str(uuid.uuid4())
        candidate = _make_cache_candidate(scorer_identifier=scorer, objectives=["cached", "uncached"])
        cached = _make_exact_cached_result(
            objective="cached",
            scorer_identifier=scorer,
            parent_id=parent_id,
        )
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._collect_reusable_cached_results = MagicMock(return_value={"attack_a": [cached]})

        bench._apply_reusable_cached_results(atomic_attacks=[candidate])

        candidate.drop_seed_groups_with_hashes.assert_called_once_with(hashes={to_sha256("cached")})
        assert bench._precomputed_cached_results == {"attack_a": [cached]}

    def test_persisted_cache_copy_is_attributed_to_current_run(self):
        scorer = _make_scorer_identifier(question="achieved")
        source_parent_id = str(uuid.uuid4())
        current_parent_id = str(uuid.uuid4())
        source_result_id = str(uuid.uuid4())
        cached = _make_exact_cached_result(
            objective="objective",
            scorer_identifier=scorer,
            parent_id=source_parent_id,
            attack_result_id=source_result_id,
        )
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._memory = MagicMock(spec=MemoryInterface)
        bench._scenario_result_id = current_parent_id
        bench._memory_labels = {"pipeline_build_id": "42"}
        bench._precomputed_cached_results = {"attack_a": [cached]}

        bench._persist_precomputed_cached_results()

        persisted = bench._memory.add_attack_results_to_memory.call_args.kwargs["attack_results"]
        assert len(persisted) == 1
        cached_copy = persisted[0]
        assert cached_copy.attack_result_id != source_result_id
        assert cached_copy.conversation_id == cached.conversation_id
        assert cached_copy.attribution_parent_id == current_parent_id
        assert cached_copy.attribution_data == {
            "parent_collection": "attack_a",
            "parent_eval_hash": "technique-hash",
        }
        assert cached_copy.labels == {"source": "prior", "pipeline_build_id": "42"}
        assert cached_copy.metadata["cached_from_attack_result_id"] == source_result_id
        assert cached.attribution_parent_id == source_parent_id
        assert bench._precomputed_cached_results == {}

    async def test_all_cached_scenario_completes_from_persisted_copy(self, sqlite_instance):
        scenario_identifier = ScenarioIdentifier(
            class_name="AdversarialBenchmark",
            class_module="pyrit.scenario.scenarios.benchmark.adversarial",
            version=AdversarialBenchmark.VERSION,
            objective_target=TargetIdentifier(
                class_name="MockObjectiveTarget",
                class_module="tests.unit.scenario.benchmark.test_adversarial",
            ),
        )
        source_scenario = ScenarioResult(scenario_identifier=scenario_identifier, attack_results={})
        current_scenario = ScenarioResult(scenario_identifier=scenario_identifier, attack_results={})
        sqlite_instance.add_scenario_results_to_memory(scenario_results=[source_scenario, current_scenario])
        source_result = AttackResult(
            conversation_id=str(uuid.uuid4()),
            objective="objective",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=str(source_scenario.id),
            attribution_data={"parent_collection": "attack_a", "parent_eval_hash": "technique-hash"},
            labels={"source": "prior"},
        )
        sqlite_instance.add_attack_results_to_memory(attack_results=[source_result])

        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._memory = sqlite_instance
        bench._scenario_result_id = str(current_scenario.id)
        bench._memory_labels = {"pipeline_build_id": "42"}
        bench._precomputed_cached_results = {"attack_a": [source_result]}
        candidate = MagicMock(spec=AtomicAttack)
        candidate.atomic_attack_name = "attack_a"
        candidate.technique_eval_hash = "technique-hash"
        candidate.seed_groups = [AttackSeedGroup(seeds=[SeedObjective(value="objective")])]
        candidate.drop_seed_groups_with_hashes.side_effect = lambda *, hashes: setattr(candidate, "seed_groups", [])
        bench._atomic_attacks = [candidate]

        result = await bench.run_async()

        source_reloaded = sqlite_instance.get_scenario_results(scenario_result_ids=[str(source_scenario.id)])[0]
        current_reloaded = sqlite_instance.get_scenario_results(scenario_result_ids=[str(current_scenario.id)])[0]
        assert source_reloaded.attack_results["attack_a"][0].attack_result_id == source_result.attack_result_id
        cached_copy = current_reloaded.attack_results["attack_a"][0]
        assert cached_copy.attack_result_id != source_result.attack_result_id
        assert cached_copy.conversation_id == source_result.conversation_id
        assert cached_copy.labels == {"source": "prior", "pipeline_build_id": "42"}
        assert result.scenario_run_state is ScenarioRunState.COMPLETED
        candidate.run_async.assert_not_called()
