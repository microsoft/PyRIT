# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``TextAdaptive`` scenario."""

from __future__ import annotations

import uuid
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import (
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioDatasetSummary,
    ScenarioRunPlanGroupKind,
    SeedObjective,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario import BaselineAttackPolicy, CompoundDatasetAttackConfiguration
from pyrit.scenario.scenarios.adaptive import (
    AdaptiveTechniqueDispatcher,
    AdaptiveTechniqueIdentifier,
    TextAdaptive,
)
from pyrit.score import TrueFalseScorer

_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(100)]
_LIGHT_TECHNIQUES = {
    "role_play_movie_script",
    "role_play_video_game",
    "role_play_trivia_game",
    "role_play_persuasion",
    "role_play_persuasion_written",
    "many_shot",
    "red_teaming",
    "context_compliance",
    "flip",
}


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target() -> MagicMock:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


@pytest.fixture(autouse=True)
def reset_technique_registry():
    """Reset registries and the cached technique class between tests."""
    from pyrit.registry import TargetRegistry

    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    TextAdaptive._cached_technique_class = None
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    TextAdaptive._cached_technique_class = None


@pytest.fixture(autouse=True)
def patch_many_shot_load():
    with patch(
        "pyrit.executor.attack.single_turn.many_shot_jailbreak.load_many_shot_jailbreaking_dataset",
        return_value=_MOCK_MANY_SHOT_EXAMPLES,
    ):
        yield


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


def _make_seed_group(*, value: str, harm_categories: list[str] | None = None) -> AttackSeedGroup:
    return AttackSeedGroup(seeds=[SeedObjective(value=value, harm_categories=harm_categories)])


def _make_fake_factory(
    *,
    seed_technique=None,
    adversarial_chat=None,
    scoring_config_type=None,
    attack_identifier: ComponentIdentifier | None = None,
    factory_identifier: ComponentIdentifier | None = None,
    technique_identifier: ComponentIdentifier | None = None,
) -> MagicMock:
    """Return a stub attack-technique factory that produces a fake ``AttackTechnique``.

    Mocks the surface ``AdaptiveScenario._build_techniques_dict`` consumes
    (``factory.create(...)``, ``factory.get_identifier()``,
    ``factory.adversarial_chat``, and ``factory.scoring_config_type``).
    Each call assigns unique attack and factory identities unless a test
    deliberately supplies shared identities.
    """
    fake_id = uuid.uuid4().hex[:8]

    fake_technique = MagicMock()
    fake_attack = MagicMock(name=f"fake-attack-technique-{fake_id}")
    fake_attack.get_identifier.return_value = attack_identifier or ComponentIdentifier(
        class_name=f"FakeAttack{fake_id}", class_module="test_text_adaptive"
    )
    fake_technique.attack = fake_attack
    fake_technique.seed_technique = seed_technique
    fake_technique.get_identifier.return_value = technique_identifier or ComponentIdentifier(
        class_name=f"FakeTechnique{fake_id}", class_module="test_text_adaptive"
    )
    factory = MagicMock()
    factory.create.return_value = fake_technique
    factory.adversarial_chat = adversarial_chat
    factory.uses_adversarial = adversarial_chat is not None
    factory.scoring_config_type = scoring_config_type
    factory.get_identifier.return_value = factory_identifier or ComponentIdentifier(
        class_name=f"FakeFactory{fake_id}", class_module="test_text_adaptive"
    )
    return factory


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestTextAdaptiveBasics:
    def test_version(self):
        assert TextAdaptive.VERSION == 1

    def test_baseline_enabled(self):
        assert TextAdaptive.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled

    def test_default_dataset_config(self):
        config = TextAdaptive.default_dataset_config()
        assert isinstance(config, CompoundDatasetAttackConfiguration)
        assert all(child.max_dataset_size == 4 for child in config._configurations)
        assert config.dataset_names == TextAdaptive.required_datasets()

    def test_required_datasets_non_empty(self):
        assert len(TextAdaptive.required_datasets()) > 0

    async def test_targetless_run_size_exposes_attack_range(self, mock_objective_scorer):
        """A targetless estimate exposes baseline-to-candidate bounds."""
        selected_groups = {
            "harmbench": [
                _make_seed_group(value="objective-1"),
                _make_seed_group(value="objective-2"),
            ]
        }
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"include_baseline": True})

        with patch.object(
            scenario,
            "_resolve_dataset_groups_for_estimate_async",
            new_callable=AsyncMock,
            return_value=(selected_groups, []),
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=False)

        assert estimate.estimated_attack_count is None
        assert estimate.minimum_attack_count == 2
        assert estimate.maximum_attack_count == 4

    async def test_binding_dataset_cap_exposes_attack_range(
        self,
        mock_objective_scorer,
        mock_objective_target,
    ):
        """A randomized cap exposes bounds when the exact compatibility mix is unknown."""
        selected_groups = {
            "harmbench": [
                _make_seed_group(value="objective-1"),
                _make_seed_group(value="objective-2"),
            ]
        }
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"include_baseline": True})
        scenario._objective_target = mock_objective_target
        scenario._include_baseline = True
        scenario._estimate_target_is_configured = True
        scenario._estimate_has_binding_size_cap = True

        with (
            patch.object(
                scenario,
                "_resolve_dataset_groups_for_estimate_async",
                new_callable=AsyncMock,
                return_value=(selected_groups, []),
            ),
            patch.object(scenario, "_build_techniques_dict", return_value={}),
            patch(
                "pyrit.scenario.scenarios.adaptive.adaptive_scenario.AdaptiveTechniqueDispatcher"
            ) as mock_dispatcher_class,
        ):
            mock_dispatcher_class.return_value.compatible_techniques.side_effect = [[], ["compatible"]]
            estimate = await scenario._estimate_run_size_async()

        assert estimate.estimated_attack_count is None
        assert estimate.minimum_attack_count == 2
        assert estimate.maximum_attack_count == 4

    def test_max_attempts_parameter_distinguishes_techniques_from_retries(self):
        parameter = next(
            parameter
            for parameter in TextAdaptive.additional_parameters()
            if parameter.name == "max_attempts_per_objective"
        )
        assert parameter.default == 3
        assert "different compatible techniques" in parameter.description
        assert "stopping after the first success" in parameter.description
        assert "separate from retries" in parameter.description

    def test_get_technique_class_is_cached(self):
        cls_a = TextAdaptive.get_technique_class()
        cls_b = TextAdaptive.get_technique_class()
        assert cls_a is cls_b

    def test_get_default_technique(self):
        strat = TextAdaptive.get_technique_class().default()
        # The default aggregate must resolve to something runnable.
        assert strat is not None
        assert strat.value == "default"

    @patch("pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer")
    def test_init_stores_adaptive_params(self, mock_get_scorer, mock_objective_scorer):
        mock_get_scorer.return_value = mock_objective_scorer
        scenario = TextAdaptive()
        scenario.set_params_from_args(
            args={
                "max_attempts_per_objective": 7,
            }
        )
        assert scenario.params["max_attempts_per_objective"] == 7


