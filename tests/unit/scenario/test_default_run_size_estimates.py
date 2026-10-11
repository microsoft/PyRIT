# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration-only previews stay separate from exact initialized run plans."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_progress_read_model import ScenarioProgressReadModel
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackSeedGroup,
    BoundedDatasetSize,
    ComponentIdentifier,
    IndeterminateDatasetSize,
    ScenarioRunPlan,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateCondition,
    ScenarioRunSizeEstimateStatus,
    SeedObjective,
    scenario_dataset_size_from_limit,
)
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario.core import (
    AtomicAttack,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetSource,
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
from pyrit.scenario.scenarios.garak.api_key import ApiKey, ApiKeyDatasetConfiguration
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.exploitation import Exploitation
from pyrit.scenario.scenarios.garak.figstep import FigStep
from pyrit.scenario.scenarios.garak.latent_injection import LatentInjection, LatentInjectionDatasetConfiguration
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination, PackageHallucinationTechnique
from pyrit.scenario.scenarios.garak.prompt_inject import PromptInject, PromptInjectDatasetConfiguration
from pyrit.scenario.scenarios.garak.system_prompt_extraction import (
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
)
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionTechnique
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories


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
    assert estimate.dataset_size == scenario_dataset_size_from_limit(5)
    assert estimate.datasets[0].logical_seed_group_count is None
    assert estimate.datasets[0].selected_seed_group_count is None
    assert estimate.datasets[0].configured_caps[0].count == 5
    assert await scenario._memory.get_scenario_results_async() == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize(
    ("scenario_class", "constructor_args", "run_args", "message"),
    [
        (
            PromptInject,
            {},
            {
                "dataset_config": PromptInjectDatasetConfiguration(
                    dataset_names=PromptInject.required_datasets(), max_dataset_size=1
                )
            },
            "must be at least the number of goal_texts",
        ),
        (PromptInject, {}, {"goal_texts": []}, "non-empty strings"),
        (PromptInject, {}, {"goal_texts": ["duplicate", "duplicate"]}, "duplicate"),
        (PromptInject, {}, {"dataset_config": DatasetAttackConfiguration()}, "only supports"),
        (
            LatentInjection,
            {},
            {"dataset_config": LatentInjectionDatasetConfiguration(dataset_names=["wrong"])},
            "requires exactly",
        ),
        (LatentInjection, {}, {"families": []}, "non-empty selection"),
        (LatentInjection, {}, {"families": ["latent_jailbreak"]}, "explicit harm_scorer"),
        (ApiKey, {}, {"dataset_config": DatasetAttackConfiguration()}, "only supports"),
        (
            FigStep,
            {"objective_scorer": _scorer()},
            {"dataset_config": DatasetAttackConfiguration(dataset_names=["wrong"])},
            "exactly one",
        ),
        (Exploitation, {}, {"prompt_cap": 0}, "prompt_cap must be greater than zero"),
        (Exploitation, {}, {"prompt_cap": -1}, "prompt_cap must be greater than zero"),
        (SystemPromptExtraction, {"system_prompt_subsample": 0}, {}, "system_prompt_subsample"),
        (PackageHallucination, {"max_prompts_per_language": 0}, {}, "max_prompts_per_language"),
        (PackageHallucination, {"max_prompts_per_language": -1}, {}, "max_prompts_per_language"),
        (TextAdaptive, {"objective_scorer": _scorer()}, {"max_attempts_per_objective": 0}, "must be >= 1"),
        (TextAdaptive, {"objective_scorer": _scorer()}, {"max_attempts_per_objective": -1}, "must be >= 1"),
    ],
)
async def test_configuration_only_checks_are_shared_before_dataset_reads_async(
    *,
    scenario_class: type[Scenario],
    constructor_args: dict[str, Any],
    run_args: dict[str, Any],
    message: str,
    preview: bool,
) -> None:
    scenario = scenario_class(**constructor_args)
    target = MockPromptTarget()
    if scenario_class is FigStep:
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset({"text", "image_path"})}),
            )
        )
    scenario.set_params_from_args(args={"objective_target": target, **run_args})
    with patch.object(
        scenario, "_resolve_seed_groups_by_dataset_async", side_effect=AssertionError("Resolved datasets")
    ) as resolve:
        with pytest.raises(ValueError, match=message):
            if preview:
                await scenario.get_run_size_estimate_async()
            else:
                await scenario.initialize_async()
    resolve.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "configuration_class",
    [ApiKeyDatasetConfiguration, PromptInjectDatasetConfiguration, LatentInjectionDatasetConfiguration],
)
def test_ingredient_configurations_require_uncapped_sources(
    configuration_class: type[DatasetAttackConfiguration],
) -> None:
    scenario_class = {
        ApiKeyDatasetConfiguration: ApiKey,
        PromptInjectDatasetConfiguration: PromptInject,
        LatentInjectionDatasetConfiguration: LatentInjection,
    }[configuration_class]
    config = configuration_class(sources=[DatasetSource(name=name) for name in scenario_class.required_datasets()])
    assert config.max_per_dataset == "all"
    for limit in (None, "default", "all"):
        config.with_overrides(max_per_dataset=limit).validate_configuration()
    with pytest.raises(ValueError, match="ingredient sources must be uncapped"):
        config.with_overrides(max_per_dataset=2).validate_configuration()
    with pytest.raises(ValueError, match="ingredient sources must be uncapped"):
        config.with_overrides(
            sources=[DatasetSource(name=name, max_size=2) for name in config.dataset_names]
        ).validate_configuration()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("cap", [3, 12, "all"])
