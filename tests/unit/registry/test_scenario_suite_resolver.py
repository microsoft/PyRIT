# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for registry-backed scenario preset and suite launch resolution."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    DatasetSourceInput,
    LaunchScenarioSuiteRequest,
    ResolveScenarioPresetRequest,
    ScenarioDatasetRequest,
    ScenarioPreset,
    ScenarioPresetLaunchDefaults,
    ScenarioSuite,
    ScenarioSuiteEntry,
    ScenarioSuiteLaunchEntrySnapshot,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.registry import (
    ResolvedScenarioSuiteEntry,
    ScenarioPresetRegistry,
    ScenarioPresetStorage,
    ScenarioRegistry,
    ScenarioSuiteExecutionNotFoundError,
    ScenarioSuiteLaunchConflictError,
    ScenarioSuiteLaunchResolver,
    ScenarioSuiteNotRunnableError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
    resolve_launch_defaults,
    resolve_scenario_preset_run_request,
)

ENTRY_ID = UUID("11111111-1111-4111-8111-111111111111")
EXECUTION_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
OPERATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _preset(**overrides: object) -> ScenarioPreset:
    """Build one launchable preset definition."""
    fields: dict[str, object] = {
        "name": "nightly",
        "scenario_name": "foundry.red_team_agent",
    }
    fields.update(overrides)
    return ScenarioPreset(**fields)  # type: ignore[arg-type]


def _suite() -> ScenarioSuite:
    """Build one suite referencing the test preset."""
    return ScenarioSuite(
        name="weekly",
        entries=[ScenarioSuiteEntry(entry_id=ENTRY_ID, preset_name="nightly")],
    )


@pytest.fixture
def registries(tmp_path: Path) -> tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry]:
    """Create isolated suite and preset registries."""
    return (
        ScenarioSuiteRegistry(storage=ScenarioSuiteStorage(source=str(tmp_path / "suites"))),
        ScenarioPresetRegistry(storage=ScenarioPresetStorage(source=str(tmp_path / "presets"))),
    )


class TestPresetRunRequestResolution:
    """A preset plus shared launch fields becomes an ordinary run request."""

    def test_unset_run_fields_use_ordinary_request_defaults(self) -> None:
        resolved = resolve_scenario_preset_run_request(
            preset=_preset(),
            launch=ResolveScenarioPresetRequest(target_name="gpt4"),
        )

        assert resolved.max_concurrency == 10
        assert resolved.max_retries == 0
        assert resolved.operation_id is None
        assert resolved.techniques is None
        assert resolved.datasets is None
        assert resolved.dataset_request is None
        assert resolved.include_baseline is None
        assert resolved.scenario_params is None

    def test_preset_and_launch_fields_are_preserved(self) -> None:
        preset = _preset(
            techniques=["crescendo"],
            datasets=ScenarioDatasetRequest(
                sources=[DatasetSourceInput(name="harmbench", max_size=10)],
                max_total=25,
                filters={"harm_categories": ["violence"]},
            ),
            include_baseline=False,
            scenario_params={"max_turns": 3},
        )
        launch = ResolveScenarioPresetRequest(
            target_name="gpt4",
            adversarial_target_name="adversary",
            initializers=["scorer"],
            initializer_args={"scorer": {"threshold": 0.5}},
            max_concurrency=4,
            max_retries=2,
            operation_id=OPERATION_ID,
            labels={"operator": "red"},
        )

        resolved = resolve_scenario_preset_run_request(preset=preset, launch=launch)

        assert resolved.scenario_name == preset.scenario_name
        assert resolved.techniques == preset.techniques
        assert resolved.datasets == preset.datasets
        assert resolved.include_baseline is False
        assert resolved.scenario_params == preset.scenario_params
        assert resolved.target_name == "gpt4"
        assert resolved.adversarial_target_name == "adversary"
        assert resolved.initializers == ["scorer"]
        assert resolved.initializer_args == {"scorer": {"threshold": 0.5}}
        assert resolved.max_concurrency == 4
        assert resolved.max_retries == 2
        assert resolved.operation_id == OPERATION_ID
        assert resolved.labels == {"operator": "red"}


