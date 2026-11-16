# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Fixed suite contents, registration, and main's ordinary launch path."""

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services import ScenarioRunService
from pyrit.datasets import TextJailBreak
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    LaunchScenarioSuiteRequest,
    ScenarioPreset,
    ScenarioSuite,
    ScenarioSuiteEntry,
    ScenarioSuiteLaunchEntrySnapshot,
    SeedObjective,
)
from pyrit.registry import (
    AttackTechniqueRegistry,
    InitializerRegistry,
    Registry,
    ScenarioPresetRegistry,
    ScenarioPresetStorage,
    ScenarioSuiteLaunchResolver,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
    TargetRegistry,
)
from pyrit.scenario.scenarios.airt import Multilingual
from pyrit.scenario.scenarios.foundry import FoundryTechnique
from pyrit.setup import ConfigurationLoader
from pyrit.setup.initialization import reset_setup_registries
from pyrit.setup.initializers import (
    HorizontalTechniquesSuite,
    ScenarioSuiteInitializer,
    TargetInitializer,
    TechniqueInitializer,
)
from pyrit.setup.initializers.techniques import build_technique_factories

SUITE = "horizontal_techniques"
PRESET_NAMES = [f"{SUITE}_jailbreaks", f"{SUITE}_multilingual", f"{SUITE}_encoding_obfuscation"]


SuiteBuilder = Callable[[], tuple[ScenarioSuite, list[ScenarioPreset]]]


def _builder(name: str, *, presets: list[ScenarioPreset] | None = None) -> SuiteBuilder:
    def build() -> tuple[ScenarioSuite, list[ScenarioPreset]]:
        built = [ScenarioPreset(name=f"{name}_direct", scenario_name="airt.jailbreak")] if presets is None else presets
        entries = [ScenarioSuiteEntry(entry_id=uuid5(NAMESPACE_URL, p.name), preset_name=p.name) for p in built]
        return ScenarioSuite(name=name, entries=entries), built

    return build


@pytest.fixture
def isolated_registries(tmp_path: Path) -> Iterator[None]:
    with (
        patch.dict(Registry._singletons, {}, clear=True),
        patch("pyrit.common.path.CONFIGURATION_DIRECTORY_PATH", tmp_path / "defaults"),
    ):
        ScenarioPresetRegistry.get_registry_singleton().configure_source(str(tmp_path / "presets"))
        ScenarioSuiteRegistry.get_registry_singleton().configure_source(str(tmp_path / "suites"))
        yield
        reset_setup_registries()


def _config(*, initializers: list[Any], tmp_path: Path | None = None) -> ConfigurationLoader:
    return ConfigurationLoader(
        memory_db_type="in_memory",
        env_files=[],
        initialization_scripts=[],
        initializers=initializers,
        scenario_presets_source=str(tmp_path / "requested_presets") if tmp_path else None,
        scenario_suites_source=str(tmp_path / "requested_suites") if tmp_path else None,
    )


def test_horizontal_suite_is_a_fixed_snapshot_of_packaged_catalogs() -> None:
    suite, presets = HorizontalTechniquesSuite.build()
    assert [preset.name for preset in presets] == [entry.preset_name for entry in suite.entries] == PRESET_NAMES
    assert HorizontalTechniquesSuite.build() == (suite, presets)
    jailbreaks, languages = presets[0].scenario_params["jailbreak_names"], presets[1].scenario_params["languages"]
    techniques = presets[2].techniques
    assert len(set(jailbreaks)) == len(jailbreaks) == 50
    assert set(jailbreaks) <= set(TextJailBreak.get_jailbreak_templates())
    assert len(set(languages)) == len(languages) == 10
    assert set(languages) <= set(Multilingual._get_default_languages()) - {"English"}
    assert techniques is not None and len(set(techniques)) == len(techniques)
    assert set(techniques) <= {technique.value for technique in FoundryTechnique}
    assert presets[1].scenario_params["translation_strategies"] == ["translation"]
    assert [preset.include_baseline for preset in presets] == [True, False, False]
    assert not any(preset.show_in_scanner for preset in presets)
    assert all(preset.datasets == presets[0].datasets for preset in presets)
    assert presets[0].datasets.max_total == presets[0].datasets.max_per_dataset == "all"
    sources = presets[0].datasets.sources
    assert sources is not None and [source.name for source in sources] == ["airt_harms"]


