# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Psychosocial scenario (per-subharm scoring + technique-axis strategies)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import CrescendoAttack, PromptSendingAttack, RolePlayAttack
from pyrit.models import ComponentIdentifier, SeedAttackGroup, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.scenarios.airt.psychosocial import (
    _SUBHARMS,
    Psychosocial,
    _psychosocial_techniques,
)
from pyrit.scenario.scenarios.airt.psychosocial import (
    PsychosocialStrategy as _PsychosocialStrategy,
)
from pyrit.score import TrueFalseScorer


def _strategy_class():
    """Return the module-level PsychosocialStrategy class (typed callable for ty)."""
    return _PsychosocialStrategy


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _make_subharm_seed_groups() -> dict[str, list[SeedAttackGroup]]:
    """Mirror the live (split) dataset shape: 2 imminent_crisis seeds + 1 licensed_therapist seed."""
    return {
        "airt_imminent_crisis": [
            SeedAttackGroup(seeds=[SeedObjective(value="crisis seed A", harm_categories=["imminent_crisis"])]),
            SeedAttackGroup(seeds=[SeedObjective(value="crisis seed B", harm_categories=["imminent_crisis"])]),
        ],
        "airt_licensed_therapist": [
            SeedAttackGroup(seeds=[SeedObjective(value="therapist seed", harm_categories=["licensed_therapist"])]),
        ],
    }


def _patch_seed_groups(groups):
    """Patch the base seed resolution so ``context.seed_groups_by_dataset`` returns ``groups``."""
    return patch.object(
        Psychosocial, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock, return_value=groups
    )


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    mock.capabilities.includes.return_value = True
    return mock


@pytest.fixture(autouse=True)
def register_default_targets():
    """Register mock adversarial + scorer targets so default-target resolution avoids OpenAIChatTarget."""
    TargetRegistry.reset_registry_singleton()
    adv = MagicMock(spec=PromptTarget)
    adv.capabilities.includes.return_value = True
    scorer_chat = MagicMock(spec=PromptTarget)
    scorer_chat.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(adv, name="adversarial_chat")
    registry.instances.register(scorer_chat, name="objective_scorer_chat")
    yield
    TargetRegistry.reset_registry_singleton()


@pytest.fixture(autouse=True)
def patch_build_scorer():
    """Return a distinct mock scorer per call so real scorer/target construction is avoided.

    The scenario builds one scorer per subharm (and reuses it across that subharm's technique
    attacks + baseline), so distinct return values let the routing test assert that the two
    subharms use different scorer instances.
    """

    def _fresh_scorer(**_kwargs):
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.get_identifier.return_value = _mock_id("MockSubharmScorer")
        return scorer

    with patch.object(Psychosocial, "_build_scorer", side_effect=_fresh_scorer):
        yield


FIXTURES = ["patch_central_database"]


# ===========================================================================
# Strategy enum shape
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialStrategyEnum:
    def test_default_expands_to_single_turn_techniques(self):
        strat = _strategy_class()
        default_members = {m.value for m in strat.expand({strat("default")})}
        assert default_members == {"prompt_sending", "role_play"}

    def test_all_includes_crescendo(self):
        strat = _strategy_class()
        all_members = {m.value for m in strat.expand({strat("all")})}
        assert all_members == {"prompt_sending", "role_play", "crescendo"}

    def test_crescendo_is_out_of_default(self):
        strat = _strategy_class()
        default_members = {m.value for m in strat.expand({strat("default")})}
        assert "crescendo" not in default_members


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialTechniques:
    def test_three_techniques_by_name(self):
        by_name = {f.name for f in _psychosocial_techniques()}
        assert by_name == {"prompt_sending", "role_play", "crescendo"}

    def test_factories_with_target_wire_adversarial(self):
        adv = MagicMock(spec=PromptTarget)
        by_name = {f.name: f for f in _psychosocial_techniques(adversarial_chat=adv)}
        assert by_name["role_play"].adversarial_chat is adv
        assert by_name["crescendo"].adversarial_chat is adv

    def test_attack_classes(self):
        by_name = {f.name: f for f in _psychosocial_techniques()}
        assert by_name["prompt_sending"].attack_class is PromptSendingAttack
        assert by_name["role_play"].attack_class is RolePlayAttack
        assert by_name["crescendo"].attack_class is CrescendoAttack