@pytest.mark.usefixtures(*FIXTURES)
class TestTextAdaptiveAtomicAttacks:
    """Tests for ``_get_atomic_attacks_async`` overriding."""

    async def _build_scenario_and_attacks(
        self,
        *,
        mock_objective_target,
        mock_objective_scorer,
        seed_groups: dict[str, list[AttackSeedGroup]],
        **scenario_kwargs,
    ):
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=seed_groups,
        ):
            scenario = TextAdaptive(
                objective_scorer=mock_objective_scorer,
                **scenario_kwargs,
            )
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
            return scenario, scenario._atomic_attacks

    async def test_one_atomic_per_objective(self, mock_objective_target, mock_objective_scorer):
        groups = {
            "violence": [
                _make_seed_group(value="obj-v1", harm_categories=["violence"]),
                _make_seed_group(value="obj-v2", harm_categories=["violence"]),
            ],
            "hate": [
                _make_seed_group(value="obj-h1", harm_categories=["hate"]),
            ],
        }
        _scenario, attacks = await self._build_scenario_and_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=groups,
        )
        # One atomic per objective; each carries exactly one seed group.
        assert len(attacks) == 3
        for a in attacks:
            assert len(a.seed_groups) == 1

    async def test_dispatchers_share_one_selector(self, mock_objective_target, mock_objective_scorer):
        """All per-dataset dispatchers share one TechniqueSelector instance so
        learning accumulates globally (selection is committed up-front but the
        selector is still shared by reference across constructions).
        """
        selectors_seen: list = []
        real_init = AdaptiveTechniqueDispatcher.__init__

        def _spy_init(self, *args, **kwargs):
            selectors_seen.append(kwargs["selector"])
            return real_init(self, *args, **kwargs)

        groups = {
            "violence": [_make_seed_group(value="obj-v1", harm_categories=["violence"])],
            "hate": [_make_seed_group(value="obj-h1", harm_categories=["hate"])],
        }
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            # Spy on the dispatcher construction that initialize_async triggers.
            with patch.object(AdaptiveTechniqueDispatcher, "__init__", _spy_init):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                    }
                )
                await scenario.initialize_async()

        # One dispatcher per dataset; all share the same selector identity.
        assert len(selectors_seen) == 2
        assert len({id(s) for s in selectors_seen}) == 1

    async def test_atomic_names_contain_dataset_and_objective_hash(self, mock_objective_target, mock_objective_scorer):
        groups = {
            "violence": [_make_seed_group(value=f"obj-{i}", harm_categories=["violence"]) for i in range(5)],
            "hate": [_make_seed_group(value=f"hate-{i}", harm_categories=["hate"]) for i in range(3)],
        }
        _scenario, attacks = await self._build_scenario_and_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=groups,
        )
        names = [atomic.atomic_attack_name for atomic in attacks]
        # All names unique; each name embeds its dataset name.
        assert len(set(names)) == len(names) == 8
        for atomic in attacks:
            assert any(ds in atomic.atomic_attack_name for ds in groups)

    async def test_display_group_is_dataset_name(self, mock_objective_target, mock_objective_scorer):
        groups = {
            "violence": [_make_seed_group(value="obj-v", harm_categories=["violence"])],
            "hate": [_make_seed_group(value="obj-h", harm_categories=["hate"])],
        }
        _scenario, attacks = await self._build_scenario_and_attacks(
            mock_objective_target=mock_objective_target,
            mock_objective_scorer=mock_objective_scorer,
            seed_groups=groups,
        )
        display_groups = {atomic.display_group for atomic in attacks}
        assert display_groups == {"violence", "hate"}

    async def test_no_usable_techniques_raises(self, mock_objective_target, mock_objective_scorer):
        groups = {"violence": [_make_seed_group(value="obj")]}
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            # Force the factory map to be empty; initialize_async builds the atomic
            # attacks and must raise when no techniques are usable.
            with patch.object(scenario, "_get_attack_technique_factories", return_value={}):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                    }
                )
                with pytest.raises(ValueError, match="no usable techniques"):
                    await scenario.initialize_async()

    async def test_techniques_with_seed_technique_are_kept(self, mock_objective_target, mock_objective_scorer):
        """Factories that declare a ``seed_technique`` participate in the pool
        (the old behavior silently dropped them with a warning).
        """
        groups = {"violence": [_make_seed_group(value="obj")]}
        plain_factory = _make_fake_factory(seed_technique=None)
        seeded_factory = _make_fake_factory(seed_technique=MagicMock(name="seed_technique"))

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=True),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            factories = {"role_play_movie_script": plain_factory, "many_shot": seeded_factory}
            with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                        "scenario_techniques": [
                            technique_class("role_play_movie_script"),
                            technique_class("many_shot"),
                        ],
                    }
                )
                await scenario.initialize_async()
                attacks = scenario._atomic_attacks
                techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        # One atomic for the single objective.
        assert len(attacks) == 1
        # Both factories survive in the technique pool; in particular the
        # seeded one is no longer silently dropped.
        technique_names = {b.name for b in techniques.values()}
        assert "role_play_movie_script" in technique_names
        assert "many_shot" in technique_names

    @pytest.mark.parametrize(("max_attempts", "expected_attempts"), [(4, 84), (5, 105)])
    async def test_light_keeps_nine_distinct_factory_arms_and_attempt_bound(
        self,
        mock_objective_target,
        mock_objective_scorer,
        max_attempts,
        expected_attempts,
    ):
        shared_attack_identifier = _mock_id("SharedPromptSendingAttack")
        factories = {
            name: _make_fake_factory(
                attack_identifier=shared_attack_identifier,
                factory_identifier=_mock_id(f"Factory_{name}"),
            )
            for name in _LIGHT_TECHNIQUES
        }
        groups = {"adaptive": [_make_seed_group(value=f"obj-{index}") for index in range(21)]}
        summaries = [
            ScenarioDatasetSummary(
                name="adaptive",
                logical_seed_group_count=21,
                selected_seed_group_count=21,
            )
        ]
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        technique_class = scenario.get_technique_class()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [technique_class("light")],
                "include_baseline": False,
                "max_attempts_per_objective": max_attempts,
            }
        )
        scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=(groups, summaries))

        with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
            estimate = await scenario.get_run_size_estimate_async()
            techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        assert len(techniques) == 9
        assert {bundle.name for bundle in techniques.values()} == _LIGHT_TECHNIQUES
        parsed_identifiers = [AdaptiveTechniqueIdentifier.parse(identifier) for identifier in techniques]
        assert all(identifier is not None for identifier in parsed_identifiers)
        assert len({identifier.factory_hash for identifier in parsed_identifiers if identifier is not None}) == 9
        assert len({identifier.technique_eval_hash for identifier in parsed_identifiers if identifier is not None}) == 9
        assert estimate.adaptive_details is not None
        assert estimate.adaptive_details.selected_candidate_technique_count == 9
        assert estimate.adaptive_details.candidate_technique_count == 9
        assert estimate.adaptive_details.techniques_per_objective_upper_bound == max_attempts
        assert estimate.adaptive_details.technique_attempt_count_upper_bound == expected_attempts

    @pytest.mark.parametrize(("aggregate_name", "expected_candidate_count"), [("light", 9), ("core", 14)])
    async def test_aggregate_attempt_bound_increases_until_distinct_candidate_count(
        self,
        mock_objective_target,
        mock_objective_scorer,
        aggregate_name,
        expected_candidate_count,
    ):
        technique_class = TextAdaptive.get_technique_class()
        aggregate = technique_class(aggregate_name)
        selected_names = {technique.value for technique in technique_class.expand({aggregate})}
        assert len(selected_names) == expected_candidate_count

        shared_attack_identifier = _mock_id("SharedAttackImplementation")
        factories = {
            name: _make_fake_factory(
                attack_identifier=shared_attack_identifier,
                factory_identifier=_mock_id(f"Factory_{name}"),
            )
            for name in selected_names
        }
        groups = {"adaptive": [_make_seed_group(value=f"obj-{index}") for index in range(21)]}
        summaries = [
            ScenarioDatasetSummary(
                name="adaptive",
                logical_seed_group_count=21,
                selected_seed_group_count=21,
            )
        ]
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=(groups, summaries))
        effective_caps: list[int] = []

        with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
            for limit in range(1, expected_candidate_count + 3):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "scenario_techniques": [aggregate],
                        "include_baseline": False,
                        "max_attempts_per_objective": limit,
                    }
                )
                estimate = await scenario.get_run_size_estimate_async()

                assert estimate.adaptive_details is not None
                expected_effective_cap = min(limit, expected_candidate_count)
                effective_caps.append(estimate.adaptive_details.techniques_per_objective_upper_bound)
                assert estimate.adaptive_details.candidate_technique_count == expected_candidate_count
                assert estimate.adaptive_details.techniques_per_objective_upper_bound == expected_effective_cap
                assert estimate.adaptive_details.technique_attempt_count_upper_bound == 21 * expected_effective_cap

        assert effective_caps == [
            *range(1, expected_candidate_count + 1),
            expected_candidate_count,
            expected_candidate_count,
        ]

    def test_exact_duplicate_registered_technique_dedupes_only_itself(
        self,
        mock_objective_target,
        mock_objective_scorer,
    ):
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        technique_class = scenario.get_technique_class()
        scenario._scenario_techniques = [
            technique_class("role_play_movie_script"),
            technique_class("role_play_movie_script"),
        ]
        factory = _make_fake_factory()

        with patch.object(
            scenario,
            "_get_attack_technique_factories",
            return_value={"role_play_movie_script": factory},
        ):
            techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        assert len(techniques) == 1
        factory.create.assert_called_once()

    async def test_incompatible_seed_technique_is_filtered_per_objective(
        self, mock_objective_target, mock_objective_scorer
    ):
        """When one technique's ``seed_technique`` is incompatible but another
        is universally compatible, the objective still produces an atomic that
        uses only the compatible technique.
        """
        groups = {"violence": [_make_seed_group(value="obj")]}
        plain_factory = _make_fake_factory(seed_technique=None)
        incompatible_factory = _make_fake_factory(seed_technique=MagicMock(name="incompatible_seed_technique"))

        # Only the plain factory (no seed_technique) is compatible.
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=False),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            factories = {"role_play_movie_script": plain_factory, "many_shot": incompatible_factory}
            with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                        "scenario_techniques": [
                            technique_class("role_play_movie_script"),
                            technique_class("many_shot"),
                        ],
                    }
                )
                await scenario.initialize_async()
                attacks = scenario._atomic_attacks
                techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        # Atomic survives because the plain factory keeps the compatible pool non-empty.
        assert len(attacks) == 1
        assert len(attacks[0].seed_groups) == 1
        # Both factories live in the pool; per-objective compatibility filtering
        # inside the dispatcher (``AdaptiveTechniqueDispatcher.compatible_techniques``)
        # then drops the incompatible one before selection.
        technique_names = {b.name for b in techniques.values()}
        assert "role_play_movie_script" in technique_names
        assert "many_shot" in technique_names

    async def test_objective_skipped_when_no_compatible_techniques(
        self, mock_objective_target, mock_objective_scorer, caplog
    ):
        """When every technique requires an incompatible seed_technique, the
        objective is dropped with a warning rather than producing an atomic
        attack with an empty technique pool.
        """
        groups = {
            "violence": [_make_seed_group(value="obj-keep")],
            "hate": [_make_seed_group(value="obj-skip")],
        }
        seeded_factory = _make_fake_factory(seed_technique=MagicMock(name="seed_technique"))

        # is_compatible_with_technique returns True for "obj-keep", False for "obj-skip".
        def _selective_compat(self_group, *, technique):
            return self_group.objective.value == "obj-keep"

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", _selective_compat),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            with patch.object(
                scenario,
                "_get_attack_technique_factories",
                return_value={"role_play_movie_script": seeded_factory},
            ):
                import logging

                with caplog.at_level(logging.WARNING):
                    scenario.set_params_from_args(
                        args={
                            "objective_target": mock_objective_target,
                            "include_baseline": False,
                            "scenario_techniques": [technique_class("role_play_movie_script")],
                        }
                    )
                    await scenario.initialize_async()
                    attacks = scenario._atomic_attacks

        # Only the compatible objective produced an atomic attack.
        assert len(attacks) == 1
        # Skip was logged with the affected objective value.
        assert any("obj-skip" in record.getMessage() for record in caplog.records)

    async def test_factory_with_narrowed_scoring_config_type_receives_subtype(
        self, mock_objective_target, mock_objective_scorer
    ):
        """When a factory's attack class narrows ``attack_scoring_config`` to a
        subtype, the scenario builds and passes that subtype to ``create``."""
        from pyrit.executor.attack import AttackScoringConfig

        class NarrowScoringConfig(AttackScoringConfig):
            pass

        groups = {"violence": [_make_seed_group(value="obj")]}
        narrow_factory = _make_fake_factory(scoring_config_type=NarrowScoringConfig)
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=True),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            with patch.object(
                scenario,
                "_get_attack_technique_factories",
                return_value={"role_play_movie_script": narrow_factory},
            ):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                        "scenario_techniques": [technique_class("role_play_movie_script")],
                    }
                )
                await scenario.initialize_async()

        narrow_factory.create.assert_called_once()
        kwargs = narrow_factory.create.call_args.kwargs
        passed_config = kwargs["attack_scoring_config"]
        assert isinstance(passed_config, NarrowScoringConfig)
        assert passed_config.objective_scorer is mock_objective_scorer

    async def test_factory_with_incompatible_narrowed_scoring_config_is_skipped(
        self, mock_objective_target, mock_objective_scorer, caplog
    ):
        """When the narrowed ``attack_scoring_config`` subtype rejects the
        scenario's objective scorer, the technique is skipped with a warning
        rather than silently falling back to the base config (which could let
        a WARN-policy factory substitute its internal default scorer)."""
        import logging

        from pyrit.executor.attack import AttackScoringConfig

        class StrictScoringConfig(AttackScoringConfig):
            def __init__(self, *, objective_scorer):
                raise ValueError("StrictScoringConfig requires FloatScaleThresholdScorer")

        groups = {"violence": [_make_seed_group(value="obj")]}
        good_factory = _make_fake_factory()
        strict_factory = _make_fake_factory(scoring_config_type=StrictScoringConfig)

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=True),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            factories = {"role_play_movie_script": good_factory, "tap": strict_factory}
            with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
                with caplog.at_level(logging.WARNING):
                    scenario.set_params_from_args(
                        args={
                            "objective_target": mock_objective_target,
                            "include_baseline": False,
                            "scenario_techniques": [technique_class("role_play_movie_script"), technique_class("tap")],
                        }
                    )
                    await scenario.initialize_async()
                    techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        # Strict factory's create is never called — incompatibility surfaces
        # before construction, not via the factory's override policy.
        strict_factory.create.assert_not_called()
        # Only the compatible technique remains in the pool.
        technique_names = {b.name for b in techniques.values()}
        assert technique_names == {"role_play_movie_script"}
        # The skip reason mentions the required config type so operators can
        # diagnose the mismatch.
        assert any("tap" in r.getMessage() and "StrictScoringConfig" in r.getMessage() for r in caplog.records)

    async def test_factory_create_failure_skips_technique(self, mock_objective_target, mock_objective_scorer, caplog):
        """A factory whose ``create`` raises ``ValueError`` (e.g. the attack
        rejects the scenario's objective scorer) is logged and skipped, while
        sibling techniques still build successfully.
        """
        import logging

        groups = {"violence": [_make_seed_group(value="obj")]}
        good_factory = _make_fake_factory()
        bad_factory = _make_fake_factory()
        bad_factory.create.side_effect = ValueError("requires FloatScaleThresholdScorer")

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=True),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            factories = {"role_play_movie_script": good_factory, "tap": bad_factory}
            with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
                with caplog.at_level(logging.WARNING):
                    scenario.set_params_from_args(
                        args={
                            "objective_target": mock_objective_target,
                            "include_baseline": False,
                            "scenario_techniques": [technique_class("role_play_movie_script"), technique_class("tap")],
                        }
                    )
                    await scenario.initialize_async()
                    attacks = scenario._atomic_attacks
                    techniques = scenario._build_techniques_dict(objective_target=mock_objective_target)

        assert len(attacks) == 1
        technique_names = {b.name for b in techniques.values()}
        assert technique_names == {"role_play_movie_script"}
        assert any("tap" in r.getMessage() and "Skipping" in r.getMessage() for r in caplog.records)

    async def test_all_factories_failing_raises_with_reason(self, mock_objective_target, mock_objective_scorer):
        """When every technique's ``create`` fails, ``_build_techniques_dict``
        raises a ``ValueError`` that names the incompatible technique(s)."""
        groups = {"violence": [_make_seed_group(value="obj")]}
        bad_factory = _make_fake_factory()
        bad_factory.create.side_effect = ValueError("requires FloatScaleThresholdScorer")

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(AttackSeedGroup, "is_compatible_with_technique", return_value=True),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            with patch.object(
                scenario,
                "_get_attack_technique_factories",
                return_value={"tap": bad_factory},
            ):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                        "scenario_techniques": [technique_class("tap")],
                    }
                )
                with pytest.raises(ValueError, match="incompatible with scenario scorer.*tap"):
                    await scenario.initialize_async()