def test_suite_resolver_returns_ordered_child_request_and_metadata(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test full versioned resolution without depending on backend services."""
    suite_registry, preset_registry = registries
    preset = preset_registry.save_preset(preset=_preset(), expected_version=None)
    suite = suite_registry.save_suite(suite=_suite(), expected_version=None)
    resolver = ScenarioSuiteLaunchResolver(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
    )
    request = LaunchScenarioSuiteRequest(
        expected_version=suite.version,
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ID,
                preset_name="nightly",
                origin="stored",
                preset_version=preset.version,
            )
        ],
        target_name="gpt4",
    )

    with patch.object(preset_registry, "check_preset", return_value=[]):
        resolved = resolver.resolve(name="weekly", request=request)

    assert resolved.suite.suite == suite.suite
    assert resolved.suite.origin == "stored"
    assert resolved.suite.version == suite.version
    assert len(resolved.entries) == 1
    entry = resolved.entries[0]
    assert entry.entry_id == ENTRY_ID
    assert entry.entry_position == 1
    assert entry.preset_origin == "stored"
    assert entry.preset_version == preset.version
    assert entry.request.scenario_name == "foundry.red_team_agent"
    assert entry.request.target_name == "gpt4"
    assert entry.metadata(suite_execution_id=EXECUTION_ID, suite=suite)[SCENARIO_SUITE_EXECUTION_METADATA_KEY] == {
        "version": 1,
        "suite_execution_id": str(EXECUTION_ID),
        "suite_name": "weekly",
        "suite_version": suite.version,
        "suite_entry_id": str(ENTRY_ID),
        "entry_position": 1,
        "preset_name": "nightly",
        "preset_version": preset.version,
    }


def test_suite_resolver_requires_a_reviewed_stored_version(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test stored definitions cannot launch without optimistic-concurrency evidence."""
    suite_registry, preset_registry = registries
    preset = preset_registry.save_preset(preset=_preset(), expected_version=None)
    suite_registry.save_suite(suite=_suite(), expected_version=None)
    resolver = ScenarioSuiteLaunchResolver(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
    )
    request = LaunchScenarioSuiteRequest(
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ID,
                preset_name="nightly",
                origin="stored",
                preset_version=preset.version,
            )
        ],
        target_name="gpt4",
    )

    with pytest.raises(ScenarioSuiteLaunchConflictError, match="no reviewed version"):
        resolver.resolve(name="weekly", request=request)


def test_suite_resolver_reports_a_missing_definition(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test definition absence is distinct from a stale reviewed snapshot."""
    suite_registry, preset_registry = registries
    resolver = ScenarioSuiteLaunchResolver(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
    )
    request = LaunchScenarioSuiteRequest(
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ID,
                preset_name="nightly",
                origin=None,
                preset_version=None,
            )
        ],
        target_name="gpt4",
    )

    with pytest.raises(ScenarioSuiteExecutionNotFoundError, match="weekly"):
        resolver.resolve(name="weekly", request=request)


def test_suite_resolver_rejects_an_unavailable_preset(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test a saved suite stays editable but cannot launch with an unresolved entry."""
    suite_registry, preset_registry = registries
    suite = suite_registry.save_suite(suite=_suite(), expected_version=None)
    resolver = ScenarioSuiteLaunchResolver(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
    )
    request = LaunchScenarioSuiteRequest(
        expected_version=suite.version,
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ID,
                preset_name="nightly",
                origin=None,
                preset_version=None,
            )
        ],
        target_name="gpt4",
    )

    with pytest.raises(ScenarioSuiteNotRunnableError, match="Entry 1"):
        resolver.resolve(name="weekly", request=request)


