# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for canonical scenario catalog models."""

import pytest
from pydantic import ValidationError

from pyrit.models import (
    ScenarioDatasetPopulationStatus,
    ScenarioDatasetSelection,
    ScenarioDatasetSelectionOverrideScope,
    ScenarioDatasetSizeCap,
    ScenarioDatasetSizeLimit,
    ScenarioDatasetSizeLimitDefaultScope,
    ScenarioDatasetSizeLimitOverrideScope,
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
        "dataset_cap_provenance": [],
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
            "dataset_names": ["harmbench"],
        }
    ]
    assert payload["datasets"][0]["population_status"] == "known"
    assert payload["datasets"][0]["effective_cap"] == 4
    assert payload["dataset_cap_provenance"] == [
        {
            "label": "per-dataset cap",
            "count": 4,
            "configured_on": "dataset",
            "dataset_name": "harmbench",
            "dataset_names": ["harmbench"],
        }
    ]


def test_unknown_dataset_population_serializes_null_counts_and_effective_cap() -> None:
    """An unmaterialized dataset remains explicitly unknown rather than looking empty."""
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name="harmbench",
                population_status=ScenarioDatasetPopulationStatus.Unknown,
                logical_seed_group_count=None,
                selected_seed_group_count=None,
                effective_cap=4,
                configured_caps=[
                    ScenarioDatasetSizeCap(
                        label="per-dataset cap",
                        count=4,
                        configured_on="dataset",
                        dataset_name="harmbench",
                    )
                ],
            )
        ]
    )

    payload = estimate.model_dump(mode="json")
    assert payload["datasets"][0]["population_status"] == "unknown"
    assert payload["datasets"][0]["logical_seed_group_count"] is None
    assert payload["datasets"][0]["selected_seed_group_count"] is None
    assert payload["datasets"][0]["effective_cap"] == 4


def test_dataset_cap_rejects_multiple_dataset_attribution() -> None:
    """A per-dataset cap cannot be ambiguously attributed to multiple datasets."""
    with pytest.raises(ValidationError, match="at most one dataset"):
        ScenarioDatasetSizeCap(
            label="per-dataset cap",
            count=4,
            configured_on="dataset",
            dataset_names=["first", "second"],
        )


def test_dataset_summary_rejects_inconsistent_effective_cap() -> None:
    """The effective cap must match the independently attributable configured caps."""
    with pytest.raises(ValidationError, match="effective_cap"):
        ScenarioDatasetSummary(
            name="first",
            logical_seed_group_count=4,
            selected_seed_group_count=2,
            effective_cap=3,
            configured_caps=[
                ScenarioDatasetSizeCap(
                    label="per-dataset cap",
                    count=2,
                    configured_on="dataset",
                    dataset_name="first",
                )
            ],
        )


def test_repeated_child_caps_do_not_impose_one_effective_cap() -> None:
    """Two separately sampled children can contribute more groups than either child's cap."""
    caps = [
        ScenarioDatasetSizeCap(label="per-dataset cap", count=2, configured_on="dataset", dataset_name="inline")
        for _ in range(2)
    ]
    summary = ScenarioDatasetSummary(
        name="inline",
        logical_seed_group_count=6,
        selected_seed_group_count=4,
        configured_caps=caps,
    )
    assert summary.effective_cap is None

    estimate = ScenarioRunSizeEstimate(datasets=[summary])
    assert estimate.dataset_cap_provenance == caps
    assert len(estimate.model_dump(mode="json")["dataset_cap_provenance"]) == 2

    one_capped_child = ScenarioDatasetSummary(
        name="inline",
        logical_seed_group_count=6,
        selected_seed_group_count=5,
        effective_cap=None,
        configured_caps=[caps[0]],
    )
    assert one_capped_child.effective_cap is None

    with pytest.raises(ValidationError, match="multiple child caps"):
        ScenarioDatasetSummary(
            name="inline",
            logical_seed_group_count=6,
            selected_seed_group_count=4,
            effective_cap=2,
            configured_caps=caps,
        )


def test_legacy_per_dataset_caps_without_names_remain_attributable() -> None:
    """Legacy caps that omit dataset_name are still distinct for distinct populations."""
    cap = ScenarioDatasetSizeCap(label="per-dataset cap", count=2)
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name=name,
                logical_seed_group_count=3,
                selected_seed_group_count=2,
                configured_caps=[cap],
            )
            for name in ("first", "second")
        ]
    )

    assert [(item.dataset_name, item.dataset_names) for item in estimate.dataset_cap_provenance] == [
        ("first", ["first"]),
        ("second", ["second"]),
    ]


