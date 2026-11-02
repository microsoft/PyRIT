# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for registry-backed scenario preset and suite launch resolution."""

from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    LaunchScenarioSuiteRequest,
    ScenarioSuiteLaunchEntrySnapshot,
)
from pyrit.models.catalog import ResolveScenarioPresetRequest, ScenarioPreset, ScenarioSuite, ScenarioSuiteEntry
from pyrit.models.scenario_dataset import DatasetSourceInput, ScenarioDatasetRequest
from pyrit.registry import (
    ScenarioPresetRegistry,
    ScenarioPresetStorage,
    ScenarioSuiteExecutionNotFoundError,
    ScenarioSuiteLaunchConflictError,
    ScenarioSuiteLaunchResolver,
    ScenarioSuiteNotRunnableError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
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
