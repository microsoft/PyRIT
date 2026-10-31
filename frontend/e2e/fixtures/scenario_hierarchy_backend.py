# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Disposable saved Scenario run with compound results for hierarchy browser acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import UUID

from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory

RUN_ID = UUID(int=0x3602)
CHILD_COUNT = 520
MISSING_POSITION = 1
DUPLICATE_POSITION = 3


async def _seed_async(memory: SQLiteMemory) -> None:
    from pyrit.common.utils import to_sha256
    from pyrit.models import (
        SCENARIO_RUN_PLAN_METADATA_KEY,
        AtomicAttackIdentifier,
        AttackIdentifier,
        AttackOutcome,
        AttackResult,
        AttackResultRole,
        ScenarioIdentifier,
        ScenarioResult,
        ScenarioRunPlan,
        ScenarioRunPlanAtomicGroup,
        ScenarioRunPlanGroupKind,
        ScenarioRunPlanSeedGroup,
        ScenarioRunState,
        TargetIdentifier,
    )

    memory.disable_embedding()
    timestamp = datetime(2026, 10, 28, tzinfo=UTC)
    target = TargetIdentifier(class_name="MockPromptTarget", class_module="hierarchy_browser_fixture")
    identifier = AtomicAttackIdentifier.build(
        attack_identifier=AttackIdentifier(
            class_name="SequentialAttack",
            class_module="pyrit.executor.attack",
            objective_target=target,
        )
    )
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=name,
                atomic_attack_name=name,
                display_group=display,
                technique_eval_hash=f"{name}-eval",
                kind=ScenarioRunPlanGroupKind.ATTACK,
                seed_group_ids=["seed"],
            )
            for name, display in [("sequential", "Sequential"), ("single", "Single turn")]
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed", objective="seed", objective_sha256=to_sha256("seed"))],
    )

    def result(
        index: int,
        group: str,
        *,
        objective: str,
        outcome: AttackOutcome = AttackOutcome.FAILURE,
        child_ids: list[str] | None = None,
        attempt_index: int | None = None,
    ) -> AttackResult:
        return AttackResult(
            attack_result_id=str(UUID(int=0x360200000 + index)),
            conversation_id=str(UUID(int=0x360300000 + index)),
            objective=objective,
            outcome=outcome,
            timestamp=timestamp + timedelta(seconds=index),
            atomic_attack_identifier=identifier,
            attribution_parent_id=str(RUN_ID),
            attribution_data={
                "seed_group_id": "seed",
                "parent_eval_hash": f"{group}-eval",
                "parent_collection": group,
                # Like SequentialAttack: children share the parent's attribution plus their position.
                **({"attempt_index": attempt_index} if attempt_index is not None else {}),
                "result_role": (
                    AttackResultRole.ORCHESTRATION.value
                    if child_ids is not None
                    else AttackResultRole.TARGET_FACING.value
                ),
            },
            metadata={"child_attack_result_ids": child_ids} if child_ids is not None else {},
        )

    grandchildren = [
        result(1000 + i, "sequential", objective=f"Nested step {i + 1}", attempt_index=i + 1) for i in range(2)
    ]
    children = [
        result(
            10 + i,
            "sequential",
            objective=f"Step {i + 1}",
            outcome=AttackOutcome.SUCCESS if i == 4 else AttackOutcome.FAILURE,
            child_ids=[g.attack_result_id for g in grandchildren] if i == 0 else None,
            attempt_index=i + 1,
        )
        for i in range(CHILD_COUNT)
    ]
    child_ids = [child.attack_result_id for child in children]
    child_ids[MISSING_POSITION] = str(UUID(int=0x3602DEAD))
    child_ids[DUPLICATE_POSITION] = child_ids[0]
    parent = result(1, "sequential", objective="Sequential parent", child_ids=child_ids)
    single = result(2, "single", objective="Single-turn attempt", outcome=AttackOutcome.SUCCESS)

    rows = {"sequential": [parent, *children, *grandchildren], "single": [single]}
    await memory.add_attack_results_to_memory_async(attack_results=[row for group in rows.values() for row in group])
    await memory.add_scenario_results_to_memory_async(
        scenario_results=[
            ScenarioResult(
                id=RUN_ID,
                scenario_identifier=ScenarioIdentifier(
                    class_name="HierarchyBrowserScenario",
                    class_module="hierarchy_browser_fixture",
                    version=1,
                    objective_target=target,
                ),
                scenario_description="Offline compound-result hierarchy acceptance fixture.",
                attack_results=rows,
                metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
                scenario_run_state=ScenarioRunState.COMPLETED,
                creation_time=timestamp,
                completion_time=timestamp + timedelta(minutes=1),
            )
        ]
    )


if __name__ == "__main__":
    with patch.object(analytics_backend, "_seed_async", _seed_async):
        analytics_backend.main()
