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
    ScenarioRunSizeEstimateCondition,
    ScenarioRunSizeEstimateRequest,
    ScenarioRunSizeEstimateStatus,
    ScenarioRunSizeFactor,
)
from pyrit.models.catalog.scenario import RunScenarioRequest


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_adversarial_default_request_field_is_optional_and_round_trips(model: type) -> None:
    required = {"scenario_name": "airt.scam", "target_name": "objective"} if model is RunScenarioRequest else {}
    assert model(**required).adversarial_target_name is None
    request = model(adversarial_target_name="adversarial", **required)
    assert model.model_validate_json(request.model_dump_json()).adversarial_target_name == "adversarial"
    with pytest.raises(ValidationError, match="adversarial_target_name"):
        model(adversarial_target_name="", **required)


def test_run_size_estimate_compatibility_alias_is_canonical_model() -> None:
    """The descriptive default-estimate name aliases the existing public model."""
    assert ScenarioDefaultRunSizeEstimate is ScenarioRunSizeEstimate


def test_run_size_estimate_preserves_legacy_total_and_serializes_additively() -> None:
    """The original total field remains available beside the canonical structured fields."""
    estimate = ScenarioRunSizeEstimate(
        estimated_attack_count=6,
        components=[
            ScenarioRunSizeComponent(
                label="Techniques",
                count=4,
                factors=[
                    ScenarioRunSizeFactor(label="seed groups", count=2),
                    ScenarioRunSizeFactor(label="techniques", count=2),
                ],
            ),
            ScenarioRunSizeComponent(
                label="Baseline",
                count=2,
                factors=[ScenarioRunSizeFactor(label="seed groups", count=2)],
                is_baseline=True,
            ),
        ],
        effective_parameters={"include_baseline": True, "techniques": ["one", "two"]},
        note="Retries are excluded.",
    )

    assert estimate.status is ScenarioRunSizeEstimateStatus.Exact
    assert estimate.total_attack_count == 6
    assert estimate.estimated_attack_count == 6
    assert estimate.minimum_attack_count == 6
    assert estimate.maximum_attack_count == 6
    payload = estimate.model_dump(mode="json")
    assert list(payload["effective_parameters"]) == ["include_baseline", "techniques"]
    assert payload == {
        "status": "exact",
        "total_attack_count": 6,
        "minimum_attack_count": 6,
        "maximum_attack_count": 6,
        "condition": None,
        "components": [
            {
                "label": "Techniques",
                "count": 4,
                "factors": [
                    {"label": "seed groups", "count": 2},
                    {"label": "techniques", "count": 2},
                ],
                "is_baseline": False,
                "note": None,
            },
            {
                "label": "Baseline",
                "count": 2,
                "factors": [{"label": "seed groups", "count": 2}],
                "is_baseline": True,
                "note": None,
            },
        ],
        "datasets": [],
        "configured_dataset_size": None,
        "effective_parameters": {
            "include_baseline": True,
            "techniques": ["one", "two"],
        },
        "note": "Retries are excluded.",
        "estimated_attack_count": 6,
    }


def test_run_size_estimate_rejects_conflicting_total_aliases() -> None:
    """Canonical and compatibility totals cannot drift."""
    with pytest.raises(ValidationError, match="compatibility total fields must match"):
        ScenarioRunSizeEstimate.model_validate(
            {
                "total_attack_count": 2,
                "estimated_attack_count": 3,
                "components": [{"label": "Techniques", "count": 2}],
            }
        )


def test_approximate_estimate_round_trip_preserves_unknown_population() -> None:
    estimate = ScenarioRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Approximate,
        total_attack_count=10,
        configured_dataset_size=5,
        components=[ScenarioRunSizeComponent(label="Techniques", count=10)],
        datasets=[ScenarioDatasetSummary(name="not-loaded")],
    )
    restored = ScenarioRunSizeEstimate.model_validate_json(estimate.model_dump_json())
    assert restored.status is ScenarioRunSizeEstimateStatus.Approximate
    assert restored.estimated_attack_count == 10
    assert restored.minimum_attack_count is None
    assert restored.maximum_attack_count is None
    assert restored.datasets[0].logical_seed_group_count is None
    assert restored.datasets[0].selected_seed_group_count is None


