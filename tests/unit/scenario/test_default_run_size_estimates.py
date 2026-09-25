# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration-only previews stay separate from exact initialized run plans."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.services.scenario_progress_read_model import ScenarioProgressReadModel
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioRunPlan,
    ScenarioRunSizeEstimateCondition,
    ScenarioRunSizeEstimateStatus,
    SeedObjective,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core import (
    AtomicAttack,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.scenario.scenarios.foundry.red_team_agent import FoundryComposite, FoundryTechnique, RedTeamAgent
from pyrit.scenario.scenarios.garak.api_key import ApiKey
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.exploitation import Exploitation
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination, PackageHallucinationTechnique
from pyrit.scenario.scenarios.garak.system_prompt_extraction import (
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
)
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionTechnique
from pyrit.score import TrueFalseScorer
from tests.unit.mocks import MockPromptTarget


class _TwoTechniqueDefault(ScenarioTechnique):
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    ONE = ("one", {"default"})
    TWO = ("two", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "default"}

    @classmethod
    def default(cls) -> "_TwoTechniqueDefault":
        return cls.DEFAULT


class _JailbreakDefault(ScenarioTechnique):
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    PROMPT_SENDING = ("prompt_sending", {"default"})
    SYSTEM_PROMPT = ("jailbreak_system_prompt", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "default"}

    @classmethod
    def default(cls) -> "_JailbreakDefault":
        return cls.DEFAULT


def _scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="MockScorer", class_module="test")
    return scorer


class _MatrixEstimateScenario(Scenario):
    def __init__(self) -> None:
        super().__init__(
            version=1,
            technique_class=_TwoTechniqueDefault,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["missing"]),
            objective_scorer=_scorer(),
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        names = [technique.value for technique in context.scenario_techniques]
        if context.include_baseline:
            names.append("baseline")
        return [
            AtomicAttack(
                atomic_attack_name=name,
                attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=context.objective_target)),
                seed_groups=list(context.seed_groups),
            )
            for name in names
        ]


@pytest.fixture(autouse=True)
def no_preview_dataset_reads() -> Iterator[None]:
    """Fail on dataset resolution rather than supplying fake populations."""
    with (
        patch.object(
            Scenario, "_resolve_seed_groups_by_dataset_async", side_effect=AssertionError("Preview resolved seeds")
        ),
        patch.object(
            DatasetAttackConfiguration,
            "_collect_named_seeds_async",
            side_effect=AssertionError("Preview queried datasets"),
        ),
    ):
        yield


@pytest.mark.usefixtures("patch_central_database")
async def test_default_estimate_uses_five_without_population_or_persistence_async() -> None:
    scenario = _MatrixEstimateScenario()
    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == 15
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None
    assert [component.count for component in estimate.components] == [10, 5]
    assert estimate.configured_dataset_size == 5
    assert estimate.datasets[0].logical_seed_group_count is None
    assert estimate.datasets[0].selected_seed_group_count is None
    assert estimate.datasets[0].configured_caps[0].count == 5
    assert scenario._memory.get_scenario_results() == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("baseline", "expected"), [(False, 7), (True, 14)])
async def test_configured_estimate_uses_selected_techniques_and_limit_async(*, baseline: bool, expected: int) -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.ONE],
            "include_baseline": baseline,
            "dataset_config": DatasetAttackConfiguration(dataset_names=["also-missing"], max_dataset_size=7),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == expected
    assert estimate.datasets[0].name == "also-missing"
    assert estimate.effective_parameters["max_dataset_size"] == 7


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_expands_aggregate_and_applies_combined_cap_once_async() -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.ALL],
            "include_baseline": False,
            "dataset_config": DatasetAttackConfiguration(dataset_names=["one", "two"], max_dataset_size=3),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 6
    assert estimate.configured_dataset_size == 3
    assert all(dataset.configured_caps[0].configured_on == "configuration" for dataset in estimate.datasets)


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_combines_independent_child_limits_async() -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "include_baseline": False,
            "dataset_config": CompoundDatasetAttackConfiguration.per_dataset(dataset_names=["one", "two"]),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.configured_dataset_size == 10
    assert estimate.estimated_attack_count == 20


@pytest.mark.usefixtures("patch_central_database")
async def test_unlimited_estimate_does_not_load_data_or_invent_a_count_async() -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={"dataset_config": DatasetAttackConfiguration(dataset_names=["missing"], max_dataset_size=None)}
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert "No size limit" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_preview_five_becomes_exact_three_in_persisted_run_plan_async() -> None:
    groups = [AttackSeedGroup(seeds=[SeedObjective(value=f"objective-{index}")]) for index in range(3)]
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "dataset_config": DatasetAttackConfiguration(seed_groups=groups),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 15
    with patch.object(scenario, "_resolve_seed_groups_by_dataset_async", return_value={"inline": groups}):
        await scenario.initialize_async()
    [stored] = scenario._memory.get_scenario_results(scenario_result_ids=[scenario._scenario_result_id])
    plan = stored.metadata[SCENARIO_RUN_PLAN_METADATA_KEY]
    assert sum(len(group["seed_group_ids"]) for group in plan["atomic_groups"]) == 9
    assert len(plan["seed_groups"]) == 3
    snapshot = ScenarioProgressReadModel(memory=scenario._memory).get_snapshot(
        scenario_result_id=scenario._scenario_result_id,
        plan=ScenarioRunPlan.model_validate(plan),
        plan_complete=True,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )
    assert snapshot.summary.overall.planned == 9
    assert snapshot.summary.overall.completed == 0


