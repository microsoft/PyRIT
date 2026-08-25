# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Psychosocial scenario (per-sub-harm simulated crescendo swept across converters)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.converter import (
    Converter,
    InsertPunctuationConverter,
    PersuasionConverter,
    ToneConverter,
    TranslationConverter,
)
from pyrit.memory import CentralMemory
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.scenario.core.dataset_configuration import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
)
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.scenarios.airt.psychosocial import (
    _SUB_HARMS,
    Psychosocial,
    PsychosocialTechnique,
    _converter_for_technique,
)
from pyrit.score import TrueFalseScorer


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


def _make_seed_groups() -> dict[str, list[AttackSeedGroup]]:
    """Mirror the split dataset shape: 2 imminent_crisis seeds + 1 licensed_therapist seed."""
    return {
        "airt_imminent_crisis": [
            AttackSeedGroup(seeds=[SeedObjective(value="crisis seed A", harm_categories=["imminent_crisis"])]),
            AttackSeedGroup(seeds=[SeedObjective(value="crisis seed B", harm_categories=["imminent_crisis"])]),
        ],
        "airt_licensed_therapist": [
            AttackSeedGroup(seeds=[SeedObjective(value="therapist seed", harm_categories=["licensed_therapist"])]),
        ],
    }


def _patch_base_seed_groups(groups):
    """Patch the BASE loader so the scenario's own override still runs (hard-binding datasets)."""
    return patch.object(
        Scenario,
        "_resolve_seed_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value=groups,
    )


def _mock_scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = _mock_id("MockHarmScorer")
    return scorer


def _scenario_with_mock_scorers(**kwargs) -> Psychosocial:
    """Construct Psychosocial with distinct mock per-harm scorers (avoids real scorer plumbing)."""
    return Psychosocial(
        imminent_crisis_scorer=_mock_scorer(),
        licensed_therapist_scorer=_mock_scorer(),
        **kwargs,
    )


def _non_baseline(scenario: Psychosocial):
    return [a for a in scenario._atomic_attacks if not a.atomic_attack_name.endswith("_baseline")]


def _baselines(scenario: Psychosocial):
    return [a for a in scenario._atomic_attacks if a.atomic_attack_name.endswith("_baseline")]


def _attack_scorer(atomic_attack):
    """The scorer actually used by the underlying attack (set on the inner attack)."""
    return atomic_attack._attack_technique.attack._objective_scorer


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
    adv.get_identifier.return_value = _mock_id("MockAdversarial")
    adv.capabilities.includes.return_value = True
    scorer_chat = MagicMock(spec=PromptTarget)
    scorer_chat.get_identifier.return_value = _mock_id("MockScorerChat")
    scorer_chat.capabilities.includes.return_value = True
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(adv, name="adversarial_chat")
    registry.instances.register(scorer_chat, name="objective_scorer_chat")
    yield
    TargetRegistry.reset_registry_singleton()


FIXTURES = ["patch_central_database"]


# ===========================================================================
# Sub-harm configuration
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestSubHarmConfigs:
    def test_two_sub_harms(self):
        assert {h.name for h in _SUB_HARMS} == {"imminent_crisis", "licensed_therapist"}

    def test_sub_harm_datasets(self):
        assert {h.dataset_name for h in _SUB_HARMS} == {
            "airt_imminent_crisis",
            "airt_licensed_therapist",
        }

    def test_distinct_escalation_prompts(self):
        assert {h.escalation_prompt_path.name for h in _SUB_HARMS} == {
            "escalation_crisis_simulated.yaml",
            "therapist.yaml",
        }

    def test_escalation_prompts_exist(self):
        for harm in _SUB_HARMS:
            assert harm.escalation_prompt_path.exists()

    def test_each_sub_harm_builds_its_own_scorer(self):
        """Each sub-harm owns a build_scorer that produces a scorer from the chat target."""
        target = MagicMock(spec=PromptTarget)
        for harm in _SUB_HARMS:
            scorer = harm.build_scorer(target)
            assert scorer is not None
            assert hasattr(scorer, "get_identifier")


