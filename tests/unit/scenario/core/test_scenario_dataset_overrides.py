# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline dataset contract coverage across Python, REST, scanner, and shell."""

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
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination
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
        (PackageHallucination, {"max_total": 1}, "total dataset"),
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