@pytest.mark.usefixtures("patch_central_database")
async def test_adaptive_estimate_preserves_envelopes_and_inner_attempt_limit_async() -> None:
    with patch.object(TextAdaptive, "get_technique_class", return_value=_TwoTechniqueDefault):
        scenario = TextAdaptive(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"include_baseline": False, "max_attempts_per_objective": 7})
    with patch.object(scenario, "_build_techniques_dict", side_effect=AssertionError("Loaded techniques")):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == scenario._get_run_size_budget()
    assert estimate.effective_parameters["max_attempts_per_objective"] == 7
    assert "7 selected technique attempts" in estimate.note


@pytest.fixture
def jailbreak() -> Jailbreak:
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        return Jailbreak(objective_scorer=_scorer())


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_default_keeps_target_capability_range_async(jailbreak: Jailbreak) -> None:
    estimate = await jailbreak.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count == 12
    assert estimate.maximum_attack_count == 20
    assert [component.count for component in estimate.components] == [4, 8, 8]
    assert estimate.effective_parameters["num_jailbreaks"] == 2


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("baseline", "expected"), [(False, 24), (True, 28)])
async def test_jailbreak_keeps_template_attempt_and_baseline_factors_async(
    *, jailbreak: Jailbreak, baseline: bool, expected: int
) -> None:
    jailbreak.set_params_from_args(
        args={
            "scenario_techniques": [_JailbreakDefault.PROMPT_SENDING],
            "include_baseline": baseline,
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 3,
        }
    )
    estimate = await jailbreak.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == expected


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_explicit_names_replace_template_count_async(jailbreak: Jailbreak) -> None:
    jailbreak.set_params_from_args(
        args={
            "scenario_techniques": [_JailbreakDefault.PROMPT_SENDING],
            "include_baseline": False,
            "jailbreak_names": ["aim.yaml", "dan.yaml", "third.yaml"],
            "num_jailbreak_attempts": 2,
        }
    )
    estimate = await jailbreak.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 24
    assert estimate.effective_parameters["jailbreak_names"] == ["aim.yaml", "dan.yaml", "third.yaml"]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("supports_system", [False, True])
async def test_jailbreak_system_delivery_validates_target_async(*, jailbreak: Jailbreak, supports_system: bool) -> None:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="Target", class_module="test")
    target.configuration.includes.return_value = supports_system
    jailbreak.set_params_from_args(
        args={
            "objective_target": target,
            "scenario_techniques": [_JailbreakDefault.SYSTEM_PROMPT],
            "include_baseline": False,
        }
    )
    if supports_system:
        estimate = await jailbreak.get_run_size_estimate_async()
        assert estimate.estimated_attack_count == 8
    else:
        with pytest.raises(ValueError, match="requires an objective target with editable history"):
            await jailbreak.get_run_size_estimate_async()
    target.send_prompt_async.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_encoding_keeps_converter_and_prompt_configuration_factors_async() -> None:
    estimate = await Encoding(objective_scorer=_scorer()).get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 20 * 15 * 5 + 20
    assert estimate.configured_dataset_size == 20


@pytest.mark.usefixtures("patch_central_database")
async def test_web_injection_estimate_does_not_load_or_synthesize_populations_async() -> None:
    scenario = WebInjection()
    with (
        patch.object(scenario, "_load_dataset_values", side_effect=AssertionError("Loaded dataset")),
        patch.object(scenario, "_build_synthesized_seed_groups", side_effect=AssertionError("Synthesized seeds")),
    ):
        estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert estimate.estimated_attack_count is None
    assert estimate.configured_dataset_size is None


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "technique",
    [
        WebInjectionTechnique.MarkdownImageExfil,
        WebInjectionTechnique.ColabAIDataLeakage,
        WebInjectionTechnique.PlaygroundMarkdownExfil,
        WebInjectionTechnique.MarkdownXSS,
    ],
)
async def test_web_injection_uncapped_sources_ignore_dataset_limit_async(technique: WebInjectionTechnique) -> None:
    scenario = WebInjection()
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [technique, WebInjectionTechnique.TaskXSS],
            "dataset_config": DatasetAttackConfiguration(dataset_names=["missing"], max_dataset_size=1),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("dataset_limit", [1, None])