# ===========================================================================
# Subharm configuration
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestSubharmConfigs:
    def test_two_subharms(self):
        assert {c.dataset_name for c in _SUBHARMS} == {"airt_imminent_crisis", "airt_licensed_therapist"}

    def test_distinct_scorer_prompts(self):
        prompts = [c.scorer_system_prompt for c in _SUBHARMS]
        assert prompts[0] != prompts[1]

    def test_distinct_crescendo_paths(self):
        paths = {c.crescendo_escalation_path.name for c in _SUBHARMS}
        assert paths == {"escalation_crisis.yaml", "therapist.yaml"}


# ===========================================================================
# Initialization / construction
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialInitialization:
    def test_no_arg_construct_works(self):
        """Registry metadata introspection instantiates with no args."""
        scenario = Psychosocial()
        assert scenario is not None

    def test_version_is_3(self):
        assert Psychosocial.VERSION == 3

    def test_default_strategy_is_default(self):
        strat = _strategy_class()
        assert Psychosocial()._default_strategy == strat("default")

    def test_default_dataset_config_has_both_subharms(self):
        config = Psychosocial()._default_dataset_config
        assert isinstance(config, DatasetAttackConfiguration)
        assert set(config.dataset_names) == {"airt_imminent_crisis", "airt_licensed_therapist"}

    def test_custom_adversarial_chat_stored(self):
        adv = MagicMock(spec=PromptTarget)
        assert Psychosocial(adversarial_chat=adv)._adversarial_chat is adv


# ===========================================================================
# dataset_config validation
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialDatasetConfigValidation:
    async def test_no_dataset_config_uses_defaults(self, mock_objective_target):
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        assert len(scenario._atomic_attacks) > 0

    async def test_subset_one_subharm_allowed(self, mock_objective_target):
        scenario = Psychosocial()
        cfg = DatasetAttackConfiguration(dataset_names=["airt_imminent_crisis"], max_dataset_size=1)
        groups = {"airt_imminent_crisis": _make_subharm_seed_groups()["airt_imminent_crisis"]}
        with _patch_seed_groups(groups):
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=cfg)
        assert len(scenario._atomic_attacks) > 0

    async def test_custom_dataset_name_rejected(self, mock_objective_target):
        scenario = Psychosocial()
        cfg = DatasetAttackConfiguration(dataset_names=["some_other_dataset"])
        with pytest.raises(ValueError, match="tied to its subharms"):
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=cfg)

    async def test_mixed_valid_and_invalid_name_rejected(self, mock_objective_target):
        scenario = Psychosocial()
        cfg = DatasetAttackConfiguration(dataset_names=["airt_imminent_crisis", "evil"])
        with pytest.raises(ValueError, match="invalid dataset name"):
            await scenario.initialize_async(objective_target=mock_objective_target, dataset_config=cfg)