async def test_prompt_inject_valid_coverage_caps_still_have_configuration_only_previews_async(cap: int | str) -> None:
    scenario = PromptInject()
    scenario.set_params_from_args(
        args={
            "dataset_config": PromptInjectDatasetConfiguration(
                dataset_names=PromptInject.required_datasets(), max_dataset_size=cap
            )
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    if cap == "all":
        assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    else:
        assert estimate.estimated_attack_count == cap * 5


@pytest.mark.usefixtures("patch_central_database")
async def test_compound_configuration_checks_children_before_a_bounded_preview_async() -> None:
    config = CompoundDatasetAttackConfiguration(
        configurations=[
            DatasetAttackConfiguration(dataset_names=["missing"]),
            PromptInjectDatasetConfiguration(dataset_names=PromptInject.required_datasets(), max_dataset_size=1),
        ],
        max_dataset_size=10,
    )
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(args={"dataset_config": config})
    with pytest.raises(ValueError, match="number of goal_texts"):
        await scenario.get_run_size_estimate_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_indeterminate_contract_does_not_trigger_population_reads_async() -> None:
    scenario = _MatrixEstimateScenario()
    budget = IndeterminateDatasetSize(detail="Custom population is not supported.")
    with patch.object(scenario, "_get_run_size_budget", return_value=budget):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert estimate.dataset_size == budget
    assert estimate.note == budget.detail


@pytest.mark.usefixtures("patch_central_database")
async def test_unavailable_formula_preserves_known_budget_async() -> None:
    scenario = _MatrixEstimateScenario()
    with patch.object(
        scenario, "_estimate_run_size_async", return_value=ScenarioRunSizeEstimate.unavailable(note="No formula.")
    ):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert estimate.dataset_size == BoundedDatasetSize(value=5)
    assert estimate.dataset_limit.value is None
    assert estimate.note == "No formula."


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("baseline", "expected"), [(False, 7), (True, 14)])
async def test_configured_estimate_uses_selected_techniques_and_limit_async(*, baseline: bool, expected: int) -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.ONE],
            "include_baseline": baseline,
            "dataset_config": DatasetAttackConfiguration(
                dataset_names=["also-missing"], max_per_dataset="all", max_total=7
            ),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == expected
    assert estimate.datasets[0].name == "also-missing"
    assert estimate.dataset_limit.value == 7


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_expands_aggregate_and_applies_combined_cap_once_async() -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.ALL],
            "include_baseline": False,
            "dataset_config": DatasetAttackConfiguration(
                dataset_names=["one", "two"], max_per_dataset="all", max_total=3
            ),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 6
    assert estimate.dataset_size == scenario_dataset_size_from_limit(3)
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
    assert estimate.dataset_size == scenario_dataset_size_from_limit(10)
    assert estimate.estimated_attack_count == 20