def test_approximate_estimate_still_requires_consistent_components() -> None:
    with pytest.raises(ValidationError, match="components total"):
        ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Approximate,
            total_attack_count=10,
            components=[ScenarioRunSizeComponent(label="Techniques", count=9)],
        )


def test_run_size_estimate_accepts_canonical_total_input() -> None:
    """The canonical total is the stored model field."""
    estimate = ScenarioRunSizeEstimate(
        total_attack_count=2,
        components=[ScenarioRunSizeComponent(label="Techniques", count=2)],
    )

    assert estimate.total_attack_count == 2
    assert estimate.estimated_attack_count == 2
    assert "total_attack_count" in ScenarioRunSizeEstimate.model_json_schema(mode="validation")["properties"]


@pytest.mark.parametrize(
    "payload",
    [
        {"total_attack_count": 3, "estimated_attack_count": None},
        {"total_attack_count": None, "estimated_attack_count": 3},
    ],
)
def test_run_size_estimate_normalizes_null_compatibility_totals(payload: dict[str, int | None]) -> None:
    """A null duplicate total cannot hide the known value from another accepted spelling."""
    estimate = ScenarioRunSizeEstimate.model_validate(
        {
            **payload,
            "components": [{"label": "Techniques", "count": 3}],
        }
    )

    assert estimate.status is ScenarioRunSizeEstimateStatus.Exact
    assert estimate.total_attack_count == 3
    assert estimate.estimated_attack_count == 3


def test_run_size_estimate_requires_exact_total_to_match_components() -> None:
    """Exact estimates require an additive component total."""
    with pytest.raises(ValidationError, match="components total 6, not 7"):
        ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Exact,
            estimated_attack_count=7,
            components=[ScenarioRunSizeComponent(label="Techniques", count=6)],
        )


@pytest.mark.parametrize("field_name", ["minimum_attack_count", "maximum_attack_count"])
def test_run_size_estimate_requires_exact_bounds_to_match_total(field_name: str) -> None:
    """Exact bounds cannot disagree with the authoritative total."""
    with pytest.raises(ValidationError, match=f"{field_name} to equal total_attack_count"):
        ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Exact,
            estimated_attack_count=6,
            components=[ScenarioRunSizeComponent(label="Techniques", count=6)],
            **{field_name: 5},
        )


def test_conditional_run_size_preserves_unknown_values_and_explanation() -> None:
    """Conditional estimates retain structure without turning unknown totals into zero."""
    estimate = ScenarioRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Conditional,
        condition=ScenarioRunSizeEstimateCondition.TargetCapabilities,
        components=[ScenarioRunSizeComponent(label="Candidate techniques", count=6)],
        note="The final count depends on target capabilities.",
    )

    assert estimate.status is ScenarioRunSizeEstimateStatus.Conditional
    assert estimate.total_attack_count is None
    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None
    assert estimate.components[0].count == 6


def test_ranged_run_size_exposes_explicit_bounds() -> None:
    """A conditional range remains numeric without inventing an exact total."""
    estimate = ScenarioRunSizeEstimate(
        status=ScenarioRunSizeEstimateStatus.Conditional,
        minimum_attack_count=2,
        maximum_attack_count=4,
        condition=ScenarioRunSizeEstimateCondition.LaunchConfiguration,
        components=[
            ScenarioRunSizeComponent(
                label="Candidate techniques",
                count=4,
                factors=[
                    ScenarioRunSizeFactor(label="seed groups", count=2),
                    ScenarioRunSizeFactor(label="techniques", count=2),
                ],
            )
        ],
    )

    assert estimate.total_attack_count is None
    assert estimate.minimum_attack_count == 2
    assert estimate.maximum_attack_count == 4
    assert estimate.model_dump(mode="json")["status"] == "conditional"


def test_run_size_estimate_rejects_inverted_bounds() -> None:
    """The minimum estimate cannot exceed the maximum estimate."""
    with pytest.raises(ValidationError, match="minimum_attack_count must be less than or equal"):
        ScenarioRunSizeEstimate(minimum_attack_count=8, maximum_attack_count=4)


