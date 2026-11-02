# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario suite registry."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from pyrit.models.catalog import ScenarioSuite, ScenarioSuiteEntry
from pyrit.registry import (
    ScenarioSuiteConflictError,
    ScenarioSuiteNameRegisteredError,
    ScenarioSuiteRegistry,
    ScenarioSuiteStorage,
)

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _suite(
    name: str = "weekly",
    *,
    description: str | None = None,
    preset_names: tuple[str, ...] = ("nightly",),
) -> ScenarioSuite:
    """Build a suite with stable entry identifiers."""
    entry_ids = (ENTRY_ONE, ENTRY_TWO)
    return ScenarioSuite(
        name=name,
        description=description,
        entries=[
            ScenarioSuiteEntry(entry_id=entry_ids[index], preset_name=preset_name)
            for index, preset_name in enumerate(preset_names)
        ],
    )


@pytest.fixture
def registry(tmp_path: Path) -> ScenarioSuiteRegistry:
    """Create a registry backed by an isolated suite directory."""
    return ScenarioSuiteRegistry(storage=ScenarioSuiteStorage(source=str(tmp_path)))


def test_saved_suite_round_trips_as_stored(registry: ScenarioSuiteRegistry) -> None:
    """Test stored origin and opaque version projection."""
    created = registry.save_suite(suite=_suite(), expected_version=None)

    entry = registry.get_suite(name="weekly")

    assert entry is not None
    assert entry.suite == created.suite
    assert entry.origin == "stored"
    assert entry.version == created.version


def test_listing_merges_both_origins_in_name_order(registry: ScenarioSuiteRegistry) -> None:
    """Test the combined suite library ordering."""
    registry.save_suite(suite=_suite("stored_suite"), expected_version=None)
    registry.register_suite(suite=_suite("code_suite"))

    assert [(entry.suite.name, entry.origin) for entry in registry.list_suites()] == [
        ("code_suite", "registered"),
        ("stored_suite", "stored"),
    ]


def test_registered_suite_is_readable_and_has_no_version(registry: ScenarioSuiteRegistry) -> None:
    """Test the read-only code-registration contract."""
    registry.register_suite(suite=_suite())

    entry = registry.get_suite(name="weekly")

    assert entry is not None
    assert entry.origin == "registered"
    assert entry.version is None
    assert registry.get_suite_version(name="weekly") is None


def test_registered_suites_support_instance_tags(registry: ScenarioSuiteRegistry) -> None:
    """Test registry metadata for future built-in suite discovery."""
    registry.register_suite(suite=_suite(), tags=["starter"])

    assert [entry.name for entry in registry.instances.get_by_tag(tag="starter")] == ["weekly"]
    assert len(registry.instances.list_metadata()) == 1


def test_registering_the_same_name_twice_is_refused(registry: ScenarioSuiteRegistry) -> None:
    """Test one code owner per registered name."""
    registry.register_suite(suite=_suite())

    with pytest.raises(ValueError, match="weekly"):
        registry.register_suite(suite=_suite())


def test_stored_document_shadows_registered_suite(
    registry: ScenarioSuiteRegistry,
    tmp_path: Path,
) -> None:
    """Test operator precedence on both list and point reads."""
    registry.register_suite(suite=_suite(description="code"))
    (tmp_path / "weekly.json").write_text(
        json.dumps({"description": "operator", "entries": []}),
        encoding="utf-8",
    )

    listed = registry.list_suites()
    read = registry.get_suite(name="weekly")

    assert len(listed) == 1
    assert listed[0].origin == "stored"
    assert listed[0].suite.description == "operator"
    assert read is not None
    assert read.origin == "stored"
    assert read.suite.description == "operator"