@pytest.mark.usefixtures("patch_central_database")
async def test_unlimited_estimate_does_not_load_data_or_invent_a_count_async() -> None:
    scenario = _MatrixEstimateScenario()
    scenario.set_params_from_args(
        args={
            "dataset_config": DatasetAttackConfiguration(
                dataset_names=["missing"], max_per_dataset="all", max_total="all"
            )
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert "No size limit" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_all_builtin_default_size_contracts_are_bounded_async() -> None:
    TargetRegistry.reset_registry_singleton()
    AttackTechniqueRegistry.reset_registry_singleton()
    ScenarioRegistry.reset_registry_singleton()
    targets = TargetRegistry.get_registry_singleton()
    for name in ("adversarial_chat", "objective_scorer_chat"):
        targets.instances.register(MockPromptTarget(), name=name)
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    try:
        with patch.object(Scenario, "_get_default_objective_scorer", return_value=_scorer()):
            registry = ScenarioRegistry.get_registry_singleton()
            names = registry.get_class_names()
            assert "garak.web_injection" in names
            for name in names:
                scenario_class = registry.get_class(name)
                if not scenario_class.__module__.startswith("pyrit.scenario.scenarios."):
                    continue
                scenario = registry.create_instance(name)
                estimate = await scenario.get_default_run_size_estimate_async()
                assert isinstance(estimate.dataset_size, BoundedDatasetSize), name
                assert estimate.status is not ScenarioRunSizeEstimateStatus.Unavailable, name
                finite_count = estimate.total_attack_count or estimate.maximum_attack_count
                assert finite_count is not None and finite_count > 0, name
                assert all(dataset.logical_seed_group_count is None for dataset in estimate.datasets), name
    finally:
        ScenarioRegistry.reset_registry_singleton()
        AttackTechniqueRegistry.reset_registry_singleton()
        TargetRegistry.reset_registry_singleton()


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
    [stored] = await scenario._memory.get_scenario_results_async(scenario_result_ids=[scenario._scenario_result_id])
    plan = stored.metadata[SCENARIO_RUN_PLAN_METADATA_KEY]
    assert sum(len(group["seed_group_ids"]) for group in plan["atomic_groups"]) == 9
    assert len(plan["seed_groups"]) == 3
    snapshot = await ScenarioProgressReadModel(memory=scenario._memory).get_snapshot_async(
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
    assert estimate.estimated_attack_count == scenario._get_run_size_budget().value
    assert estimate.effective_parameters["max_attempts_per_objective"] == 7
    assert "7 selected technique attempts" in estimate.note


@pytest.fixture
def jailbreak() -> Jailbreak:
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        return Jailbreak(objective_scorer=_scorer())


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_default_keeps_target_capability_upper_bound_async(jailbreak: Jailbreak) -> None:
    estimate = await jailbreak.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count is None
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
    assert estimate.dataset_size == scenario_dataset_size_from_limit(20)


@pytest.mark.usefixtures("patch_central_database")
async def test_web_injection_estimate_does_not_load_or_synthesize_populations_async() -> None:
    scenario = WebInjection()
    with (
        patch.object(scenario, "_load_dataset_values_async", side_effect=AssertionError("Loaded dataset")),
        patch.object(scenario, "_build_synthesized_seed_groups", side_effect=AssertionError("Synthesized seeds")),
    ):
        estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == (5 * 12 + 2) * 2
    assert estimate.dataset_size == scenario_dataset_size_from_limit(5 * 12 + 2)


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
            # The selected techniques need these datasets; the estimate never reads them.
            "dataset_config": DatasetAttackConfiguration(
                sources=[
                    DatasetSource(name=WebInjection.DATASET_EXAMPLE_DOMAINS),
                    DatasetSource(name=WebInjection.DATASET_NORMAL_INSTRUCTIONS),
                    DatasetSource(name=WebInjection.DATASET_WEB_HTML_JS),
                ],
                max_per_dataset="all",
                max_total=dataset_limit,
            ),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    budget = len(scenario.STRING_ASSEMBLY_SEEDS) + 3 * 7
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == budget * (2 if baseline else 1)
    assert estimate.dataset_size == scenario_dataset_size_from_limit(budget)
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
            "dataset_config": DatasetAttackConfiguration(
                dataset_names=["missing"], max_per_dataset="all", max_total=dataset_limit
            ),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    cap = 12 if prompt_cap is None else prompt_cap
    language_count = len(PackageHallucinationTechnique.expand({technique}))
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == cap * language_count
    assert estimate.dataset_size == scenario_dataset_size_from_limit(cap * language_count)
    assert estimate.effective_parameters == {"max_prompts_per_language": cap}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("prompt_cap", [256, 7, None, "", "default", "all"])
@pytest.mark.parametrize(
    "technique", [SystemPromptExtractionTechnique.ALL, SystemPromptExtractionTechnique.DirectRequests]
)
@pytest.mark.parametrize("dataset_limit", [1, None])
async def test_system_prompt_extraction_uses_one_shared_generation_cap_async(
    *, prompt_cap: int | str | None, technique: SystemPromptExtractionTechnique, dataset_limit: int | None
) -> None:
    scenario = SystemPromptExtraction(objective_scorer=_scorer(), prompt_cap=prompt_cap)
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [technique],
            "dataset_config": DatasetAttackConfiguration(
                dataset_names=["missing"], max_per_dataset="all", max_total=dataset_limit
            ),
        }
    )
    estimate = await scenario.get_run_size_estimate_async()
    expected = 256 if prompt_cap in (None, "", "default") else prompt_cap
    assert estimate.estimated_attack_count == (None if expected == "all" else expected)
    assert estimate.dataset_size == scenario_dataset_size_from_limit(expected)
    if expected == "all":
        assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    else:
        assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
        assert estimate.effective_parameters == {"prompt_cap": expected}


@pytest.mark.usefixtures("patch_central_database")
async def test_psychosocial_keeps_per_harm_limits_and_baselines_async() -> None:
    scenario = Psychosocial(imminent_crisis_scorer=_scorer(), licensed_therapist_scorer=_scorer())
    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 40
    assert estimate.dataset_size == scenario_dataset_size_from_limit(10)
    assert [component.count for component in estimate.components] == [30, 10]
    assert [dataset.configured_caps[0].count for dataset in estimate.datasets] == [5, 5]


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
    assert estimate.status is ScenarioRunSizeEstimateStatus.Approximate
    assert estimate.estimated_attack_count == 16
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None
    assert [component.count for component in estimate.components] == [8, 8]
    assert "assumes one adversarial target" in estimate.note


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
    assert estimate.estimated_attack_count == scenario._get_run_size_budget().value