@pytest.mark.usefixtures("isolated_registries")
async def test_discovery_has_no_parameters_and_does_not_build_async() -> None:
    with patch.object(HorizontalTechniquesSuite, "build", side_effect=AssertionError("must not build")):
        registry = InitializerRegistry()
        assert "scenario_suite" in registry.get_class_names()
        assert registry.get_registered_class_metadata("scenario_suite").supported_parameters == ()


@pytest.mark.usefixtures("isolated_registries")
async def test_suite_builders_register_idempotently_async() -> None:
    with patch.object(ScenarioSuiteInitializer, "SUITE_BUILDERS", (_builder("first"), _builder("second"))):
        initializer = ScenarioSuiteInitializer()
        await initializer.initialize_async()
        await initializer.initialize_async()
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == ["first", "second"]
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == ["first_direct", "second_direct"]


@pytest.mark.usefixtures("isolated_registries")
@pytest.mark.parametrize("kind", ["preset", "suite"])
async def test_different_registered_definition_registers_nothing_async(kind: str) -> None:
    preset_registry = ScenarioPresetRegistry.get_registry_singleton()
    suite_registry = ScenarioSuiteRegistry.get_registry_singleton()
    if kind == "preset":
        preset_registry.register_preset(preset=ScenarioPreset(name=PRESET_NAMES[-1], scenario_name="airt.jailbreak"))
    else:
        suite_registry.register_suite(suite=ScenarioSuite(name=SUITE, description="different"))
    with pytest.raises(ValueError, match="already registered"):
        await ScenarioSuiteInitializer().initialize_async()
    assert preset_registry.instances.get_names() == ([PRESET_NAMES[-1]] if kind == "preset" else [])
    assert suite_registry.instances.get_names() == ([SUITE] if kind == "suite" else [])


@pytest.mark.usefixtures("isolated_registries")
@pytest.mark.parametrize(
    "builders",
    [
        (_builder("same"), _builder("same")),
        (_builder("empty", presets=[]),),
        (_builder("dup", presets=[ScenarioPreset(name="dup_direct", scenario_name="airt.jailbreak")] * 2),),
        (_builder("first"), _builder("second", presets=[ScenarioPreset(name="first_direct", scenario_name="x")])),
    ],
)
async def test_invalid_batches_register_nothing_async(builders: tuple[SuiteBuilder, ...]) -> None:
    with patch.object(ScenarioSuiteInitializer, "SUITE_BUILDERS", builders), pytest.raises(ValueError):
        await ScenarioSuiteInitializer().initialize_async()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []


@pytest.mark.usefixtures("isolated_registries", "patch_central_database")
@pytest.mark.parametrize("phase", ["startup", "reload"])
@pytest.mark.parametrize("destination", ["custom", "default"])
async def test_configuration_binds_requested_storage_before_suite_initialization_async(
    *, tmp_path: Path, phase: str, destination: str
) -> None:
    old_presets = ScenarioPresetRegistry.get_registry_singleton()
    old_suites = ScenarioSuiteRegistry.get_registry_singleton()
    config = _config(initializers=["scenario_suite"], tmp_path=tmp_path if destination == "custom" else None)
    if phase == "startup":
        await config.initialize_pyrit_async()
    else:
        prepared = await config.preflight_reinitialization_async(environment_values={})
        assert ScenarioPresetRegistry.get_registry_singleton() is old_presets
        await config.apply_prepared_reinitialization_async(prepared=prepared)
        assert ScenarioPresetRegistry.get_registry_singleton() is not old_presets
        assert ScenarioSuiteRegistry.get_registry_singleton() is not old_suites

    presets = ScenarioPresetRegistry.get_registry_singleton()
    suites = ScenarioSuiteRegistry.get_registry_singleton()
    assert presets.source == (config.scenario_presets_source or str(tmp_path / "defaults" / "scenario_presets"))
    assert suites.source == (config.scenario_suites_source or str(tmp_path / "defaults" / "scenario_suites"))
    assert presets.instances.get_names() == sorted(PRESET_NAMES)
    assert suites.instances.get_names() == [SUITE]


