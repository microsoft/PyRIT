# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario scan definition models."""

from uuid import UUID

import pytest

from pyrit.models.catalog.scenario_scan import SCAN_SCHEMA_VERSION, ScenarioScan, ScenarioScanEntry

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _entry(*, entry_id: UUID = ENTRY_ONE, preset_name: str = "nightly") -> ScenarioScanEntry:
    """Build a scan entry."""
    return ScenarioScanEntry(entry_id=entry_id, preset_name=preset_name)


def test_scan_preserves_entry_order_exactly() -> None:
    """Test that ordered preset references are not sorted or deduplicated."""
    scan = ScenarioScan(
        name="weekly",
        entries=[
            _entry(entry_id=ENTRY_TWO, preset_name="second"),
            _entry(entry_id=ENTRY_ONE, preset_name="first"),
        ],
    )

    assert [entry.entry_id for entry in scan.entries] == [ENTRY_TWO, ENTRY_ONE]
    assert [entry.preset_name for entry in scan.entries] == ["second", "first"]


def test_scan_allows_duplicate_preset_names_with_distinct_entry_ids() -> None:
    """Test that one preset may appear multiple times in a scan."""
    scan = ScenarioScan(
        name="weekly",
        entries=[
            _entry(entry_id=ENTRY_ONE),
            _entry(entry_id=ENTRY_TWO),
        ],
    )

    assert [entry.preset_name for entry in scan.entries] == ["nightly", "nightly"]


def test_scan_rejects_duplicate_entry_ids() -> None:
    """Test that stable entry identity remains unique within one scan."""
    with pytest.raises(ValueError, match="entry_id.*duplicated"):
        ScenarioScan(
            name="weekly",
            entries=[
                _entry(entry_id=ENTRY_ONE, preset_name="first"),
                _entry(entry_id=ENTRY_ONE, preset_name="second"),
            ],
        )


def test_scan_defaults_optional_metadata_and_entries() -> None:
    """Test the minimal user-authored scan shape."""
    scan = ScenarioScan(name="weekly")

    assert scan.schema_version == 1
    assert scan.description is None
    assert scan.author is None
    assert scan.entries == []


def test_schema_version_default_stays_pinned_to_first_shape() -> None:
    """Test that a key-less historical document remains a version 1 document."""
    assert ScenarioScan.model_fields["schema_version"].default == 1


def test_scan_rejects_future_schema_version() -> None:
    """Test that a future document cannot be silently reinterpreted."""
    with pytest.raises(ValueError, match="newer than this build reads"):
        ScenarioScan(name="weekly", schema_version=SCAN_SCHEMA_VERSION + 1)


@pytest.mark.parametrize("name", ["Weekly", "weekly-scan", "1weekly", "", "a" * 65])
def test_scan_rejects_invalid_names(name: str) -> None:
    """Test that a scan name must be safe as a document key."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        ScenarioScan(name=name)


@pytest.mark.parametrize("preset_name", ["Nightly", "nightly-preset", "1nightly", ""])
def test_entry_rejects_invalid_preset_names(preset_name: str) -> None:
    """Test that referenced preset names use the registry naming contract."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        _entry(preset_name=preset_name)


def test_scan_rejects_unknown_fields() -> None:
    """Test that misspelled hand-authored fields fail loudly."""
    with pytest.raises(ValueError, match="presets"):
        ScenarioScan(name="weekly", presets=[])  # type: ignore[call-arg]
