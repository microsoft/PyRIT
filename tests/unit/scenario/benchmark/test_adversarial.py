# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the post-collapse AdversarialBenchmark scenario.

AdversarialBenchmark now owns its adversarial target axis directly via
the ``adversarial_targets`` parameter declared in
``supported_parameters``. Targets are user-supplied registry names
that resolve to ``PromptTarget`` instances via ``TargetRegistry``. The
``(technique × target × dataset)`` cross-product is built lazily inside
``_get_atomic_attacks_async`` using per-pair non-registered factories;
no global ``AttackTechniqueRegistry`` state is mutated.

These tests cover the new contract:
* Class metadata (VERSION, BASELINE policy, defaults).
* Strategy enum is built from source ``SCENARIO_TECHNIQUES`` entries that
  require an adversarial chat target; ``light`` aggregate preserves the
  source ``light`` tag (excludes ``tap`` / ``crescendo_simulated``).
* ``supported_parameters`` declares ``adversarial_targets: list[str]``.
* ``_resolve_adversarial_targets`` raises with available names on typos.
* ``_get_atomic_attacks_async`` produces ``N × M × D`` atomic attacks
  with the expected ``atomic_attack_name`` and ``display_group``.
* ``_collect_cached_completion_pairs`` delegates to
  ``pyrit.analytics.get_cached_results_for_technique`` per unique
  technique hash and returns the set of technique hashes with at least
  one ``SUCCESS`` / ``FAILURE`` match for the scenario's objective target.
* ``skip_cached`` filters cached candidates end-to-end.
"""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import AttackOutcome, SeedAttackGroup, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core import BaselineAttackPolicy
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES, _spec_needs_adversarial
from pyrit.scenario.scenarios.benchmark.adversarial import (
    AdversarialBenchmark,
    _build_benchmark_strategy,
)
from pyrit.score import TrueFalseScorer

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_registries():
    """Reset both registries between tests so target/technique state doesn't leak."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


def _register_adversarial_target(*, name: str) -> PromptTarget:
    """Register a mock adversarial target in TargetRegistry."""
    target = MagicMock(spec=PromptTarget)
    registry = TargetRegistry.get_registry_singleton()
    registry.register_instance(target, name=name)
    return target


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkMetadata:
    """Tests for class-level metadata that doesn't depend on any runtime state."""

    def test_version_is_2(self):
        """VERSION matches the post-collapse ``atomic_attack_name`` format so cached results still match."""
        assert AdversarialBenchmark.VERSION == 2

    def test_baseline_attack_policy_is_forbidden(self):
        """A baseline contributes no signal to a model-comparison benchmark, so it is forbidden."""
        assert AdversarialBenchmark.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden

    def test_default_dataset_config_uses_harmbench(self):
        config = AdversarialBenchmark.default_dataset_config()
        assert config.get_default_dataset_names() == ["harmbench"]

    def test_default_dataset_config_max_size_is_8(self):
        assert AdversarialBenchmark.default_dataset_config().max_dataset_size == 8


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


# ---------------------------------------------------------------------------
# Strategy class construction
# ---------------------------------------------------------------------------


