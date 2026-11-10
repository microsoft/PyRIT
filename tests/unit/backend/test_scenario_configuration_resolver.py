# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Adversarial target resolution validates without changing execution scopes."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import DatasetSourceInput, ScenarioDatasetOptions, ScenarioDatasetRequest, SeedObjective
from pyrit.models.catalog import RestoreFailureReason, RunScenarioRequest
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.dataset_limit import DatasetLimit, ResolvedDatasetLimit
from pyrit.prompt_target import TargetCapabilities
from pyrit.registry import (
    AttackTechniqueRegistry,
    Registry,
    SavedEntryUnavailableError,
    ScenarioRegistry,
    ScorerRegistry,
    TargetRegistry,
    TechniqueSelectionUnavailableError,
    UnavailableEntry,
)
from pyrit.scenario import DatasetAttackConfiguration, DatasetFetchPolicy, DatasetSource, Scenario
from pyrit.scenario.core import (
    get_default_adversarial_target,
    override_default_adversarial_target,
    scenario_target_defaults,
)
from pyrit.scenario.core.dataset_overrides import apply_dataset_overrides
from pyrit.scenario.scenarios.adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt import RapidResponse
from pyrit.scenario.scenarios.garak import (
    ApiKey,
    Exploitation,
    PackageHallucination,
    PromptInject,
    PromptInjectDatasetConfiguration,
    SystemPromptExtraction,
    WebInjection,
)
from pyrit.score import SubStringScorer, TrueFalseScorer
from unit.mocks import MockPromptTarget


@pytest.mark.usefixtures("patch_central_database")
def test_scorer_override_preserves_defaults_and_rejects_stale_or_unsupported_selections() -> None:
    resolver = ScenarioConfigurationResolver()
    with patch.object(ScorerRegistry, "get_registry_singleton", return_value=ScorerRegistry()):
        scorer = SubStringScorer(substring="expected")
        registry = ScorerRegistry.get_registry_singleton()
        registry.instances.register(scorer, name="custom")
        reference = RegisteredComponentReference(registry_name="custom", identifier_hash=scorer.get_identifier().hash)
        assert resolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=None) == {}
        assert resolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=reference) == {
            "objective_scorer": scorer
        }
        assert (
            ApiKey(
                **resolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=reference)
            )._objective_scorer
            is scorer
        )

        class FixedScenario(ApiKey):
            def __init__(self) -> None:
                super().__init__()

        assert not ScenarioRegistry.supports_objective_scorer(FixedScenario)
        with pytest.raises(ValueError, match="does not support"):
            resolver.resolve_constructor_kwargs(scenario_class=FixedScenario, objective_scorer=reference)
        registry.instances.register(SubStringScorer(substring="changed"), name="custom", replace=True)
        with pytest.raises(ValueError, match="changed"):
            resolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=reference)


def _datasets(**fields: object) -> ScenarioDatasetRequest | None:
    return RunScenarioRequest.model_validate({"scenario_name": "s", "target_name": "t", **fields}).dataset_request


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("limit", "expected"), [(None, 20), ("", 20), ("default", 20), ("all", "all"), (7, 7)])
def test_dataset_name_override_applies_explicit_total(*, limit: DatasetLimit, expected: ResolvedDatasetLimit) -> None:
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.api_key",
        scenario_class=ApiKey,
        datasets=_datasets(dataset_names=ApiKey.required_datasets(), max_dataset_size=limit),
    )
    scenario = ApiKey()
    scenario.set_params_from_args(args=resolved)
    scenario._resolve_runtime_configuration(require_objective_target=False)
    assert scenario._dataset_config.max_total == expected


@pytest.mark.usefixtures("patch_central_database")
def test_dataset_overrides_preserve_subclass_state_and_default() -> None:
    scenario = PromptInject()
    original = PromptInjectDatasetConfiguration(
        dataset_names=PromptInject.required_datasets(),
        goal_texts=["first goal", "second goal"],
    )
    scenario._default_dataset_config = original
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.prompt_inject",
        scenario_class=MagicMock(return_value=scenario),
        datasets=_datasets(
            dataset_names=PromptInject.required_datasets(),
            max_dataset_size=7,
            dataset_filters={"data_types": ["text"]},
        ),
    )["dataset_overrides"]
    resolved = apply_dataset_overrides(config=original, request=resolved, options=ScenarioDatasetOptions())
    assert type(resolved) is PromptInjectDatasetConfiguration
    assert resolved._goal_texts == ["first goal", "second goal"]
    assert resolved.max_total == 7
    assert resolved.filters == {"data_types": ["text"]}
    assert original.max_total == 12
    assert original.filters == {}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [RapidResponse, TextAdaptive])
