# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario progress plan validation."""

import pytest
from pydantic import ValidationError

from pyrit.models import ScenarioRunPlan, ScenarioRunPlanAtomicGroup, ScenarioRunPlanGroupKind, ScenarioRunPlanSeedGroup


def _seed(*, seed_id: str = "seed-1") -> ScenarioRunPlanSeedGroup:
    return ScenarioRunPlanSeedGroup(id=seed_id, objective_sha256=f"sha-{seed_id}", objective=seed_id)


def _group(*, group_id: str = "group-1", seed_group_ids: list[str] | None = None) -> ScenarioRunPlanAtomicGroup:
    return ScenarioRunPlanAtomicGroup(
        id=group_id,
        atomic_attack_name=group_id,
        display_group=group_id,
        technique_eval_hash=f"eval-{group_id}",
        seed_group_ids=seed_group_ids or ["seed-1"],
    )


@pytest.mark.parametrize(
    ("atomic_groups", "seed_groups", "match"),
    [
        ([_group(), _group()], [_seed()], "duplicate atomic group IDs"),
        ([_group()], [_seed(), _seed()], "duplicate seed group IDs"),
        ([_group(seed_group_ids=["seed-1", "seed-1"])], [_seed()], "duplicate seed group IDs"),
        ([_group(seed_group_ids=["missing"])], [_seed()], "unknown seed group IDs"),
    ],
)
def test_run_plan_rejects_ambiguous_or_invalid_normalized_ids(
    atomic_groups: list[ScenarioRunPlanAtomicGroup],
    seed_groups: list[ScenarioRunPlanSeedGroup],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        ScenarioRunPlan(atomic_groups=atomic_groups, seed_groups=seed_groups)


def test_run_plan_preserves_ordered_adaptive_choices() -> None:
    group = _group().model_copy(
        update={
            "kind": ScenarioRunPlanGroupKind.ADAPTIVE,
            "selected_technique_eval_hashes": ["second", "first"],
        }
    )
    plan = ScenarioRunPlan(atomic_groups=[group], seed_groups=[_seed()])

    restored = ScenarioRunPlan.model_validate_json(plan.model_dump_json(exclude_none=True))

    assert restored.atomic_groups[0].selected_technique_eval_hashes == ["second", "first"]


def test_run_plan_without_adaptive_choices_round_trips_unchanged() -> None:
    plan = ScenarioRunPlan(atomic_groups=[_group()], seed_groups=[_seed()])
    payload = plan.model_dump(mode="json", exclude_none=True)

    assert "selected_technique_eval_hashes" not in payload["atomic_groups"][0]
    restored = ScenarioRunPlan.model_validate(payload)
    assert restored.model_dump(mode="json", exclude_none=True) == payload


def test_run_plan_rejects_empty_adaptive_choices() -> None:
    payload = _group().model_dump()
    payload["selected_technique_eval_hashes"] = []

    with pytest.raises(ValidationError, match="selected_technique_eval_hashes"):
        ScenarioRunPlanAtomicGroup.model_validate(payload)
