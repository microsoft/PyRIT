# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for canonical scenario catalog models."""

import pytest
from pydantic import ValidationError

from pyrit.models import (
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    ScenarioDefaultRunSizeEstimate,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateRequest,
    ScenarioRunSizeEstimateStatus,
    ScenarioRunSizeFactor,
)


def test_run_size_estimate_compatibility_alias_is_canonical_model() -> None:
    """The initial DTO name remains an unambiguous alias of the versioned model."""
    assert ScenarioRunSizeEstimate is ScenarioDefaultRunSizeEstimate


def test_run_size_estimate_accepts_legacy_fields_and_serializes_canonically() -> None:
    """Legacy constructors parse while the wire shape remains singular and versioned."""
    estimate = ScenarioRunSizeEstimate.model_validate(
        {
            "status": "exact",
            "total": 2,
            "components": [{"label": "Sweep", "count": 2}],
            "datasets": [
                {
                    "name": "harmbench",
                    "seed_group_count": 100,
                    "selected_seed_group_count": 2,
                }
            ],
            "caveat": "Legacy explanation.",
        }
    )

    assert estimate.total == 2
    assert estimate.caveat == "Legacy explanation."
    payload = estimate.model_dump(mode="json")
    assert payload["version"] == 1
    assert payload["total_attack_count"] == 2
    assert payload["note"] == "Legacy explanation."
    assert payload["datasets"][0]["logical_seed_group_count"] == 100
    assert "total" not in payload
    assert "caveat" not in payload
    assert "seed_group_count" not in payload["datasets"][0]


def test_run_size_estimate_normalizes_legacy_componentless_exact_total() -> None:
    estimate = ScenarioRunSizeEstimate.model_validate({"status": "exact", "total": 2})

    assert estimate.total_attack_count == 2
    assert estimate.components == [
        ScenarioRunSizeComponent(
            label="Legacy total",
            count=2,
            note="Normalized from a legacy component-less estimate.",
        )
    ]


def test_exact_default_run_size_requires_component_total() -> None:
    """Exact estimates reject totals that do not match their additive components."""
    with pytest.raises(ValidationError, match="components total 6, not 7"):
        ScenarioDefaultRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Exact,
            total_attack_count=7,
            components=[
                ScenarioRunSizeComponent(
                    label="Techniques",
                    count=6,
                    factors=[
                        ScenarioRunSizeFactor(label="seed groups", count=3),
                        ScenarioRunSizeFactor(label="techniques", count=2),
                    ],
                )
            ],
        )


def test_run_size_component_requires_factor_product() -> None:
    """Components reject counts that disagree with their ordered formula factors."""
    with pytest.raises(ValidationError, match="factor product \\(6\\)"):
        ScenarioRunSizeComponent(
            label="Techniques",
            count=7,
            factors=[
                ScenarioRunSizeFactor(label="seed groups", count=3),
                ScenarioRunSizeFactor(label="techniques", count=2),
            ],
        )


def test_default_run_size_serializes_versioned_api_shape() -> None:
    """The estimate exposes stable status, total, component, and factor fields."""
    estimate = ScenarioDefaultRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Exact,
        total_attack_count=6,
        components=[
            ScenarioRunSizeComponent(
                label="Techniques",
                count=6,
                factors=[
                    ScenarioRunSizeFactor(label="seed groups", count=3),
                    ScenarioRunSizeFactor(label="techniques", count=2),
                ],
            )
        ],
    )

    assert estimate.model_dump(mode="json") == {
        "version": 1,
        "status": "exact",
        "total_attack_count": 6,
        "components": [
            {
                "label": "Techniques",
                "count": 6,
                "factors": [
                    {"label": "seed groups", "count": 3},
                    {"label": "techniques", "count": 2},
                ],
                "note": None,
                "is_baseline": False,
            }
        ],
        "datasets": [],
        "note": None,
        "retries_included": False,
    }


def test_conditional_estimate_exposes_dataset_counts_structurally() -> None:
    """Conditionality and effective dataset selection are machine-readable."""
    estimate = ScenarioDefaultRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Conditional,
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
        note="The final total depends on target capabilities.",
    )

    payload = estimate.model_dump(mode="json")
    assert payload["status"] == "conditional"
    assert payload["total_attack_count"] is None
    assert payload["datasets"] == [
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
    assert payload["retries_included"] is False


def test_estimate_request_reuses_dataset_filter_validation() -> None:
    """Configured estimates reject the same unsupported dataset filters as launches."""
    with pytest.raises(ValidationError, match="Unknown dataset filter 'unknown'"):
        ScenarioRunSizeEstimateRequest(dataset_filters={"unknown": ["value"]})
