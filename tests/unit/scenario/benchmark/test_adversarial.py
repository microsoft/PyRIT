# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the post-collapse AdversarialBenchmark scenario.

AdversarialBenchmark no longer takes an ``adversarial_models`` constructor
parameter and no longer builds local factories. It reads fanned variants
from ``AttackTechniqueRegistry`` (registered by ``BenchmarkInitializer``)
and inherits the base ``Scenario._get_atomic_attacks_async`` loop, with
an opt-in caching wrapper for cross-run skip-on-completion.

These tests cover the new contract:
* Class metadata (VERSION, BASELINE policy, defaults).
* Strategy enum is built from ``benchmark_fanout``-tagged registry entries.
* Display grouping uses the target-label portion of fanned technique names.
* Construction accepts ``objective_scorer``, ``skip_cached``, and
  ``scenario_result_id``.
* ``skip_cached`` filters prior SUCCESS/FAILURE completions, keeps
  ERROR/UNDETERMINED, respects eval-hash disambiguation, and only counts
  COMPLETED scenario runs of the matching name + version.
"""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import AttackOutcome
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.scenarios.benchmark.adversarial import (
    BENCHMARK_FANOUT_TAG,
    AdversarialBenchmark,
    _build_benchmark_strategy,
)
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers import BenchmarkInitializer
from pyrit.setup.initializers.components.targets import TargetInitializerTags

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registries_and_cache():
    """Reset both registries and AdversarialBenchmark's strategy-class cache between tests."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    AdversarialBenchmark._cached_strategy_class = None
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    AdversarialBenchmark._cached_strategy_class = None


def _register_adversarial_target(*, name: str) -> PromptTarget:
    """Register a mock adversarial-tagged target in TargetRegistry."""
    target = MagicMock(spec=PromptTarget)
    target.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(target, name=name, tags=[TargetInitializerTags.ADVERSARIAL.value])
    return target


async def _fan_out(*, target_names: list[str]) -> None:
    """Register mock targets + run BenchmarkInitializer to populate AttackTechniqueRegistry."""
    for name in target_names:
        _register_adversarial_target(name=name)
    init = BenchmarkInitializer()
    await init.initialize_async()


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkMetadata:
    """Tests for class-level metadata that doesn't depend on fan-out state."""

    def test_version_is_2(self):
        """VERSION is bumped from 1 because the atomic_attack_name format changed."""
        assert AdversarialBenchmark.VERSION == 2

    def test_baseline_attack_policy_is_forbidden(self):
        """A baseline contributes no signal to a model-comparison benchmark, so it is forbidden."""
        assert AdversarialBenchmark.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden

    def test_default_dataset_config_uses_harmbench(self):
        config = AdversarialBenchmark.default_dataset_config()
        assert config.get_default_dataset_names() == ["harmbench"]

    def test_default_dataset_config_max_size_is_8(self):
        assert AdversarialBenchmark.default_dataset_config().max_dataset_size == 8

    def test_benchmark_fanout_tag_value(self):
        """The shared tag value must match what BenchmarkInitializer applies."""
        assert BENCHMARK_FANOUT_TAG == "benchmark_fanout"


