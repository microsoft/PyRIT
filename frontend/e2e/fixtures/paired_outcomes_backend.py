# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Disposable saved Scenario runs for paired-outcome browser acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import UUID, uuid4

from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory


async def _seed_async(memory: SQLiteMemory) -> None:
    from pyrit.common.utils import to_sha256
    from pyrit.models import (
        SCENARIO_RUN_PLAN_METADATA_KEY,
        AtomicAttackIdentifier,
        AttackIdentifier,
        AttackOutcome,
        AttackResult,
        Conversation,
        MessagePiece,
        ScenarioIdentifier,
        ScenarioResult,
        ScenarioRunPlan,
        ScenarioRunPlanAtomicGroup,
        ScenarioRunPlanGroupKind,
        ScenarioRunPlanSeedGroup,
        ScenarioRunPlanSeedPrompt,
        ScenarioRunState,
        TargetIdentifier,
    )

    memory.disable_embedding()
    timestamp = datetime(2026, 10, 20, tzinfo=UTC)
    shared = ["a", "b", "c", "d", "missing", "undetermined", "error", "child-only"]
    target = TargetIdentifier(class_name="MockPromptTarget", class_module="paired_browser_fixture")
    identifier = AtomicAttackIdentifier.build(
        attack_identifier=AttackIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack",
            objective_target=target,
        )
    )
    for index, state in enumerate(
        [
            ScenarioRunState.COMPLETED,
            ScenarioRunState.COMPLETED,
            ScenarioRunState.FAILED,
            ScenarioRunState.COMPLETED,
            ScenarioRunState.COMPLETED,
        ]
    ):
        scenario_id = UUID(int=9100 + index)
        page_seeds = (
            [f"page-{number:02d}" + ("x" * 900 if index == 4 and number == 23 else "") for number in range(30)]
            if index in (3, 4)
            else []
        )
        selected_seeds = shared + page_seeds
        plan = ScenarioRunPlan(
            atomic_groups=[
                ScenarioRunPlanAtomicGroup(
                    id=name,
                    atomic_attack_name=name,
                    display_group=name.title(),
                    technique_eval_hash=f"{name}-eval",
                    kind=kind,
                    seed_group_ids=selected_seeds + [extra],
                )
                for name, kind, extra in [
                    ("baseline", ScenarioRunPlanGroupKind.BASELINE, "base-only"),
                    ("attack", ScenarioRunPlanGroupKind.ATTACK, "attack-only"),
                ]
            ],
            seed_groups=[
                ScenarioRunPlanSeedGroup(
                    id=seed,
                    objective=seed,
                    objective_sha256=to_sha256(seed),
                    prompts=[
                        ScenarioRunPlanSeedPrompt(value="LongPrompt" * 80, sequence=0, data_type="text", role="user")
                    ]
                    if index == 4 and seed == "a"
                    else [],
                )
                for seed in selected_seeds + ["base-only", "attack-only"]
            ],
        )
        rows: dict[str, list[AttackResult]] = {"baseline": [], "attack": []}
        for name, outcomes in [
            ("baseline", [AttackOutcome.FAILURE, AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.SUCCESS]),
            ("attack", [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.FAILURE, AttackOutcome.SUCCESS]),
        ]:
            for seed, outcome in zip(shared[:4], outcomes, strict=True):
                rows[name].append(
                    AttackResult(
                        attack_result_id=str(uuid4()),
                        conversation_id=str(uuid4()),
                        objective=seed,
                        outcome=outcome,
                        timestamp=timestamp,
                        atomic_attack_identifier=identifier,
                        attribution_parent_id=str(scenario_id),
                        attribution_data={
                            "seed_group_id": seed,
                            "parent_eval_hash": f"{name}-eval",
                            "parent_collection": name,
                        },
                    )
                )
        for seed in page_seeds:
            for name, outcome in [("baseline", AttackOutcome.FAILURE), ("attack", AttackOutcome.SUCCESS)]:
                rows[name].append(
                    AttackResult(
                        attack_result_id=str(uuid4()),
                        conversation_id=str(uuid4()),
                        objective=seed,
                        outcome=outcome,
                        timestamp=timestamp,
                        atomic_attack_identifier=identifier,
                        attribution_parent_id=str(scenario_id),
                        attribution_data={
                            "seed_group_id": seed,
                            "parent_eval_hash": f"{name}-eval",
                            "parent_collection": name,
                        },
                    )
                )
        for seed, outcome in [
            ("undetermined", AttackOutcome.UNDETERMINED),
            ("error", AttackOutcome.ERROR),
            ("child-only", AttackOutcome.FAILURE),
        ]:
            for name in ["baseline", "attack"]:
                rows[name].append(
                    AttackResult(
                        attack_result_id=str(uuid4()),
                        conversation_id=str(uuid4()),
                        objective=seed,
                        outcome=AttackOutcome.FAILURE if name == "baseline" else outcome,
                        timestamp=timestamp,
                        atomic_attack_identifier=identifier,
                        attribution_parent_id=str(scenario_id),
                        attribution_data={
                            "seed_group_id": seed,
                            "parent_eval_hash": f"{name}-eval",
                            "parent_collection": name,
                            **({"attempt_index": 1} if seed == "child-only" and name == "attack" else {}),
                        },
                    )
                )
        rows["attack"].append(
            AttackResult(
                attack_result_id=str(uuid4()),
                conversation_id=str(uuid4()),
                objective="a",
                outcome=AttackOutcome.FAILURE,
                timestamp=timestamp - timedelta(seconds=1),
                atomic_attack_identifier=identifier,
                attribution_parent_id=str(scenario_id),
                attribution_data={
                    "seed_group_id": "a",
                    "parent_eval_hash": "attack-eval",
                    "parent_collection": "attack",
                },
            )
        )
        if index == 4:
            parent = rows["attack"][0]
            child = parent.model_copy(deep=True)
            child.attack_result_id = str(uuid4())
            child.conversation_id = str(uuid4())
            assert child.attribution_data is not None
            child.attribution_data["attempt_index"] = 1
            parent.conversation_id = ""
            parent.metadata["child_attack_result_ids"] = [child.attack_result_id]
            rows["attack"].append(child)
        for attack_result in (item for group in rows.values() for item in group):
            if not attack_result.conversation_id:
                continue
            await memory.add_conversation_to_memory_async(
                conversation=Conversation(
                    conversation_id=attack_result.conversation_id,
                    target_identifier=target,
                    attack_result_id=attack_result.attack_result_id,
                )
            )
            await memory.add_message_pieces_to_memory_async(
                message_pieces=[
                    MessagePiece(
                        role="user",
                        conversation_id=attack_result.conversation_id,
                        sequence=0,
                        original_value=f"Offline objective: {attack_result.objective}",
                    ),
                    MessagePiece(
                        role="assistant",
                        conversation_id=attack_result.conversation_id,
                        sequence=1,
                        original_value=f"Offline {attack_result.outcome.value} example; no model was called.",
                    ),
                ]
            )
        await memory.add_attack_results_to_memory_async(
            attack_results=[row for group in rows.values() for row in group]
        )
        await memory.add_scenario_results_to_memory_async(
            scenario_results=[
                ScenarioResult(
                    id=scenario_id,
                    scenario_identifier=ScenarioIdentifier(
                        class_name="PairedBrowserScenario",
                        class_module="paired_browser_fixture",
                        version=1,
                        objective_target=target,
                    ),
                    scenario_description="Offline paired outcomes acceptance fixture.",
                    attack_results=rows,
                    metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")} if index != 1 else {},
                    scenario_run_state=state,
                    creation_time=timestamp,
                    completion_time=timestamp + timedelta(minutes=1),
                )
            ]
        )


if __name__ == "__main__":
    with patch.object(analytics_backend, "_seed_async", _seed_async):
        analytics_backend.main()
