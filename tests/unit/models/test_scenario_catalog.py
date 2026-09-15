# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for canonical scenario catalog models."""

import pytest
from pydantic import ValidationError

from pyrit.models import (
    ScenarioAdaptiveRunSizeDetails,
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateCondition,
    ScenarioRunSizeEstimateRequest,
)
from pyrit.models.catalog import (
    ScenarioDefaultRunSizeEstimate,
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
    assert payload["minimum_attack_count"] is None
    assert payload["maximum_attack_count"] is None
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


@pytest.mark.parametrize("field_name", ["minimum_attack_count", "maximum_attack_count"])
def test_exact_default_run_size_requires_bounds_to_match_total(field_name: str) -> None:
    """Exact estimates reject bounds that disagree with their authoritative total."""
    with pytest.raises(ValidationError, match=f"{field_name} to equal total_attack_count"):
        ScenarioDefaultRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Exact,
            total_attack_count=6,
            components=[ScenarioRunSizeComponent(label="Techniques", count=6)],
            **{field_name: 5},
        )


def test_default_run_size_requires_ordered_nonnegative_bounds() -> None:
    """Conditional estimate bounds remain nonnegative and ordered."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ScenarioDefaultRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Conditional,
            minimum_attack_count=-1,
        )

    with pytest.raises(ValidationError, match="minimum_attack_count must be less than or equal"):
        ScenarioDefaultRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Conditional,
            minimum_attack_count=20,
            maximum_attack_count=12,
        )


def test_conditional_default_run_size_allows_unknown_bounds() -> None:
    """Conditional estimates may remain unbounded when no truthful range is available."""
    estimate = ScenarioDefaultRunSizeEstimate(status=ScenarioRunSizeEstimateStatus.Conditional)

    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None


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
        "minimum_attack_count": None,
        "maximum_attack_count": None,
        "condition": None,
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
                "condition": None,
            }
        ],
        "datasets": [],
        "effective_parameters": {},
        "adaptive_details": None,
        "note": None,
        "retries_included": False,
    }


def test_run_size_estimate_rejects_inverted_bounds() -> None:
    """The minimum estimate cannot exceed the maximum estimate."""
    with pytest.raises(ValidationError, match="minimum_attack_count must be less than or equal"):
        ScenarioRunSizeEstimate(minimum_attack_count=8, maximum_attack_count=4)


def test_unavailable_run_size_estimate_has_no_count() -> None:
    """The unavailable factory communicates that a count cannot be calculated."""
    estimate = ScenarioRunSizeEstimate.unavailable()

    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert estimate.total_attack_count is None


def test_adaptive_run_size_details_serialize_derived_attempt_bounds() -> None:
    """Adaptive estimates expose progress objectives and underlying attempt bounds separately."""
    details = ScenarioAdaptiveRunSizeDetails(
        objective_count=21,
        selected_candidate_technique_count=2,
        candidate_technique_count=2,
        max_attempts_per_objective=3,
        techniques_per_objective_upper_bound=2,
        technique_attempt_count_upper_bound=42,
    )

    assert details.model_dump(mode="json") == {
        "objective_count": 21,
        "selected_candidate_technique_count": 2,
        "candidate_technique_count": 2,
        "max_attempts_per_objective": 3,
        "techniques_per_objective_upper_bound": 2,
        "technique_attempt_count_upper_bound": 42,
        "stop_on_first_success": True,
        "compatibility_may_reduce_attempts": True,
    }


def test_adaptive_run_size_details_reject_inconsistent_attempt_bounds() -> None:
    """Adaptive work bounds cannot drift from the selected pool and configured cap."""
    with pytest.raises(ValidationError, match="min\\(candidate_technique_count, max_attempts_per_objective\\)"):
        ScenarioAdaptiveRunSizeDetails(
            objective_count=21,
            selected_candidate_technique_count=2,
            candidate_technique_count=2,
            max_attempts_per_objective=3,
            techniques_per_objective_upper_bound=3,
            technique_attempt_count_upper_bound=63,
        )


def test_adaptive_run_size_details_accept_legacy_version_one_payload() -> None:
    """Version-one payloads without the additive selected count remain readable."""
    details = ScenarioAdaptiveRunSizeDetails.model_validate(
        {
            "objective_count": 21,
            "candidate_technique_count": 2,
            "max_attempts_per_objective": 3,
            "techniques_per_objective_upper_bound": 2,
            "technique_attempt_count_upper_bound": 42,
        }
    )

    assert details.selected_candidate_technique_count == 2


def test_adaptive_run_size_details_reject_more_compatible_than_selected_candidates() -> None:
    """Resolved compatible candidates cannot exceed the concrete selected pool."""
    with pytest.raises(ValidationError, match="cannot exceed selected_candidate_technique_count"):
        ScenarioAdaptiveRunSizeDetails(
            objective_count=21,
            selected_candidate_technique_count=2,
            candidate_technique_count=3,
            max_attempts_per_objective=3,
            techniques_per_objective_upper_bound=3,
            technique_attempt_count_upper_bound=63,
        )


def test_conditional_estimate_exposes_dataset_counts_structurally() -> None:
    """Conditionality and effective dataset selection are machine-readable."""
    estimate = ScenarioDefaultRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Conditional,
        minimum_attack_count=12,
        maximum_attack_count=20,
        condition=ScenarioRunSizeEstimateCondition.TargetCapabilities,
        datasets=[
            ScenarioDatasetSummary(
                name="harmbench",
                logical_seed_group_count=100,
                selected_seed_group_count=4,
                selection_note="The default selection uses 4 of 100 available objectives.",
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
    assert payload["minimum_attack_count"] == 12
    assert payload["maximum_attack_count"] == 20
    assert payload["condition"] == "target_capabilities"
    assert payload["datasets"] == [
        {
            "name": "harmbench",
            "kind": "dataset",
            "logical_seed_group_count": 100,
            "selected_seed_group_count": 4,
            "selection_note": "The default selection uses 4 of 100 available objectives.",
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