# ---------------------------------------------------------------------------
# Strategy class construction
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkStrategy:
    """Tests for _build_benchmark_strategy and the cached get_strategy_class accessor."""

    async def test_strategy_built_from_fanned_registry_entries(self):
        """Every benchmark_fanout-tagged entry produces one concrete enum member."""
        await _fan_out(target_names=["adv_a", "adv_b"])

        strategy_cls = AdversarialBenchmark.get_strategy_class()
        aggregate_names = {"all"} | strategy_cls.get_aggregate_tags()
        concrete_members = [m for m in strategy_cls if m.value not in aggregate_names]

        assert len(concrete_members) > 0
        for member in concrete_members:
            assert "__" in member.value, f"Expected fanned format with '__', got: {member.value}"

    async def test_strategy_concrete_member_count_matches_registry(self):
        """Concrete enum members count equals fanned spec count in the registry."""
        await _fan_out(target_names=["adv_a", "adv_b"])

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        fanned_entries = attack_registry.get_by_tag(tag=BENCHMARK_FANOUT_TAG)

        strategy_cls = AdversarialBenchmark.get_strategy_class()
        aggregate_names = {"all"} | strategy_cls.get_aggregate_tags()
        concrete_members = [m for m in strategy_cls if m.value not in aggregate_names]

        assert len(concrete_members) == len(fanned_entries)

    async def test_strategy_exposes_per_model_selection(self):
        """Each fanned variant inherits its model:* tag, accessible by name on the enum."""
        await _fan_out(target_names=["adv_a"])

        attack_registry = AttackTechniqueRegistry.get_registry_singleton()
        model_a_entries = attack_registry.get_by_tag(tag="model:adv_a")
        assert len(model_a_entries) > 0

        strategy_cls = AdversarialBenchmark.get_strategy_class()
        for entry in model_a_entries:
            member = strategy_cls(entry.name)
            assert "model:adv_a" in member.tags

    async def test_strategy_includes_required_aggregates(self):
        """The strategy enum exposes all, light, single_turn, multi_turn aggregates."""
        await _fan_out(target_names=["adv_a"])

        strategy_cls = AdversarialBenchmark.get_strategy_class()
        aggregates = strategy_cls.get_aggregate_tags()

        assert "all" in aggregates
        assert "light" in aggregates
        assert "single_turn" in aggregates
        assert "multi_turn" in aggregates

    async def test_get_strategy_class_is_cached(self):
        """Repeated calls within a process return the same class instance."""
        await _fan_out(target_names=["adv_a"])

        first = AdversarialBenchmark.get_strategy_class()
        second = AdversarialBenchmark.get_strategy_class()

        assert first is second

    async def test_cache_can_be_cleared_to_rebuild(self):
        """Setting _cached_strategy_class = None forces a rebuild from current registry state."""
        await _fan_out(target_names=["adv_a"])
        first = AdversarialBenchmark.get_strategy_class()

        await _fan_out(target_names=["adv_b"])
        AdversarialBenchmark._cached_strategy_class = None
        second = AdversarialBenchmark.get_strategy_class()

        assert first is not second

    async def test_default_strategy_is_light(self):
        """get_default_strategy returns the 'light' aggregate so quick benchmark runs are the default."""
        await _fan_out(target_names=["adv_a"])

        default = AdversarialBenchmark.get_default_strategy()
        assert default.value == "light"

    def test_build_benchmark_strategy_empty_registry_produces_aggregates_only(self):
        """No fan-out → enum still constructs (aggregates always present), just with zero concrete members."""
        strategy_cls = _build_benchmark_strategy()
        aggregate_names = {"all"} | strategy_cls.get_aggregate_tags()
        concrete_members = [m for m in strategy_cls if m.value not in aggregate_names]
        assert concrete_members == []


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkInit:
    """Tests for the collapsed __init__ surface (objective_scorer + scenario_result_id only)."""

    @pytest.mark.usefixtures("patch_central_database")
    async def test_construct_with_default_objective_scorer(self):
        """When no scorer is supplied, _get_default_objective_scorer is consulted."""
        await _fan_out(target_names=["adv_a"])

        default_scorer = MagicMock(spec=TrueFalseScorer)
        with patch.object(AdversarialBenchmark, "_get_default_objective_scorer", return_value=default_scorer):
            bench = AdversarialBenchmark()

        assert bench._objective_scorer is default_scorer

    @pytest.mark.usefixtures("patch_central_database")
    async def test_construct_with_explicit_objective_scorer(self):
        """An explicit scorer is used as-is, no default consulted."""
        await _fan_out(target_names=["adv_a"])

        explicit_scorer = MagicMock(spec=TrueFalseScorer)
        bench = AdversarialBenchmark(objective_scorer=explicit_scorer)

        assert bench._objective_scorer is explicit_scorer

    async def test_construct_takes_no_adversarial_models_param(self):
        """Regression: the old adversarial_models constructor param is removed."""
        await _fan_out(target_names=["adv_a"])

        with pytest.raises(TypeError):
            AdversarialBenchmark(adversarial_models=[MagicMock(spec=PromptTarget)])  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Display grouping
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialBenchmarkDisplayGroup:
    """Tests for _build_display_group's fanned-name parsing."""

    async def _make_bench(self) -> AdversarialBenchmark:
        await _fan_out(target_names=["adv_a"])
        return AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))

    async def test_extracts_target_label_after_double_underscore(self):
        bench = await self._make_bench()
        result = bench._build_display_group(
            technique_name="red_teaming__adversarial_chat_singleturn",
            seed_group_name="seed_group_1",
        )
        assert result == "adversarial_chat_singleturn"

    async def test_falls_back_to_full_name_when_no_separator(self):
        """Non-fanned names (no ``__``) return the full technique name unchanged."""
        bench = await self._make_bench()
        result = bench._build_display_group(
            technique_name="prompt_sending",
            seed_group_name="seed_group_1",
        )
        assert result == "prompt_sending"

    async def test_ignores_seed_group_name(self):
        """seed_group_name input must not influence the result (display rolls up per-target)."""
        bench = await self._make_bench()
        first = bench._build_display_group(
            technique_name="red_teaming__adv_a",
            seed_group_name="seed_group_a",
        )
        second = bench._build_display_group(
            technique_name="red_teaming__adv_a",
            seed_group_name="seed_group_b",
        )
        assert first == second == "adv_a"