# ===========================================================================
# Cross-product build + per-subharm scoring
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialCrossProduct:
    async def test_default_yields_4_technique_attacks_plus_2_baselines(self, mock_objective_target):
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        names = [a.atomic_attack_name for a in scenario._atomic_attacks]
        technique_names = [n for n in names if not n.startswith("baseline")]
        baseline_names = [n for n in names if n.startswith("baseline")]
        # 2 techniques x 2 subharms
        assert sorted(technique_names) == [
            "prompt_sending_imminent_crisis",
            "prompt_sending_licensed_therapist",
            "role_play_imminent_crisis",
            "role_play_licensed_therapist",
        ]
        assert len(baseline_names) == 2

    async def test_all_yields_6_technique_attacks_plus_2_baselines(self, mock_objective_target):
        strat = _strategy_class()
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target, scenario_strategies=[strat("all")])
        names = [a.atomic_attack_name for a in scenario._atomic_attacks]
        technique_names = [n for n in names if not n.startswith("baseline")]
        assert len(technique_names) == 6  # 3 techniques x 2 subharms
        assert len([n for n in names if n.startswith("baseline")]) == 2

    async def test_per_subharm_baselines_named_and_flagged(self, mock_objective_target):
        """Each per-subharm baseline is named 'baseline_<subharm>' and flagged is_baseline."""
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        baselines = [a for a in scenario._atomic_attacks if a.is_baseline]
        baseline_names = {a.atomic_attack_name for a in baselines}
        assert baseline_names == {"baseline_imminent_crisis", "baseline_licensed_therapist"}
        # No generic 'baseline' — the base central guard recognizes is_baseline and does not double-prepend.
        assert "baseline" not in baseline_names
        assert len(baselines) == 2

    async def test_baselines_are_first(self, mock_objective_target):
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        assert scenario._atomic_attacks[0].is_baseline is True
        assert scenario._atomic_attacks[0].atomic_attack_name.startswith("baseline_")
        # Strategy (non-baseline) attacks are not flagged.
        assert all(
            not a.is_baseline for a in scenario._atomic_attacks if not a.atomic_attack_name.startswith("baseline")
        )

    async def test_include_baseline_false_emits_no_baselines(self, mock_objective_target):
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target, include_baseline=False)
        names = [a.atomic_attack_name for a in scenario._atomic_attacks]
        assert all(not n.startswith("baseline") for n in names)
        assert len(names) == 4  # 2 techniques x 2 subharms, no baselines

    async def test_per_subharm_scorer_routing(self, mock_objective_target):
        """Each subharm's attacks share one scorer; the two subharms use DIFFERENT scorers.

        This is the fix for main's wrong-scorer-on-ALL bug, where every subharm was scored
        with the crisis-rubric fallback.
        """
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        crisis_scorers = {
            id(a._objective_scorer) for a in scenario._atomic_attacks if a.display_group == "imminent_crisis"
        }
        therapist_scorers = {
            id(a._objective_scorer) for a in scenario._atomic_attacks if a.display_group == "licensed_therapist"
        }
        assert len(crisis_scorers) == 1
        assert len(therapist_scorers) == 1
        assert crisis_scorers.isdisjoint(therapist_scorers)

    async def test_display_group_matches_subharm(self, mock_objective_target):
        scenario = Psychosocial()
        with _patch_seed_groups(_make_subharm_seed_groups()):
            await scenario.initialize_async(objective_target=mock_objective_target)
        groups = {a.display_group for a in scenario._atomic_attacks}
        assert groups == {"imminent_crisis", "licensed_therapist"}

    async def test_only_therapist_subharm_single_baseline(self, mock_objective_target):
        """Subset selection edge: only therapist has seeds -> exactly one baseline for it.

        Guards against the central guard double-prepending a crisis-scored generic baseline. The
        per-subharm baseline is flagged is_baseline, so the base guard recognizes it and does not
        add another.
        """
        scenario = Psychosocial()
        groups = {"airt_licensed_therapist": _make_subharm_seed_groups()["airt_licensed_therapist"]}
        with _patch_seed_groups(groups):
            await scenario.initialize_async(objective_target=mock_objective_target)
        baselines = [a for a in scenario._atomic_attacks if a.is_baseline]
        assert [a.atomic_attack_name for a in baselines] == ["baseline_licensed_therapist"]
        assert scenario._atomic_attacks[0].is_baseline is True
        assert scenario._atomic_attacks[0].display_group == "licensed_therapist"

    async def test_no_seeds_for_any_subharm_raises_clear_error(self, mock_objective_target):
        """All selected subharms empty -> a clear error, not the base 'seed_groups cannot be empty'."""
        scenario = Psychosocial()
        with _patch_seed_groups({}):
            with pytest.raises(ValueError, match="No seed groups were loaded for any selected psychosocial subharm"):
                await scenario.initialize_async(objective_target=mock_objective_target)