def test_dataset_summary_rejects_unrelated_cap_provenance() -> None:
    """Nested cap provenance must include the population it annotates."""
    with pytest.raises(ValidationError, match="summarized dataset"):
        ScenarioDatasetSummary(
            name="first",
            logical_seed_group_count=4,
            selected_seed_group_count=2,
            configured_caps=[
                ScenarioDatasetSizeCap(
                    label="per-dataset cap",
                    count=2,
                    configured_on="dataset",
                    dataset_name="second",
                )
            ],
        )


def test_combined_cap_provenance_is_serialized_once_in_dataset_order() -> None:
    """A shared configuration cap is one ordered provenance record, not one per dataset."""
    first_cap = ScenarioDatasetSizeCap(
        label="per-dataset cap",
        count=3,
        configured_on="dataset",
        dataset_name="first",
    )
    second_cap = ScenarioDatasetSizeCap(
        label="per-dataset cap",
        count=4,
        configured_on="dataset",
        dataset_name="second",
    )
    shared_cap = ScenarioDatasetSizeCap(
        label="combined configuration cap",
        count=5,
        configured_on="configuration",
        dataset_names=["first", "second"],
    )
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name="first",
                logical_seed_group_count=3,
                selected_seed_group_count=2,
                configured_caps=[first_cap, shared_cap],
            ),
            ScenarioDatasetSummary(
                name="second",
                logical_seed_group_count=4,
                selected_seed_group_count=3,
                configured_caps=[second_cap, shared_cap],
            ),
        ]
    )

    payload = estimate.model_dump(mode="json")
    assert [(cap.configured_on, cap.dataset_names) for cap in estimate.dataset_cap_provenance] == [
        ("dataset", ["first"]),
        ("dataset", ["second"]),
        ("configuration", ["first", "second"]),
    ]
    assert [cap["count"] for cap in payload["dataset_cap_provenance"]] == [3, 4, 5]


def test_mixed_scope_cap_provenance_keeps_application_order() -> None:
    """A compound cap follows every child cap even when a later child uses another scope."""
    shared = ScenarioDatasetSizeCap(
        label="shared first child",
        count=2,
        configured_on="configuration",
        dataset_names=["first", "second"],
    )
    per_dataset = ScenarioDatasetSizeCap(
        label="third child",
        count=1,
        configured_on="dataset",
        dataset_name="third",
    )
    combined = ScenarioDatasetSizeCap(
        label="combined parent",
        count=3,
        configured_on="compound",
        dataset_names=["first", "second", "third"],
    )
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name="first",
                logical_seed_group_count=3,
                selected_seed_group_count=1,
                configured_caps=[shared, combined],
            ),
            ScenarioDatasetSummary(
                name="second",
                logical_seed_group_count=3,
                selected_seed_group_count=1,
                configured_caps=[shared, combined],
            ),
            ScenarioDatasetSummary(
                name="third",
                logical_seed_group_count=3,
                selected_seed_group_count=1,
                configured_caps=[per_dataset, combined],
            ),
        ]
    )

    assert [cap.label for cap in estimate.dataset_cap_provenance] == [
        "shared first child",
        "third child",
        "combined parent",
    ]
    assert [cap["label"] for cap in estimate.model_dump(mode="json")["dataset_cap_provenance"]] == [
        "shared first child",
        "third child",
        "combined parent",
    ]


def test_conflicting_cap_application_orders_raise() -> None:
    """Two datasets cannot describe the same caps in contradictory orders."""
    first = ScenarioDatasetSizeCap(label="first", count=2, configured_on="configuration", dataset_names=["a", "b"])
    second = ScenarioDatasetSizeCap(label="second", count=2, configured_on="configuration", dataset_names=["a", "b"])
    with pytest.raises(ValidationError, match="conflicting cap application order"):
        ScenarioRunSizeEstimate(
            datasets=[
                ScenarioDatasetSummary(
                    name="a",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                    configured_caps=[first, second],
                ),
                ScenarioDatasetSummary(
                    name="b",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                    configured_caps=[second, first],
                ),
            ]
        )


