# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for versioned scenario suite execution metadata."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    LaunchScenarioSuiteRequest,
    ResolveScenarioPresetRequest,
    ScenarioRunState,
    ScenarioSuiteExecutionEntrySummary,
    ScenarioSuiteExecutionMetadata,
    ScenarioSuiteLaunchEntrySnapshot,
    ScenarioSuiteSubmissionState,
)

ENTRY_ID = UUID("11111111-1111-4111-8111-111111111111")


def test_suite_execution_metadata_round_trips_through_scenario_metadata() -> None:
    """Test the persisted envelope keeps server and definition identities intact."""
    assert SCENARIO_SUITE_EXECUTION_METADATA_KEY == "scenario_suite_execution"

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


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ScenarioSuiteSubmissionState.SUBMITTING, "SUBMITTING"),
        (ScenarioRunState.QUEUED, "QUEUED"),
    ],
)
def test_entry_summary_reuses_submission_and_scenario_run_states(
    state: ScenarioSuiteSubmissionState | ScenarioRunState,
    expected: str,
) -> None:
    """Test the shared response accepts suite admission and ordinary run states."""
    summary = ScenarioSuiteExecutionEntrySummary(
        entry_id=ENTRY_ID,
        entry_position=1,
        preset_name="nightly",
        preset_origin="registered",
        state=state,
    )

    assert summary.state == state
    assert summary.model_dump(mode="json")["state"] == expected


def test_preset_and_suite_launch_contracts_share_run_validation() -> None:
    """Test both launch surfaces reject concurrency outside the ordinary run contract."""
    with pytest.raises(ValidationError):
        ResolveScenarioPresetRequest(target_name="gpt4", max_concurrency=0)

    with pytest.raises(ValidationError):
        LaunchScenarioSuiteRequest(
            entries=[
                ScenarioSuiteLaunchEntrySnapshot(
                    entry_id=ENTRY_ID,
                    preset_name="nightly",
                    origin="registered",
                    preset_version=None,
                )
            ],
            target_name="gpt4",
            max_concurrency=0,
        )