# ===========================================================================
# Technique (converter) enum shape
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialTechniqueEnum:
    def test_default_subset(self):
        default_members = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.DEFAULT})}
        assert default_members == {"none", "tone_soften", "persuasion_logical_appeal"}

    def test_all_expands_to_full_sweep(self):
        all_members = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.ALL})}
        # 23 converter members (incl. the bare `none` base) + the live `crescendo` technique.
        assert len(all_members) == 24
        assert "crescendo" in all_members
        assert "none" in all_members

    def test_family_aggregates_expand_to_their_members(self):
        tone = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.TONE})}
        assert tone == {"tone_soften", "tone_upset", "tone_angry", "tone_sad", "tone_urgent"}
        language = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.LANGUAGE})}
        assert language == {"language_spanish", "language_french", "language_german", "language_japanese"}
        persuasion = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.PERSUASION})}
        assert persuasion == {
            "persuasion_logical_appeal",
            "persuasion_authority_endorsement",
            "persuasion_evidence_based",
            "persuasion_expert_endorsement",
            "persuasion_misrepresentation",
        }
        deterministic = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.DETERMINISTIC})}
        assert deterministic == {
            "insert_punctuation",
            "random_capitalization",
            "diacritic",
            "char_swap",
            "colloquial_wordswap",
        }

    def test_default_is_subset_of_all(self):
        default_members = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.DEFAULT})}
        all_members = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.ALL})}
        assert default_members <= all_members

    def test_crescendo_out_of_default(self):
        default_members = {m.value for m in PsychosocialTechnique.expand({PsychosocialTechnique.DEFAULT})}
        assert "crescendo" not in default_members


# ===========================================================================
# Converter mapping
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestConverterMapping:
    def test_none_maps_to_no_converter(self):
        assert _converter_for_technique(PsychosocialTechnique.NoConverter, adversarial_chat=MagicMock()) is None

    def test_tone_soften_maps_to_tone_converter(self):
        conv = _converter_for_technique(PsychosocialTechnique.ToneSoften, adversarial_chat=MagicMock(spec=PromptTarget))
        assert isinstance(conv, ToneConverter)

    def test_persuasion_maps_to_persuasion_converter(self):
        conv = _converter_for_technique(
            PsychosocialTechnique.PersuasionLogicalAppeal,
            adversarial_chat=MagicMock(spec=PromptTarget),
        )
        assert isinstance(conv, PersuasionConverter)

    def test_language_maps_to_translation_converter(self):
        conv = _converter_for_technique(
            PsychosocialTechnique.LanguageSpanish,
            adversarial_chat=MagicMock(spec=PromptTarget),
        )
        assert isinstance(conv, TranslationConverter)

    def test_deterministic_maps_without_target(self):
        conv = _converter_for_technique(PsychosocialTechnique.InsertPunctuation, adversarial_chat=MagicMock())
        assert isinstance(conv, InsertPunctuationConverter)

    def test_all_converter_techniques_build(self):
        """Every converter technique (all but the live Crescendo) yields a Converter or None."""
        adv = MagicMock(spec=PromptTarget)
        for technique in PsychosocialTechnique.expand({PsychosocialTechnique.ALL}):
            if technique is PsychosocialTechnique.Crescendo:
                continue
            conv = _converter_for_technique(technique, adversarial_chat=adv)
            assert conv is None or isinstance(conv, Converter)

    def test_crescendo_is_not_a_converter_technique(self):
        with pytest.raises(ValueError, match="Not a psychosocial converter technique"):
            _converter_for_technique(PsychosocialTechnique.Crescendo, adversarial_chat=MagicMock())


# ===========================================================================
# Construction / parameters
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialConstruction:
    def test_no_arg_construct_works(self):
        """Registry metadata introspection instantiates with no args."""
        assert Psychosocial() is not None

    def test_version_is_3(self):
        assert Psychosocial.VERSION == 3

    def test_default_technique_is_default(self):
        assert _scenario_with_mock_scorers()._default_technique == PsychosocialTechnique.DEFAULT

    def test_default_dataset_config_has_both_sub_harms(self):
        config = _scenario_with_mock_scorers()._default_dataset_config
        assert isinstance(config, DatasetAttackConfiguration)
        assert set(config.dataset_names) == {
            "airt_imminent_crisis",
            "airt_licensed_therapist",
        }

    def test_custom_adversarial_chat_stored(self):
        adv = MagicMock(spec=PromptTarget)
        assert _scenario_with_mock_scorers(adversarial_chat=adv)._adversarial_chat is adv

    def test_passed_in_scorers_stored(self):
        crisis, therapist = _mock_scorer(), _mock_scorer()
        scenario = Psychosocial(imminent_crisis_scorer=crisis, licensed_therapist_scorer=therapist)
        assert scenario._scorers_by_harm["imminent_crisis"] is crisis
        assert scenario._scorers_by_harm["licensed_therapist"] is therapist

    def test_injected_scorers_skip_default_scorer_target(self):
        """Regression: injecting both sub-harm scorers must not resolve the default scorer target,
        so callers without scorer-target configuration can still construct the scenario."""
        with patch("pyrit.scenario.scenarios.airt.psychosocial.get_default_scorer_target") as mock_target:
            Psychosocial(imminent_crisis_scorer=_mock_scorer(), licensed_therapist_scorer=_mock_scorer())
        mock_target.assert_not_called()

    def test_missing_scorer_resolves_default_scorer_target(self):
        """When a sub-harm scorer is omitted, the default scorer target is resolved once to build it."""
        with patch(
            "pyrit.scenario.scenarios.airt.psychosocial.get_default_scorer_target",
            return_value=MagicMock(spec=PromptTarget),
        ) as mock_target:
            scenario = Psychosocial(imminent_crisis_scorer=_mock_scorer())
        mock_target.assert_called_once()
        assert scenario._scorers_by_harm["licensed_therapist"] is not None

    def test_supported_parameters_keeps_dataset_config(self):
        """dataset_config is retained so --max-dataset-size still works (names are hard-bound)."""
        names = {p.name for p in Psychosocial.supported_parameters()}
        assert "dataset_config" in names

    def test_additional_parameters_add_sub_harm_and_max_turns(self):
        names = {p.name for p in Psychosocial.additional_parameters()}
        assert {"sub_harm", "max_turns"} == names

    def test_sub_harm_default_is_all(self):
        param = next(p for p in Psychosocial.additional_parameters() if p.name == "sub_harm")
        assert param.default == "all"
        assert param.param_type is str


