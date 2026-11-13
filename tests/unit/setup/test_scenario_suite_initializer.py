# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Catalog selection, construction, registration, and main's ordinary launch path."""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services import ScenarioRunService
from pyrit.datasets import TextJailBreak
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    DatasetFetchPolicy,
    LaunchScenarioSuiteRequest,
    ScenarioPreset,
    ScenarioSuite,
    ScenarioSuiteLaunchEntrySnapshot,
    SeedObjective,
)
from pyrit.registry import (
    AttackTechniqueRegistry,
    InitializerRegistry,
    Registry,
    ScenarioPresetRegistry,
    ScenarioSuiteLaunchResolver,
    ScenarioSuiteRegistry,
    TargetRegistry,
)
from pyrit.scenario.airt import Multilingual
from pyrit.scenario.foundry import FoundryTechnique
from pyrit.setup import ConfigurationLoader
from pyrit.setup.initialization import reset_setup_registries
from pyrit.setup.initializers import (
    HorizontalTechniquesSuite,
    ScenarioSuiteConfig,
    ScenarioSuiteInitializer,
)
from pyrit.setup.initializers.techniques import build_technique_factories

SUITE = "horizontal_techniques"


@pytest.fixture
def isolated_registries(tmp_path: Path) -> Iterator[None]:
    with patch.dict(Registry._singletons, {}, clear=True):
        ScenarioPresetRegistry.get_registry_singleton().configure_source(str(tmp_path / "presets"))
        ScenarioSuiteRegistry.get_registry_singleton().configure_source(str(tmp_path / "suites"))
        yield
        reset_setup_registries()


@pytest.fixture
def catalogs() -> Iterator[None]:
    with (
        patch.object(TextJailBreak, "get_jailbreak_templates", return_value=[f"t{i}.yaml" for i in range(10)]),
        patch.object(
            Multilingual,
            "get_default_languages",
            return_value=["English", "French", "Spanish", "Bengali", "Japanese", "Korean", "Swahili"],
        ),
    ):
        yield


def _configured(args: dict[str, Any]) -> ScenarioSuiteInitializer:
    initializer = ScenarioSuiteInitializer()
    initializer.set_params_from_args(args={"suite_args": {SUITE: args}})
    return initializer


@pytest.mark.usefixtures("catalogs")
def test_explicit_all_and_default_family_selections() -> None:
    presets = HorizontalTechniquesSuite(
        jailbreak_names=["t3.yaml", "t1.yaml", "t3.yaml"],
        languages=["  Klingon", "klingon", "Mandarin_Chinese"],
        encoding_techniques=["rot13", "base64", "rot13"],
        obfuscation_techniques=["all"],
    ).build_presets(suite_name=SUITE)
    assert presets[0].scenario_params == {"jailbreak_names": ["t3.yaml", "t1.yaml"]}
    assert presets[1].scenario_params["languages"] == ["Klingon", "Mandarin Chinese"]
    assert presets[2].techniques == ["rot13", "base64"]
    assert presets[3].techniques == sorted(
        member.value for member in FoundryTechnique.get_techniques_by_tag("obfuscation")
    )
    defaults = HorizontalTechniquesSuite().build_presets(suite_name=SUITE)
    assert len(defaults[0].scenario_params["jailbreak_names"]) == 5
    assert len(defaults[1].scenario_params["languages"]) == 4
    assert len(defaults[2].techniques) == len(defaults[3].techniques) == 4
    assert "English" not in defaults[1].scenario_params["languages"]
    assert [preset.include_baseline for preset in defaults] == [True, False, False, False]
    assert not any(preset.show_in_scanner for preset in defaults)
    assert all(preset.datasets == defaults[0].datasets for preset in defaults)
    assert defaults[0].datasets.max_total == defaults[0].datasets.max_per_dataset == "all"
    assert [source.name for source in defaults[0].datasets.sources] == ["airt_harms"]


@pytest.mark.usefixtures("catalogs")
def test_sampling_is_repeatable_name_independent_and_family_isolated() -> None:
    builder = HorizontalTechniquesSuite()
    first = builder.build_presets(suite_name="first")
    renamed = builder.build_presets(suite_name="second")
    assert [preset.model_dump(exclude={"name"}) for preset in first] == [
        preset.model_dump(exclude={"name"}) for preset in renamed
    ]
    with patch.object(
        TextJailBreak, "get_jailbreak_templates", return_value=[f"t{i}.yaml" for i in reversed(range(10))]
    ):
        assert builder.build_presets(suite_name="first") == first
    assert HorizontalTechniquesSuite(num_jailbreaks=1).build_presets(suite_name="first")[1:] == first[1:]
    assert HorizontalTechniquesSuite(selection_seed=42).build_presets(suite_name="first") != first


