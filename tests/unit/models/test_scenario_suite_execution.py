# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for versioned scenario suite execution metadata."""

from uuid import UUID

from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    ScenarioSuiteExecutionMetadata,
)


def test_suite_execution_metadata_round_trips_through_scenario_metadata() -> None:
    """Test the persisted envelope keeps server and definition identities intact."""
    metadata = ScenarioSuiteExecutionMetadata(
        suite_execution_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        suite_name="weekly",
        suite_version="suite-v1",
        suite_entry_id=UUID("11111111-1111-4111-8111-111111111111"),
        entry_position=2,
        preset_name="nightly",
        preset_version="preset-v1",
    )

    fields = metadata.to_metadata_fields()

    assert fields[SCENARIO_SUITE_EXECUTION_METADATA_KEY]["version"] == 1
    assert ScenarioSuiteExecutionMetadata.from_metadata(fields) == metadata


def test_direct_run_has_no_suite_execution_metadata() -> None:
    """Test absence remains distinct from an invalid or empty suite envelope."""
    assert ScenarioSuiteExecutionMetadata.from_metadata({"run_plan": {}}) is None