@pytest.mark.parametrize("total", [10, 100, "all"])
async def test_total_override_preserves_source_limits_async(
    *, scenario_class: type[Scenario], total: int | Literal["all"]
) -> None:
    technique_class = PromptInject()._technique_class
    with (
        patch.object(Scenario, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)),
        patch(
            "pyrit.scenario.scenarios.airt.rapid_response._build_rapid_response_technique",
            return_value=technique_class,
        ),
        patch.object(TextAdaptive, "get_technique_class", return_value=technique_class),
    ):
        scenario = scenario_class()
    original = scenario._default_dataset_config
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="test",
        scenario_class=MagicMock(return_value=scenario),
        datasets=_datasets(max_dataset_size=total),
    )["dataset_overrides"]
    resolved = apply_dataset_overrides(config=original, request=resolved, options=ScenarioDatasetOptions())
    populations = {
        name: [SeedObjective(value=f"{name}-{index}", dataset_name=name) for index in range(20)]
        for name in original.dataset_names
    }
    memory = MagicMock(spec=MemoryInterface)
    memory.get_seeds_async = AsyncMock(side_effect=lambda *, dataset_name: populations[dataset_name])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await resolved.get_attack_groups_by_dataset_async()
    assert sum(len(population) for population in groups.values()) == (
        4 * len(populations) if total == "all" else min(total, 4 * len(populations))
    )
    assert all(len(population) <= 4 for population in groups.values())
    assert resolved.max_total == total
    assert resolved.max_per_dataset == 4
    assert original.max_total == "all"
    assert original.max_per_dataset == 4


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("total", [None, 7])
@pytest.mark.parametrize("configured_provider", [False, True], ids=["registered", "configured"])
def test_name_override_retains_source_options_and_adds_defaults_for_new_names(
    *, total: int | None, configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "retained"
    retained = DatasetSource(
        name="retained",
        max_size=2,
        fetch=DatasetFetchPolicy.NEVER,
        provider=provider if configured_provider else None,
    )
    original = DatasetAttackConfiguration(
        sources=[retained, DatasetSource(name="removed")], max_per_dataset=4, max_total=20
    )
    scenario = PromptInject()
    scenario._default_dataset_config = original
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="test",
        scenario_class=MagicMock(return_value=scenario),
        datasets=_datasets(dataset_names=["new", "retained"], max_dataset_size=total),
    )["dataset_overrides"]
    resolved = apply_dataset_overrides(config=original, request=resolved, options=ScenarioDatasetOptions())
    assert resolved.sources == (DatasetSource(name="new"), retained)
    assert resolved.sources[1] is retained
    assert resolved.sources[1].provider is retained.provider
    assert resolved.source_limit("new") == 4
    assert resolved.source_limit("retained") == 2
    assert resolved.max_total == (20 if total is None else total)
    assert original.dataset_names == ["retained", "removed"]
    assert original.max_total == 20