async def test_web_injection_capped_techniques_use_generation_limits_async(
    *, baseline: bool, dataset_limit: int | None
) -> None:
    scenario = WebInjection(max_prompts_per_technique=7)
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [
                WebInjectionTechnique.StringAssemblyDataExfil,
                WebInjectionTechnique.MarkdownURIImageExfilExtended,
                WebInjectionTechnique.MarkdownURINonImageExfilExtended,
                WebInjectionTechnique.TaskXSS,
            ],
            "include_baseline": baseline,
            "dataset_config": DatasetAttackConfiguration(dataset_names=["missing"], max_dataset_size=dataset_limit),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    budget = len(scenario.STRING_ASSEMBLY_SEEDS) + 3 * 7
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == budget * (2 if baseline else 1)
    assert estimate.configured_dataset_size == budget
    assert estimate.effective_parameters == {"max_prompts_per_technique": 7}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("prompt_cap", [None, 3])
@pytest.mark.parametrize("technique", [PackageHallucinationTechnique.DEFAULT, PackageHallucinationTechnique.ALL])
@pytest.mark.parametrize("dataset_limit", [1, None])
async def test_package_hallucination_uses_per_language_generation_cap_async(
    *, prompt_cap: int | None, technique: PackageHallucinationTechnique, dataset_limit: int | None
) -> None:
    scenario = PackageHallucination(objective_scorer=_scorer(), max_prompts_per_language=prompt_cap)
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [technique],
            "dataset_config": DatasetAttackConfiguration(dataset_names=["missing"], max_dataset_size=dataset_limit),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    cap = 12 if prompt_cap is None else prompt_cap
    language_count = len(PackageHallucinationTechnique.expand({technique}))
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == cap * language_count
    assert estimate.configured_dataset_size == cap * language_count
    assert estimate.effective_parameters == {"max_prompts_per_language": cap}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("prompt_cap", [256, 7, None])
@pytest.mark.parametrize(
    "technique", [SystemPromptExtractionTechnique.ALL, SystemPromptExtractionTechnique.DirectRequests]
)
@pytest.mark.parametrize("dataset_limit", [1, None])
async def test_system_prompt_extraction_uses_one_shared_generation_cap_async(
    *, prompt_cap: int | None, technique: SystemPromptExtractionTechnique, dataset_limit: int | None
) -> None:
    scenario = SystemPromptExtraction(objective_scorer=_scorer(), prompt_cap=prompt_cap)
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [technique],
            "dataset_config": DatasetAttackConfiguration(dataset_names=["missing"], max_dataset_size=dataset_limit),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == prompt_cap
    assert estimate.configured_dataset_size == prompt_cap
    if prompt_cap is None:
        assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    else:
        assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
        assert estimate.effective_parameters == {"prompt_cap": prompt_cap}


@pytest.mark.usefixtures("patch_central_database")
async def test_psychosocial_keeps_per_harm_limits_and_baselines_async() -> None:
    scenario = Psychosocial(imminent_crisis_scorer=_scorer(), licensed_therapist_scorer=_scorer())
    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 40
    assert estimate.configured_dataset_size == 10
    assert [component.count for component in estimate.components] == [15, 5, 15, 5]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("use_cached", [False, True])
async def test_benchmark_keeps_target_axis_and_cache_caveat_async(use_cached: bool) -> None:
    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial._build_benchmark_technique",
        return_value=_TwoTechniqueDefault,
    ):
        scenario = AdversarialBenchmark(objective_scorer=_scorer(), use_cached=use_cached)
    scenario.set_params_from_args(args={"adversarial_targets": ["a", "b"]})
    with patch.object(scenario, "_resolve_adversarial_targets", return_value=[MockPromptTarget(), MockPromptTarget()]):
        estimate = await scenario.get_run_size_estimate_async()
    assert [component.count for component in estimate.components] == [16, 16]
    if use_cached:
        assert estimate.maximum_attack_count == 32
        assert estimate.condition is ScenarioRunSizeEstimateCondition.PriorExecutionResults
    else:
        assert estimate.estimated_attack_count == 32


@pytest.mark.usefixtures("patch_central_database")
async def test_benchmark_without_targets_reports_per_target_budget_async() -> None:
    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial._build_benchmark_technique",
        return_value=_TwoTechniqueDefault,
    ):
        scenario = AdversarialBenchmark(objective_scorer=_scorer())
    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Conditional
    assert estimate.minimum_attack_count == 16
    assert estimate.maximum_attack_count is None
    assert "per adversarial target" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_foundry_counts_compositions_not_flattened_techniques_async() -> None:
    scenario = RedTeamAgent(
        adversarial_chat=MagicMock(spec=PromptTarget),
        attack_scoring_config=AttackScoringConfig(objective_scorer=_scorer()),
    )
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [
                FoundryComposite(
                    attack=FoundryTechnique.Crescendo,
                    converters=[FoundryTechnique.Base64, FoundryTechnique.ROT13],
                ),
                FoundryComposite(attack=None, converters=[FoundryTechnique.Tense]),
            ],
            "include_baseline": False,
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 8
    assert [component.count for component in estimate.components] == [4, 4]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [ApiKey, Exploitation])
async def test_synthesized_scenarios_apply_shared_caps_once_async(scenario_class: type[Scenario]) -> None:
    scenario = scenario_class(objective_scorer=_scorer())
    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == scenario._get_run_size_budget()