# ---------------------------------------------------------------------------
# skip_cached behavior (Commit 6 / F3)
# ---------------------------------------------------------------------------


def _make_scenario_result(*, result_id: str, run_state: str = "COMPLETED") -> MagicMock:
    """Build a minimal ScenarioResult stand-in for cache-key tests."""
    sr = MagicMock()
    sr.id = result_id
    sr.scenario_run_state = run_state
    return sr


def _make_attack_result(
    *,
    outcome: AttackOutcome,
    parent_collection: str | None,
    parent_eval_hash: str | None,
) -> MagicMock:
    """Build a minimal AttackResult stand-in with the attribution_data shape Commit 6 reads."""
    ar = MagicMock()
    ar.outcome = outcome
    if parent_collection is None and parent_eval_hash is None:
        ar.attribution_data = None
    else:
        data: dict[str, str] = {}
        if parent_collection is not None:
            data["parent_collection"] = parent_collection
        if parent_eval_hash is not None:
            data["parent_eval_hash"] = parent_eval_hash
        ar.attribution_data = data
    return ar


def _make_candidate(*, name: str, eval_hash: str) -> MagicMock:
    """Build a minimal AtomicAttack stand-in with the two fields the cache filter reads."""
    candidate = MagicMock()
    candidate.atomic_attack_name = name
    candidate.technique_eval_hash = eval_hash
    return candidate


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialBenchmarkSkipCachedFilter:
    """Tests for the _get_atomic_attacks_async caching wrapper."""

    async def _make_bench(self, *, skip_cached: bool) -> AdversarialBenchmark:
        await _fan_out(target_names=["adv_a"])
        return AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            skip_cached=skip_cached,
        )

    async def test_skip_cached_default_false_means_no_filtering(self):
        """skip_cached defaults to False; super() output is returned unchanged."""
        bench = await self._make_bench(skip_cached=False)
        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]

        with patch.object(
            AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates
        ) as super_mock:
            result = await bench._get_atomic_attacks_async()

        assert result == candidates
        super_mock.assert_awaited_once()

    async def test_skip_cached_true_drops_completed_pairs(self):
        """SUCCESS and FAILURE prior outcomes drop the matching candidate."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [
            _make_candidate(name="red_teaming__adv_a", eval_hash="hash_a"),
            _make_candidate(name="tap__adv_a", eval_hash="hash_b"),
            _make_candidate(name="crescendo_simulated__adv_a", eval_hash="hash_c"),
        ]
        prior_sr = _make_scenario_result(result_id="sid-1")
        prior_attacks = [
            _make_attack_result(
                outcome=AttackOutcome.SUCCESS,
                parent_collection="red_teaming__adv_a",
                parent_eval_hash="hash_a",
            ),
            _make_attack_result(
                outcome=AttackOutcome.FAILURE,
                parent_collection="tap__adv_a",
                parent_eval_hash="hash_b",
            ),
        ]

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [prior_sr]
        bench._memory.get_attack_results.return_value = prior_attacks

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        names = [c.atomic_attack_name for c in result]
        assert names == ["crescendo_simulated__adv_a"]

    async def test_skip_cached_keeps_error_outcomes(self):
        """ERROR outcomes must retry — not be cached."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]
        prior_sr = _make_scenario_result(result_id="sid-1")
        prior_attacks = [
            _make_attack_result(
                outcome=AttackOutcome.ERROR,
                parent_collection="red_teaming__adv_a",
                parent_eval_hash="hash_a",
            ),
        ]

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [prior_sr]
        bench._memory.get_attack_results.return_value = prior_attacks

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates

    async def test_skip_cached_keeps_undetermined_outcomes(self):
        """UNDETERMINED outcomes must retry — not be cached."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]
        prior_sr = _make_scenario_result(result_id="sid-1")
        prior_attacks = [
            _make_attack_result(
                outcome=AttackOutcome.UNDETERMINED,
                parent_collection="red_teaming__adv_a",
                parent_eval_hash="hash_a",
            ),
        ]

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [prior_sr]
        bench._memory.get_attack_results.return_value = prior_attacks

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates

    async def test_skip_cached_respects_eval_hash_disambiguation(self):
        """Same atomic_attack_name but different parent_eval_hash → not considered cached."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="new_hash")]
        prior_sr = _make_scenario_result(result_id="sid-1")
        prior_attacks = [
            _make_attack_result(
                outcome=AttackOutcome.SUCCESS,
                parent_collection="red_teaming__adv_a",
                parent_eval_hash="old_hash",
            ),
        ]

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [prior_sr]
        bench._memory.get_attack_results.return_value = prior_attacks

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates

    async def test_skip_cached_only_considers_completed_scenarios(self):
        """Scenarios in IN_PROGRESS / FAILED / CANCELLED state must not seed the cache."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]
        in_progress = _make_scenario_result(result_id="sid-1", run_state="IN_PROGRESS")
        failed = _make_scenario_result(result_id="sid-2", run_state="FAILED")

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [in_progress, failed]
        bench._memory.get_attack_results.return_value = [
            _make_attack_result(
                outcome=AttackOutcome.SUCCESS,
                parent_collection="red_teaming__adv_a",
                parent_eval_hash="hash_a",
            ),
        ]

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates
        bench._memory.get_attack_results.assert_not_called()

    async def test_skip_cached_filters_by_scenario_name_and_version(self):
        """get_scenario_results is queried with this scenario's name + VERSION; old VERSION=1 results don't apply."""
        bench = await self._make_bench(skip_cached=True)

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = []

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=[]):
            await bench._get_atomic_attacks_async()

        bench._memory.get_scenario_results.assert_called_once_with(
            scenario_name="AdversarialBenchmark",
            scenario_version=AdversarialBenchmark.VERSION,
        )

    async def test_skip_cached_handles_missing_attribution_data(self):
        """Rows with attribution_data=None or missing parent_collection are silently skipped."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]
        prior_sr = _make_scenario_result(result_id="sid-1")
        prior_attacks = [
            _make_attack_result(
                outcome=AttackOutcome.SUCCESS,
                parent_collection=None,
                parent_eval_hash=None,
            ),
            _make_attack_result(
                outcome=AttackOutcome.SUCCESS,
                parent_collection=None,
                parent_eval_hash="hash_x",
            ),
        ]

        bench._memory = MagicMock()
        bench._memory.get_scenario_results.return_value = [prior_sr]
        bench._memory.get_attack_results.return_value = prior_attacks

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates

    async def test_skip_cached_memory_error_falls_back_to_no_filter(self):
        """An exception from get_scenario_results must not block the run — return base candidates as-is."""
        bench = await self._make_bench(skip_cached=True)

        candidates = [_make_candidate(name="red_teaming__adv_a", eval_hash="hash_a")]
        bench._memory = MagicMock()
        bench._memory.get_scenario_results.side_effect = RuntimeError("db down")

        with patch.object(AdversarialBenchmark.__bases__[0], "_get_atomic_attacks_async", return_value=candidates):
            result = await bench._get_atomic_attacks_async()

        assert result == candidates


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialBenchmarkSkipCachedInit:
    """Tests for the skip_cached constructor surface."""

    async def test_skip_cached_defaults_to_false(self):
        await _fan_out(target_names=["adv_a"])
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        assert bench._skip_cached is False

    async def test_skip_cached_can_be_set_true(self):
        await _fan_out(target_names=["adv_a"])
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            skip_cached=True,
        )
        assert bench._skip_cached is True