class TestAdversarialBenchmarkStrategy:
    """Tests for ``_build_benchmark_strategy`` and the cached ``get_strategy_class`` accessor."""

    def test_strategy_built_from_adversarial_specs(self):
        """Every adversarial-capable spec in ``SCENARIO_TECHNIQUES`` produces one concrete enum member."""
        strategy_cls = _build_benchmark_strategy()
        aggregate_names = {"all"} | strategy_cls.get_aggregate_tags()
        concrete_members = [m for m in strategy_cls if m.value not in aggregate_names]

        adversarial_specs = [s for s in SCENARIO_TECHNIQUES if _spec_needs_adversarial(s)]
        adversarial_spec_names = {s.name for s in adversarial_specs}

        concrete_member_values = {m.value for m in concrete_members}
        assert concrete_member_values == adversarial_spec_names

    def test_strategy_excludes_non_adversarial_techniques(self):
        """Techniques like ``prompt_sending`` (no adversarial chat) must not be enum members."""
        strategy_cls = _build_benchmark_strategy()
        member_values = {m.value for m in strategy_cls}

        non_adversarial = [s for s in SCENARIO_TECHNIQUES if not _spec_needs_adversarial(s)]
        for spec in non_adversarial:
            assert spec.name not in member_values, (
                f"{spec.name} is not adversarial-capable but appeared as a benchmark strategy member."
            )

    def test_strategy_includes_required_aggregates(self):
        """The strategy enum exposes ``light``, ``single_turn``, ``multi_turn`` aggregates."""
        strategy_cls = _build_benchmark_strategy()
        aggregates = strategy_cls.get_aggregate_tags()

        assert "light" in aggregates
        assert "single_turn" in aggregates
        assert "multi_turn" in aggregates

    def test_light_aggregate_excludes_expensive_techniques(self):
        """``light`` must not pull in ``tap`` or ``crescendo_simulated`` — both can take hours."""
        strategy_cls = _build_benchmark_strategy()
        light_member = strategy_cls("light")

        # Expand the aggregate to its concrete child members.
        resolved_values = {child.value for child in strategy_cls.expand({light_member})}

        assert "tap" not in resolved_values
        assert "crescendo_simulated" not in resolved_values

    def test_light_aggregate_includes_red_teaming(self):
        """Sanity check: ``red_teaming`` is adversarial-capable AND tagged ``light``."""
        strategy_cls = _build_benchmark_strategy()
        light_member = strategy_cls("light")
        resolved_values = {child.value for child in strategy_cls.expand({light_member})}
        assert "red_teaming" in resolved_values

    def test_get_strategy_class_returns_same_enum_shape(self):
        """``get_strategy_class`` rebuilds on every call; the resulting enums have identical members."""
        first = AdversarialBenchmark.get_strategy_class()
        second = AdversarialBenchmark.get_strategy_class()
        assert {m.value for m in first} == {m.value for m in second}

    def test_default_strategy_is_light(self):
        """``get_default_strategy`` returns the ``light`` aggregate."""
        default = AdversarialBenchmark.get_default_strategy()
        assert default.value == "light"


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
        assert bench._skip_cached is False

    def test_skip_cached_can_be_set_true(self):
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            skip_cached=True,
        )
        assert bench._skip_cached is True


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
# _get_atomic_attacks_async — validation and cross-product
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestGetAtomicAttacksValidation:
    """Tests for validation errors raised by ``_get_atomic_attacks_async``."""

    def _make_bench(self) -> AdversarialBenchmark:
        return AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))

    async def test_uninitialized_scenario_raises(self):
        """Calling ``_get_atomic_attacks_async`` before ``initialize_async`` raises a clear error."""
        bench = self._make_bench()
        bench._objective_target = None

        with pytest.raises(ValueError, match="not properly initialized"):
            await bench._get_atomic_attacks_async()

    async def test_missing_adversarial_targets_raises_actionable_error(self):
        """Empty/missing ``adversarial_targets`` raises a message pointing at CLI / .pyrit_conf / list-targets."""
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {}

        with pytest.raises(ValueError) as exc_info:
            await bench._get_atomic_attacks_async()

        message = str(exc_info.value)
        assert "--adversarial-targets" in message
        assert ".pyrit_conf" in message
        assert "list-targets" in message

    async def test_empty_adversarial_targets_list_raises(self):
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": []}

        with pytest.raises(ValueError, match="at least one adversarial chat target"):
            await bench._get_atomic_attacks_async()

    async def test_unknown_target_name_raises_listing_available(self):
        _register_adversarial_target(name="adv_a")
        bench = self._make_bench()
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": ["missing"]}

        with pytest.raises(ValueError) as exc_info:
            await bench._get_atomic_attacks_async()

        message = str(exc_info.value)
        assert "missing" in message
        assert "adv_a" in message


