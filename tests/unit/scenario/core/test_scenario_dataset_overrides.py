# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline dataset contract coverage across Python, REST, scanner, and shell."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.cli._cli_args import (
    build_dataset_request_fields,
    parse_run_arguments,
)
from pyrit.cli.pyrit_scan import _build_run_request, parse_args
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSelectionMode,
    DatasetSourceInput,
    ScenarioDatasetRequest,
    SeedObjective,
    SeedPrompt,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.registry import ScenarioRegistry
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource, Scenario
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.scenario.scenarios.garak.api_key import ApiKey
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.figstep import FigStep
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination, PackageHallucinationTechnique
from pyrit.scenario.scenarios.garak.prompt_inject import PromptInject, PromptInjectTechnique
from pyrit.scenario.scenarios.garak.system_prompt_extraction import (
    DATASET_EXTRACTION_TEMPLATES,
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
)
from pyrit.scenario.scenarios.garak.web_injection import WebInjection, WebInjectionTechnique
from pyrit.score import SubStringScorer


class _Technique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", {"direct"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _Scenario(Scenario):
    def __init__(self, *, scenario_result_id: str | None = None) -> None:
        super().__init__(
            version=1,
            technique_class=_Technique,
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name="a", max_size=2, fetch=DatasetFetchPolicy.NEVER), DatasetSource(name="b")],
                max_per_dataset=5,
                max_total=7,
            ),
            objective_scorer=SubStringScorer(substring="yes"),
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return [
            build_baseline_atomic_attack(
                objective_target=context.objective_target,
                objective_scorer=self._objective_scorer,
                seed_groups=list(context.seed_groups),
                memory_labels=context.memory_labels,
            )
        ]


@pytest.mark.usefixtures("patch_central_database")
async def test_python_rest_scanner_shell_resolve_identical_settings_without_fetching() -> None:
    tokens = "--datasets a b:all --max-per-dataset 3 --max-total 4 --dataset-fetch never"
    scanner_request = _build_run_request(
        parsed_args=parse_args(["run", "test", "--target", "test", *tokens.split()]), scenario_name="test"
    )
    scanner = scanner_request.datasets
    shell = build_dataset_request_fields(parse_run_arguments(args_string="test " + tokens))["datasets"]
    python = ScenarioDatasetRequest(
        sources=[DatasetSourceInput(name="a"), DatasetSourceInput(name="b", max_size="all")],
        max_per_dataset=3,
        max_total=4,
        fetch=DatasetFetchPolicy.NEVER,
    )
    assert scanner == shell == python
    request = RunScenarioRequest(scenario_name="test", target_name="test", datasets=python)
    transported = RunScenarioRequest.model_validate_json(request.model_dump_json(exclude_none=True))
    mapped = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="test",
        scenario_class=_Scenario,
        datasets=transported.dataset_request,
    )
    assert "dataset_config" not in mapped
    estimates = []
    memory = CentralMemory.get_memory_instance()
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("No preparation")),
        patch.object(memory, "get_seeds_async", side_effect=AssertionError("No seed reads")),
    ):
        for overrides in [python, scanner, shell, mapped["dataset_overrides"]]:
            scenario = _Scenario()
            scenario.set_params_from_args(args={"dataset_overrides": overrides})
            estimates.append(await scenario.get_run_size_estimate_async())
    assert all(estimate.effective_datasets == estimates[0].effective_datasets for estimate in estimates)
    estimate = estimates[0]
    assert estimate.dataset_defaults.max_total == 7
    assert estimate.effective_datasets.max_total == 4
    assert [source.max_size for source in estimate.effective_datasets.sources] == [2, "all"]
    assert estimate.dataset_defaults.sources[0].fetch is DatasetFetchPolicy.NEVER


