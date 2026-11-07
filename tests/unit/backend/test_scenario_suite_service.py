# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario suite definition service."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from azure.core.exceptions import AzureError

from pyrit.backend.services.scenario_suite_service import ScenarioSuiteService, get_scenario_suite_service
from pyrit.models.catalog import PresetEntry, PresetIssue, ScenarioPreset, ScenarioSuite, ScenarioSuiteEntry
from pyrit.registry import (
    ScenarioPresetRegistry,
    ScenarioSuiteConflictError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
)

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _suite(*, name: str = "weekly", preset_names: tuple[str, ...] = ("nightly", "nightly")) -> ScenarioSuite:
    """Build a suite with stable entry IDs."""
    entry_ids = (ENTRY_ONE, ENTRY_TWO)
    return ScenarioSuite(
        name=name,
        entries=[
            ScenarioSuiteEntry(entry_id=entry_ids[index], preset_name=preset_name)
            for index, preset_name in enumerate(preset_names)
        ],
    )


@pytest.fixture
def preset_registry() -> Iterator[MagicMock]:
    """Install a controlled preset registry singleton."""
    registry = MagicMock(spec=ScenarioPresetRegistry)
    registry.get_preset.return_value = PresetEntry(
        preset=ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent"),
        origin="stored",
        version="preset-v1",
    )
    registry.check_preset.return_value = []
    registry.get_preset_version.return_value = None
    with patch.object(ScenarioPresetRegistry, "get_registry_singleton", return_value=registry):
        yield registry


@pytest.fixture
def service(tmp_path: Path, preset_registry: MagicMock) -> ScenarioSuiteService:
    """Create a service backed by an isolated suite registry."""
    registry = ScenarioSuiteRegistry(storage=ScenarioSuiteStorage(source=str(tmp_path)))
    return ScenarioSuiteService(registry=registry)


async def test_crud_preserves_order_and_duplicate_preset_references(
    service: ScenarioSuiteService,
) -> None:
    """Test the stored definition without deduplicating entries."""
    created = await service.save_suite_async(suite=_suite(), expected_version=None)
    loaded = await service.get_suite_async(name="weekly")

    assert loaded is not None
    assert loaded.version == created.version
    assert loaded.origin == "stored"
    assert [status.entry_id for status in loaded.entry_statuses] == [ENTRY_ONE, ENTRY_TWO]
    assert [status.preset_name for status in loaded.entry_statuses] == ["nightly", "nightly"]


async def test_duplicate_references_resolve_the_preset_once(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
) -> None:
    """Test that duplicates share one consistent live projection per read."""
    await service.save_suite_async(suite=_suite(), expected_version=None)
    preset_registry.reset_mock()

    response = await service.get_suite_async(name="weekly")

    assert response is not None
    preset_registry.get_preset.assert_called_once_with(name="nightly")
    preset_registry.check_preset.assert_called_once()


async def test_list_reuses_preset_projection_across_suites(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
) -> None:
    """Test that one list request reads each distinct preset only once."""
    await service.save_suite_async(suite=_suite(name="alpha"), expected_version=None)
    await service.save_suite_async(suite=_suite(name="beta"), expected_version=None)
    preset_registry.reset_mock()

    response = await service.list_suites_async()

    assert [item.suite.name for item in response.items] == ["alpha", "beta"]
    preset_registry.get_preset.assert_called_once_with(name="nightly")
    preset_registry.check_preset.assert_called_once()


async def test_registered_suite_is_listed_with_nullable_version(
    service: ScenarioSuiteService,
) -> None:
    """Test code-defined suite projection through the backend contract."""
    registry = service._get_registry()
    registry.register_suite(suite=_suite(name="code_suite", preset_names=("nightly",)))

    response = await service.list_suites_async()

    assert [(item.suite.name, item.origin, item.version) for item in response.items] == [
        ("code_suite", "registered", None),
    ]