@pytest.mark.usefixtures(*FIXTURES)
class TestTextAdaptiveBaselinePolicy:
    async def test_initialize_async_accepts_explicit_baseline(self, mock_objective_target, mock_objective_scorer):
        groups = {"violence": [_make_seed_group(value="obj", harm_categories=["violence"])]}
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            # Baseline is Enabled by default, so explicit include_baseline=True must not raise.
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "include_baseline": True,
                }
            )
            await scenario.initialize_async()

    async def test_baseline_emitted_at_index_zero_by_default(self, mock_objective_target, mock_objective_scorer):
        """
        Under ``BASELINE_ATTACK_POLICY = Enabled`` (the default), the base
        scenario must prepend a baseline atomic attack at index 0.
        """
        groups = {"violence": [_make_seed_group(value="obj", harm_categories=["violence"])]}
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(
                CompoundDatasetAttackConfiguration,
                "resolve_attack_groups_for_estimate_async",
                new_callable=AsyncMock,
                return_value=(groups, groups),
            ),
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                scenario.set_params_from_args(args={"objective_target": mock_objective_target})
                await scenario.initialize_async()

            assert scenario._atomic_attacks, "expected at least one atomic attack"
            assert scenario._atomic_attacks[0].atomic_attack_name == "baseline", (
                f"baseline must be prepended at index 0; got {[a.atomic_attack_name for a in scenario._atomic_attacks]}"
            )
            estimate = await scenario.get_run_size_estimate_async()
            plan = scenario._build_run_plan()
            planned_units = sum(len(group.seed_group_ids) for group in plan.atomic_groups)
            assert planned_units == 2
            assert [group.group_kind for group in plan.atomic_groups] == [
                ScenarioRunPlanGroupKind.DIRECT_BASELINE,
                ScenarioRunPlanGroupKind.ADAPTIVE,
            ]
            assert estimate.total_attack_count == planned_units
            assert [component.count for component in estimate.components] == [1, 1]