@pytest.mark.usefixtures("patch_central_database")
async def test_resume_replays_saved_selection_without_preparation() -> None:
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(
        seeds=[SeedObjective(value=f"{name}-{index}", dataset_name=name) for name in ["a", "b"] for index in range(6)],
        added_by="test",
    )
    target = MockPromptTarget()
    overrides = ScenarioDatasetRequest(max_total=4, fetch=DatasetFetchPolicy.NEVER)
    scenario = _Scenario()
    scenario.set_params_from_args(args={"objective_target": target, "dataset_overrides": overrides})
    with patch.object(DatasetAttackConfiguration, "prepare_async", new_callable=AsyncMock) as prepare:
        await scenario.initialize_async()
    prepare.assert_awaited_once()
    selected = [group.logical_id for group in scenario._atomic_attacks[0].seed_groups]
    assert len(selected) == 4

    # Changed caps do not matter on resume: the stored run plan fixes the selected seed groups.
    resumed = _Scenario(scenario_result_id=scenario._scenario_result_id)
    resumed._default_dataset_config = resumed._default_dataset_config.with_overrides(max_total=1, max_per_dataset=1)
    resumed.set_params_from_args(args={"objective_target": target, "dataset_overrides": overrides})
    with patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("No resume preparation")):
        await resumed.initialize_async()
    assert [group.logical_id for group in resumed._atomic_attacks[0].seed_groups] == selected

    # A different dataset selection is an error, never a silent replacement.
    diverged = _Scenario(scenario_result_id=scenario._scenario_result_id)
    diverged.set_params_from_args(
        args={
            "objective_target": target,
            "dataset_overrides": ScenarioDatasetRequest(sources=[DatasetSourceInput(name="a")]),
        }
    )
    with pytest.raises(ValueError, match="does not match"):
        await diverged.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("scenario_class", "overrides", "error"),
    [
        (FigStep, {"sources": [{"name": "other"}]}, "Select datasets"),
        (FigStep, {"sources": [{"name": "figstep"}, {"name": "figstep_pro"}]}, "at most"),
        (ApiKey, {"max_per_dataset": 1}, "per-dataset"),
        (ApiKey, {"sources": [{"name": "harmbench"}]}, "fixed"),
        (Encoding, {"sources": [{"name": "garak_slur_terms_en"}]}, "ambiguous"),
        (PackageHallucination, {"max_per_dataset": 1}, "per-dataset"),
    ],
)
async def test_constrained_scenarios_reject_unsupported_overrides_before_preparation(
    *,
    scenario_class: type[Scenario],
    overrides: dict[str, object],
    error: str,
) -> None:
    scenario = scenario_class(objective_scorer=SubStringScorer(substring="yes"))
    scenario.set_params_from_args(args={"dataset_overrides": overrides})
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("No preparation")),
        pytest.raises(ValueError, match=error),
    ):
        await scenario.get_run_size_estimate_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_encoding_global_overrides_preserve_child_caps() -> None:
    scenario = Encoding()
    scenario.set_params_from_args(args={"dataset_overrides": {"max_total": 3, "fetch": "never"}})
    estimate = await scenario.get_run_size_estimate_async()
    assert [child.max_total for child in scenario._dataset_config.configurations] == [10, 10]
    assert estimate.effective_datasets.max_total == 3


