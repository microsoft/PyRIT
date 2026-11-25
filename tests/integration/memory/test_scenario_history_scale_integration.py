# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-history reads over production-shaped run plans on Azure SQL."""

import json
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter

import pytest
from sqlalchemy.orm import Session

from pyrit.backend.services import ScenarioRunService
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultEntry, AzureSQLMemory
from pyrit.memory.memory_models import ConversationEntry, ScenarioResultEntry
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    ComponentIdentifier,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunPlanSeedPrompt,
    ScenarioRunState,
)

# Shape of the production runs that stalled scenario history.
_RUN_COUNT = 5
_ATOMIC_GROUP_COUNT = 70
_SEED_GROUP_COUNT = 20
_PROMPTS_PER_SEED_GROUP = 3
_PROMPT_LENGTH = 2_000
_MIN_ATTEMPTS_PER_RUN = 100
_ATTEMPT_STEP_PER_RUN = 35
_MIN_PLAN_BYTES = 100_000
_MAX_READ_SECONDS = 10
_TEST_TIMEOUT_SECONDS = 600
_T0 = datetime(2026, 11, 24, tzinfo=UTC)
_OUTCOMES = (AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.ERROR, AttackOutcome.UNDETERMINED)


def _objective(seed_index: int) -> str:
    return f"Scale objective {seed_index}"


def _build_plan() -> ScenarioRunPlan:
    seed_groups = [
        ScenarioRunPlanSeedGroup(
            id=f"seed-{seed_index:02d}",
            objective_sha256=to_sha256(_objective(seed_index)),
            objective=_objective(seed_index),
            prompts=[
                ScenarioRunPlanSeedPrompt(value=f"{seed_index}-{sequence}-" + "p" * _PROMPT_LENGTH, sequence=sequence)
                for sequence in range(_PROMPTS_PER_SEED_GROUP)
            ],
        )
        for seed_index in range(_SEED_GROUP_COUNT)
    ]
    atomic_groups = [
        ScenarioRunPlanAtomicGroup(
            id=f"group-{group_index:02d}",
            atomic_attack_name=f"technique_{group_index:02d}",
            display_group=f"Technique {group_index % 10}",
            technique_eval_hash=f"eval-{group_index:02d}",
            # 35 groups with 3 seed groups and 35 with 4: 245 planned units.
            seed_group_ids=[
                seed_groups[(group_index * 7 + offset) % _SEED_GROUP_COUNT].id for offset in range(3 + group_index % 2)
            ],
        )
        for group_index in range(_ATOMIC_GROUP_COUNT)
    ]
    return ScenarioRunPlan(
        scenario_registry_name="scale.scenario", atomic_groups=atomic_groups, seed_groups=seed_groups
    )


def _build_attempts(
    *, run_id: str, conversation_prefix: str, plan: ScenarioRunPlan, count: int
) -> tuple[list[AttackResult], set[str]]:
    """
    Build attempts that mix explicit seed attribution, objective-only attribution, and unplanned rows.

    Returns:
        tuple[list[AttackResult], set[str]]: The attempts and the planned units they hit.
    """
    seeds = {seed.id: seed for seed in plan.seed_groups}
    attempts: list[AttackResult] = []
    planned_units: set[str] = set()
    for index in range(count):
        group = plan.atomic_groups[(index * 11) % len(plan.atomic_groups)]
        seed = seeds[group.seed_group_ids[index % len(group.seed_group_ids)]]
        attribution_data: dict[str, str | int] = {
            "parent_collection": group.atomic_attack_name,
            "parent_eval_hash": group.technique_eval_hash,
            "result_role": AttackResultRole.TARGET_FACING.value,
        }
        if index % 10 == 9:
            attribution_data["parent_collection"] = "unplanned_technique"
        elif index % 5 != 4:
            attribution_data["seed_group_id"] = seed.id
            planned_units.add(f"{group.id}/{seed.id}")
        else:
            planned_units.add(f"{group.id}/{seed.id}")
        attempts.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=f"{conversation_prefix}{run_id[:8]}-{index}",
                objective=seed.objective,
                outcome=_OUTCOMES[index % len(_OUTCOMES)],
                timestamp=_T0 + timedelta(seconds=index),
                attribution_parent_id=run_id,
                attribution_data=attribution_data,
                total_retries=index % 3,
            )
        )
    return attempts, planned_units