SECOND_ENTRY_ID = UUID("22222222-2222-4222-8222-222222222222")
REFUSE_SCORER = RegisteredComponentReference(registry_name="inverted_refusal", identifier_hash="a" * 64)
ALLOW_SCORER = RegisteredComponentReference(registry_name="refusal", identifier_hash="b" * 64)
OVERRIDE_SCORER = RegisteredComponentReference(registry_name="override", identifier_hash="c" * 64)


def _scenario_registry(*, built_in_scorer_scenarios: frozenset[str] = frozenset()) -> MagicMock:
    """Build a scenario registry whose named scenarios refuse a custom objective scorer."""
    registry = MagicMock(spec=ScenarioRegistry)
    registry.get_registered_class_metadata.side_effect = lambda name: MagicMock(
        supports_objective_scorer=name not in built_in_scorer_scenarios
    )
    return registry


def _resolve_two_scored_entries(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
    *,
    objective_scorer: RegisteredComponentReference | None,
    second_scorer: RegisteredComponentReference | None,
    second_scenario: str = "foundry.red_team_agent",
    scenario_registry: MagicMock | None = None,
) -> tuple[ResolvedScenarioSuiteEntry, ...]:
    suite_registry, preset_registry = registries
    refuse = preset_registry.save_preset(
        preset=_preset(name="refuse", launch_defaults=ScenarioPresetLaunchDefaults(objective_scorer=REFUSE_SCORER)),
        expected_version=None,
    )
    allow = preset_registry.save_preset(
        preset=_preset(
            name="allow",
            scenario_name=second_scenario,
            launch_defaults=ScenarioPresetLaunchDefaults(objective_scorer=second_scorer),
        ),
        expected_version=None,
    )
    suite = suite_registry.save_suite(
        suite=ScenarioSuite(
            name="weekly",
            entries=[
                ScenarioSuiteEntry(entry_id=ENTRY_ID, preset_name="refuse"),
                ScenarioSuiteEntry(entry_id=SECOND_ENTRY_ID, preset_name="allow"),
            ],
        ),
        expected_version=None,
    )
    request = LaunchScenarioSuiteRequest(
        expected_version=suite.version,
        entries=[
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=ENTRY_ID, preset_name="refuse", origin="stored", preset_version=refuse.version
            ),
            ScenarioSuiteLaunchEntrySnapshot(
                entry_id=SECOND_ENTRY_ID, preset_name="allow", origin="stored", preset_version=allow.version
            ),
        ],
        target_name="gpt4",
        objective_scorer=objective_scorer,
    )
    resolver = ScenarioSuiteLaunchResolver(
        suite_registry=suite_registry,
        preset_registry=preset_registry,
        scenario_registry=scenario_registry if scenario_registry is not None else _scenario_registry(),
    )
    with patch.object(preset_registry, "check_preset", return_value=[]):
        return resolver.resolve(name="weekly", request=request).entries


