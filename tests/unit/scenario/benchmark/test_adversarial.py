# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the post-collapse AdversarialBenchmark scenario.

AdversarialBenchmark no longer takes an ``adversarial_models`` constructor
parameter and no longer builds local factories. It reads fanned variants
from ``AttackTechniqueRegistry`` (registered by ``BenchmarkInitializer``)
and inherits the base ``Scenario._get_atomic_attacks_async`` loop.

These tests cover the new contract:
* Class metadata (VERSION, BASELINE policy, defaults).
* Strategy enum is built from ``benchmark_fanout``-tagged registry entries.
* Display grouping uses the target-label portion of fanned technique names.
* Construction accepts only ``objective_scorer`` and ``scenario_result_id``.
"""

from unittest.mock import MagicMock, patch

import pytest

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