def test_distinct_shared_caps_with_equal_values_remain_separate() -> None:
    """Equal cap values on disjoint populations do not collapse into one provenance record."""
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name="first",
                logical_seed_group_count=3,
                selected_seed_group_count=2,
                configured_caps=[
                    ScenarioDatasetSizeCap(
                        label="combined configuration cap",
                        count=2,
                        configured_on="configuration",
                        dataset_names=["first"],
                    )
                ],
            ),
            ScenarioDatasetSummary(
                name="second",
                logical_seed_group_count=3,
                selected_seed_group_count=2,
                configured_caps=[
                    ScenarioDatasetSizeCap(
                        label="combined configuration cap",
                        count=2,
                        configured_on="configuration",
                        dataset_names=["second"],
                    )
                ],
            ),
        ]
    )

    assert [cap.dataset_names for cap in estimate.dataset_cap_provenance] == [["first"], ["second"]]


def test_repeated_shared_caps_deduplicate_only_across_dataset_summaries() -> None:
    """Equal shared caps applied twice stay distinct while their per-dataset copies collapse."""
    shared = ScenarioDatasetSizeCap(
        label="combined configuration cap",
        count=2,
        configured_on="configuration",
        dataset_names=["first", "second"],
    )
    estimate = ScenarioRunSizeEstimate(
        datasets=[
            ScenarioDatasetSummary(
                name=name,
                logical_seed_group_count=3,
                selected_seed_group_count=1,
                configured_caps=[shared, shared],
            )
            for name in ("first", "second")
        ]
    )

    assert estimate.dataset_cap_provenance == [shared, shared]


@pytest.mark.parametrize(
    ("default_scope", "default_count"),
    [
        (ScenarioDatasetSizeLimitDefaultScope.None_, 4),
        (ScenarioDatasetSizeLimitDefaultScope.Heterogeneous, 4),
        (ScenarioDatasetSizeLimitDefaultScope.PerDataset, None),
        (ScenarioDatasetSizeLimitDefaultScope.Combined, None),
    ],
)
def test_dataset_size_limit_rejects_inconsistent_default_count(
    default_scope: ScenarioDatasetSizeLimitDefaultScope,
    default_count: int | None,
) -> None:
    """Only uniform capped defaults carry one client-facing default count."""
    with pytest.raises(ValidationError, match="default_count"):
        ScenarioDatasetSizeLimit(
            default_scope=default_scope,
            default_count=default_count,
            override_scope=ScenarioDatasetSizeLimitOverrideScope.PerDataset,
        )


@pytest.mark.parametrize(
    ("scope", "allowed_names"),
    [
        (ScenarioDatasetSelectionOverrideScope.Fixed, None),
        (ScenarioDatasetSelectionOverrideScope.OneOf, []),
        (ScenarioDatasetSelectionOverrideScope.Any, ["a"]),
        (ScenarioDatasetSelectionOverrideScope.Unsupported, ["a"]),
        (ScenarioDatasetSelectionOverrideScope.FixedSet, ["a", "a"]),
    ],
)
def test_dataset_selection_rejects_inconsistent_metadata(
    scope: ScenarioDatasetSelectionOverrideScope,
    allowed_names: list[str] | None,
) -> None:
    with pytest.raises(ValidationError):
        ScenarioDatasetSelection(override_scope=scope, allowed_names=allowed_names)


def test_dataset_selection_serializes_allowed_names_in_order() -> None:
    selection = ScenarioDatasetSelection(
        override_scope=ScenarioDatasetSelectionOverrideScope.OneOf,
        allowed_names=["figstep", "figstep_pro"],
    )
    assert selection.model_dump(mode="json") == {
        "override_scope": "one_of",
        "allowed_names": ["figstep", "figstep_pro"],
    }


def test_estimate_request_reuses_dataset_filter_validation() -> None:
    """Configured estimates reject the same unsupported dataset filters as launches."""
    with pytest.raises(ValidationError, match="Unknown dataset filter 'unknown'"):
        ScenarioRunSizeEstimateRequest(dataset_filters={"unknown": ["value"]})


@pytest.mark.parametrize("dataset_names", [[], ["duplicate", "duplicate"]])
def test_estimate_request_rejects_ambiguous_dataset_names(dataset_names: list[str]) -> None:
    """Explicit dataset selections must be non-empty and unique."""
    with pytest.raises(ValidationError, match="dataset_names"):
        ScenarioRunSizeEstimateRequest(dataset_names=dataset_names)


@pytest.mark.parametrize("dataset_names", [[], ["duplicate", "duplicate"]])
def test_launch_request_rejects_ambiguous_dataset_names(dataset_names: list[str]) -> None:
    """Launch validates the same explicit dataset selection shape as preview."""
    with pytest.raises(ValidationError, match="dataset_names"):
        RunScenarioRequest(
            scenario_name="example",
            target_name="target",
            dataset_names=dataset_names,
        )