# ===========================================================================
# Sub-harm selection
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestSubHarmSelection:
    def test_default_selects_both(self):
        scenario = _scenario_with_mock_scorers()
        scenario.set_params_from_args(args={"objective_target": MagicMock(spec=PromptTarget)})
        assert [h.name for h in scenario._selected_sub_harms()] == [
            "imminent_crisis",
            "licensed_therapist",
        ]

    def test_single_sub_harm_string(self):
        scenario = _scenario_with_mock_scorers()
        scenario.set_params_from_args(
            args={
                "objective_target": MagicMock(spec=PromptTarget),
                "sub_harm": "licensed_therapist",
            }
        )
        assert [h.name for h in scenario._selected_sub_harms()] == ["licensed_therapist"]

    def test_all_selects_both(self):
        scenario = _scenario_with_mock_scorers()
        scenario.set_params_from_args(args={"objective_target": MagicMock(spec=PromptTarget), "sub_harm": "all"})
        assert [h.name for h in scenario._selected_sub_harms()] == [
            "imminent_crisis",
            "licensed_therapist",
        ]

    def test_invalid_sub_harm_raises(self):
        scenario = _scenario_with_mock_scorers()
        scenario.set_params_from_args(args={"objective_target": MagicMock(spec=PromptTarget), "sub_harm": "bogus"})
        with pytest.raises(ValueError, match="Unknown psychosocial sub_harm 'bogus'"):
            scenario._selected_sub_harms()


