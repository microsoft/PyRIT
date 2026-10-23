# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario suite definition models."""

from uuid import UUID

import pytest

from pyrit.models.catalog.scenario_suite import SUITE_SCHEMA_VERSION, ScenarioSuite, ScenarioSuiteEntry

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _entry(*, entry_id: UUID = ENTRY_ONE, preset_name: str = "nightly") -> ScenarioSuiteEntry:
    """Build a suite entry."""
    return ScenarioSuiteEntry(entry_id=entry_id, preset_name=preset_name)


def test_suite_preserves_entry_order_exactly() -> None:
    """Test that ordered preset references are not sorted or deduplicated."""
    suite = ScenarioSuite(
        name="weekly",
        entries=[
            _entry(entry_id=ENTRY_TWO, preset_name="second"),
            _entry(entry_id=ENTRY_ONE, preset_name="first"),
        ],
    )

    assert [entry.entry_id for entry in suite.entries] == [ENTRY_TWO, ENTRY_ONE]
    assert [entry.preset_name for entry in suite.entries] == ["second", "first"]


def test_suite_allows_duplicate_preset_names_with_distinct_entry_ids() -> None:
    """Test that one preset may appear multiple times in a suite."""
    suite = ScenarioSuite(
        name="weekly",
        entries=[
            _entry(entry_id=ENTRY_ONE),
            _entry(entry_id=ENTRY_TWO),
        ],
    )

    assert [entry.preset_name for entry in suite.entries] == ["nightly", "nightly"]


def test_suite_rejects_duplicate_entry_ids() -> None:
    """Test that stable entry identity remains unique within one suite."""
    with pytest.raises(ValueError, match="entry_id.*duplicated"):
        ScenarioSuite(
            name="weekly",
            entries=[
                _entry(entry_id=ENTRY_ONE, preset_name="first"),
                _entry(entry_id=ENTRY_ONE, preset_name="second"),
            ],
        )


def test_suite_defaults_optional_metadata_and_entries() -> None:
    """Test the minimal user-authored suite shape."""
    suite = ScenarioSuite(name="weekly")

    assert suite.schema_version == 1
    assert suite.description is None
    assert suite.author is None
    assert suite.entries == []


def test_schema_version_default_stays_pinned_to_first_shape() -> None:
    """Test that a key-less historical document remains a version 1 document."""
    assert ScenarioSuite.model_fields["schema_version"].default == 1


def test_suite_rejects_future_schema_version() -> None:
    """Test that a future document cannot be silently reinterpreted."""
    with pytest.raises(ValueError, match="newer than this build reads"):
        ScenarioSuite(name="weekly", schema_version=SUITE_SCHEMA_VERSION + 1)


@pytest.mark.parametrize("name", ["Weekly", "weekly-suite", "1weekly", "", "a" * 65])
def test_suite_rejects_invalid_names(name: str) -> None:
    """Test that a suite name must be safe as a document key."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        ScenarioSuite(name=name)


@pytest.mark.parametrize("preset_name", ["Nightly", "nightly-preset", "1nightly", ""])
def test_entry_rejects_invalid_preset_names(preset_name: str) -> None:
    """Test that referenced preset names use the registry naming contract."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        _entry(preset_name=preset_name)


def test_suite_rejects_unknown_fields() -> None:
    """Test that misspelled hand-authored fields fail loudly."""
    with pytest.raises(ValueError, match="presets"):
        ScenarioSuite(name="weekly", presets=[])  # type: ignore[call-arg]