@pytest.mark.parametrize(
    "error",
    [AzureError("expired preset credentials"), OSError("preset directory unavailable")],
    ids=["azure", "local"],
)
async def test_preset_storage_failure_is_advisory_after_committed_save(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
    error: Exception,
) -> None:
    """Test that response projection cannot turn a committed write into a failure."""
    preset_registry.get_preset.side_effect = error

    response = await service.save_suite_async(
        suite=_suite(preset_names=("nightly",)),
        expected_version=None,
    )

    stored = await service.load_suite_async(name="weekly")
    assert stored is not None
    assert response.entry_statuses[0].origin is None
    assert response.entry_statuses[0].preset_version is None
    assert "preset storage is temporarily unavailable" in response.entry_statuses[0].issues[0].message


async def test_current_preset_origin_version_and_issues_are_projected(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
) -> None:
    """Test that suite reads expose the preset registry's current state."""
    issue = PresetIssue(field="scenario_name", message="Scenario is not registered.")
    preset_registry.get_preset.return_value = PresetEntry(
        preset=ScenarioPreset(name="nightly", scenario_name="retired.scenario"),
        origin="registered",
        version=None,
    )
    preset_registry.check_preset.return_value = [issue]

    response = await service.save_suite_async(
        suite=_suite(preset_names=("nightly",)),
        expected_version=None,
    )

    assert response.entry_statuses[0].origin == "registered"
    assert response.entry_statuses[0].preset_version is None
    assert response.entry_statuses[0].issues == [issue]


async def test_missing_preset_is_advisory_and_does_not_block_save(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
) -> None:
    """Test portable suites with a dangling preset reference."""
    preset_registry.get_preset.return_value = None
    preset_registry.get_preset_version.return_value = None

    response = await service.save_suite_async(
        suite=_suite(preset_names=("missing",)),
        expected_version=None,
    )

    assert response.suite.entries[0].preset_name == "missing"
    assert response.entry_statuses[0].origin is None
    assert "not available" in response.entry_statuses[0].issues[0].message
    assert await service.get_suite_async(name="weekly") is not None


async def test_invalid_stored_preset_is_distinguished_from_absence(
    service: ScenarioSuiteService,
    preset_registry: MagicMock,
) -> None:
    """Test actionable advice when a preset document exists but cannot be parsed."""
    preset_registry.get_preset.return_value = None
    preset_registry.get_preset_version.return_value = "broken-version"

    response = await service.save_suite_async(
        suite=_suite(preset_names=("broken",)),
        expected_version=None,
    )

    status = response.entry_statuses[0]
    assert status.preset_version == "broken-version"
    assert "invalid or unreadable" in status.issues[0].message


async def test_list_is_sorted_by_suite_name(service: ScenarioSuiteService) -> None:
    """Test stable library ordering."""
    for name in ("zebra", "alpha", "middle"):
        await service.save_suite_async(suite=_suite(name=name), expected_version=None)

    response = await service.list_suites_async()

    assert [item.suite.name for item in response.items] == ["alpha", "middle", "zebra"]


async def test_update_requires_the_version_from_the_read(service: ScenarioSuiteService) -> None:
    """Test optimistic concurrency through the service."""
    created = await service.save_suite_async(suite=_suite(), expected_version=None)
    await service.save_suite_async(
        suite=_suite().model_copy(update={"description": "winner"}),
        expected_version=created.version,
    )

    with pytest.raises(ScenarioSuiteConflictError):
        await service.save_suite_async(
            suite=_suite().model_copy(update={"description": "stale"}),
            expected_version=created.version,
        )


async def test_delete_reports_presence(service: ScenarioSuiteService) -> None:
    """Test the delete result used by the HTTP layer."""
    await service.save_suite_async(suite=_suite(), expected_version=None)

    assert await service.delete_suite_async(name="weekly") is True
    assert await service.delete_suite_async(name="weekly") is False


def test_configure_source_replaces_storage(tmp_path: Path, preset_registry: MagicMock) -> None:
    """Test that the service reports its registry's configured source."""
    registry = ScenarioSuiteRegistry()
    service = ScenarioSuiteService(registry=registry)

    registry.configure_source(str(tmp_path))

    assert service.source == str(tmp_path)


def test_service_factory_is_a_singleton() -> None:
    """Test the backend service lifetime."""
    assert get_scenario_suite_service() is get_scenario_suite_service()