@pytest.mark.usefixtures("isolated_registries", "catalogs")
@pytest.mark.parametrize(
    "args",
    [
        {"jailbreak_names": ["missing.yaml"]},
        {"encoding_techniques": ["char_swap"]},
        {"obfuscation_techniques": ["base64"]},
        {"num_languages": 1000},
        {"num_jailbreaks": 1000},
    ],
)
async def test_catalog_dependent_errors_register_nothing_async(args: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        await _configured(args).initialize_async()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []


@pytest.mark.usefixtures("isolated_registries")
@pytest.mark.parametrize(
    "args",
    [
        {"tags": []},
        {"tags": ["unknown"]},
        {"tags": ["all", "default"]},
        {"suite_args": "invalid"},
        {"suite_args": {"unknown": {}}},
        {"suite_args": {1: {}}},
        {"suite_args": {SUITE: []}},
        {"suite_args": {SUITE: {"unknown": None}}},
        {"suite_args": {SUITE: {"languages": "French"}}},
        {"suite_args": {SUITE: {"languages": [3]}}},
        {"suite_args": {SUITE: {"num_jailbreaks": True}}},
        {"suite_args": {SUITE: {"selection_seed": "42"}}},
        {"suite_args": {SUITE: {"num_encodings": 0}}},
        {"suite_args": {SUITE: {"languages": []}}},
        {"suite_args": {SUITE: {"jailbreak_names": ["all", "t1.yaml"]}}},
        {"suite_args": {SUITE: {"languages": ["French"], "num_languages": 2}}},
        {"suite_args": {SUITE: {"dataset_names": []}}},
        {"suite_args": {SUITE: {"dataset_fetch": "always"}}},
    ],
)
async def test_invalid_configuration_fails_in_preflight_without_building_async(args: dict[str, Any]) -> None:
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        env_files=[],
        initialization_scripts=[],
        initializers=[{"name": "scenario_suite", "args": args}],
    )
    with (
        patch.object(HorizontalTechniquesSuite, "build_presets", side_effect=AssertionError("must not build")),
        patch.object(TextJailBreak, "get_jailbreak_templates", side_effect=AssertionError("must not read")),
        pytest.raises(ValueError),
    ):
        await config.preflight_reinitialization_async(environment_values={})
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []


@pytest.mark.parametrize("args", [{"num_jailbreaks": True}, {"languages": "French"}, {"num_encodings": -1}])
def test_python_constructor_rejects_invalid_inputs(args: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        HorizontalTechniquesSuite(**args)


def test_python_constructor_rejects_per_preset_dataset_refresh() -> None:
    with pytest.raises(ValueError, match="once per preset"):
        HorizontalTechniquesSuite(dataset_fetch=DatasetFetchPolicy.ALWAYS)


@pytest.mark.usefixtures("isolated_registries", "catalogs")
async def test_reset_allows_changed_and_removed_configuration_async(tmp_path: Path) -> None:
    await ScenarioSuiteInitializer().initialize_async()
    original = ScenarioPresetRegistry.get_registry_singleton().instances.get(f"{SUITE}_jailbreaks")

    reset_setup_registries()
    ScenarioSuiteRegistry.get_registry_singleton().configure_source(str(tmp_path / "suites"))
    await _configured({"num_jailbreaks": 1}).initialize_async()
    changed = ScenarioPresetRegistry.get_registry_singleton().instances.get(f"{SUITE}_jailbreaks")
    assert original is not None and changed is not None
    assert len(original.scenario_params["jailbreak_names"]) == 5
    assert len(changed.scenario_params["jailbreak_names"]) == 1

    reset_setup_registries()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []


@pytest.mark.usefixtures("isolated_registries", "catalogs")
@pytest.mark.parametrize(
    ("tags", "expected"),
    [(["default"], ["first"]), (["extra", "default"], ["first", "second"]), (["all"], ["first", "second", "untagged"])],
)
async def test_catalog_selection_and_multiple_configurations_async(*, tags: list[str], expected: list[str]) -> None:
    configs = (
        ScenarioSuiteConfig(registry_name="first", suite_class=HorizontalTechniquesSuite),
        ScenarioSuiteConfig(registry_name="second", suite_class=HorizontalTechniquesSuite, tags=("extra",)),
        ScenarioSuiteConfig(registry_name="untagged", suite_class=HorizontalTechniquesSuite, tags=()),
    )
    with patch.object(ScenarioSuiteInitializer, "CONFIGS", configs):
        initializer = ScenarioSuiteInitializer()
        initializer.set_params_from_args(args={"tags": tags})
        await initializer.initialize_async()
        await initializer.initialize_async()
    registry = ScenarioSuiteRegistry.get_registry_singleton()
    assert registry.instances.get_names() == expected
    assert len(ScenarioPresetRegistry.get_registry_singleton().instances.get_names()) == 4 * len(expected)
    for suite in registry.instances.get_all_instances():
        for entry in suite.instance.entries:
            assert entry.entry_id == uuid5(NAMESPACE_URL, f"pyrit:scenario_suite:{suite.name}:{entry.preset_name}")


def test_overrides_reset_defaults_replace_lists_and_preserve_catalog() -> None:
    args = {"num_jailbreaks": 2, "languages": ["French", "Spanish"], "selection_seed": 42}
    config = ScenarioSuiteConfig(registry_name=SUITE, suite_class=HorizontalTechniquesSuite, constructor_args=args)
    overrides = {
        "num_jailbreaks": None,
        "jailbreak_names": ["aim.yaml"],
        "languages": ["Japanese"],
        "selection_seed": None,
    }
    with patch.object(ScenarioSuiteInitializer, "CONFIGS", (config,)):
        builder = _configured(overrides)._configured_recipes()[0][1]
        assert isinstance(builder, HorizontalTechniquesSuite)
        assert builder.num_jailbreaks is None and builder.selection_seed == 0
        builder.languages.append("Korean")
        unmodified = ScenarioSuiteInitializer()._configured_recipes()[0][1]
        assert isinstance(unmodified, HorizontalTechniquesSuite)
        assert unmodified.languages == ["French", "Spanish"]
    assert args == {"num_jailbreaks": 2, "languages": ["French", "Spanish"], "selection_seed": 42}
    assert overrides["languages"] == ["Japanese"]


@pytest.mark.usefixtures("isolated_registries", "catalogs")
@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("kind", ["preset", "suite"])
async def test_late_conflicts_preserve_existing_content_and_register_nothing_async(*, stored: bool, kind: str) -> None:
    preset_registry = ScenarioPresetRegistry.get_registry_singleton()
    suite_registry = ScenarioSuiteRegistry.get_registry_singleton()
    if kind == "preset":
        conflict = ScenarioPreset(name=f"{SUITE}_obfuscation", scenario_name="airt.jailbreak")
        if stored:
            preset_registry.save_preset(preset=conflict, expected_version=None)
        else:
            preset_registry.register_preset(preset=conflict)
    else:
        conflict = ScenarioSuite(name=SUITE, description="different")
        if stored:
            suite_registry.save_suite(suite=conflict, expected_version=None)
        else:
            suite_registry.register_suite(suite=conflict)
    with pytest.raises(ValueError, match="stored document|already registered"):
        await ScenarioSuiteInitializer().initialize_async()
    assert preset_registry.instances.get_names() == ([conflict.name] if kind == "preset" and not stored else [])
    assert suite_registry.instances.get_names() == ([SUITE] if kind == "suite" and not stored else [])


@pytest.mark.usefixtures("isolated_registries")
async def test_custom_builder_contract_and_duplicate_presets_async() -> None:
    class StaticSuite:
        DESCRIPTION: ClassVar[str] = "Static test recipe."

        def build_presets(self, *, suite_name: str) -> list[ScenarioPreset]:
            return [ScenarioPreset(name=f"{suite_name}_direct", scenario_name="airt.jailbreak")]

    class RequiredSuite(StaticSuite):
        def __init__(self, *, label: str) -> None:
            self.label = label

    with patch.object(
        ScenarioSuiteInitializer, "CONFIGS", (ScenarioSuiteConfig(registry_name="required", suite_class=RequiredSuite),)
    ):
        with pytest.raises(ValueError, match="requires 'label'"):
            ScenarioSuiteInitializer().validate_params()
    with patch.object(
        ScenarioSuiteInitializer, "CONFIGS", (ScenarioSuiteConfig(registry_name="custom", suite_class=StaticSuite),)
    ):
        await ScenarioSuiteInitializer().initialize_async()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == ["custom_direct"]


@pytest.mark.usefixtures("isolated_registries", "catalogs")
async def test_empty_and_duplicate_batches_do_not_register_async() -> None:
    preset = ScenarioPreset(name="duplicate", scenario_name="airt.jailbreak")
    for presets in ([], [preset, preset]):
        with patch.object(HorizontalTechniquesSuite, "build_presets", return_value=presets), pytest.raises(ValueError):
            await ScenarioSuiteInitializer().initialize_async()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []


@pytest.mark.usefixtures("isolated_registries", "catalogs")
async def test_catalog_loading_is_off_loop_and_discovery_does_not_build_async() -> None:
    loop_thread = threading.get_ident()

    def discover() -> list[str]:
        assert threading.get_ident() != loop_thread
        return [f"t{i}.yaml" for i in range(10)]

    with patch.object(HorizontalTechniquesSuite, "build_presets", side_effect=AssertionError("must not build")):
        registry = InitializerRegistry()
        names = registry.get_class_names()
        assert "scenario_suite" in names and "horizontal_techniques" not in names
        metadata = registry.get_registered_class_metadata("scenario_suite")
        assert {p.name for p in metadata.supported_parameters} == {"tags", "suite_args"}
        for parameter in metadata.supported_parameters:
            parameter.model_dump_json()
    with patch.object(TextJailBreak, "get_jailbreak_templates", side_effect=discover):
        await ScenarioSuiteInitializer().initialize_async()


@pytest.mark.usefixtures("isolated_registries")
async def test_plain_recipe_constructed_once_per_initialization_without_preflight_reuse_async() -> None:
    constructed = []
    built = []

    class PlainRecipe:
        DESCRIPTION: ClassVar[str] = "No base class or constructor caching."

        def __init__(self, *, label: str = "configured") -> None:
            self.label = label
            constructed.append(self)

        def build_presets(self, *, suite_name: str) -> list[ScenarioPreset]:
            built.append(self)
            return [ScenarioPreset(name=f"{suite_name}_direct", scenario_name="airt.jailbreak", description=self.label)]

    configs = (ScenarioSuiteConfig(registry_name="plain", suite_class=PlainRecipe),)
    with patch.object(ScenarioSuiteInitializer, "CONFIGS", configs):
        initializer = ScenarioSuiteInitializer()
        initializer.validate_params()
        assert len(constructed) == 1
        assert built == []

        await initializer.initialize_async()
        assert len(constructed) == 2
        assert built == [constructed[1]]

        await initializer.initialize_async()
        assert len(constructed) == 3
        assert built == [constructed[1], constructed[2]]


@pytest.mark.usefixtures("isolated_registries")
@pytest.mark.parametrize("phase", ["preflight", "initialize"])
async def test_custom_constructor_validation_is_preserved_async(phase: str) -> None:
    class ValidatedRecipe:
        DESCRIPTION: ClassVar[str] = "Side-effect-free domain validation."

        def __init__(self, *, count: int = 1) -> None:
            if count < 1:
                raise ValueError("count must be positive")

        def build_presets(self, *, suite_name: str) -> list[ScenarioPreset]:
            raise AssertionError("Invalid configuration must not build presets")

    configs = (ScenarioSuiteConfig(registry_name="custom", suite_class=ValidatedRecipe),)
    with patch.object(ScenarioSuiteInitializer, "CONFIGS", configs):
        initializer = ScenarioSuiteInitializer()
        for value in (0, True):
            initializer.set_params_from_args(args={"suite_args": {"custom": {"count": value}}})
            with pytest.raises(ValueError):
                if phase == "preflight":
                    initializer.validate_params()
                else:
                    await initializer.initialize_async()
    assert ScenarioPresetRegistry.get_registry_singleton().instances.get_names() == []
    assert ScenarioSuiteRegistry.get_registry_singleton().instances.get_names() == []


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
        seeds=[SeedObjective(value=objective, dataset_name="suite_objectives")], added_by="test"
    )
    initializer = _configured(
        {
            "dataset_names": ["suite_objectives"],
            "dataset_fetch": "never",
            "jailbreak_names": ["aim.yaml"],
            "languages": ["French"],
            "encoding_techniques": ["base64"],
            "obfuscation_techniques": ["char_swap"],
        }
    )
    await initializer.initialize_async()
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
    service = ScenarioRunService()
    try:
        for entry, count in zip(resolved.entries, [2, 1, 1, 1], strict=True):
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