@pytest.mark.usefixtures("patch_central_database")
def test_omitted_total_retains_default() -> None:
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.api_key",
        scenario_class=ApiKey,
        datasets=_datasets(dataset_names=ApiKey.required_datasets()),
    )
    scenario = ApiKey()
    scenario.set_params_from_args(args=resolved)
    scenario._resolve_runtime_configuration(require_objective_target=False)
    assert scenario._dataset_config.max_total == 20


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("selection", [None, "selected"])
def test_resolve_adversarial_target_does_not_mutate_scope(selection: str | None) -> None:
    outer, selected = MockPromptTarget(), MockPromptTarget()
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.return_value = selected
    with (
        override_default_adversarial_target(outer),
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        patch.object(
            scenario_target_defaults,
            "_adversarial_target_override",
            wraps=scenario_target_defaults._adversarial_target_override,
        ) as scoped_default,
    ):
        result = ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert result is (selected if selection else None)
        assert get_default_adversarial_target() is outer
        scoped_default.set.assert_not_called()
        if selection is None:
            registry.instances.get.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ("missing", "not found"),
        ("wrong_type", "must be a PromptTarget"),
        ("single_turn", "must support multi_turn"),
    ],
)
def test_resolve_adversarial_target_rejects_invalid_selection_without_changing_scope(
    *, selection: str, message: str
) -> None:
    outer, single_turn = MockPromptTarget(), MockPromptTarget()
    single_turn.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
    targets = {"wrong_type": object(), "single_turn": single_turn}
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.side_effect = targets.get
    registry.instances.get_names.return_value = list(targets)
    with (
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        override_default_adversarial_target(outer),
    ):
        with pytest.raises(ValueError, match=message):
            ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert get_default_adversarial_target() is outer


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [SystemPromptExtraction, WebInjection, PackageHallucination])
@pytest.mark.parametrize("limit", [12, "all"])
async def test_total_limit_reaches_generated_objective_selection(
    *, scenario_class: type[Scenario], limit: DatasetLimit
) -> None:
    with (
        patch.object(Scenario, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)),
    ):
        request = ScenarioDatasetRequest(max_total=limit)
        resolved = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="test", scenario_class=scenario_class, datasets=request
        )
        scenario = scenario_class()
        scenario.set_params_from_args(args=resolved)
        estimate = await scenario.get_run_size_estimate_async()
    assert resolved["dataset_overrides"] is request
    assert estimate.effective_datasets.max_total == limit


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [SystemPromptExtraction, WebInjection, PackageHallucination])
async def test_fixed_source_names_reach_scenarios_through_dataset_overrides(scenario_class: type[Scenario]) -> None:
    with patch.object(Scenario, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)):
        names = [source.name for source in scenario_class().get_default_dataset_settings().sources]
        resolved = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="test",
            scenario_class=scenario_class,
            datasets=ScenarioDatasetRequest(sources=[DatasetSourceInput(name=name) for name in names]),
        )
        scenario = scenario_class()
        scenario.set_params_from_args(args=resolved)
        estimate = await scenario.get_run_size_estimate_async()
    assert [source.name for source in estimate.effective_datasets.sources] == names


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "overrides",
    [
        {"max_total": 3},
        {"sources": [{"name": "garak_exploitation_sql_injection"}]},
        {"filters": {"data_types": ["text"]}},
    ],
)
def test_dataset_overrides_are_rejected_for_a_fixed_dataset_scenario(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="dataset_overrides"):
        resolved = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="garak.exploitation",
            scenario_class=Exploitation,
            datasets=ScenarioDatasetRequest(**overrides),
        )
        Exploitation().set_params_from_args(args=resolved)


class _RegistryCatalog:
    _selection_catalog = ("builder", "revision")

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "default", "custom"}


class _FixedCatalog:
    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


def _scenario(catalog: type, *, takes_techniques: bool = True) -> MagicMock:
    scenario_class = MagicMock()
    parameters = [SimpleNamespace(name="scenario_techniques")] if takes_techniques else []
    scenario_class.supported_parameters.return_value = parameters
    scenario_class.return_value._technique_class = catalog
    return scenario_class


@pytest.fixture
def held_technique(patch_central_database: object) -> Iterator[None]:
    with patch.dict(Registry._singletons, {}, clear=True):
        AttackTechniqueRegistry.get_registry_singleton().instances.mark_unavailable(
            UnavailableEntry(kind="technique", name="formal", reason=RestoreFailureReason.BUILD_ERROR, message="boom")
        )
        yield


@pytest.mark.usefixtures("held_technique")
@pytest.mark.parametrize("techniques", [None, [], ["all"], ["default"], ["Custom"], ["base", "all:base64"]])
def test_a_broad_technique_selection_is_refused_while_a_saved_technique_is_held(techniques: list[str] | None) -> None:
    with pytest.raises(TechniqueSelectionUnavailableError, match="'formal'"):
        ScenarioConfigurationResolver._refuse_broad_selection_while_techniques_are_held(
            scenario_class=_scenario(_RegistryCatalog), techniques=techniques, technique_override=None
        )


@pytest.mark.usefixtures("held_technique")
def test_the_scenario_default_is_refused_before_resolution_returns_early() -> None:
    with pytest.raises(TechniqueSelectionUnavailableError):
        ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="registry_scenario", scenario_class=_scenario(_RegistryCatalog)
        )


@pytest.mark.usefixtures("held_technique")
def test_naming_a_held_technique_fails_with_its_reason() -> None:
    with pytest.raises(SavedEntryUnavailableError, match="'Formal' couldn't be restored: boom"):
        ScenarioConfigurationResolver._refuse_broad_selection_while_techniques_are_held(
            scenario_class=_scenario(_RegistryCatalog), techniques=["Formal:base64"], technique_override=None
        )


@pytest.mark.usefixtures("held_technique")
@pytest.mark.parametrize(
    ("scenario_class", "techniques"),
    [
        (_scenario(_RegistryCatalog), ["base", "other:base64"]),
        (_scenario(_FixedCatalog), None),
        (_scenario(_FixedCatalog), ["all"]),
        (_scenario(_RegistryCatalog, takes_techniques=False), None),
    ],
)
def test_named_techniques_and_other_scenarios_are_unaffected(
    scenario_class: MagicMock, techniques: list[str] | None
) -> None:
    ScenarioConfigurationResolver._refuse_broad_selection_while_techniques_are_held(
        scenario_class=scenario_class, techniques=techniques, technique_override=None
    )
