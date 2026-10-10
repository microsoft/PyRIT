# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``TextAdaptive`` scenario."""

from __future__ import annotations

import uuid
import warnings
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import AttackStrategy, PromptSendingAttack
from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    ScenarioRunPlanGroupKind,
    SeedGroupRequirements,
    SeedObjective,
    SeedPrompt,
)
from pyrit.models.identifiers import ComponentIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario import IncompatibleTechniqueError, IncompatibleTechniquePolicy, TechniqueRequirements
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy
from pyrit.scenario.scenarios.adaptive.dispatcher import AdaptiveTechniqueDispatcher
from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive
from pyrit.score import TrueFalseScorer
from tests.unit.mocks import MockPromptTarget

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.memory import SQLiteMemory

_MOCK_MANY_SHOT_EXAMPLES = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(100)]


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
    seed_technique: AttackTechniqueSeedGroup | None = None,
    adversarial_chat: PromptTarget | None = None,
    scoring_config_type: type | None = None,
    requirements: TechniqueRequirements | None = None,
) -> MagicMock:
    """Return a stub attack-technique factory that produces a fake ``AttackTechnique``.

    Mocks the surface ``AdaptiveScenario._build_techniques_dict`` consumes
    (``factory.create(...)``, ``factory.adversarial_chat``, and
    ``factory.scoring_config_type``). Each call assigns a unique fake
    attack identifier (via a fresh UUID) so the bundle dict keys (eval
    hashes) don't collide across calls — no shared mutable test state, so
    test execution order doesn't shift hash values.
    """
    fake_id = uuid.uuid4().hex[:8]

    fake_attack = MagicMock(spec=AttackStrategy, name=f"fake-attack-technique-{fake_id}")
    fake_attack.get_identifier.return_value = ComponentIdentifier(
        class_name=f"FakeAttack{fake_id}",
        class_module="test_text_adaptive",
    )
    fake_technique = AttackTechnique(attack=fake_attack, seed_technique=seed_technique, requirements=requirements)
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.attack_class = PromptSendingAttack
    factory.requirements = fake_technique.requirements
    factory.create.return_value = fake_technique
    factory.adversarial_chat = adversarial_chat
    factory.scoring_config_type = scoring_config_type
    return factory


FIXTURES = ["patch_central_database", "mock_runtime_env"]