@pytest.mark.usefixtures("patch_central_database")
async def test_catalog_dataset_controls_do_not_depend_on_estimates() -> None:
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(ApiKey, name="garak.api_key")
        with patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry):
            [item] = (await ScenarioService().list_scenarios_async(include_estimates=False)).items
    assert item.dataset_options.mode is DatasetSelectionMode.FIXED
    assert item.dataset_options.per_dataset_limit is False
    assert [source.name for source in item.dataset_defaults.sources] == ApiKey.required_datasets()
    assert item.dataset_defaults.max_total == 20


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("kind", ["system_prompt", "web", "prompt_inject"])
async def test_synthesized_scenarios_build_only_from_matching_seeds(kind: str) -> None:
    target = MockPromptTarget()
    if kind == "system_prompt":
        scenario = SystemPromptExtraction(prompt_cap="all")
        techniques = [SystemPromptExtractionTechnique.DirectRequests]
        expected = 2
    elif kind == "web":
        scenario = WebInjection(max_prompts_per_technique=100)
        techniques = [WebInjectionTechnique.TaskXSS]
        expected = 1
    else:
        scenario = PromptInject()
        techniques = [PromptInjectTechnique.IgnorePrint]
        expected = 1
    seeds = []
    for name in scenario.required_datasets() if kind != "web" else scenario._default_dataset_config.dataset_names:
        for category in ("selected", "excluded"):
            value = f"{category}:{name}"
            seed_name = name
            metadata = {"technique": "direct_requests"} if name == DATASET_EXTRACTION_TEMPLATES else {}
            if kind == "prompt_inject":
                value += " {{ goal_text }}" if name == PromptInject.TECHNIQUE_DATASET_NAME else " {{ technique_text }}"
                seed_name = "ignore_print"
            seeds.append(
                SeedPrompt(
                    value=value, name=seed_name, dataset_name=name, harm_categories=[category], metadata=metadata
                )
            )
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(seeds=seeds, added_by="test")
    args = {
        "objective_target": target,
        "scenario_techniques": techniques,
        "include_baseline": False,
        "dataset_overrides": ScenarioDatasetRequest(
            filters={"harm_categories": ["selected"]}, fetch=DatasetFetchPolicy.NEVER
        ),
    }
    if kind == "prompt_inject":
        args["goal_texts"] = ["test goal"]
    scenario.set_params_from_args(args=args)
    with patch.object(memory, "get_seeds_async", side_effect=AssertionError("Estimate read seeds")):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.effective_datasets.filters == {"harm_categories": ["selected"]}
    with patch.object(memory, "get_seeds_async", wraps=memory.get_seeds_async) as read:
        await scenario.initialize_async()
    assert all(call.kwargs.get("harm_categories") == ["selected"] for call in read.await_args_list)
    groups = [group for attack in scenario._atomic_attacks for group in attack.seed_groups]
    assert len(groups) == expected
    assert all("excluded:" not in seed.value for group in groups for seed in group.seeds)
    if kind == "prompt_inject":
        assert scenario._technique_templates["ignore_print"].value.startswith("selected:")


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("kind", ["system_prompt", "web", "packages"])
async def test_generated_objective_limits_preserve_ingredients_and_resume(kind: str) -> None:
    scorer = SubStringScorer(substring="yes")
    if kind == "system_prompt":
        scenario = SystemPromptExtraction(objective_scorer=scorer)
        techniques = [SystemPromptExtractionTechnique.DirectRequests]
    elif kind == "web":
        scenario = WebInjection(objective_scorer=scorer)
        techniques = [WebInjectionTechnique.TaskXSS, WebInjectionTechnique.MarkdownURIImageExfilExtended]
    else:
        scenario = PackageHallucination(objective_scorer=scorer)
        techniques = [PackageHallucinationTechnique.Rust, PackageHallucinationTechnique.Python]
    args = {
        "objective_target": MockPromptTarget(),
        "scenario_techniques": techniques,
        "include_baseline": False,
        "dataset_overrides": ScenarioDatasetRequest(max_total=3, fetch=DatasetFetchPolicy.NEVER),
    }
    scenario.set_params_from_args(args=args)
    memory = CentralMemory.get_memory_instance()
    with patch.object(memory, "get_seeds_async", side_effect=AssertionError("Estimate read seeds")):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 3
    assert estimate.dataset_limit.value == 3
    names = [source.name for source in estimate.effective_datasets.sources]
    seeds = [
        SeedPrompt(
            value=f"{name}-{index} <language> <task>" if name.endswith("_stubs") else f"{name}-{index}",
            dataset_name=name,
            metadata={"technique": "direct_requests"} if name == DATASET_EXTRACTION_TEMPLATES else {},
        )
        for name in names
        for index in range(4)
    ]
    await memory.add_seeds_to_memory_async(seeds=seeds, added_by="test")
    await scenario.initialize_async()
    assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 3
    assert all(attack.seed_groups for attack in scenario._atomic_attacks)
    for name in names:
        assert len(await memory.get_seeds_async(dataset_name=name)) == 4
    plan = scenario._build_run_plan()
    resumed = type(scenario)(objective_scorer=scorer, scenario_result_id=scenario._scenario_result_id)
    resumed.set_params_from_args(args=args)
    with patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("Resume prepared data")):
        await resumed.initialize_async()
    assert resumed._build_run_plan() == plan

    uncapped = type(scenario)(objective_scorer=scorer)
    uncapped.set_params_from_args(
        args={**args, "dataset_overrides": ScenarioDatasetRequest(max_total="all", fetch=DatasetFetchPolicy.NEVER)}
    )
    await uncapped.initialize_async()
    assert sum(len(attack.seed_groups) for attack in uncapped._atomic_attacks) > 3


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [WebInjection, PackageHallucination])
@pytest.mark.parametrize("operation", ["estimate", "load", "initialize"])
async def test_generated_limits_reject_missing_technique_coverage_before_loading(
    *, scenario_class: type[Scenario], operation: str
) -> None:
    scenario = scenario_class()
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scenario_techniques": [scenario._technique_class.ALL],
            "dataset_overrides": ScenarioDatasetRequest(max_total=1),
        }
    )
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("Prepared datasets")),
        pytest.raises(ValueError, match="Select at least .* objectives"),
    ):
        if operation == "estimate":
            await scenario.get_run_size_estimate_async()
        elif operation == "load":
            await scenario.load_datasets_async()
        else:
            await scenario.initialize_async()


def test_scenarios_do_not_bypass_configured_seed_reads() -> None:
    root = Path(__file__).parents[4] / "pyrit" / "scenario" / "scenarios"
    direct_reads = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        direct_reads.extend(
            f"{path.relative_to(root)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get_seeds", "get_seeds_async", "get_seed_groups", "get_seed_groups_async"}
        )
    assert not direct_reads, f"Scenario seed reads must use the dataset configuration so filters apply: {direct_reads}"