def test_registering_a_shadowed_name_logs_the_precedence(
    registry: ScenarioSuiteRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that an existing collision is not silent."""
    registry.save_suite(suite=_suite(), expected_version=None)

    with caplog.at_level(logging.WARNING):
        registry.register_suite(suite=_suite())

    assert "takes precedence" in caplog.text


def test_saving_a_registered_name_raises_dedicated_error(registry: ScenarioSuiteRegistry) -> None:
    """Test actionable rejection distinct from optimistic concurrency."""
    registry.register_suite(suite=_suite())

    with pytest.raises(ScenarioSuiteNameRegisteredError) as caught:
        registry.save_suite(suite=_suite(), expected_version=None)

    assert caught.value.name == "weekly"
    assert not isinstance(caught.value, ScenarioSuiteConflictError)


def test_updating_a_shadowing_document_raises_registered_error(registry: ScenarioSuiteRegistry) -> None:
    """Test that an existing operator shadow cannot turn a code-owned name into an edit path."""
    created = registry.save_suite(suite=_suite(description="operator"), expected_version=None)
    registry.register_suite(suite=_suite(description="code"))

    with pytest.raises(ScenarioSuiteNameRegisteredError):
        registry.save_suite(suite=_suite(description="updated"), expected_version=created.version)


def test_update_with_stale_version_still_uses_storage_conflict(registry: ScenarioSuiteRegistry) -> None:
    """Test that ordinary stored concurrency behavior is preserved."""
    created = registry.save_suite(suite=_suite(), expected_version=None)
    registry.save_suite(suite=_suite(description="winner"), expected_version=created.version)

    with pytest.raises(ScenarioSuiteConflictError):
        registry.save_suite(suite=_suite(description="stale"), expected_version=created.version)


def test_delete_never_removes_a_registered_suite(registry: ScenarioSuiteRegistry) -> None:
    """Test that deletion is limited to the persistence layer."""
    registry.register_suite(suite=_suite())

    assert registry.delete_suite(name="weekly") is False
    assert registry.get_suite(name="weekly") is not None


def test_delete_shadowed_document_reveals_registered_suite(registry: ScenarioSuiteRegistry) -> None:
    """Test fallback to code after removing an operator override."""
    registry.save_suite(suite=_suite(description="operator"), expected_version=None)
    registry.register_suite(suite=_suite(description="code"))

    assert registry.delete_suite(name="weekly") is True
    remaining = registry.get_suite(name="weekly")
    assert remaining is not None
    assert remaining.origin == "registered"
    assert remaining.suite.description == "code"


def test_resolve_checks_stored_version(registry: ScenarioSuiteRegistry) -> None:
    """Test future execution resolution against the reviewed document."""
    created = registry.save_suite(suite=_suite(), expected_version=None)

    resolved = registry.resolve_suite(name="weekly", expected_version=created.version)

    assert resolved is not None
    assert resolved.origin == "stored"
    with pytest.raises(ScenarioSuiteConflictError):
        registry.resolve_suite(name="weekly", expected_version="stale")


def test_resolve_registered_suite_rejects_a_document_version(registry: ScenarioSuiteRegistry) -> None:
    """Test transition from a reviewed document to a registered fallback."""
    created = registry.save_suite(suite=_suite(), expected_version=None)
    registry.delete_suite(name="weekly")
    registry.register_suite(suite=_suite())

    with pytest.raises(ScenarioSuiteConflictError):
        registry.resolve_suite(name="weekly", expected_version=created.version)

    resolved = registry.resolve_suite(name="weekly", expected_version=None)
    assert resolved is not None
    assert resolved.origin == "registered"


def test_get_version_recovers_a_malformed_document(
    registry: ScenarioSuiteRegistry,
    tmp_path: Path,
) -> None:
    """Test overwriting unreadable operator data only with its current version."""
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    version = registry.get_suite_version(name="broken")

    assert version is not None
    assert registry.get_suite(name="broken") is None
    assert registry.save_suite(suite=_suite("broken"), expected_version=version).suite.name == "broken"


def test_external_storage_changes_are_visible_immediately(
    registry: ScenarioSuiteRegistry,
    tmp_path: Path,
) -> None:
    """Test that the registry does not cache stored suites."""
    assert registry.list_suites() == []
    (tmp_path / "external.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    assert [entry.suite.name for entry in registry.list_suites()] == ["external"]


def test_startup_scan_counts_valid_suites_and_reports_invalid_ones(
    registry: ScenarioSuiteRegistry,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test startup visibility without failing on malformed operator data."""
    registry.save_suite(suite=_suite(), expected_version=None)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        loaded = registry.scan_stored_suites()

    assert loaded == 1
    assert "broken" in caplog.text


def test_unavailable_storage_does_not_fail_startup(caplog: pytest.LogCaptureFixture) -> None:
    """Test startup resilience when the configured source is unreachable."""
    storage = MagicMock(spec=ScenarioSuiteStorage)
    storage.list_suites.side_effect = OSError("source unavailable")
    registry = ScenarioSuiteRegistry(storage=storage)

    with caplog.at_level(logging.ERROR):
        assert registry.scan_stored_suites() == 0

    assert "Could not read scenario suites" in caplog.text


def test_configure_source_replaces_an_earlier_source(tmp_path: Path) -> None:
    """Test registry ownership of suite persistence configuration."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = ScenarioSuiteRegistry(storage=ScenarioSuiteStorage(source=str(first)))
    registry.save_suite(suite=_suite(), expected_version=None)

    registry.configure_source(str(second))

    assert registry.source == str(second)
    assert registry.list_suites() == []


def test_constructing_registry_does_not_touch_storage() -> None:
    """Test lazy default storage construction."""
    with patch.object(ScenarioSuiteStorage, "__init__", side_effect=AssertionError("storage was constructed")):
        ScenarioSuiteRegistry()


def test_registry_singleton_is_shared() -> None:
    """Test the common registry singleton lifecycle."""
    ScenarioSuiteRegistry.reset_registry_singleton()
    try:
        assert ScenarioSuiteRegistry.get_registry_singleton() is ScenarioSuiteRegistry.get_registry_singleton()
    finally:
        ScenarioSuiteRegistry.reset_registry_singleton()