def _build_scenario(*, run_id: str, run_index: int, test_id: str, plan: ScenarioRunPlan) -> ScenarioResult:
    return ScenarioResult(
        id=uuid.UUID(run_id),
        scenario_identifier=ScenarioIdentifier(
            class_name="ScaleScenario",
            class_module="tests.integration.memory.test_scenario_history_scale_integration",
            version=1,
            params={},
            objective_target=ComponentIdentifier(class_name="ScaleTarget", class_module="tests.integration"),
        ),
        scenario_run_state=ScenarioRunState.COMPLETED,
        attack_results={},
        labels={"test_id": test_id},
        creation_time=_T0 + timedelta(minutes=run_index),
        completion_time=_T0 + timedelta(hours=1, minutes=run_index),
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )


@asynccontextmanager
async def _cleanup_runs_async(
    memory: AzureSQLMemory, *, run_ids: Sequence[str], conversation_prefix: str
) -> AsyncGenerator[None, None]:
    try:
        yield
    finally:
        async with await memory.get_session_async() as async_session:

            def cleanup(session: Session) -> None:
                entry_ids = [uuid.UUID(run_id) for run_id in run_ids]
                session.query(AttackResultEntry).filter(AttackResultEntry.attribution_parent_id.in_(entry_ids)).delete(
                    synchronize_session=False
                )
                session.query(ConversationEntry).filter(
                    ConversationEntry.conversation_id.startswith(conversation_prefix)
                ).delete(synchronize_session=False)
                session.query(ScenarioResultEntry).filter(ScenarioResultEntry.id.in_(entry_ids)).delete(
                    synchronize_session=False
                )
                session.commit()

            await async_session.run_sync(cleanup)


@pytest.mark.run_only_if_all_tests
@pytest.mark.timeout(_TEST_TIMEOUT_SECONDS)
async def test_scenario_history_reads_production_shaped_plans_within_budget_async(
    azuresql_instance: AzureSQLMemory,
) -> None:
    """History page and run hierarchy stay fast when large run plans meet hundreds of attempts."""
    test_id = str(uuid.uuid4())[:8]
    plan = _build_plan()
    assert len(json.dumps(plan.model_dump(mode="json"))) >= _MIN_PLAN_BYTES
    assert sum(len(group.seed_group_ids) for group in plan.atomic_groups) == 245
    run_ids = [str(uuid.uuid4()) for _ in range(_RUN_COUNT)]
    conversation_prefix = f"scale-{test_id}-"

    async with _cleanup_runs_async(azuresql_instance, run_ids=run_ids, conversation_prefix=conversation_prefix):
        expected_units: dict[str, int] = {}
        for run_index, run_id in enumerate(run_ids):
            await azuresql_instance.add_scenario_results_to_memory_async(
                scenario_results=[_build_scenario(run_id=run_id, run_index=run_index, test_id=test_id, plan=plan)]
            )
            attempts, planned_units = _build_attempts(
                run_id=run_id,
                conversation_prefix=conversation_prefix,
                plan=plan,
                count=_MIN_ATTEMPTS_PER_RUN + run_index * _ATTEMPT_STEP_PER_RUN,
            )
            await azuresql_instance.add_attack_results_to_memory_async(attack_results=attempts)
            expected_units[run_id] = len(planned_units)

        started = perf_counter()
        records, aggregates, has_more = await azuresql_instance.get_scenario_run_history_page_async(
            labels={"test_id": test_id}, limit=_RUN_COUNT
        )
        history_seconds = perf_counter() - started

        service = ScenarioRunService()
        # The first hierarchy read discovers the scenario registry; time the steady-state request.
        await service.get_run_hierarchy_async(scenario_result_id=run_ids[0])
        started = perf_counter()
        hierarchy = await service.get_run_hierarchy_async(scenario_result_id=run_ids[-1])
        hierarchy_seconds = perf_counter() - started

        print(f"scenario history scale: page {history_seconds:.3f}s, hierarchy {hierarchy_seconds:.3f}s")
        assert {record.scenario_result_id for record in records} == set(run_ids)
        assert not has_more
        assert {run_id: aggregates[run_id].unit_count for run_id in run_ids} == expected_units
        assert hierarchy is not None
        assert history_seconds < _MAX_READ_SECONDS
        assert hierarchy_seconds < _MAX_READ_SECONDS