# ===========================================================================
# Cross product build + per-harm scoring
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialCrossProduct:
    async def test_default_yields_6_attacks_plus_2_baselines(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        # DEFAULT techniques (none, tone_soften, persuasion_logical_appeal) x 2 sub-harms = 6.
        assert sorted(a.atomic_attack_name for a in _non_baseline(scenario)) == [
            "imminent_crisis_none",
            "imminent_crisis_persuasion_logical_appeal",
            "imminent_crisis_tone_soften",
            "licensed_therapist_none",
            "licensed_therapist_persuasion_logical_appeal",
            "licensed_therapist_tone_soften",
        ]
        assert len(_baselines(scenario)) == 2

    async def test_all_converters_yield_full_sweep(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "scenario_techniques": [PsychosocialTechnique.ALL],
                }
            )
            await scenario.initialize_async()
        # 24 techniques x 2 sub-harms = 48.
        assert len(_non_baseline(scenario)) == 48
        assert len(_baselines(scenario)) == 2

    async def test_single_sub_harm_only_builds_that_harm(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "sub_harm": "imminent_crisis",
                }
            )
            await scenario.initialize_async()
        # Only the imminent_crisis sub-harm is built, with its own per-harm baseline.
        assert {a.display_group for a in scenario._atomic_attacks} == {"imminent_crisis"}
        assert [a.atomic_attack_name for a in _baselines(scenario)] == ["imminent_crisis_baseline"]

    async def test_single_sub_harm_hard_binds_dataset_config(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "sub_harm": "licensed_therapist",
                }
            )
            await scenario.initialize_async()
        assert list(scenario._dataset_config.dataset_names) == ["airt_licensed_therapist"]

    async def test_max_dataset_size_applied_per_sub_harm_when_hard_binding(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": DatasetAttackConfiguration(dataset_names=["ignored"], max_dataset_size=7),
                }
            )
            await scenario.initialize_async()
        # --max-dataset-size is a PER-sub-harm budget: each child caps at 7 and the parent cap is
        # 7 x 2 sub-harms (never trims the union, yet stays non-None so resume pinning survives).
        assert isinstance(scenario._dataset_config, CompoundDatasetAttackConfiguration)
        assert all(child.max_dataset_size == 7 for child in scenario._dataset_config._configurations)
        assert scenario._dataset_config.max_dataset_size == 14
        assert set(scenario._dataset_config.dataset_names) == {
            "airt_imminent_crisis",
            "airt_licensed_therapist",
        }

    async def test_max_dataset_size_one_keeps_both_sub_harms(self, mock_objective_target):
        """A global budget of 1 starves a sub-harm; the per-sub-harm compound keeps both.

        Patches only the seed source so the REAL dataset resolver/sampler runs -- the starvation
        regression cannot hide behind a mocked base resolver.
        """
        seeds_by_dataset = {
            "airt_imminent_crisis": [
                SeedObjective(value="crisis A", harm_categories=["imminent_crisis"]),
                SeedObjective(value="crisis B", harm_categories=["imminent_crisis"]),
            ],
            "airt_licensed_therapist": [
                SeedObjective(value="therapist A", harm_categories=["licensed_therapist"]),
                SeedObjective(value="therapist B", harm_categories=["licensed_therapist"]),
            ],
        }

        def _get_seeds(*, dataset_name, **_):
            return list(seeds_by_dataset.get(dataset_name, []))

        memory = CentralMemory.get_memory_instance()
        scenario = _scenario_with_mock_scorers()
        with patch.object(memory, "get_seeds", side_effect=_get_seeds):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "sub_harm": "all",
                    "scenario_techniques": [PsychosocialTechnique.NoConverter],
                    "dataset_config": DatasetAttackConfiguration(
                        dataset_names=["ignored"], max_dataset_size=1, auto_fetch=False
                    ),
                }
            )
            await scenario.initialize_async()

        # Per-sub-harm compound: each child budget is 1, parent cap = 1 x 2 (non-None so the base
        # still pins the sampled objective subset for resume).
        assert isinstance(scenario._dataset_config, CompoundDatasetAttackConfiguration)
        assert scenario._dataset_config.max_dataset_size == 2
        # Both sub-harms survive the budget-of-1 (the global-budget bug dropped one entirely).
        assert {a.display_group for a in _non_baseline(scenario)} == {"imminent_crisis", "licensed_therapist"}
        assert {a.atomic_attack_name for a in _baselines(scenario)} == {
            "imminent_crisis_baseline",
            "licensed_therapist_baseline",
        }

    async def test_display_group_matches_sub_harm(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        assert {a.display_group for a in scenario._atomic_attacks} == {
            "imminent_crisis",
            "licensed_therapist",
        }

    async def test_per_harm_scorer_routing(self, mock_objective_target):
        """Each sub-harm's attacks use that sub-harm's scorer; the two use different scorers."""
        crisis, therapist = _mock_scorer(), _mock_scorer()
        scenario = Psychosocial(imminent_crisis_scorer=crisis, licensed_therapist_scorer=therapist)
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        for attack in scenario._atomic_attacks:
            expected = crisis if attack.display_group == "imminent_crisis" else therapist
            assert _attack_scorer(attack) is expected

    async def test_attacks_carry_adversarial_and_scorer(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        for attack in _non_baseline(scenario):
            assert attack._adversarial_chat is not None
            assert attack._objective_scorer is not None

    async def test_no_seeds_raises_clear_error(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups({}):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            with pytest.raises(
                ValueError,
                match="No seed groups were loaded for any selected psychosocial sub-harm",
            ):
                await scenario.initialize_async()


# ===========================================================================
# Baselines (per-sub-harm, emitted by the scenario)
# ===========================================================================


@pytest.mark.usefixtures(*FIXTURES)
class TestPsychosocialBaselines:
    async def test_baselines_named_per_sub_harm(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        assert {a.atomic_attack_name for a in _baselines(scenario)} == {
            "imminent_crisis_baseline",
            "licensed_therapist_baseline",
        }

    async def test_baselines_are_prepended(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        assert scenario._atomic_attacks[0].atomic_attack_name.endswith("_baseline")

    async def test_no_generic_baseline(self, mock_objective_target):
        """The base must not also prepend a generic 'baseline' on top of the per-harm ones."""
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        assert "baseline" not in {a.atomic_attack_name for a in scenario._atomic_attacks}

    async def test_include_baseline_false_emits_no_baselines(self, mock_objective_target):
        scenario = _scenario_with_mock_scorers()
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()
        assert _baselines(scenario) == []
        assert len(scenario._atomic_attacks) == 6

    async def test_per_harm_baseline_uses_harm_scorer(self, mock_objective_target):
        crisis, therapist = _mock_scorer(), _mock_scorer()
        scenario = Psychosocial(imminent_crisis_scorer=crisis, licensed_therapist_scorer=therapist)
        with _patch_base_seed_groups(_make_seed_groups()):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.initialize_async()
        by_group = {a.display_group: a for a in _baselines(scenario)}
        assert _attack_scorer(by_group["imminent_crisis"]) is crisis
        assert _attack_scorer(by_group["licensed_therapist"]) is therapist