def test_suite_children_each_receive_their_preset_scorer(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that presets pinning different scorers launch children with different scorers."""
    refuse, allow = _resolve_two_scored_entries(registries, objective_scorer=None, second_scorer=ALLOW_SCORER)

    assert refuse.request.objective_scorer == REFUSE_SCORER
    assert allow.request.objective_scorer == ALLOW_SCORER
    assert refuse.launch_value_sources["objective_scorer"] == "preset"
    assert allow.launch_value_sources["objective_scorer"] == "preset"


def test_suite_entry_without_a_preset_scorer_keeps_the_scenario_default(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that an unpinned preset still defers to the scenario's own scorer."""
    refuse, allow = _resolve_two_scored_entries(registries, objective_scorer=None, second_scorer=None)

    assert refuse.request.objective_scorer == REFUSE_SCORER
    assert allow.request.objective_scorer is None
    assert allow.launch_value_sources["objective_scorer"] == "scenario_default"


def test_launch_scorer_overrides_every_entry(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that an explicit launch scorer replaces pinned and unpinned scorers alike."""
    entries = _resolve_two_scored_entries(registries, objective_scorer=OVERRIDE_SCORER, second_scorer=None)

    assert [entry.request.objective_scorer for entry in entries] == [OVERRIDE_SCORER, OVERRIDE_SCORER]
    assert [entry.launch_value_sources["objective_scorer"] for entry in entries] == ["launch", "launch"]


def test_launch_scorer_skips_entries_whose_scenario_uses_a_built_in_scorer(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that one launch scorer does not make a mixed-capability suite unlaunchable."""
    scored, built_in = _resolve_two_scored_entries(
        registries,
        objective_scorer=OVERRIDE_SCORER,
        second_scorer=None,
        second_scenario="airt.psychosocial",
        scenario_registry=_scenario_registry(built_in_scorer_scenarios=frozenset({"airt.psychosocial"})),
    )

    assert scored.request.objective_scorer == OVERRIDE_SCORER
    assert scored.launch_value_sources["objective_scorer"] == "launch"
    assert built_in.request.objective_scorer is None
    assert built_in.launch_value_sources["objective_scorer"] == "unsupported"


def test_suite_without_a_launch_scorer_does_not_consult_scenario_capabilities(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that presets alone resolve exactly as before, without a scenario-registry lookup."""
    scenario_registry = _scenario_registry()

    _resolve_two_scored_entries(
        registries, objective_scorer=None, second_scorer=ALLOW_SCORER, scenario_registry=scenario_registry
    )

    scenario_registry.get_registered_class_metadata.assert_not_called()


def test_unregistered_scenario_keeps_the_launch_scorer_for_readiness_to_report(
    registries: tuple[ScenarioSuiteRegistry, ScenarioPresetRegistry],
) -> None:
    """Test that an unknown scenario is not silently treated as using a built-in scorer."""
    scenario_registry = MagicMock(spec=ScenarioRegistry)
    scenario_registry.get_registered_class_metadata.return_value = None

    entries = _resolve_two_scored_entries(
        registries, objective_scorer=OVERRIDE_SCORER, second_scorer=None, scenario_registry=scenario_registry
    )

    assert [entry.request.objective_scorer for entry in entries] == [OVERRIDE_SCORER, OVERRIDE_SCORER]


def test_unsupported_field_is_reported_only_when_set() -> None:
    """Test that an unsupported field left unset still reads as the scenario default."""
    preset = _preset(launch_defaults=ScenarioPresetLaunchDefaults(objective_scorer=REFUSE_SCORER))
    unset = resolve_launch_defaults(
        preset=_preset(),
        launch=ResolveScenarioPresetRequest(target_name="gpt4"),
        unsupported_fields=frozenset({"objective_scorer"}),
    )
    pinned = resolve_launch_defaults(
        preset=preset,
        launch=ResolveScenarioPresetRequest(target_name="gpt4"),
        unsupported_fields=frozenset({"objective_scorer"}),
    )

    assert unset.sources["objective_scorer"] == "scenario_default"
    assert pinned.values["objective_scorer"] is None
    assert pinned.sources["objective_scorer"] == "unsupported"


def test_single_preset_resolution_uses_the_pinned_scorer_unless_overridden() -> None:
    """Test that resolving one preset applies the same precedence as a suite entry."""
    preset = _preset(launch_defaults=ScenarioPresetLaunchDefaults(objective_scorer=REFUSE_SCORER))

    pinned = resolve_launch_defaults(preset=preset, launch=ResolveScenarioPresetRequest(target_name="gpt4"))
    overridden = resolve_scenario_preset_run_request(
        preset=preset, launch=ResolveScenarioPresetRequest(target_name="gpt4", objective_scorer=OVERRIDE_SCORER)
    )

    assert pinned.values["objective_scorer"] == REFUSE_SCORER
    assert pinned.sources["objective_scorer"] == "preset"
    assert overridden.objective_scorer == OVERRIDE_SCORER
