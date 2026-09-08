# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for canonical scenario catalog models."""

import pytest
from pydantic import ValidationError

from pyrit.models import (
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
)


def test_run_size_estimate_requires_available_total_to_match_components() -> None:
    """Available estimates require an additive component total."""
    with pytest.raises(ValidationError, match="components total 6, not 7"):
        ScenarioRunSizeEstimate(
            estimated_attack_count=7,
            components=[ScenarioRunSizeComponent(label="Techniques", count=6)],
        )


def test_run_size_estimate_allows_unavailable_count_with_components() -> None:
    """Unavailable estimates retain useful candidate components and an explanatory note."""
    estimate = ScenarioRunSizeEstimate(
        components=[ScenarioRunSizeComponent(label="Candidate techniques", count=6)],
        note="The final count depends on target capabilities.",
    )

    assert estimate.estimated_attack_count is None
    assert estimate.components[0].count == 6


def test_run_size_estimate_serializes_canonical_api_shape() -> None:
    """The estimate exposes only the available count and additive components."""
    estimate = ScenarioRunSizeEstimate(
        estimated_attack_count=6,
        components=[ScenarioRunSizeComponent(label="Techniques", count=6)],
    )

    assert estimate.model_dump(mode="json") == {
        "estimated_attack_count": 6,
        "minimum_attack_count": None,
        "maximum_attack_count": None,
        "components": [
            {
                "label": "Techniques",
                "count": 6,
                "note": None,
                "is_baseline": False,
            }
        ],
        "datasets": [],
        "effective_parameters": {},
        "note": None,
    }


def test_run_size_estimate_rejects_inverted_bounds() -> None:
    """The minimum estimate cannot exceed the maximum estimate."""
    with pytest.raises(ValidationError, match="Minimum attack count cannot exceed maximum attack count"):
        ScenarioRunSizeEstimate(minimum_attack_count=8, maximum_attack_count=4)


def test_unavailable_run_size_estimate_has_no_count() -> None:
    """The unavailable factory communicates that a count cannot be calculated."""
    estimate = ScenarioRunSizeEstimate.unavailable()

    assert estimate.estimated_attack_count is None
    assert estimate.note == "Default-run size estimate is unavailable."


def test_estimate_exposes_dataset_counts_structurally() -> None:
    """Effective dataset selection remains machine-readable."""
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name="harmbench",
                logical_seed_group_count=100,
                selected_seed_group_count=4,
                selection_note="The default selection uses 4 of 100 logical seed groups.",
                configured_caps=[
                    ScenarioDatasetSizeCap(
                        label="per-dataset cap",
                        count=4,
                        configured_on="dataset",
                        dataset_name="harmbench",
                    )
                ],
            )
        ],
        note="The final count depends on target capabilities.",
    )

    assert estimate.estimated_attack_count is None
    assert estimate.model_dump(mode="json")["datasets"] == [
        {
            "name": "harmbench",
            "kind": "dataset",
            "logical_seed_group_count": 100,
            "selected_seed_group_count": 4,
            "selection_note": "The default selection uses 4 of 100 logical seed groups.",
            "configured_caps": [
                {
                    "label": "per-dataset cap",
                    "count": 4,
                    "configured_on": "dataset",
                    "dataset_name": "harmbench",
                }
            ],
        }
    ]


def test_estimate_request_reuses_dataset_filter_validation() -> None:
    """Configured estimates reject the same unsupported dataset filters as launches."""
    with pytest.raises(ValidationError, match="Unknown dataset filter 'unknown'"):
        ScenarioRunSizeEstimateRequest(dataset_filters={"unknown": ["value"]})