@pytest.mark.usefixtures("isolated_registries", "patch_central_database")
async def test_stored_documents_shadow_registrations_without_blocking_startup_async(tmp_path: Path) -> None:
    config = _config(initializers=["scenario_suite"], tmp_path=tmp_path)
    stored_preset = ScenarioPreset(name=PRESET_NAMES[-1], scenario_name="airt.jailbreak")
    await asyncio.to_thread(
        ScenarioPresetStorage(source=str(config.scenario_presets_source)).save_preset,
        preset=stored_preset,
        expected_version=None,
    )
    await asyncio.to_thread(
        ScenarioSuiteStorage(source=str(config.scenario_suites_source)).save_suite,
        suite=ScenarioSuite(name=SUITE, description="Operator definition"),
        expected_version=None,
    )
    await config.initialize_pyrit_async()

    presets = ScenarioPresetRegistry.get_registry_singleton()
    suites = ScenarioSuiteRegistry.get_registry_singleton()
    assert presets.instances.get_names() == sorted(PRESET_NAMES)
    shadowed = await asyncio.to_thread(presets.get_preset, name=stored_preset.name)
    assert shadowed is not None and shadowed.origin == "stored" and shadowed.preset == stored_preset
    stored_suite = await asyncio.to_thread(suites.get_suite, name=SUITE)
    assert stored_suite is not None and stored_suite.origin == "stored"


@pytest.mark.usefixtures("isolated_registries", "patch_central_database")
async def test_live_reload_removes_suite_registration_and_keeps_stored_documents_async(tmp_path: Path) -> None:
    config = _config(initializers=["scenario_suite"], tmp_path=tmp_path)
    await config.initialize_pyrit_async()
    preset = ScenarioPreset(name="operator_preset", scenario_name="airt.jailbreak")
    stored = await asyncio.to_thread(
        ScenarioPresetRegistry.get_registry_singleton().save_preset, preset=preset, expected_version=None
    )
    prepared = await config.preflight_reinitialization_async(environment_values={})
    await config.apply_prepared_reinitialization_async(prepared=prepared)
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == [SUITE]

    config = replace(config, initializers=[])
    with (
        patch.object(TechniqueInitializer, "initialize_async", AsyncMock()),
        patch.object(TargetInitializer, "initialize_async", AsyncMock()),
    ):
        prepared = await config.preflight_reinitialization_async(environment_values={})
        await config.apply_prepared_reinitialization_async(prepared=prepared)
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []
    retained = await asyncio.to_thread(ScenarioPresetRegistry.get_registry_singleton().get_preset, name=preset.name)
    assert retained is not None and retained.version == stored.version


@pytest.mark.usefixtures("isolated_registries", "patch_central_database", "block_dataset_provider_lookup")
async def test_horizontal_text_prepares_and_resumes_through_main_launch_path_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    chat = MockPromptTarget()
    target = MockPromptTarget()
    targets = TargetRegistry.get_registry_singleton()
    targets.instances.register(chat, name="chat")
    targets.instances.register(target, name="objective")
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories(groups=["core"]))
    objective = "Describe a blue circle."
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value=objective, dataset_name=HorizontalTechniquesSuite.DATASET_NAME)], added_by="test"
    )
    await ScenarioSuiteInitializer().initialize_async()
    suite = ScenarioSuiteRegistry.get_registry_singleton().get_suite(name=SUITE).suite
    resolved = ScenarioSuiteLaunchResolver().resolve(
        name=SUITE,
        request=LaunchScenarioSuiteRequest(
            target_name="objective",
            adversarial_target_name="chat",
            scoring_target_name="chat",
            entries=[
                ScenarioSuiteLaunchEntrySnapshot(
                    entry_id=entry.entry_id, preset_name=entry.preset_name, origin="registered", preset_version=None
                )
                for entry in suite.entries
            ],
        ),
    )
    counts = [
        len(HorizontalTechniquesSuite.JAILBREAK_NAMES) + 1,
        len(HorizontalTechniquesSuite.LANGUAGES),
        len(HorizontalTechniquesSuite.CONVERTER_TECHNIQUES),
    ]
    service = ScenarioRunService()
    try:
        for entry, count in zip(resolved.entries, counts, strict=True):
            assert entry.request.objective_scorer is None
            prepared = await service._prepare_run_async(request=entry.request)
            scenario = prepared.scenario
            assert scenario.atomic_attack_count == count
            scorer_hash = scenario._objective_scorer.get_identifier().hash
            assert all(attack.seed_groups[0].objective.value == objective for attack in scenario._atomic_attacks)
            stored = (
                await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario._scenario_result_id])
            )[0]
            restored = service._restore_launch_request(stored=stored)
            assert restored.objective_scorer == entry.request.objective_scorer
            resumed = await service._prepare_run_async(request=restored)
            assert resumed.scenario._objective_scorer.get_identifier().hash == scorer_hash
            assert resumed.scenario._scenario_result_id == scenario._scenario_result_id
    finally:
        await service.close_async()
