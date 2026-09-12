# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario progress plan validation."""

import pytest
from pydantic import ValidationError

from pyrit.models import ScenarioRunPlan, ScenarioRunPlanAtomicGroup, ScenarioRunPlanSeedGroup


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