@pytest.mark.usefixtures("patch_central_database")
class TestGetAtomicAttacksCrossProduct:
    """Tests for the (technique × target × dataset) cross-product produced by ``_get_atomic_attacks_async``."""

    def _make_bench_with_targets(self, *, target_names: list[str]) -> AdversarialBenchmark:
        for name in target_names:
            _register_adversarial_target(name=name)
        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": target_names}

        red_teaming_strategy = MagicMock()
        red_teaming_strategy.value = "red_teaming"
        bench._scenario_strategies = [red_teaming_strategy]

        # Dataset config: one dataset with one real seed group (AtomicAttack hashes objectives).
        seed_group = SeedAttackGroup(seeds=[SeedObjective(value="benchmark_objective_1")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.get_seed_attack_groups.return_value = {"harmbench": [seed_group]}

        return bench

    def _patch_factory_builder(self, *, seed_technique=None):
        """Return a patch context manager for ``AttackTechniqueRegistry.build_factory_from_spec``."""
        factory = MagicMock()
        factory.seed_technique = seed_technique
        factory.create.return_value = MagicMock(name="AttackTechnique")
        return patch(
            "pyrit.scenario.scenarios.benchmark.adversarial.AttackTechniqueRegistry.build_factory_from_spec",
            return_value=factory,
        )

    async def test_cross_product_count_matches_n_techniques_m_targets_d_datasets(self):
        """1 technique × 2 targets × 1 dataset = 2 atomic attacks."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])

        with self._patch_factory_builder():
            result = await bench._get_atomic_attacks_async()

        assert len(result) == 2

    async def test_atomic_attack_name_format_is_technique__target_dataset(self):
        """Name format: ``{technique}__{target}_{dataset}`` (preserves VERSION=2 cache key shape)."""
        bench = self._make_bench_with_targets(target_names=["adv_a"])

        with self._patch_factory_builder():
            result = await bench._get_atomic_attacks_async()

        names = [a.atomic_attack_name for a in result]
        assert names == ["red_teaming__adv_a_harmbench"]

    async def test_display_group_equals_target_registry_name(self):
        """``display_group`` is the raw target registry name — no string parsing."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])

        with self._patch_factory_builder():
            result = await bench._get_atomic_attacks_async()

        display_groups = sorted({a.display_group for a in result})
        assert display_groups == ["adv_a", "adv_b"]

    async def test_display_group_uses_registry_name_not_target_model_name(self):
        """Regression: ``display_group`` must come from the registry name passed in via
        ``adversarial_targets`` — not from any internal field on the ``PromptTarget`` instance
        (``_model_name``, ``_underlying_model``, ``_endpoint``, etc.). If a future refactor
        causes the scenario to source ``display_group`` from the target's own attributes,
        users' per-target ASR roll-ups would silently change shape based on whatever model
        name the target was constructed with.
        """
        # Register a target under the registry name "adv_a" with an utterly different
        # internal model/endpoint identity. After resolution, display_group should still
        # be "adv_a" — the registry name — not anything that leaked from the target.
        target = MagicMock(spec=PromptTarget)
        target._model_name = "totally-different-model-name"
        target._underlying_model = "another-model-identity"
        target._endpoint = "https://hijacked.example.com/openai/v1"
        target.name = "name-attribute-that-must-not-leak"
        TargetRegistry.get_registry_singleton().register_instance(target, name="adv_a")

        bench = AdversarialBenchmark(objective_scorer=MagicMock(spec=TrueFalseScorer))
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench.params = {"adversarial_targets": ["adv_a"]}

        red_teaming_strategy = MagicMock()
        red_teaming_strategy.value = "red_teaming"
        bench._scenario_strategies = [red_teaming_strategy]

        seed_group = SeedAttackGroup(seeds=[SeedObjective(value="display_group_regression_objective")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.get_seed_attack_groups.return_value = {"harmbench": [seed_group]}

        with self._patch_factory_builder():
            result = await bench._get_atomic_attacks_async()

        assert len(result) == 1
        atomic = result[0]
        assert atomic.display_group == "adv_a", (
            f"display_group must equal the registry name 'adv_a', got {atomic.display_group!r}. "
            "If this is failing, the scenario started sourcing display_group from the target's "
            "internal attributes (_model_name, etc.) — restore the registry-name behavior."
        )
        # Belt-and-suspenders: also assert the atomic_attack_name uses the registry name,
        # since the same plumbing pipes both.
        assert atomic.atomic_attack_name == "red_teaming__adv_a_harmbench"

    async def test_factory_built_per_target_with_overridden_adversarial_chat(self):
        """Each (spec, target) pair gets its own ``build_factory_from_spec`` call with a replaced spec."""
        bench = self._make_bench_with_targets(target_names=["adv_a", "adv_b"])

        with self._patch_factory_builder() as build_mock:
            await bench._get_atomic_attacks_async()

        # 1 selected technique × 2 targets = 2 factory builds.
        assert build_mock.call_count == 2
        # Each pair_spec has adversarial_chat replaced; verify the (replaced spec).adversarial_chat
        # matches the corresponding registry entry.
        target_a = TargetRegistry.get_registry_singleton().get_instance_by_name("adv_a")
        target_b = TargetRegistry.get_registry_singleton().get_instance_by_name("adv_b")
        replaced_targets = {call.args[0].adversarial_chat for call in build_mock.call_args_list}
        assert replaced_targets == {target_a, target_b}


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

    def _make_candidate(self, *, technique_eval_hash: str | None) -> MagicMock:
        candidate = MagicMock()
        candidate.technique_eval_hash = technique_eval_hash
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
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            self._patch_identifier(eval_hash="obj_hash"),
            patch(
                self._ANALYTICS_PATH,
                return_value=[_make_attack_result_with_outcome(AttackOutcome.SUCCESS)],
            ),
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"hash_a"}

    def test_returns_hash_when_failure_match_exists(self):
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            self._patch_identifier(),
            patch(
                self._ANALYTICS_PATH,
                return_value=[_make_attack_result_with_outcome(AttackOutcome.FAILURE)],
            ),
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"hash_a"}

    def test_excludes_hash_when_only_error_or_undetermined_matches(self):
        """ERROR / UNDETERMINED outcomes must NOT count as cached so transient failures retry."""
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            self._patch_identifier(),
            patch(
                self._ANALYTICS_PATH,
                return_value=[
                    _make_attack_result_with_outcome(AttackOutcome.ERROR),
                    _make_attack_result_with_outcome(AttackOutcome.UNDETERMINED),
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
        """Three candidates sharing two unique hashes → analytics called twice, not three times."""
        bench = self._make_bench()
        candidates = [
            self._make_candidate(technique_eval_hash="hash_a"),
            self._make_candidate(technique_eval_hash="hash_b"),
            self._make_candidate(technique_eval_hash="hash_a"),  # duplicate
        ]

        with (
            self._patch_identifier(eval_hash="obj_hash"),
            patch(
                self._ANALYTICS_PATH,
                return_value=[_make_attack_result_with_outcome(AttackOutcome.SUCCESS)],
            ) as analytics_mock,
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == {"hash_a", "hash_b"}
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

    def test_analytics_lookup_exception_is_swallowed_per_hash(self):
        """A failing analytics lookup for one hash must not block the others — that hash is not cached."""
        bench = self._make_bench()
        candidates = [
            self._make_candidate(technique_eval_hash="hash_a"),
            self._make_candidate(technique_eval_hash="hash_b"),
        ]

        def fake_analytics(_memory, *, technique_eval_hash, objective_target_eval_hash):
            if technique_eval_hash == "hash_a":
                raise RuntimeError("analytics blew up")
            return [_make_attack_result_with_outcome(AttackOutcome.SUCCESS)]

        with self._patch_identifier(), patch(self._ANALYTICS_PATH, side_effect=fake_analytics):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        # hash_a was the failed lookup → not cached (will retry). hash_b succeeded → cached.
        assert cached == {"hash_b"}

    def test_identifier_construction_failure_falls_back_to_empty(self):
        """If ``ObjectiveTargetEvaluationIdentifier`` raises, cache becomes a no-op rather than blocking."""
        bench = self._make_bench()
        candidates = [self._make_candidate(technique_eval_hash="hash_a")]

        with (
            patch(self._IDENTIFIER_PATH, side_effect=RuntimeError("bad identifier")),
            patch(self._ANALYTICS_PATH) as analytics_mock,
        ):
            cached = bench._collect_cached_completion_pairs(atomic_attacks=candidates)

        assert cached == set()
        analytics_mock.assert_not_called()


# ---------------------------------------------------------------------------
# skip_cached end-to-end through _get_atomic_attacks_async
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
class TestSkipCachedFilter:
    """End-to-end tests for the ``skip_cached`` filter applied in ``_get_atomic_attacks_async``."""

    _ANALYTICS_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.get_cached_results_for_technique"
    _IDENTIFIER_PATH = "pyrit.scenario.scenarios.benchmark.adversarial.ObjectiveTargetEvaluationIdentifier"

    def _make_bench(self, *, skip_cached: bool) -> AdversarialBenchmark:
        _register_adversarial_target(name="adv_a")
        bench = AdversarialBenchmark(
            objective_scorer=MagicMock(spec=TrueFalseScorer),
            skip_cached=skip_cached,
        )
        bench._objective_target = MagicMock(spec=PromptTarget)
        bench._objective_target_identifier = MagicMock()
        bench.params = {"adversarial_targets": ["adv_a"]}

        red_teaming_strategy = MagicMock()
        red_teaming_strategy.value = "red_teaming"
        bench._scenario_strategies = [red_teaming_strategy]

        seed_group = SeedAttackGroup(seeds=[SeedObjective(value="skip_cached_objective")])
        bench._dataset_config = MagicMock()
        bench._dataset_config.get_seed_attack_groups.return_value = {"harmbench": [seed_group]}

        return bench

    def _patch_factory_builder(self):
        factory = MagicMock()
        factory.seed_technique = None
        factory.create.return_value = MagicMock(name="AttackTechnique")
        return patch(
            "pyrit.scenario.scenarios.benchmark.adversarial.AttackTechniqueRegistry.build_factory_from_spec",
            return_value=factory,
        )

    def _patch_identifier(self, eval_hash: str = "obj_hash"):
        identifier_instance = MagicMock()
        identifier_instance.eval_hash = eval_hash
        return patch(self._IDENTIFIER_PATH, return_value=identifier_instance)

    async def test_skip_cached_false_returns_all_candidates_without_analytics_call(self):
        bench = self._make_bench(skip_cached=False)

        with self._patch_factory_builder(), patch(self._ANALYTICS_PATH) as analytics_mock:
            result = await bench._get_atomic_attacks_async()

        assert len(result) == 1
        analytics_mock.assert_not_called()

    async def test_skip_cached_true_filters_matching_candidates(self):
        bench = self._make_bench(skip_cached=True)

        # Stub every candidate's technique_eval_hash to a known value so the analytics
        # lookup key matches the cached set.
        with (
            self._patch_factory_builder(),
            self._patch_identifier(),
            patch(
                "pyrit.scenario.core.atomic_attack.AtomicAttack.technique_eval_hash",
                new_callable=lambda: property(lambda self: "cached_hash"),
            ),
            patch(
                self._ANALYTICS_PATH,
                return_value=[_make_attack_result_with_outcome(AttackOutcome.SUCCESS)],
            ),
        ):
            result = await bench._get_atomic_attacks_async()

        assert result == []

    async def test_skip_cached_true_keeps_unmatched_candidates(self):
        bench = self._make_bench(skip_cached=True)

        # Analytics returns no matches → no candidate is cached, so all pass through.
        with (
            self._patch_factory_builder(),
            self._patch_identifier(),
            patch(self._ANALYTICS_PATH, return_value=[]),
        ):
            result = await bench._get_atomic_attacks_async()

        assert len(result) == 1