@pytest.mark.parametrize(
    ("minimum", "maximum", "component_count", "message"),
    [
        (3, 5, 2, "below minimum_attack_count 3"),
        (1, 3, 4, "above maximum_attack_count 3"),
    ],
)
def test_ranged_run_size_rejects_component_total_outside_bounds(
    minimum: int,
    maximum: int,
    component_count: int,
    message: str,
) -> None:
    """Ranged estimates reject explanatory arithmetic outside their bounds."""
    with pytest.raises(ValidationError, match=message):
        ScenarioRunSizeEstimate(
            minimum_attack_count=minimum,
            maximum_attack_count=maximum,
            components=[ScenarioRunSizeComponent(label="Candidates", count=component_count)],
        )


def test_run_size_component_requires_factor_product() -> None:
    """A component count must match its ordered multiplicative factors."""
    with pytest.raises(ValidationError, match=r"factor product \(6\)"):
        ScenarioRunSizeComponent(
            label="Techniques",
            count=7,
            factors=[
                ScenarioRunSizeFactor(label="seed groups", count=3),
                ScenarioRunSizeFactor(label="techniques", count=2),
            ],
        )


@pytest.mark.parametrize(
    "status",
    [
        ScenarioRunSizeEstimateStatus.Conditional,
        ScenarioRunSizeEstimateStatus.Unavailable,
    ],
)
def test_non_exact_run_size_rejects_total(status: ScenarioRunSizeEstimateStatus) -> None:
    """A numeric total cannot contradict a non-exact status."""
    with pytest.raises(ValidationError, match="cannot include total_attack_count"):
        ScenarioRunSizeEstimate(
            status=status,
            estimated_attack_count=1,
            components=[ScenarioRunSizeComponent(label="Candidate", count=1)],
        )


@pytest.mark.parametrize(
    "status",
    [
        ScenarioRunSizeEstimateStatus.Exact,
        ScenarioRunSizeEstimateStatus.Unavailable,
    ],
)
def test_non_conditional_run_size_rejects_condition(status: ScenarioRunSizeEstimateStatus) -> None:
    """Only conditional estimates can carry a reason that their total is unresolved."""
    kwargs: dict[str, object] = {}
    if status is ScenarioRunSizeEstimateStatus.Exact:
        kwargs = {
            "total_attack_count": 1,
            "components": [ScenarioRunSizeComponent(label="Candidate", count=1)],
        }

    message = f"{status.value.capitalize()} run-size estimates cannot include condition"
    with pytest.raises(ValidationError, match=message):
        ScenarioRunSizeEstimate(
            status=status,
            condition=ScenarioRunSizeEstimateCondition.LaunchConfiguration,
            **kwargs,
        )


def test_unavailable_run_size_estimate_has_no_count() -> None:
    """The unavailable factory communicates that a count cannot be calculated."""
    estimate = ScenarioRunSizeEstimate.unavailable()

    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert estimate.total_attack_count is None
    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None
    assert estimate.note == "Default-run size estimate is unavailable."


@pytest.mark.parametrize(
    "estimate_kwargs",
    [
        {"minimum_attack_count": 1},
        {"components": [ScenarioRunSizeComponent(label="Candidate", count=1)]},
    ],
)
def test_unavailable_run_size_rejects_numeric_values(estimate_kwargs: dict[str, object]) -> None:
    """Unavailable estimates cannot disguise numeric information as unknown."""
    with pytest.raises(ValidationError, match="Unavailable run-size estimates cannot include numeric"):
        ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Unavailable,
            **estimate_kwargs,
        )


def test_estimate_preserves_ordered_datasets_structurally() -> None:
    """Dataset and cap order remains stable through construction and serialization."""
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
            ),
            ScenarioDatasetSummary(
                name="second",
                logical_seed_group_count=2,
                selected_seed_group_count=2,
            ),
        ],
        note="The final count depends on target capabilities.",
    )

    assert estimate.estimated_attack_count is None
    payload = estimate.model_dump(mode="json")
    assert [dataset["name"] for dataset in payload["datasets"]] == ["harmbench", "second"]
    assert payload["datasets"][0]["configured_caps"] == [
        {
            "label": "per-dataset cap",
            "count": 4,
            "configured_on": "dataset",
            "dataset_name": "harmbench",
        }
    ]


def test_estimate_request_reuses_dataset_filter_validation() -> None:
    """Configured estimates reject the same unsupported dataset filters as launches."""
    with pytest.raises(ValidationError, match="Unknown dataset filter 'unknown'"):
        ScenarioRunSizeEstimateRequest(dataset_filters={"unknown": ["value"]})