@pytest.mark.usefixtures(*FIXTURES)
class TestTextAdaptiveBasics:
    def test_default_adversarial_usage_comes_from_adaptive_pool(self, mock_objective_scorer: MagicMock) -> None:
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        assert scenario.uses_default_adversarial_target is True
        with patch.object(scenario, "_get_attack_technique_factories", return_value={}):
            assert scenario.uses_default_adversarial_target is False

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

    async def test_targetless_run_size_uses_configured_budget(self, mock_objective_scorer):
        """A targetless estimate uses the budget without reading populations."""
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"include_baseline": True})

        with patch.object(
            scenario,
            "_resolve_seed_groups_by_dataset_async",
            new_callable=AsyncMock,
            side_effect=AssertionError("Preview resolved datasets"),
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=False)

        assert estimate.estimated_attack_count == scenario._default_dataset_config.get_size_budget().value * 2
        assert estimate.minimum_attack_count is None
        assert estimate.maximum_attack_count is None

    async def test_dataset_cap_estimate_does_not_build_compatibility_dispatcher(
        self,
        mock_objective_scorer,
        mock_objective_target,
    ):
        """Seed compatibility is deferred until initialization."""
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        scenario.set_params_from_args(args={"include_baseline": True, "objective_target": mock_objective_target})

        with (
            patch.object(
                scenario,
                "_resolve_seed_groups_by_dataset_async",
                new_callable=AsyncMock,
                side_effect=AssertionError("Preview resolved datasets"),
            ),
            patch.object(scenario, "_build_techniques_dict", return_value={}),
            patch(
                "pyrit.scenario.scenarios.adaptive.adaptive_scenario.AdaptiveTechniqueDispatcher"
            ) as mock_dispatcher_class,
        ):
            estimate = await scenario.get_run_size_estimate_async()
            mock_dispatcher_class.assert_not_called()

        assert estimate.estimated_attack_count == scenario._default_dataset_config.get_size_budget().value * 2
        assert estimate.minimum_attack_count is None
        assert estimate.maximum_attack_count is None

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
    async def test_selected_adaptation_is_recorded_without_replacing_source_groups_async(
        self, *, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock
    ) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context")])
        original = source.model_dump()
        factory = _make_fake_factory(
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True))
        )
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        technique_class = scenario.get_technique_class()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "include_baseline": False,
                "scenario_techniques": [technique_class("many_shot")],
            }
        )
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value={"dataset": [source]},
            ),
            patch.object(scenario, "_get_attack_technique_factories", return_value={"many_shot": factory}),
        ):
            await scenario.initialize_async()

        atomic = scenario._atomic_attacks[0]
        child = atomic._attack_technique.attack._child_attacks[0]
        assert atomic.seed_groups[0] is source
        assert len(child.seed_group.seeds) == 1
        assert child.atomic_attack_identifier.logical_seed_group_id == source.logical_id
        stored = (
            await scenario._memory.get_scenario_results_async(scenario_result_ids=[scenario._scenario_result_id])
        )[0]
        assert stored.metadata["adaptive_seed_group_adaptations"] == [
            {
                "atomic_attack_group_id": atomic.logical_group_id,
                "source_seed_group_id": source.logical_id,
                "technique_eval_hash": child.atomic_attack_identifier.eval_hash,
                "adaptation": "objective_only",
            }
        ]
        assert source.model_dump() == original

    @pytest.mark.parametrize("policy", ["skip", "raise"])
    async def test_dataset_mismatch_policy_applies_with_usable_alternative_async(
        self, *, policy: str, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock
    ) -> None:
        source = AttackSeedGroup(seeds=[SeedObjective(value="objective"), SeedPrompt(value="context", sequence=99)])
        strict = _make_fake_factory(
            requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=True))
        )
        plain = _make_fake_factory()
        scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
        technique_class = scenario.get_technique_class()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "include_baseline": False,
                "incompatible_technique_policy": policy,
                "scenario_techniques": [technique_class("many_shot"), technique_class("role_play_movie_script")],
            }
        )
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value={"dataset": [source]},
            ),
            patch.object(
                scenario,
                "_get_attack_technique_factories",
                return_value={"many_shot": strict, "role_play_movie_script": plain},
            ),
        ):
            if policy == "raise":
                with pytest.raises(IncompatibleTechniqueError, match="only an objective"):
                    await scenario.initialize_async()
            else:
                await scenario.initialize_async()
                child = scenario._atomic_attacks[0]._attack_technique.attack._child_attacks[0]
                assert child.strategy is plain.create.return_value.attack

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
        seeded_factory = _make_fake_factory(
            seed_technique=AttackTechniqueSeedGroup.from_system_prompt("Use the supplied format.")
        )

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
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

    async def test_incompatible_seed_technique_is_filtered_per_objective(
        self, mock_objective_target, mock_objective_scorer
    ):
        """When one technique's ``seed_technique`` is incompatible but another
        is universally compatible, the objective still produces an atomic that
        uses only the compatible technique.
        """
        groups = {
            "violence": [
                AttackSeedGroup(seeds=[SeedObjective(value="obj"), SeedPrompt(value="user turn", role="user")])
            ]
        }
        plain_factory = _make_fake_factory(seed_technique=None)
        incompatible_factory = _make_fake_factory(
            seed_technique=AttackTechniqueSeedGroup(
                seeds=[SeedPrompt(value="system framing", role="system", is_general_technique=True)]
            )
        )

        # Only the plain factory (no seed_technique) is compatible.
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
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
            "hate": [
                AttackSeedGroup(seeds=[SeedObjective(value="obj-skip"), SeedPrompt(value="user turn", role="user")])
            ],
        }
        seeded_factory = _make_fake_factory(
            seed_technique=AttackTechniqueSeedGroup(
                seeds=[SeedPrompt(value="system framing", role="system", is_general_technique=True)]
            )
        )

        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
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
        assert any("dataset 'hate': skipped 1 seed group" in record.getMessage() for record in caplog.records)

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

        class StrictScoringConfig(TAPAttackScoringConfig):
            pass

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

    async def test_factory_create_failure_propagates_async(self, mock_objective_target, mock_objective_scorer):
        """Unrelated factory construction errors must not become skipped work."""
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
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            technique_class = scenario.get_technique_class()
            factories = {"role_play_movie_script": good_factory, "tap": bad_factory}
            with patch.object(scenario, "_get_attack_technique_factories", return_value=factories):
                scenario.set_params_from_args(
                    args={
                        "objective_target": mock_objective_target,
                        "include_baseline": False,
                        "scenario_techniques": [technique_class("role_play_movie_script"), technique_class("tap")],
                    }
                )
                with pytest.raises(ValueError, match="requires FloatScaleThresholdScorer"):
                    await scenario.initialize_async()

    async def test_factory_value_error_propagates_async(
        self, *, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock
    ) -> None:
        """An unexpected constructor error is not a compatibility skip."""
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
                with pytest.raises(ValueError, match="requires FloatScaleThresholdScorer"):
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
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
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

    async def test_run_plan_records_baseline_and_adaptive_group_kinds(
        self, mock_objective_target, mock_objective_scorer
    ):
        groups = {
            "violence": [
                _make_seed_group(value="obj-1", harm_categories=["violence"]),
                _make_seed_group(value="obj-2", harm_categories=["violence"]),
            ]
        }
        with patch.object(
            CompoundDatasetAttackConfiguration,
            "get_attack_groups_by_dataset_async",
            new_callable=AsyncMock,
            return_value=groups,
        ):
            scenario = TextAdaptive(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()

        kinds = [group.kind for group in scenario._build_run_plan().atomic_groups]
        assert kinds == [
            ScenarioRunPlanGroupKind.BASELINE,
            ScenarioRunPlanGroupKind.ADAPTIVE,
            ScenarioRunPlanGroupKind.ADAPTIVE,
        ]


@pytest.mark.usefixtures(*FIXTURES)
class TestTextAdaptiveResume:
    async def _initialize_scenario_async(
        self,
        *,
        target: PromptTarget,
        scorer: TrueFalseScorer,
        factories: dict[str, MagicMock],
        groups: dict[str, list[AttackSeedGroup]],
        selector: TechniqueSelector,
        scenario_result_id: str | None = None,
        max_attempts: int = 2,
        include_baseline: bool = False,
        policy: IncompatibleTechniquePolicy = IncompatibleTechniquePolicy.SKIP,
    ) -> TextAdaptive:
        scenario = TextAdaptive(
            objective_scorer=scorer,
            selector=selector,
            scenario_result_id=scenario_result_id,
        )
        technique_class = scenario.get_technique_class()
        scenario.set_params_from_args(
            args={
                "objective_target": target,
                "scenario_techniques": [technique_class(name) for name in factories],
                "max_attempts_per_objective": max_attempts,
                "include_baseline": include_baseline,
                "incompatible_technique_policy": policy,
            }
        )
        with (
            patch.object(
                CompoundDatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value=groups,
            ),
            patch.object(scenario, "_get_attack_technique_factories", return_value=factories),
        ):
            await scenario.initialize_async()
        return scenario

    @pytest.mark.parametrize("with_context", [False, True])
    @pytest.mark.parametrize("max_attempts", [1, 2])
    async def test_resume_replays_each_source_selection_after_history_changes_async(
        self,
        *,
        with_context: bool,
        max_attempts: int,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        sqlite_instance: SQLiteMemory,
    ) -> None:
        sources = [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(value=value),
                    *([SeedPrompt(value=f"context-{value}")] if with_context else []),
                ]
            )
            for value in ["first", "second"]
        ]
        snapshots = [source.model_dump() for source in sources]
        factories = {
            "many_shot": _make_fake_factory(
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                )
            ),
            "role_play_movie_script": _make_fake_factory(
                requirements=TechniqueRequirements(seed_group=SeedGroupRequirements(objective_only=not with_context))
            ),
        }
        committed: dict[str, list[str]] = {}

        async def select_async(
            *,
            technique_identifiers: Sequence[str],
            objective: str,
            num_top_techniques: int,
            scenario_result_id: str | None,
        ) -> list[str]:
            ordered = list(technique_identifiers)
            if objective == "second":
                ordered.reverse()
            committed[objective] = ordered[:num_top_techniques]
            return committed[objective]

        selector = MagicMock(spec=TechniqueSelector)
        selector.select_async.side_effect = select_async
        policy = IncompatibleTechniquePolicy.SKIP if with_context else IncompatibleTechniquePolicy.RAISE
        original = await self._initialize_scenario_async(
            target=mock_objective_target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups={"first_dataset": [sources[0]], "second_dataset": [sources[1]]},
            selector=selector,
            max_attempts=max_attempts,
            include_baseline=True,
            policy=policy,
        )
        scenario_result_id = original._scenario_result_id
        assert scenario_result_id is not None
        plan = original._build_run_plan()
        source_by_id = {source.logical_id: source for source in sources}
        for group in plan.atomic_groups:
            if group.kind is ScenarioRunPlanGroupKind.ADAPTIVE:
                source = source_by_id[group.seed_group_ids[0]]
                assert group.selected_technique_eval_hashes == committed[source.objective.value]

        await sqlite_instance.add_attack_results_to_memory_async(
            attack_results=[
                AttackResult(
                    conversation_id=str(uuid.uuid4()),
                    objective="historical evaluation",
                    outcome=AttackOutcome.FAILURE,
                    executed_turns=1,
                    atomic_attack_identifier=AtomicAttackIdentifier.build(
                        attack_identifier=_mock_id("HistoricalAttack")
                    ).with_eval_hash(committed["first"][0]),
                )
            ]
        )
        restored_selector = MagicMock(spec=TechniqueSelector)
        restored_selector.select_async.side_effect = AssertionError("Resume selected techniques again")
        extra_source = AttackSeedGroup(seeds=[SeedObjective(value="not sampled"), SeedPrompt(value="extra input")])
        resumed = await self._initialize_scenario_async(
            target=mock_objective_target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups={
                "second_dataset": [sources[1].model_copy(deep=True)],
                "first_dataset": [extra_source, sources[0].model_copy(deep=True)],
            },
            selector=restored_selector,
            scenario_result_id=scenario_result_id,
            max_attempts=max_attempts,
            include_baseline=True,
            policy=policy,
        )

        restored_selector.select_async.assert_not_awaited()
        assert selector.select_async.await_count == 2
        assert resumed._build_run_plan().model_dump(mode="json", exclude_none=True) == plan.model_dump(
            mode="json", exclude_none=True
        )
        original_sequences = [
            [child.strategy for child in atomic.attack_technique.attack._child_attacks]
            for atomic in original._atomic_attacks
            if atomic.group_kind is ScenarioRunPlanGroupKind.ADAPTIVE
        ]
        resumed_sequences = [
            [child.strategy for child in atomic.attack_technique.attack._child_attacks]
            for atomic in resumed._atomic_attacks
            if atomic.group_kind is ScenarioRunPlanGroupKind.ADAPTIVE
        ]
        assert resumed_sequences == original_sequences
        assert [source.model_dump() for source in sources] == snapshots
        [stored] = await original._memory.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
        assert stored.metadata["run_plan"] == plan.model_dump(mode="json", exclude_none=True)

    async def test_resume_keeps_completed_envelopes_after_restoring_choices_async(
        self, *, mock_objective_scorer: MagicMock
    ) -> None:
        target = MockPromptTarget()
        factories = {
            "many_shot": _make_fake_factory(
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                )
            ),
            "role_play_movie_script": _make_fake_factory(),
        }
        for factory in factories.values():
            factory.create.return_value = AttackTechnique(
                attack=PromptSendingAttack(objective_target=target),
                requirements=factory.requirements,
            )
        selector = MagicMock(spec=TechniqueSelector)
        selector.select_async.side_effect = lambda **kwargs: list(kwargs["technique_identifiers"])
        groups = {"dataset": [_make_seed_group(value=value) for value in ["first", "second"]]}
        original = await self._initialize_scenario_async(
            target=target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups=groups,
            selector=selector,
        )
        scenario_result_id = original._scenario_result_id
        assert scenario_result_id is not None
        first_atomic = original._atomic_attacks[0]
        first_atomic.set_scenario_result_id(scenario_result_id)
        first_result = await first_atomic.run_async()
        assert not first_result.has_incomplete
        assert target.prompt_sent == ["first", "first"]

        restored_selector = MagicMock(spec=TechniqueSelector)
        restored_selector.select_async.side_effect = AssertionError("Resume selected techniques again")
        resumed = await self._initialize_scenario_async(
            target=target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups=groups,
            selector=restored_selector,
            scenario_result_id=scenario_result_id,
        )
        result = await resumed.run_async()

        restored_selector.select_async.assert_not_awaited()
        assert str(result.id) == scenario_result_id
        assert target.prompt_sent == ["first", "first", "second", "second"]

    async def test_resume_rejects_changed_saved_technique_even_with_alternative_async(
        self, *, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock
    ) -> None:
        factories = {
            "many_shot": _make_fake_factory(
                requirements=TechniqueRequirements(
                    seed_group=SeedGroupRequirements(objective_only=True, try_adapt=True)
                )
            ),
            "role_play_movie_script": _make_fake_factory(),
        }
        selector = MagicMock(spec=TechniqueSelector)
        selector.select_async.side_effect = lambda **kwargs: list(kwargs["technique_identifiers"])
        groups = {"dataset": [_make_seed_group(value="objective")]}
        original = await self._initialize_scenario_async(
            target=mock_objective_target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups=groups,
            selector=selector,
        )
        factories["many_shot"] = _make_fake_factory(requirements=factories["many_shot"].requirements)
        selector.select_async.reset_mock()
        selector.select_async.side_effect = AssertionError("Resume selected techniques again")

        with pytest.raises(ValueError, match="Selected adaptive techniques are unavailable or incompatible"):
            await self._initialize_scenario_async(
                target=mock_objective_target,
                scorer=mock_objective_scorer,
                factories=factories,
                groups=groups,
                selector=selector,
                scenario_result_id=original._scenario_result_id,
            )
        selector.select_async.assert_not_awaited()

    @pytest.mark.parametrize("missing_data", ["choices", "run_plan"])
    async def test_resume_rejects_missing_saved_choices_async(
        self,
        *,
        missing_data: str,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
    ) -> None:
        factories = {"many_shot": _make_fake_factory()}
        selector = MagicMock(spec=TechniqueSelector)
        selector.select_async.side_effect = lambda **kwargs: list(kwargs["technique_identifiers"])
        groups = {"dataset": [_make_seed_group(value="objective")]}
        original = await self._initialize_scenario_async(
            target=mock_objective_target,
            scorer=mock_objective_scorer,
            factories=factories,
            groups=groups,
            selector=selector,
        )
        scenario_result_id = original._scenario_result_id
        assert scenario_result_id is not None
        [stored] = await original._memory.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
        metadata = dict(stored.metadata)
        if missing_data == "choices":
            del metadata["run_plan"]["atomic_groups"][0]["selected_technique_eval_hashes"]
        else:
            del metadata["run_plan"]
        await original._memory.update_scenario_metadata_async(
            scenario_result_id=scenario_result_id,
            metadata=metadata,
        )
        selector.select_async.reset_mock()
        selector.select_async.side_effect = AssertionError("Resume selected techniques again")

        with pytest.raises(ValueError, match="no saved adaptive technique choices"):
            await self._initialize_scenario_async(
                target=mock_objective_target,
                scorer=mock_objective_scorer,
                factories=factories,
                groups=groups,
                selector=selector,
                scenario_result_id=scenario_result_id,
            )
        selector.select_async.assert_not_awaited()
