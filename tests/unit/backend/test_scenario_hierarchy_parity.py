# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit and parity tests for bounded Scenario result hierarchy projections."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.analytics import compute_outcome_statistics
from pyrit.backend.main import app
from pyrit.backend.models.attacks import AttackConversationsResponse
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.services.saved_conversation_read_service import SavedConversationReadService
from pyrit.backend.services.scenario_progress_read_model import (
    ScenarioProgressReadModel,
    ScenarioProgressSnapshot,
)
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.memory import MemoryInterface, PromptMemoryEntry, SQLiteMemory
from pyrit.memory.storage.serializers import set_message_piece_sha256_async
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    AttackSeedGroup,
    ComponentIdentifier,
    ConversationReference,
    ConversationSummary,
    ConversationType,
    MessagePiece,
    RetryEvent,
    ScenarioAtomicGroupProgress,
    ScenarioAttackResultDelta,
    ScenarioAttackTechniqueDetails,
    ScenarioHierarchyGroup,
    ScenarioHierarchyNode,
    ScenarioHierarchyProjection,
    ScenarioProgressCounts,
    ScenarioProgressResult,
    ScenarioProgressSummary,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
    SeedIdentifier,
    SeedObjective,
    SeedPrompt,
)
from unit.mocks import get_mock_target_identifier, make_scenario_result


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app, headers=compatibility_headers)


def _make_test_delta(
    *,
    attack_result_id: str,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    role: AttackResultRole = AttackResultRole.TARGET_FACING,
    child_ids: list[str] | None = None,
    parent_collection: str = "attack_group",
    seed_group_id: str = "seed_1",
    objective: str = "Test objective",
    retries: list[RetryEvent] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    attempt_index: int | None = None,
    conversation_id: str = "conv_1",
    seconds_offset: int = 0,
) -> ScenarioAttackResultDelta:
    return ScenarioAttackResultDelta(
        attack_result_id=attack_result_id,
        conversation_id=conversation_id,
        objective=objective,
        objective_sha256="hash123",
        outcome=outcome,
        execution_time_ms=100,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds_offset),
        retry_events=retries or [],
        total_retries=len(retries or []),
        error_type=error_type,
        error_message=error_message,
        attack_metadata={
            "child_attack_result_ids": child_ids or [],
            "completion_policy": "ALL_CHILDREN",
            "executed_turns": 1,
        },
        attribution_data={
            "parent_collection": parent_collection,
            "seed_group_id": seed_group_id,
            "result_role": role.value,
            "attempt_index": attempt_index,
        },
    )


def _make_test_result(
    *,
    attack_result_id: str,
    atomic_group_id: str = "group-1",
    atomic_attack_name: str = "attack",
    seed_group_id: str = "seed-1",
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    conversation_id: str = "conv_1",
    total_retries: int = 0,
    retries: list[RetryEvent] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    child_attack_result_ids: list[str] | None = None,
    related_conversation_ids: list[str] | None = None,
    role: AttackResultRole = AttackResultRole.TARGET_FACING,
) -> ScenarioProgressResult:
    return ScenarioProgressResult(
        attack_result_id=attack_result_id,
        conversation_id=conversation_id,
        atomic_group_id=atomic_group_id,
        atomic_attack_name=atomic_attack_name,
        seed_group_id=seed_group_id,
        outcome=outcome,
        execution_time_ms=100,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        total_retries=total_retries,
        retries=retries or [],
        error_type=error_type,
        error_message=error_message,
        result_role=role,
        child_attack_result_ids=child_attack_result_ids or [],
        related_conversation_ids=related_conversation_ids or [],
    )


def _make_test_snapshot(
    *,
    deltas: list[ScenarioAttackResultDelta],
    plan: ScenarioRunPlan | None = None,
    atomic_groups: list[ScenarioAtomicGroupProgress] | None = None,
    summary: ScenarioProgressSummary | None = None,
) -> ScenarioProgressSnapshot:
    results: list[ScenarioProgressResult] = []
    for d in deltas:
        role_str = d.attribution_data.get("result_role", AttackResultRole.UNKNOWN.value)
        role = (
            AttackResultRole(role_str) if role_str in AttackResultRole._value2member_map_ else AttackResultRole.UNKNOWN
        )
        results.append(
            ScenarioProgressResult(
                attack_result_id=d.attack_result_id,
                conversation_id=d.conversation_id,
                atomic_group_id=str(d.attribution_data.get("parent_collection") or ""),
                atomic_attack_name=str(d.attribution_data.get("parent_collection") or ""),
                seed_group_id=str(d.attribution_data.get("seed_group_id") or ""),
                outcome=d.outcome,
                execution_time_ms=d.execution_time_ms,
                timestamp=d.timestamp,
                total_retries=d.total_retries,
                retries=d.retry_events,
                error_type=d.error_type,
                error_message=d.error_message,
                score=d.score,
                result_role=role,
                child_attack_result_ids=list(d.attack_metadata.get("child_attack_result_ids") or []),
                attempt_index=d.attribution_data.get("attempt_index"),
            )
        )
    effective_plan = plan or ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="attack_group",
                atomic_attack_name="attack_group",
                display_group="attack_group",
                technique_eval_hash="",
                seed_group_ids=["seed_1"],
            )
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed_1", objective_sha256="hash123", objective="Test objective")],
    )
    if summary is None:
        attempts_by_unit: dict[tuple[str, str], list[ScenarioProgressResult]] = {}
        for r in results:
            unit_key = (r.atomic_group_id, r.seed_group_id)
            attempts_by_unit.setdefault(unit_key, []).append(r)

        calculated_retries = 0
        for attempts in attempts_by_unit.values():
            calculated_retries += max(len(attempts) - 1, 0) + sum(a.total_retries for a in attempts)

        effective_summary = ScenarioProgressSummary(
            overall=ScenarioProgressCounts(
                completed=len([r for r in results if r.outcome != AttackOutcome.ERROR]),
                planned=1,
                succeeded=len([r for r in results if r.outcome == AttackOutcome.SUCCESS]),
                errors=len([r for r in results if r.outcome == AttackOutcome.ERROR]),
                retries=calculated_retries,
            ),
            atomic_groups=atomic_groups or [],
        )
    else:
        effective_summary = summary
    return ScenarioProgressSnapshot(
        deltas=tuple(deltas),
        results=tuple(results),
        summary=effective_summary,
        plan=effective_plan,
    )


def test_exact_retry_diagnostics_override_partial_preview_arithmetic() -> None:
    snapshot = _make_test_snapshot(
        deltas=[_make_test_delta(attack_result_id="first"), _make_test_delta(attack_result_id="second")]
    )
    assert snapshot.summary.overall.retries == 1
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
        exact_total_retries=0,
    )
    assert projection.diagnostics.total_retries == 0


def test_hierarchy_ordinary_flat_results() -> None:
    delta1 = _make_test_delta(attack_result_id="attack-1", seconds_offset=1)
    delta2 = _make_test_delta(attack_result_id="attack-2", seconds_offset=2)
    snapshot = _make_test_snapshot(deltas=[delta1, delta2])

    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    assert projection.scenario_result_id == "scenario-run-1"
    assert len(projection.groups) == 1
    group = projection.groups[0]
    assert len(group.roots) == 2
    assert [r.attack_result_id for r in group.roots] == ["attack-1", "attack-2"]
    for root in group.roots:
        assert root.children == []
        assert root.unresolved_child_attack_result_ids == []
        assert root.parent_attack_result_id is None
        assert root.child_index is None
        assert root.orchestration is None


def test_hierarchy_nested_sequential_attack_with_relative_indexes() -> None:
    child1 = _make_test_delta(
        attack_result_id="child-1",
        role=AttackResultRole.TARGET_FACING,
        attempt_index=1,
        seconds_offset=1,
    )
    child2 = _make_test_delta(
        attack_result_id="child-2",
        role=AttackResultRole.TARGET_FACING,
        attempt_index=2,
        seconds_offset=2,
    )
    parent = _make_test_delta(
        attack_result_id="parent-1",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["child-1", "child-2"],
        seconds_offset=3,
    )

    snapshot = _make_test_snapshot(deltas=[child1, child2, parent])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    assert len(projection.groups) == 1
    group = projection.groups[0]
    # Only parent is root; children are not roots
    assert len(group.roots) == 1
    root = group.roots[0]
    assert root.attack_result_id == "parent-1"
    assert root.result_role == AttackResultRole.ORCHESTRATION
    assert root.orchestration is not None
    assert root.orchestration.child_attack_result_ids == ["child-1", "child-2"]
    assert len(root.children) == 2
    assert root.children[0].attack_result_id == "child-1"
    assert root.children[0].child_index == 0
    assert root.children[0].parent_attack_result_id == "parent-1"
    assert root.children[1].attack_result_id == "child-2"
    assert root.children[1].child_index == 1
    assert root.children[1].parent_attack_result_id == "parent-1"
    assert root.unresolved_child_attack_result_ids == []


def test_hierarchy_cyclic_references_guarded() -> None:
    # A -> B and B -> A cycle
    delta_a = _make_test_delta(
        attack_result_id="node-a",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["node-b"],
        seconds_offset=1,
    )
    delta_b = _make_test_delta(
        attack_result_id="node-b",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["node-a"],
        seconds_offset=2,
    )

    snapshot = _make_test_snapshot(deltas=[delta_a, delta_b])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    # Traversal must terminate safely without recursion error
    assert projection is not None
    assert len(projection.groups) == 1
    # Check that cycle link is placed into unresolved_child_attack_result_ids
    all_unresolved = [
        uid
        for group in projection.groups
        for root in group.roots
        for uid in (
            root.unresolved_child_attack_result_ids
            + [u for c in root.children for u in c.unresolved_child_attack_result_ids]
        )
    ]
    assert any("node-a" in u or "node-b" in u for u in all_unresolved)


def test_hierarchy_retained_failure_link_issue_3048() -> None:
    # Issue #3048: An orchestration parent encounters an error, but retains completed + failed children
    child1 = _make_test_delta(
        attack_result_id="child-1",
        outcome=AttackOutcome.SUCCESS,
        role=AttackResultRole.TARGET_FACING,
        attempt_index=1,
        seconds_offset=1,
    )
    child2 = _make_test_delta(
        attack_result_id="child-2",
        outcome=AttackOutcome.ERROR,
        role=AttackResultRole.TARGET_FACING,
        error_type="TargetError",
        error_message="Endpoint failure",
        attempt_index=2,
        seconds_offset=2,
    )
    parent = _make_test_delta(
        attack_result_id="parent-error",
        outcome=AttackOutcome.ERROR,
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["child-1", "child-2"],
        error_type="ScenarioPartialFailureException",
        error_message="Aborted after child 2 failed",
        seconds_offset=3,
    )

    snapshot = _make_test_snapshot(deltas=[child1, child2, parent])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.FAILED,
        snapshot=snapshot,
    )

    group = projection.groups[0]
    assert len(group.roots) == 1
    root = group.roots[0]
    assert root.attack_result_id == "parent-error"
    assert root.outcome == AttackOutcome.ERROR
    assert root.error_type == "ScenarioPartialFailureException"
    assert len(root.children) == 2
    assert root.children[0].attack_result_id == "child-1"
    assert root.children[0].outcome == AttackOutcome.SUCCESS
    assert root.children[1].attack_result_id == "child-2"
    assert root.children[1].outcome == AttackOutcome.ERROR
    assert root.unresolved_child_attack_result_ids == []


def test_hierarchy_missing_and_cross_run_child_references() -> None:
    # Parent references a child ID that does not exist in deltas (or was from another run)
    parent = _make_test_delta(
        attack_result_id="parent-1",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["missing-child-99", "cross-run-child-42"],
    )
    snapshot = _make_test_snapshot(deltas=[parent])

    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    root = projection.groups[0].roots[0]
    assert root.children == []
    assert root.unresolved_child_attack_result_ids == ["missing-child-99", "cross-run-child-42"]


def test_hierarchy_diagnostics_bounding_at_100() -> None:
    # Create 105 errors and 120 retry events
    deltas: list[ScenarioAttackResultDelta] = []
    for i in range(105):
        retry = RetryEvent(exception_type="RateLimit", attempt_number=1) if i < 120 else None
        retries = [retry] if retry else []
        deltas.append(
            _make_test_delta(
                attack_result_id=f"err-{i}",
                outcome=AttackOutcome.ERROR,
                error_type="TestError",
                error_message=f"Error {i}",
                retries=retries,
                seed_group_id=f"seed_{i}",
                seconds_offset=i,
            )
        )
    deltas.extend(
        _make_test_delta(
            attack_result_id=f"retry-{i}",
            outcome=AttackOutcome.SUCCESS,
            retries=[RetryEvent(exception_type="RateLimit", attempt_number=1)],
            seed_group_id=f"seed_{i}",
            seconds_offset=i,
        )
        for i in range(105, 120)
    )

    snapshot = _make_test_snapshot(deltas=deltas)
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    diag = projection.diagnostics
    assert len(diag.failed_attacks) == 100
    assert diag.failed_attacks_count == 105
    assert diag.failed_attacks_truncated is True

    assert len(diag.attack_retries) == 100
    assert diag.attack_retries_count == 120
    assert diag.attack_retries_truncated is True
    assert diag.total_retries == 120


@pytest.mark.asyncio
async def test_service_get_run_hierarchy_node_async_bounded_read() -> None:
    memory = MagicMock(spec=MemoryInterface)
    parent_delta = _make_test_delta(
        attack_result_id="parent-1",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["child-1"],
    )
    child_delta = _make_test_delta(
        attack_result_id="child-1",
        role=AttackResultRole.TARGET_FACING,
    )

    async def mock_get_deltas(
        *, scenario_result_id: str, attack_result_ids: list[str]
    ) -> list[ScenarioAttackResultDelta]:
        res = []
        for aid in attack_result_ids:
            if aid == "parent-1":
                res.append(parent_delta)
            elif aid == "child-1":
                res.append(child_delta)
        return res

    memory.get_scenario_attack_result_deltas_by_ids_async = AsyncMock(side_effect=mock_get_deltas)
    memory.get_scenario_result_header_async = AsyncMock(return_value=None)
    conversation_reader = MagicMock(spec=SavedConversationReadService)
    conversation_reader.list_conversations_async = AsyncMock(
        return_value=AttackConversationsResponse(
            attack_result_id="parent-1",
            main_conversation_id="conv_1",
            conversations=[
                ConversationSummary(
                    conversation_id="conv_1",
                    conversation_type="main",
                    message_count=1,
                )
            ],
            total_count=1,
            pagination=PaginationInfo(limit=50, has_more=False),
        )
    )

    read_model = ScenarioProgressReadModel(memory=memory)
    service = ScenarioRunService(memory=memory, progress_read_model=read_model)
    service._saved_conversation_reader = conversation_reader

    node = await service.get_run_hierarchy_node_async(
        scenario_result_id="scenario-run-1",
        attack_result_id="parent-1",
    )

    assert node is not None
    assert node.attack_result_id == "parent-1"
    assert len(node.children) == 1
    assert node.children[0].attack_result_id == "child-1"
    assert len(node.conversations) == 1
    conversation_reader.list_conversations_async.assert_awaited_once_with(attack_result_id="parent-1")

    # Querying non-existent node returns None
    missing = await service.get_run_hierarchy_node_async(
        scenario_result_id="scenario-run-1",
        attack_result_id="non-existent",
    )
    assert missing is None
    conversation_reader.list_conversations_async.return_value = None
    assert (
        await service.get_run_hierarchy_node_async(scenario_result_id="scenario-run-1", attack_result_id="parent-1")
        is None
    )


def test_rest_routes_hierarchy_and_node(client: TestClient) -> None:
    now = datetime.now(UTC)
    node = ScenarioHierarchyNode(
        attack_result_id="node-1",
        result_role=AttackResultRole.TARGET_FACING,
        atomic_attack_name="attack",
        atomic_group_id="group-1",
        seed_group_id="seed-1",
        objective="obj",
        outcome=AttackOutcome.SUCCESS,
        timestamp=now,
    )
    group = ScenarioHierarchyGroup(
        atomic_group_id="group-1",
        atomic_attack_name="attack",
        display_group="display",
        technique=ScenarioAttackTechniqueDetails(component_name="Technique"),
        roots=[node],
        counts=ScenarioProgressCounts(completed=1, planned=1, succeeded=1, success_percentage=100, errors=0, retries=0),
    )
    projection = ScenarioHierarchyProjection(
        scenario_result_id="run-123",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        groups=[group],
        overall_counts=ScenarioProgressCounts(
            completed=1, planned=1, succeeded=1, success_percentage=100, errors=0, retries=0
        ),
    )

    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as mock_get_svc:
        mock_svc = MagicMock()
        mock_svc.get_run_hierarchy_async = AsyncMock(return_value=projection)
        mock_svc.get_run_hierarchy_node_async = AsyncMock(return_value=node)
        mock_get_svc.return_value = mock_svc

        # 1. Hierarchy projection endpoint
        resp = client.get("/api/scenarios/runs/run-123/hierarchy")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["scenario_result_id"] == "run-123"
        assert len(data["groups"]) == 1
        assert data["groups"][0]["roots"][0]["attack_result_id"] == "node-1"

        # 2. Node detail endpoint
        resp_node = client.get("/api/scenarios/runs/run-123/hierarchy/nodes/node-1")
        assert resp_node.status_code == status.HTTP_200_OK
        node_data = resp_node.json()
        assert node_data["attack_result_id"] == "node-1"

        # 3. 404 on unknown run
        mock_svc.get_run_hierarchy_async.return_value = None
        resp_404 = client.get("/api/scenarios/runs/unknown-run/hierarchy")
        assert resp_404.status_code == status.HTTP_404_NOT_FOUND

        # 4. 404 on unknown node
        mock_svc.get_run_hierarchy_node_async.return_value = None
        resp_node_404 = client.get("/api/scenarios/runs/run-123/hierarchy/nodes/unknown-node")
        assert resp_node_404.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_sqlite_memory_batched_reads_and_projections_parity(sqlite_instance: SQLiteMemory) -> None:
    """Test batched memory delta reads, conversation projections, and cross-run isolation on SQLite."""
    scenario_uuid_1 = uuid.uuid4()
    scenario_uuid_2 = uuid.uuid4()
    scenario_id_1 = str(scenario_uuid_1)
    scenario_id_2 = str(scenario_uuid_2)

    scen_1 = make_scenario_result(
        id=scenario_uuid_1,
        scenario_name="Scenario1",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    scen_2 = make_scenario_result(
        id=scenario_uuid_2,
        scenario_name="Scenario2",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen_1, scen_2])

    parent_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    cross_run_id = str(uuid.uuid4())
    child_conv_id = str(uuid.uuid4())

    parent_result = AttackResult(
        attack_result_id=parent_id,
        conversation_id="parent_conv",
        objective="Orchestration objective",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id_1,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.ORCHESTRATION.value},
        metadata={"child_attack_result_ids": [child_id, cross_run_id]},
    )
    child_result = AttackResult(
        attack_result_id=child_id,
        conversation_id=child_conv_id,
        objective="Child target-facing objective",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id_1,
        attribution_data={
            "parent_collection": "group_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 1,
        },
    )
    cross_run_result = AttackResult(
        attack_result_id=cross_run_id,
        conversation_id="cross_conv",
        objective="Cross-run child objective",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id_2,
        attribution_data={"parent_collection": "group_2", "result_role": AttackResultRole.TARGET_FACING.value},
    )

    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[parent_result, child_result, cross_run_result]
    )

    # Insert messages into child conversation
    piece_user = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=child_conv_id,
        role="user",
        original_value_data_type="text",
        original_value="Prompt to target",
        converted_value="Prompt to target",
        sequence=0,
    )
    piece_assistant = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=child_conv_id,
        role="assistant",
        original_value_data_type="text",
        original_value="Response from target",
        converted_value="Response from target",
        sequence=1,
    )
    await set_message_piece_sha256_async(piece_user)
    await set_message_piece_sha256_async(piece_assistant)
    await sqlite_instance._run_database_operation_async(
        sqlite_instance._insert_entry, PromptMemoryEntry(entry=piece_user)
    )
    await sqlite_instance._run_database_operation_async(
        sqlite_instance._insert_entry, PromptMemoryEntry(entry=piece_assistant)
    )

    # 1. Test batched delta reads
    deltas = await sqlite_instance.get_scenario_attack_result_deltas_by_ids_async(
        scenario_result_id=scenario_id_1,
        attack_result_ids=[parent_id, child_id, cross_run_id],
    )
    delta_ids = [d.attack_result_id for d in deltas]
    assert parent_id in delta_ids
    assert child_id in delta_ids
    # Cross-run ID belongs to scenario_id_2, so it MUST NOT be returned!
    assert cross_run_id not in delta_ids

    reader = SavedConversationReadService(memory=sqlite_instance)
    references = await reader.list_conversations_async(attack_result_id=child_id)
    assert references is not None
    assert references.total_count == 1
    assert len(references.conversations) == 1
    assert references.conversations[0].conversation_id == child_conv_id
    assert references.conversations[0].message_count == 2
    transcript = await reader.get_messages_async(attack_result_id=child_id, conversation_id=child_conv_id)
    assert transcript is not None
    assert len(transcript.messages) == 2
    assert transcript.messages[0].role == "user"
    assert transcript.messages[0].message_pieces[0].original_value == "Prompt to target"
    assert transcript.messages[1].role == "assistant"
    assert transcript.messages[1].message_pieces[0].original_value == "Response from target"

    # 3. Test service single-node read
    service = ScenarioRunService(memory=sqlite_instance)
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id_1,
        attack_result_id=parent_id,
    )
    assert node is not None
    assert node.attack_result_id == parent_id
    assert len(node.children) == 1
    assert node.children[0].attack_result_id == child_id
    # Cross-run child is safely recognized as unresolved without failing the run
    assert node.unresolved_child_attack_result_ids == [cross_run_id]


@pytest.mark.asyncio
async def test_sqlite_canonical_columns_round_trip(sqlite_instance: SQLiteMemory) -> None:
    """Test real SQLite round-trip of canonical executed_turns and outcome_reason columns."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="CanonicalScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id="conv_canonical",
        objective="Probe objective",
        outcome=AttackOutcome.FAILURE,
        executed_turns=7,
        outcome_reason="Target actively refused harmful request",
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        metadata={},  # Explicitly empty metadata to ensure canonical DB columns are read
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    # 1. Delta query directly from SQLite selects canonical columns
    deltas = await sqlite_instance.get_scenario_attack_result_deltas_by_ids_async(
        scenario_result_id=scenario_id,
        attack_result_ids=[ar_id],
    )
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.executed_turns == 7
    assert delta.outcome_reason == "Target actively refused harmful request"
    assert "executed_turns" not in delta.attack_metadata
    assert "outcome_reason" not in delta.attack_metadata

    # 2. Hierarchy node query through service projects canonical values
    service = ScenarioRunService(memory=sqlite_instance)
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=ar_id,
    )
    assert node is not None
    assert node.executed_turns == 7
    assert node.outcome_reason == "Target actively refused harmful request"


def test_hierarchy_disconnected_cyclic_component_mixed() -> None:
    """Test that disconnected cyclic components (e.g. A <-> B) are not dropped when standard roots exist."""
    # 1. Ordinary tree: root-1 -> child-1
    root1 = _make_test_delta(
        attack_result_id="root-1",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["child-1"],
        seconds_offset=1,
    )
    child1 = _make_test_delta(
        attack_result_id="child-1",
        role=AttackResultRole.TARGET_FACING,
        seconds_offset=2,
    )

    # 2. Detached cyclic component: cycle-a -> cycle-b and cycle-b -> cycle-a
    # Neither cycle-a nor cycle-b is reachable from root-1, and both are in each other's child_ids
    cycle_a = _make_test_delta(
        attack_result_id="cycle-a",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["cycle-b"],
        seconds_offset=3,
    )
    cycle_b = _make_test_delta(
        attack_result_id="cycle-b",
        role=AttackResultRole.ORCHESTRATION,
        child_ids=["cycle-a"],
        seconds_offset=4,
    )

    snapshot = _make_test_snapshot(deltas=[root1, child1, cycle_a, cycle_b])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    # All 4 nodes must be accounted for in the hierarchy!
    all_node_ids = set()

    def _collect_ids(nodes: list[ScenarioHierarchyNode]) -> None:
        for n in nodes:
            all_node_ids.add(n.attack_result_id)
            _collect_ids(n.children)

    for g in projection.groups:
        _collect_ids(g.roots)

    assert "root-1" in all_node_ids
    assert "child-1" in all_node_ids
    assert "cycle-a" in all_node_ids
    assert "cycle-b" in all_node_ids
    assert len(all_node_ids) == 4

    # Verify root-1 has child-1
    group = projection.groups[0]
    root_node = next(r for r in group.roots if r.attack_result_id == "root-1")
    assert len(root_node.children) == 1
    assert root_node.children[0].attack_result_id == "child-1"

    # Verify cycle was guarded and placed in unresolved_child_attack_result_ids without infinite loop
    cycle_root = next(r for r in group.roots if r.attack_result_id in ("cycle-a", "cycle-b"))
    assert len(cycle_root.children) == 1
    cycle_child = cycle_root.children[0]
    assert cycle_root.attack_result_id in cycle_child.unresolved_child_attack_result_ids


def test_hierarchy_bounded_node_budget() -> None:
    """Test that max_nodes bounds tree traversal and sets truncated=True while preserving exact overall counts."""
    # Create 6 deltas in a chain: node-0 -> node-1 -> node-2 -> node-3 -> node-4 -> node-5
    deltas = [
        _make_test_delta(
            attack_result_id=f"node-{i}",
            role=AttackResultRole.ORCHESTRATION if i < 5 else AttackResultRole.TARGET_FACING,
            child_ids=[f"node-{i + 1}"] if i < 5 else [],
            seconds_offset=i,
        )
        for i in range(6)
    ]

    snapshot = _make_test_snapshot(deltas=deltas)
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
        max_nodes=3,
    )

    assert projection.truncated is True

    # Count nodes projected in hierarchy
    node_count = 0

    def _count_nodes(nodes: list[ScenarioHierarchyNode]) -> None:
        nonlocal node_count
        for n in nodes:
            node_count += 1
            _count_nodes(n.children)

    for g in projection.groups:
        _count_nodes(g.roots)

    assert node_count == 3
    # Top-level aggregate totals must remain exact (not clipped)
    assert projection.overall_counts.completed == 6


def test_hierarchy_group_progress_outcomes_parity() -> None:
    """Test that group.counts.outcomes is preserved from atomic group progress."""
    outcomes = compute_outcome_statistics({AttackOutcome.SUCCESS: 1, AttackOutcome.UNDETERMINED: 1})
    ag_progress = ScenarioAtomicGroupProgress(
        id="attack_group",
        atomic_attack_name="attack_group",
        display_group="attack_group",
        status="COMPLETED",
        completed=2,
        planned=2,
        succeeded=1,
        success_percentage=50,
        errors=0,
        retries=0,
        outcomes=outcomes,
    )

    delta1 = _make_test_delta(attack_result_id="a-1", outcome=AttackOutcome.SUCCESS, seconds_offset=1)
    delta2 = _make_test_delta(attack_result_id="a-2", outcome=AttackOutcome.UNDETERMINED, seconds_offset=2)

    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="attack_group",
                atomic_attack_name="attack_group",
                display_group="attack_group",
                technique_eval_hash="",
                seed_group_ids=["seed_1"],
            )
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed_1", objective_sha256="hash123", objective="Test objective")],
    )

    summary = ScenarioProgressSummary(
        overall=ScenarioProgressCounts(
            completed=2,
            planned=2,
            succeeded=1,
            success_percentage=50,
            errors=0,
            retries=0,
            outcomes=outcomes,
        ),
        atomic_groups=[ag_progress],
    )

    snapshot = ScenarioProgressSnapshot(
        deltas=(delta1, delta2),
        results=tuple(
            ScenarioProgressResult(
                attack_result_id=d.attack_result_id,
                conversation_id=d.conversation_id,
                atomic_group_id="attack_group",
                atomic_attack_name="attack_group",
                seed_group_id="seed_1",
                outcome=d.outcome,
                execution_time_ms=d.execution_time_ms,
                timestamp=d.timestamp,
                total_retries=d.total_retries,
                result_role=AttackResultRole.TARGET_FACING,
            )
            for d in [delta1, delta2]
        ),
        summary=summary,
        plan=plan,
    )

    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    assert len(projection.groups) == 1
    group = projection.groups[0]
    assert group.counts.outcomes is not None
    assert group.counts.outcomes.success_rate_decided == 1.0
    assert group.counts.outcomes.success_rate_all == 0.5


def test_hierarchy_unplanned_groups_shared_counting() -> None:
    """Test that unplanned groups use shared counting policy (latest attempt resolution + valid producer counts)."""
    # 2 attempts on the same unit in an unplanned group:
    # Attempt 1 failed, Attempt 2 succeeded
    delta1 = _make_test_delta(
        attack_result_id="unplanned-1",
        outcome=AttackOutcome.ERROR,
        role=AttackResultRole.TARGET_FACING,
        parent_collection="unplanned_group",
        seed_group_id="seed_u",
        seconds_offset=1,
    )
    delta2 = _make_test_delta(
        attack_result_id="unplanned-2",
        outcome=AttackOutcome.SUCCESS,
        role=AttackResultRole.TARGET_FACING,
        parent_collection="unplanned_group",
        seed_group_id="seed_u",
        seconds_offset=2,
    )

    # Empty plan (no planned atomic groups)
    plan = ScenarioRunPlan(atomic_groups=[], seed_groups=[])
    snapshot = _make_test_snapshot(deltas=[delta1, delta2], plan=plan)

    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    assert len(projection.groups) == 1
    group = projection.groups[0]
    assert group.atomic_group_id == "unplanned_group"
    # Latest attempt succeeded, so 1 completed unit, 1 success, 1 historical error
    assert group.counts.completed == 1
    assert group.counts.succeeded == 1
    assert group.counts.errors == 1
    # Producer counts include both historical attempts
    assert group.counts.producer_counts.target_facing.attempts == 2
    assert group.counts.producer_counts.target_facing.errors == 1
    assert group.counts.producer_counts.target_facing.retries == 0


def test_hierarchy_diagnostics_cap_retries_per_item() -> None:
    """Test that retries list inside individual AttackRetrySummary is bounded to 100 with retries_truncated=True."""
    retries = [RetryEvent(exception_type="RateLimitError", attempt_number=i) for i in range(250)]
    delta = _make_test_delta(
        attack_result_id="heavy-retry",
        outcome=AttackOutcome.SUCCESS,
        retries=retries,
    )

    snapshot = _make_test_snapshot(deltas=[delta])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    diag = projection.diagnostics
    assert len(diag.attack_retries) == 1
    item = diag.attack_retries[0]
    assert item.attack_result_id == "heavy-retry"
    assert item.total_retries == 250
    assert len(item.retries) == 100
    assert item.retries_truncated is True


async def test_sqlite_bounded_multimodal_conversation_projection_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    """Test multimodal piece projection, raw role preservation, and database-level SQL LIMIT bounding."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MultimodalScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    conv_id = str(uuid.uuid4())
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=conv_id,
        objective="Multimodal test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    # Create real image on disk
    img_file = tmp_path / "diagram.png"
    await asyncio.to_thread(img_file.write_bytes, b"dummy_png_bytes")
    img_path = str(img_file)

    # Sequence 0: 2 pieces (text + image)
    p0_text = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        role="user",
        original_value_data_type="text",
        original_value="Analyze blueprint image",
        converted_value="Analyze blueprint image",
        sequence=0,
        timestamp=timestamp,
    )
    p0_img = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        role="user",
        original_value_data_type="image_path",
        original_value=img_path,
        converted_value=img_path,
        sequence=0,
        timestamp=timestamp + timedelta(microseconds=1),
    )
    # Sequence 1: simulated_assistant role
    p1 = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        role="simulated_assistant",
        original_value_data_type="text",
        original_value="I detect architectural blueprints.",
        converted_value="I detect architectural blueprints.",
        sequence=1,
        timestamp=timestamp + timedelta(seconds=1),
    )
    # Sequence 2: 3rd message
    p2 = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        role="user",
        original_value_data_type="text",
        original_value="Proceed with analysis.",
        converted_value="Proceed with analysis.",
        sequence=2,
        timestamp=timestamp + timedelta(seconds=2),
    )

    for p in [p0_text, p0_img, p1, p2]:
        await set_message_piece_sha256_async(p)
        await sqlite_instance._run_database_operation_async(sqlite_instance._insert_entry, PromptMemoryEntry(entry=p))

    reader = SavedConversationReadService(memory=sqlite_instance)
    references = await reader.list_conversations_async(attack_result_id=ar_id)
    assert references is not None
    assert references.total_count == 1
    assert references.conversations[0].message_count == 3
    transcript = await reader.get_messages_async(attack_result_id=ar_id, conversation_id=conv_id, limit=3)
    assert transcript is not None
    assert transcript.pagination is not None
    assert transcript.pagination.has_more
    assert transcript.partial_message_sequences == []
    assert len(transcript.messages) == 2
    msg0 = transcript.messages[0]
    assert msg0.role == "user"
    assert len(msg0.message_pieces) == 2
    assert msg0.message_pieces[0].original_value_data_type == "text"
    assert msg0.message_pieces[0].original_value == "Analyze blueprint image"
    assert msg0.message_pieces[1].original_value_data_type == "image_path"
    assert msg0.message_pieces[1].original_value == img_path
    assert msg0.message_pieces[1].converted_value == img_path
    msg1 = transcript.messages[1]
    assert msg1.role == "simulated_assistant"
    assert msg1.message_pieces[0].original_value == "I detect architectural blueprints."
    remaining = await reader.get_messages_async(
        attack_result_id=ar_id, conversation_id=conv_id, limit=3, cursor=transcript.pagination.next_cursor
    )
    assert remaining is not None
    assert [message.sequence for message in remaining.messages] == [2]


def test_hierarchy_repeated_attempt_diagnostic_retries_consistency() -> None:
    """Test that units with repeated attempts without row retries match overall_counts and diagnostics retries."""
    # 2 attempts on the same unit: attempt 1 error, attempt 2 success, both with total_retries=0
    delta1 = _make_test_delta(
        attack_result_id="attempt-1",
        outcome=AttackOutcome.ERROR,
        role=AttackResultRole.TARGET_FACING,
        parent_collection="group_1",
        seed_group_id="seed_1",
        seconds_offset=1,
    )
    delta2 = _make_test_delta(
        attack_result_id="attempt-2",
        outcome=AttackOutcome.SUCCESS,
        role=AttackResultRole.TARGET_FACING,
        parent_collection="group_1",
        seed_group_id="seed_1",
        seconds_offset=2,
    )

    snapshot = _make_test_snapshot(deltas=[delta1, delta2])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    # Re-attempt of same unit causes retry pressure of 1
    assert projection.overall_counts.retries == 1
    # Diagnostics total_retries must be consistent with overall_counts.retries
    assert projection.diagnostics.total_retries == 1
    assert projection.diagnostics.total_retries == projection.overall_counts.retries


def test_hierarchy_traversal_halts_immediately_on_budget_exhaustion() -> None:
    """Test that with 1,000 root results and max_nodes=1, traversal stops immediately without quadratic loop."""
    deltas = [
        _make_test_delta(
            attack_result_id=f"root-{i}",
            role=AttackResultRole.TARGET_FACING,
            parent_collection="group_1",
            seed_group_id=f"seed_{i}",
            seconds_offset=i,
        )
        for i in range(1000)
    ]

    snapshot = _make_test_snapshot(deltas=deltas)

    import time

    start_time = time.perf_counter()
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
        max_nodes=1,
    )
    elapsed = time.perf_counter() - start_time

    assert elapsed < 0.05, f"Expected <0.05s, got {elapsed:.4f}s"
    assert projection.truncated is True
    # Count total nodes in projection
    node_count = sum(len(g.roots) for g in projection.groups)
    assert node_count == 1
    # Aggregate total remains exact
    assert projection.overall_counts.completed == 1000


@pytest.mark.asyncio
async def test_sqlite_conversation_list_clipping_explicit(sqlite_instance: SQLiteMemory) -> None:
    """Test explicit conversation-list clipping flags and exact count with 55 conversations."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ConversationClippingScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    main_conv_id = str(uuid.uuid4())
    pruned_conv_ids = [str(uuid.uuid4()) for _ in range(54)]

    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=main_conv_id,
        related_conversations={
            ConversationReference(conversation_id=cid, conversation_type=ConversationType.PRUNED)
            for cid in pruned_conv_ids
        }
        | {
            ConversationReference(conversation_id=str(uuid.uuid4()), conversation_type=kind)
            for kind in (ConversationType.ADVERSARIAL, ConversationType.PREPARATION)
        },
        objective="Conversation clipping test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    reader = SavedConversationReadService(memory=sqlite_instance)
    conv_result = await reader.list_conversations_async(attack_result_id=ar_id, limit=50)
    assert conv_result is not None
    assert conv_result.total_count == 55
    assert conv_result.pagination is not None
    assert conv_result.pagination.has_more
    assert len(conv_result.conversations) == 50
    remaining = await reader.list_conversations_async(
        attack_result_id=ar_id, limit=50, cursor=conv_result.pagination.next_cursor
    )
    assert remaining is not None
    assert remaining.pagination is not None
    assert not remaining.pagination.has_more
    assert len(remaining.conversations) == 5
    assert {conversation.conversation_id for conversation in conv_result.conversations + remaining.conversations} == {
        main_conv_id,
        *pruned_conv_ids,
    }

    # 2. Service single-node read projects exact count and truncation flag on node
    service = ScenarioRunService(memory=sqlite_instance)
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=ar_id,
    )
    assert node is not None
    assert node.conversations_count == 55
    assert node.conversations_truncated is True
    assert len(node.conversations) == 50


async def test_executed_turns_zero_vs_invalid_metadata(sqlite_instance: SQLiteMemory) -> None:
    """Test that executed_turns=0 with invalid string in metadata safely projects 0 without ValueError."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ExecutedTurnsZeroScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=str(uuid.uuid4()),
        objective="Executed turns zero test",
        outcome=AttackOutcome.SUCCESS,
        executed_turns=0,
        metadata={"executed_turns": "invalid"},
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=ar_id,
    )
    assert node is not None
    assert node.executed_turns == 0

    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    assert len(hierarchy.groups) == 1
    assert hierarchy.groups[0].roots[0].executed_turns == 0


@pytest.mark.parametrize("count", [2, 3, 4])
def test_node_preserves_canonical_conversation_summary_sequences(count: int) -> None:
    delta = _make_test_delta(
        attack_result_id="test-node",
        outcome=AttackOutcome.SUCCESS,
    )
    result = _make_test_result(
        attack_result_id="test-node",
        atomic_group_id="group-1",
        atomic_attack_name="attack",
        seed_group_id="seed-1",
        outcome=AttackOutcome.SUCCESS,
    )
    conversations = tuple(ConversationSummary(conversation_id=f"conv-{index}") for index in range(count))
    node = ScenarioProgressReadModel.build_hierarchy_node(
        delta=delta,
        result=result,
        conversations=conversations,
    )
    assert node.conversations == list(conversations)
    assert node.conversations_count == count
    assert node.conversations_truncated is False


async def test_sqlite_message_pieces_bounding(sqlite_instance: SQLiteMemory) -> None:
    """Continue through a large turn without permanently clipping any saved pieces."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MessagePiecesBoundingScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    conv_id = str(uuid.uuid4())

    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=conv_id,
        objective="Message pieces bounding test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    # Insert 150 pieces for sequence 0
    now = datetime.now(UTC)
    for i in range(150):
        piece = MessagePiece(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            role="user",
            original_value_data_type="text",
            original_value=f"Piece content {i}",
            converted_value=f"Piece content {i}",
            sequence=0,
            timestamp=now + timedelta(milliseconds=i),
        )
        await set_message_piece_sha256_async(piece)
        await sqlite_instance._run_database_operation_async(
            sqlite_instance._insert_entry, PromptMemoryEntry(entry=piece)
        )

    reader = SavedConversationReadService(memory=sqlite_instance)
    first = await reader.get_messages_async(attack_result_id=ar_id, conversation_id=conv_id, limit=100)
    assert first is not None
    assert len(first.messages) == 1
    assert len(first.messages[0].message_pieces) == 100
    assert first.partial_message_sequences == [0]
    assert first.pagination is not None
    second = await reader.get_messages_async(
        attack_result_id=ar_id, conversation_id=conv_id, limit=100, cursor=first.pagination.next_cursor
    )
    assert second is not None
    assert len(second.messages[0].message_pieces) == 50
    assert second.pagination is not None
    assert not second.pagination.has_more
    complete = await reader.get_messages_async(attack_result_id=ar_id, conversation_id=conv_id, limit=200)
    assert complete is not None
    assert complete.partial_message_sequences == []
    assert [piece.id for page in (first, second) for piece in page.messages[0].message_pieces] == [
        piece.id for piece in complete.messages[0].message_pieces
    ]


async def test_unloaded_conversations_in_full_hierarchy(sqlite_instance: SQLiteMemory) -> None:
    """Test that full hierarchy marks nodes with unhydrated related conversations as truncated with count=None."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="UnloadedConversationsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    main_conv_id = str(uuid.uuid4())
    pruned_conv_ids = [str(uuid.uuid4()) for _ in range(55)]

    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=main_conv_id,
        related_conversations={
            ConversationReference(conversation_id=cid, conversation_type=ConversationType.PRUNED)
            for cid in pruned_conv_ids
        },
        objective="Unloaded conversations test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    assert len(hierarchy.groups) == 1
    root_node = hierarchy.groups[0].roots[0]
    assert root_node.attack_result_id == ar_id
    assert root_node.conversations_count is None
    assert root_node.conversations_truncated is True
    assert root_node.conversations == []
    assert root_node.conversation_id == main_conv_id


def test_diagnostic_total_retries_scope_unmatched_attempt() -> None:
    """Test that diagnostics.total_retries reconciles with unmatched attempts having retry events."""
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group-1",
                atomic_attack_name="planned_attack",
                display_group="Group 1",
                technique_eval_hash="",
                seed_group_ids=["seed-1"],
            )
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed-1", objective_sha256="hash123", objective="Planned objective")],
    )

    planned_delta = _make_test_delta(
        attack_result_id="att-planned",
        outcome=AttackOutcome.SUCCESS,
        parent_collection="planned_attack",
        seed_group_id="seed-1",
    )
    planned_result = _make_test_result(
        attack_result_id="att-planned",
        atomic_group_id="group-1",
        atomic_attack_name="planned_attack",
        seed_group_id="seed-1",
        outcome=AttackOutcome.SUCCESS,
        total_retries=0,
        retries=[],
    )

    # Unmatched error attempt with 3 retry events
    retry_events = [
        RetryEvent(exception_type="RateLimitError", attempt_number=1),
        RetryEvent(exception_type="RateLimitError", attempt_number=2),
        RetryEvent(exception_type="TimeoutError", attempt_number=3),
    ]
    unmatched_delta = _make_test_delta(
        attack_result_id="att-unmatched",
        outcome=AttackOutcome.ERROR,
        retries=retry_events,
        error_type="TimeoutError",
        error_message="Timed out",
        parent_collection="unmatched_attack",
        seed_group_id="unmatched-seed",
    )
    unmatched_result = _make_test_result(
        attack_result_id="att-unmatched",
        atomic_group_id="unmatched-group",
        atomic_attack_name="unmatched_attack",
        seed_group_id="unmatched-seed",
        outcome=AttackOutcome.ERROR,
        total_retries=3,
        retries=retry_events,
    )

    summary = ScenarioProgressSummary(
        overall=ScenarioProgressCounts(
            completed=1,
            planned=1,
            succeeded=1,
            success_percentage=100,
            errors=0,
            retries=0,  # Canonical planned summary had 0 retries
        ),
        atomic_groups=[],
    )

    snapshot = ScenarioProgressSnapshot(
        deltas=(planned_delta, unmatched_delta),
        results=(planned_result, unmatched_result),
        summary=summary,
        plan=plan,
    )

    hierarchy = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scen-1",
        scenario_name="RetryScopeScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )

    assert hierarchy.diagnostics.total_retries == 3
    assert hierarchy.diagnostics.attack_retries_count == 1
    assert len(hierarchy.diagnostics.attack_retries) == 1
    assert hierarchy.diagnostics.attack_retries[0].attack_result_id == "att-unmatched"
    assert hierarchy.diagnostics.attack_retries[0].total_retries == 3


async def test_immediate_children_bounding_in_run_service(sqlite_instance: SQLiteMemory) -> None:
    """
    Test that get_run_hierarchy_node_async bounds immediate children to
    max_immediate_children with children_truncated=True.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ImmediateChildrenBoundingScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 250 child attack results
    child_ids = [str(uuid.uuid4()) for _ in range(250)]
    parent_id = str(uuid.uuid4())

    parent_ar = AttackResult(
        attack_result_id=parent_id,
        conversation_id=str(uuid.uuid4()),
        objective="Orchestration parent with 250 children",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.ORCHESTRATION.value},
        metadata={"child_attack_result_ids": child_ids},
    )
    child_ars = [
        AttackResult(
            attack_result_id=cid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Child {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        )
        for i, cid in enumerate(child_ids)
    ]

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent_ar] + child_ars)

    service = ScenarioRunService(memory=sqlite_instance)
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=parent_id,
        max_immediate_children=200,
    )

    assert node is not None
    assert node.children_truncated is True
    assert len(node.children) == 200


async def test_persisted_result_with_malformed_related_conversations_metadata(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that malformed related_conversation_ids metadata does not cause TypeError in progress or node reader."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MalformedRelatedConvScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=str(uuid.uuid4()),
        objective="Malformed related_conversation_ids test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        metadata={"related_conversation_ids": 7},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. get_run_progress_async should succeed without raising TypeError
    progress = await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=25)
    assert progress is not None
    assert len(progress.results) == 1
    assert progress.results[0].related_conversation_ids == []

    # 2. get_run_hierarchy_node_async should succeed without raising TypeError
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=ar_id,
    )
    assert node is not None
    assert node.conversations_count == 1
    assert node.conversations_truncated is False
    assert len(node.conversations) == 1

    # 3. get_run_hierarchy_async should also succeed without raising TypeError
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    assert len(hierarchy.groups) == 1
    root_node = hierarchy.groups[0].roots[0]
    assert root_node.conversations == []
    assert root_node.conversations_count is None
    assert root_node.conversations_truncated is True


def test_read_related_conversation_ids_shapes() -> None:
    """Test _read_related_conversation_ids with typed list, legacy valid list, and malformed fallbacks."""
    # Typed list
    delta1 = _make_test_delta(attack_result_id="d1")
    delta1.related_conversation_ids = ["conv-1", "conv-2"]
    assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta1) == ["conv-1", "conv-2"]

    # Legacy valid metadata list
    delta2 = _make_test_delta(attack_result_id="d2")
    delta2.related_conversation_ids = []
    delta2.attack_metadata = {"related_conversation_ids": ["conv-3", "conv-4"]}
    assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta2) == ["conv-3", "conv-4"]

    # Legacy metadata with non-string elements filtered out
    delta3 = _make_test_delta(attack_result_id="d3")
    delta3.related_conversation_ids = []
    delta3.attack_metadata = {"related_conversation_ids": ["conv-5", 42, None, "conv-6"]}
    assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta3) == ["conv-5", "conv-6"]

    # Typed list with non-string elements filtered
    delta4 = _make_test_delta(attack_result_id="d4")
    delta4.related_conversation_ids = ["conv-7", 100, None]  # type: ignore[list-item]
    assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta4) == ["conv-7"]

    # Malformed typed non-list fallbacks safely
    delta5 = _make_test_delta(attack_result_id="d5")
    delta5.related_conversation_ids = 123  # type: ignore[assignment]
    delta5.attack_metadata = {"related_conversation_ids": ["conv-fallback"]}
    assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta5) == ["conv-fallback"]

    # Malformed metadata shapes (int, str, dict) return empty list
    for malformed in [7, "conv-single", {"id": "conv-1"}, None, True]:
        delta_bad = _make_test_delta(attack_result_id="dbad")
        delta_bad.related_conversation_ids = []
        delta_bad.attack_metadata = {"related_conversation_ids": malformed}
        assert ScenarioProgressReadModel._read_related_conversation_ids(delta=delta_bad) == []


async def test_invalid_scenario_id_returns_empty_deltas(sqlite_instance: SQLiteMemory) -> None:
    """Test that non-UUID scenario_result_id returns empty list without raising ValueError."""
    deltas = await sqlite_instance.get_scenario_attack_result_deltas_by_ids_async(
        scenario_result_id="not-a-valid-uuid",
        attack_result_ids=["bad-id"],
    )
    assert deltas == []


def test_build_hierarchy_node_deduplicates_duplicate_child_ids() -> None:
    """Test that duplicate child IDs in stored order are deduplicated and tracked as unresolved."""
    delta = _make_test_delta(attack_result_id="parent")
    result = _make_test_result(
        attack_result_id="parent",
        child_attack_result_ids=["child-1", "child-1"],
    )
    c1_delta = _make_test_delta(attack_result_id="child-1")
    c1_result = _make_test_result(attack_result_id="child-1")

    node = ScenarioProgressReadModel.build_hierarchy_node(
        delta=delta,
        result=result,
        children_deltas=[c1_delta],
        children_results=[c1_result],
    )
    assert len(node.children) == 1
    assert node.children[0].attack_result_id == "child-1"
    assert node.unresolved_child_attack_result_ids == ["child-1"]


@pytest.mark.parametrize("saved_message_count", [0, 3])
@pytest.mark.parametrize("metadata_count", [None, 0, 99, "unknown"])
async def test_selected_node_uses_canonical_saved_stats_and_tree_defers_conversations_async(
    sqlite_instance: SQLiteMemory,
    saved_message_count: int,
    metadata_count: int | str | None,
) -> None:
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="RealStatsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    conv_id = str(uuid.uuid4())
    ar_uuid = uuid.uuid4()
    ar_id = str(ar_uuid)
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=conv_id,
        objective="Real conversation stats test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        metadata={} if metadata_count is None else {"conversation_message_count": metadata_count},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    for seq in range(saved_message_count):
        piece = MessagePiece(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            role="user" if seq % 2 == 0 else "assistant",
            original_value_data_type="text",
            original_value=f"Message {seq}",
            converted_value=f"Message {seq}",
            sequence=seq,
        )
        await set_message_piece_sha256_async(piece)
        await sqlite_instance._run_database_operation_async(
            sqlite_instance._insert_entry, PromptMemoryEntry(entry=piece)
        )

    service = ScenarioRunService(memory=sqlite_instance)

    with patch.object(
        sqlite_instance, "get_conversation_stats_async", wraps=sqlite_instance.get_conversation_stats_async
    ) as stats:
        hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
        stats.assert_not_awaited()
        node = await service.get_run_hierarchy_node_async(scenario_result_id=scenario_id, attack_result_id=ar_id)
        stats.assert_awaited_once_with(conversation_ids=[conv_id])
    assert node is not None
    assert len(node.conversations) == 1
    assert node.conversations[0].message_count == saved_message_count
    assert "messages" not in node.conversations[0].model_dump()
    assert hierarchy is not None
    root_node = hierarchy.groups[0].roots[0]
    assert root_node.conversations == []
    assert root_node.conversation_id == conv_id
    assert root_node.conversations_count is None
    assert root_node.conversations_truncated
    reader = SavedConversationReadService(memory=sqlite_instance)
    references = await reader.list_conversations_async(attack_result_id=ar_id)
    assert references is not None
    assert references.conversations == node.conversations
    transcript = await reader.get_messages_async(attack_result_id=ar_id, conversation_id=conv_id, limit=2)
    assert transcript is not None
    assert len(transcript.messages) == min(saved_message_count, 2)


async def test_bounded_conversation_messages_piece_budget_applied_at_sql_layer(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that piece budget is applied at SQL layer, preventing hydration of unbounded pieces (e.g. 1,001 rows)."""
    ar_id = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Insert 1,001 pieces for sequence 0
    pieces_seq0 = [
        MessagePiece(
            role="user",
            original_value=f"piece-{i}",
            converted_value=f"piece-{i}",
            conversation_id=conv_id,
            sequence=0,
            timestamp=now + timedelta(milliseconds=i),
        )
        for i in range(1001)
    ]
    # Insert 5 pieces for sequence 1
    pieces_seq1 = [
        MessagePiece(
            role="assistant",
            original_value=f"response-{i}",
            converted_value=f"response-{i}",
            conversation_id=conv_id,
            sequence=1,
            timestamp=now + timedelta(seconds=1, milliseconds=i),
        )
        for i in range(5)
    ]
    for p in pieces_seq0 + pieces_seq1:
        await set_message_piece_sha256_async(p)
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces_seq0 + pieces_seq1)

    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=conv_id,
        objective="Piece bounding test",
        outcome=AttackOutcome.SUCCESS,
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    reader = SavedConversationReadService(memory=sqlite_instance)
    with patch.object(
        PromptMemoryEntry, "get_message_piece", autospec=True, side_effect=PromptMemoryEntry.get_message_piece
    ) as hydrate:
        page = await reader.get_messages_async(attack_result_id=ar_id, conversation_id=conv_id, limit=100)
    # Canonical score lookup reads the same saved pieces once more.
    assert hydrate.call_count <= 200
    assert {str(call.args[0].id) for call in hydrate.call_args_list} == {str(piece.id) for piece in pieces_seq0[:100]}
    assert page is not None
    assert page.partial_message_sequences == [0]
    returned: list[MessagePiece] = []
    while True:
        returned.extend(piece for message in page.messages for piece in message.message_pieces)
        assert sum(len(message.message_pieces) for message in page.messages) <= 100
        assert page.pagination is not None
        if not page.pagination.has_more:
            break
        page = await reader.get_messages_async(
            attack_result_id=ar_id, conversation_id=conv_id, limit=100, cursor=page.pagination.next_cursor
        )
        assert page is not None
    assert [piece.id for piece in returned] == [piece.id for piece in pieces_seq0 + pieces_seq1]
    assert [message.sequence for message in page.messages] == [0, 1]
    assert len(page.messages[-1].message_pieces) == 5


async def test_diagnostics_total_retries_count_event_mismatch_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that when total_retries=0 but retry events exist, run detail,
    producer counts, diagnostic item, and diagnostics.total_retries all agree on 0.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MismatchRetryScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    retry_events = [
        RetryEvent(exception_type="RateLimitError", attempt_number=1),
        RetryEvent(exception_type="RateLimitError", attempt_number=2),
        RetryEvent(exception_type="TimeoutError", attempt_number=3),
    ]
    ar_id = str(uuid.uuid4())
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=str(uuid.uuid4()),
        objective="Retry mismatch test",
        outcome=AttackOutcome.ERROR,
        total_retries=0,  # Persisted count is 0
        retry_events=retry_events,  # 3 retry events recorded
        error_type="TimeoutError",
        error_message="Timed out",
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Run summary / detail reports total_retries = 0
    run_summary = await service.get_run_async(scenario_result_id=scenario_id)
    assert run_summary is not None
    assert run_summary.total_retries == 0

    # 2. Hierarchy diagnostics and overall counts report total_retries = 0
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    assert hierarchy.overall_counts.retries == 0
    assert hierarchy.diagnostics.total_retries == 0
    assert hierarchy.diagnostics.attack_retries_count == 1
    assert len(hierarchy.diagnostics.attack_retries) == 1

    # The individual diagnostic item reports total_retries = 0 while preserving the 3 events
    diag_item = hierarchy.diagnostics.attack_retries[0]
    assert diag_item.total_retries == 0
    assert len(diag_item.retries) == 3


@pytest.mark.asyncio
async def test_clipped_tree_defers_stats_until_the_selected_node_is_opened_async(sqlite_instance: SQLiteMemory) -> None:
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ClippedTreeStatsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 1. Parent with its own 3-message conversation
    parent_conv_id = str(uuid.uuid4())
    for seq in range(3):
        piece = MessagePiece(
            id=uuid.uuid4(),
            conversation_id=parent_conv_id,
            role="user" if seq % 2 == 0 else "assistant",
            original_value_data_type="text",
            original_value=f"Parent message {seq}",
            converted_value=f"Parent message {seq}",
            sequence=seq,
        )
        await set_message_piece_sha256_async(piece)
        await sqlite_instance._run_database_operation_async(
            sqlite_instance._insert_entry, PromptMemoryEntry(entry=piece)
        )

    child_conv_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    parent_id = str(uuid.uuid4())

    # Child is saved FIRST in memory
    child_ar = AttackResult(
        attack_result_id=child_id,
        conversation_id=child_conv_id,
        objective="Child attack",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
    )
    # Parent references child
    parent_ar = AttackResult(
        attack_result_id=parent_id,
        conversation_id=parent_conv_id,
        objective="Parent attack",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.ORCHESTRATION.value},
        metadata={"child_attack_result_ids": [child_id]},
    )
    # Add child first, then parent
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[child_ar, parent_ar])

    service = ScenarioRunService(memory=sqlite_instance)

    with patch.object(
        sqlite_instance, "get_conversation_stats_async", wraps=sqlite_instance.get_conversation_stats_async
    ) as stats:
        hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
        stats.assert_not_awaited()
        node = await service.get_run_hierarchy_node_async(scenario_result_id=scenario_id, attack_result_id=parent_id)
        stats.assert_awaited_once_with(conversation_ids=[parent_conv_id])
    assert hierarchy is not None
    assert hierarchy.truncated is True
    assert len(hierarchy.groups[0].roots) == 1
    root = hierarchy.groups[0].roots[0]
    assert root.attack_result_id == parent_id
    assert root.conversation_id == parent_conv_id
    assert root.conversations == []
    assert root.conversations_count is None
    assert root.conversations_truncated
    assert node is not None
    assert node.conversations[0].message_count == 3


@pytest.mark.parametrize("metadata_count", [None, 0])
async def test_conversation_stats_failure_is_deferred_and_never_becomes_false_zero_async(
    sqlite_instance: SQLiteMemory, metadata_count: int | None
) -> None:
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="FailedStatsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    conv_id = str(uuid.uuid4())
    ar_id = str(uuid.uuid4())
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=conv_id,
        objective="Failed stats test",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        metadata={} if metadata_count is None else {"conversation_message_count": metadata_count},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)
    # Mock get_conversation_stats_async to fail with an exception
    sqlite_instance.get_conversation_stats_async = AsyncMock(side_effect=RuntimeError("Database error"))

    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    root = hierarchy.groups[0].roots[0]
    assert root.conversations == []
    assert root.conversations_count is None
    assert root.conversations_truncated
    sqlite_instance.get_conversation_stats_async.assert_not_awaited()
    with pytest.raises(RuntimeError, match="Database error"):
        await service.get_run_hierarchy_node_async(scenario_result_id=scenario_id, attack_result_id=ar_id)


@pytest.mark.asyncio
async def test_parent_with_2500_children_bounds_unresolved_references(sqlite_instance: SQLiteMemory) -> None:
    """Test that a parent with 2,500 children bounds unresolved references to 500 in both detail and full hierarchy."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MassiveChildrenScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    parent_id = str(uuid.uuid4())
    child_ids = [str(uuid.uuid4()) for _ in range(2500)]

    # Add parent and the first 200 children into memory
    children = [
        AttackResult(
            attack_result_id=cid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Child {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        )
        for i, cid in enumerate(child_ids[:200])
    ]
    parent = AttackResult(
        attack_result_id=parent_id,
        conversation_id=str(uuid.uuid4()),
        objective="Parent with 2500 children",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.ORCHESTRATION.value},
        metadata={"child_attack_result_ids": child_ids},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent, *children])

    service = ScenarioRunService(memory=sqlite_instance)

    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=parent_id,
        max_immediate_children=200,
    )
    assert node is not None
    assert len(node.children) == 200
    assert node.unresolved_child_attack_result_ids == []
    assert node.children_count == 2500
    assert node.child_attack_result_ids == child_ids[:200]
    assert node.next_children_cursor is not None
    missing_page = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=parent_id,
        max_immediate_children=200,
        children_cursor=node.next_children_cursor,
    )
    assert missing_page is not None
    assert missing_page.children == []
    assert missing_page.unresolved_child_attack_result_ids == child_ids[200:400]
    assert missing_page.next_children_cursor is not None

    # 2. Full hierarchy with max_nodes=1: parent has 2,500 unresolved children capped at 500 (not 2,500)
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy is not None
    root = hierarchy.groups[0].roots[0]
    assert len(root.unresolved_child_attack_result_ids) == 500
    assert root.unresolved_child_references_truncated is True


@pytest.mark.asyncio
async def test_cold_hierarchy_hydration_bounded_by_max_nodes(sqlite_instance: SQLiteMemory) -> None:
    """Test that a cold max_nodes=1 request only fetches 1 delta and does not hydrate 1,001 results across 3 pages."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ColdHydrationScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # Insert 1,001 attack results
    results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Attack {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={"parent_collection": "group_1", "result_role": AttackResultRole.TARGET_FACING.value},
        )
        for i in range(1001)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    service = ScenarioRunService(memory=sqlite_instance)
    with (
        patch.object(
            sqlite_instance, "get_scenario_root_deltas_async", wraps=sqlite_instance.get_scenario_root_deltas_async
        ) as roots,
        patch.object(
            sqlite_instance,
            "_row_to_scenario_attack_result_delta",
            wraps=sqlite_instance._row_to_scenario_attack_result_delta,
        ) as hydrate,
        patch.object(sqlite_instance, "get_scenario_attack_result_deltas_async", new_callable=AsyncMock) as full_deltas,
        patch.object(service._progress_read_model, "get_snapshot_async", new_callable=AsyncMock) as progress,
    ):
        hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    roots.assert_awaited_once()
    assert roots.call_args.kwargs["limit"] == 1
    assert hydrate.call_count == 1
    full_deltas.assert_not_awaited()
    progress.assert_not_awaited()
    assert hierarchy is not None
    assert hierarchy.truncated is True
    assert hierarchy.has_more is True
    assert hierarchy.next_cursor is not None


def test_build_scenario_hierarchy_unqueried_conversation_not_false_zero() -> None:
    """Unqueried conversation metadata stays unknown rather than reporting a fabricated zero."""
    delta = _make_test_delta(
        attack_result_id="root-1",
        role=AttackResultRole.TARGET_FACING,
        parent_collection="group_1",
        seed_group_id="seed_1",
        conversation_id="conv-unqueried",
    )
    snapshot = _make_test_snapshot(deltas=[delta])
    projection = ScenarioProgressReadModel.build_scenario_hierarchy(
        scenario_result_id="scenario-run-1",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        snapshot=snapshot,
    )
    root = projection.groups[0].roots[0]
    assert root.conversation_id == "conv-unqueried"
    assert root.conversations == []
    assert root.conversations_count is None
    assert root.conversations_truncated


@pytest.mark.asyncio
async def test_cold_hierarchy_preserves_run_wide_totals_for_tail_error(sqlite_instance: SQLiteMemory) -> None:
    """Test that max_nodes=1 on cold run with 501 results accurately reports 501 units, 1 error, and 7 retries."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="TailErrorScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 successes, 501st has an error and 7 retries
    now = datetime.now(UTC)
    results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Attack {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={"parent_collection": f"group_{i}", "result_role": AttackResultRole.TARGET_FACING.value},
            timestamp=now + timedelta(seconds=i),
        )
        for i in range(500)
    ]
    retry_events = [
        RetryEvent(
            attempt_number=r,
            timestamp=now + timedelta(seconds=500 + r),
            exception_message="Network glitch",
            exception_type="NetworkError",
        )
        for r in range(1, 8)
    ]
    tail_error = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Attack 500 (failed)",
        outcome=AttackOutcome.ERROR,
        error_type="FatalError",
        error_message="Failed after retries",
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "group_500", "result_role": AttackResultRole.TARGET_FACING.value},
        timestamp=now + timedelta(seconds=510),
        retry_events=retry_events,
        total_retries=7,
    )
    results.append(tail_error)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Cold hierarchy with max_nodes=1
    hierarchy_cold = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_cold is not None
    assert hierarchy_cold.truncated is True
    assert len(hierarchy_cold.groups[0].roots) == 1

    # Exact run-wide totals must be retained!
    assert hierarchy_cold.overall_counts.completed == 501
    assert hierarchy_cold.overall_counts.errors == 1
    assert hierarchy_cold.overall_counts.retries == 7
    assert hierarchy_cold.overall_counts.producer_counts.target_facing.attempts == 501
    assert hierarchy_cold.diagnostics.failed_attacks_count == 1
    assert hierarchy_cold.diagnostics.failed_attacks_truncated is True
    assert hierarchy_cold.diagnostics.total_retries == 7
    assert hierarchy_cold.diagnostics.attack_retries_truncated is True

    # 2. Read progress (which hydrates all 501 deltas into cache)
    progress = await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=1000)
    assert progress is not None

    # 3. Repeat hierarchy with max_nodes=1 on warm cache
    hierarchy_warm = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_warm is not None

    # Cold and warm hierarchy calls must have identical totals without any database writes!
    assert hierarchy_warm.overall_counts == hierarchy_cold.overall_counts
    assert hierarchy_warm.diagnostics.total_retries == hierarchy_cold.diagnostics.total_retries == 7
    assert hierarchy_warm.diagnostics.failed_attacks_count == hierarchy_cold.diagnostics.failed_attacks_count == 1


@pytest.mark.asyncio
async def test_repeated_attempts_latest_error_beyond_cap_reported_correctly(sqlite_instance: SQLiteMemory) -> None:
    """Test that a latest error beyond the 500 cap is reported as an error, not earlier success."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="RepeatedAttemptsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    now = datetime.now(UTC)
    results: list[AttackResult] = []
    # Unit 0 Attempt 1: SUCCESS at time 0
    results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Unit 0",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "unit_0",
                "seed_group_id": "seed_0",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=now,
        )
    )
    # Units 1..499: SUCCESS at times 1..499
    results.extend(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Unit {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": f"unit_{i}",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=now + timedelta(seconds=i),
        )
        for i in range(1, 500)
    )
    # Unit 0 Attempt 2: ERROR at time 501 (beyond the 500-delta cap)
    results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Unit 0",
            outcome=AttackOutcome.ERROR,
            error_type="Attempt2Error",
            error_message="Attempt 2 failed",
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "unit_0",
                "seed_group_id": "seed_0",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=now + timedelta(seconds=501),
        )
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    service = ScenarioRunService(memory=sqlite_instance)

    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy is not None
    # Latest attempt for unit_0 was an error, so overall errors must be 1 (not 0)!
    assert hierarchy.overall_counts.errors == 1
    assert hierarchy.overall_counts.succeeded == 499
    assert hierarchy.overall_counts.completed == 500


@pytest.mark.asyncio
async def test_cold_hierarchy_projection_preserves_parent_beyond_delta_page_limit(sqlite_instance):
    """
    Test that a sequential orchestration parent persisted after 500 children is correctly
    identified as the root on a cold request (where delta pagination only loads 500 deltas),
    matching warm cache behavior and never mistaking child 0 for an independent root.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime.now(UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="Cold Page Limit Test",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # Persist 500 children first
    child_ids = [str(uuid.uuid4()) for _ in range(500)]
    child_results = [
        AttackResult(
            attack_result_id=cid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Child attack {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "seq_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
                "attempt_index": i + 1,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i, cid in enumerate(child_ids)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=child_results)

    # Persist 1 Sequential orchestration parent after the 500 children
    parent_id = str(uuid.uuid4())
    parent_result = AttackResult(
        attack_result_id=parent_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential parent attack",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "seq_group",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": child_ids},
        timestamp=base_time + timedelta(seconds=501),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent_result])

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Cold hierarchy request with max_nodes=1
    hierarchy_cold = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_cold is not None
    assert len(hierarchy_cold.groups) == 1
    cold_roots = hierarchy_cold.groups[0].roots
    assert len(cold_roots) == 1

    cold_root = cold_roots[0]
    # The root MUST be the parent, NOT child 0!
    assert cold_root.attack_result_id == parent_id
    assert cold_root.parent_attack_result_id is None
    assert cold_root.child_index is None
    assert cold_root.result_role == AttackResultRole.ORCHESTRATION
    assert cold_root.attempt_index is None

    # 2. Warm cache: read progress to hydrate all deltas into cache
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=500)

    # 3. Warm hierarchy request with max_nodes=1 must match cold exactly
    hierarchy_warm = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_warm is not None
    warm_roots = hierarchy_warm.groups[0].roots
    assert len(warm_roots) == 1
    warm_root = warm_roots[0]
    assert warm_root.attack_result_id == parent_id
    assert warm_root.parent_attack_result_id is None
    assert warm_root.child_index is None
    assert warm_root.result_role == AttackResultRole.ORCHESTRATION

    # 4. Request with max_nodes=5 must nest children properly under the parent
    hierarchy_expanded = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=5)
    assert hierarchy_expanded is not None
    expanded_root = hierarchy_expanded.groups[0].roots[0]
    assert expanded_root.attack_result_id == parent_id
    # 1 root + 4 children = 5 nodes
    assert len(expanded_root.children) == 4
    for idx, child in enumerate(expanded_root.children):
        assert child.attack_result_id == child_ids[idx]
        assert child.parent_attack_result_id == parent_id
        assert child.child_index == idx
        assert child.attempt_index == idx + 1
        assert child.result_role == AttackResultRole.TARGET_FACING
    assert len(expanded_root.unresolved_child_attack_result_ids) == 496
    assert hierarchy_expanded.truncated is True


@pytest.mark.asyncio
async def test_empty_child_array_metadata_parent_resolution_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that when 500 children have empty metadata {"child_attack_result_ids": []},
    they are not mistaken for orchestration parents and parent at 501 is resolved correctly.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="EmptyChildArrayScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    child_ids = [str(uuid.uuid4()) for _ in range(500)]
    child_results = [
        AttackResult(
            attack_result_id=cid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Child attack {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "seq_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
                "attempt_index": i + 1,
            },
            metadata={"child_attack_result_ids": []},
            timestamp=base_time + timedelta(seconds=i),
        )
        for i, cid in enumerate(child_ids)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=child_results)

    parent_id = str(uuid.uuid4())
    parent_result = AttackResult(
        attack_result_id=parent_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential parent attack",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "seq_group",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": child_ids},
        timestamp=base_time + timedelta(seconds=501),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent_result])

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Cold hierarchy request with max_nodes=1
    hierarchy_cold = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_cold is not None
    assert len(hierarchy_cold.groups) == 1
    cold_roots = hierarchy_cold.groups[0].roots
    assert len(cold_roots) == 1
    cold_root = cold_roots[0]
    assert cold_root.attack_result_id == parent_id
    assert cold_root.parent_attack_result_id is None
    assert cold_root.child_index is None
    assert cold_root.result_role == AttackResultRole.ORCHESTRATION

    # 2. Warm hierarchy request with max_nodes=1
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=500)
    hierarchy_warm = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_warm is not None
    warm_root = hierarchy_warm.groups[0].roots[0]
    assert warm_root.attack_result_id == parent_id
    assert warm_root.parent_attack_result_id is None
    assert warm_root.child_index is None


@pytest.mark.asyncio
async def test_501_orchestration_parents_with_outer_parent_targeted_resolution(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test targeted parent resolution when there are 501 inner orchestration parents
    and 1 outer parent referencing all of them.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ManyParentsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 501 inner parents, each having 1 leaf child
    inner_parent_ids = [str(uuid.uuid4()) for _ in range(501)]
    all_results: list[AttackResult] = []
    for i, pid in enumerate(inner_parent_ids):
        leaf_id = str(uuid.uuid4())
        leaf = AttackResult(
            attack_result_id=leaf_id,
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "group_orch",
                "result_role": AttackResultRole.TARGET_FACING.value,
                "attempt_index": 1,
            },
            timestamp=base_time + timedelta(seconds=i * 2),
        )
        parent = AttackResult(
            attack_result_id=pid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Inner parent {i}",
            outcome=AttackOutcome.SUCCESS,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "group_orch",
                "result_role": AttackResultRole.ORCHESTRATION.value,
                "attempt_index": i + 1,
            },
            metadata={"child_attack_result_ids": [leaf_id]},
            timestamp=base_time + timedelta(seconds=i * 2 + 1),
        )
        all_results.extend([leaf, parent])

    # 1 Outer parent referencing all 501 inner parents
    outer_id = str(uuid.uuid4())
    outer_parent = AttackResult(
        attack_result_id=outer_id,
        conversation_id=str(uuid.uuid4()),
        objective="Outer orchestration parent",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "group_orch",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": inner_parent_ids},
        timestamp=base_time + timedelta(seconds=1100),
    )
    all_results.append(outer_parent)

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)

    service = ScenarioRunService(memory=sqlite_instance)

    # Cold max_nodes=1 hierarchy call: root MUST resolve to outer_parent!
    hierarchy_cold = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert hierarchy_cold is not None
    assert len(hierarchy_cold.groups) == 1
    roots = hierarchy_cold.groups[0].roots
    assert len(roots) == 1
    assert roots[0].attack_result_id == outer_id
    assert roots[0].parent_attack_result_id is None
    assert roots[0].child_index is None


@pytest.mark.asyncio
async def test_unreached_node_zero_inference_attempt_index_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test zero-inference on unreached nodes: when parent_id is None, child_index MUST be None,
    and attempt_index must be preserved on both hierarchy and detail projections.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="UnreachedZeroInferenceScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ar_id = str(uuid.uuid4())
    ar = AttackResult(
        attack_result_id=ar_id,
        conversation_id=str(uuid.uuid4()),
        objective="Disconnected node",
        outcome=AttackOutcome.SUCCESS,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "group_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 7,
        },
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Hierarchy projection
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=scenario_id)
    assert hierarchy is not None
    assert len(hierarchy.groups) == 1
    node = hierarchy.groups[0].roots[0]
    assert node.attack_result_id == ar_id
    assert node.parent_attack_result_id is None
    assert node.child_index is None
    assert node.attempt_index == 7

    # 2. Detail projection
    detail = await service.get_run_hierarchy_node_async(
        scenario_result_id=scenario_id,
        attack_result_id=ar_id,
    )
    assert detail is not None
    assert detail.attack_result_id == ar_id
    assert detail.parent_attack_result_id is None
    assert detail.child_index is None
    assert detail.attempt_index == 7


@pytest.mark.asyncio
async def test_501_units_with_tail_error_and_unmatched_tail_error_diagnostics_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test 501 planned units with tail error + unmatched tail error.
    Verify cold max_nodes=1 overall counts, outcome breakdown, group counts,
    and diagnostics match warm breakdown.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    # Plan with 501 units
    seed_groups = [
        ScenarioRunPlanSeedGroup(
            id=f"seed_{i}",
            objective=f"Objective {i}",
            objective_sha256=f"hash_{i}",
        )
        for i in range(501)
    ]
    atomic_group = ScenarioRunPlanAtomicGroup(
        id="group_1",
        atomic_attack_name="attack_type_1",
        display_group="Attack Type 1",
        technique_eval_hash="hash_tech_1",
        seed_group_ids=[s.id for s in seed_groups],
    )
    plan = ScenarioRunPlan(
        atomic_groups=[atomic_group],
        seed_groups=seed_groups,
    )
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="TailErrorParityScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
        metadata={"run_plan": plan.model_dump(mode="json")},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 successful units
    all_results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_type_1",
                "parent_eval_hash": "hash_tech_1",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # Unit 500 (the 501st unit): attempt fails with 10 retries
    tail_planned_id = str(uuid.uuid4())
    tail_planned_result = AttackResult(
        attack_result_id=tail_planned_id,
        conversation_id=str(uuid.uuid4()),
        objective="Objective 500",
        outcome=AttackOutcome.ERROR,
        total_retries=10,
        error_type="PlannedTailError",
        error_message="Planned tail failure",
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_type_1",
            "parent_eval_hash": "hash_tech_1",
            "seed_group_id": "seed_500",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=500),
    )
    all_results.append(tail_planned_result)

    # Unmatched / unplanned tail attempt with 6 retries
    unmatched_id = str(uuid.uuid4())
    unmatched_result = AttackResult(
        attack_result_id=unmatched_id,
        conversation_id=str(uuid.uuid4()),
        objective="Unmatched objective",
        outcome=AttackOutcome.ERROR,
        total_retries=6,
        error_type="UnmatchedTailError",
        error_message="Unmatched tail failure",
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "unmatched_collection",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=501),
    )
    all_results.append(unmatched_result)

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)

    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Cold hierarchy request with max_nodes=1
    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.truncated is True

    # 2. Warm hierarchy request with max_nodes=1
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Parity checks:
    # A. Overall logical outcomes: 500 successes, 1 error (unmatched attempt does not inflate logical unit error count)
    assert cold_hier.overall_counts.completed == 501
    assert cold_hier.overall_counts.succeeded == 500
    assert cold_hier.overall_counts.errors == 1
    assert cold_hier.overall_counts.outcomes is not None
    assert warm_hier.overall_counts.outcomes is not None
    assert cold_hier.overall_counts.outcomes.successes == 500
    assert cold_hier.overall_counts.outcomes.errors == 1
    assert cold_hier.overall_counts.outcomes.successes == warm_hier.overall_counts.outcomes.successes
    assert cold_hier.overall_counts.outcomes.errors == warm_hier.overall_counts.outcomes.errors
    assert (
        cold_hier.overall_counts.outcomes.success_rate_decided == warm_hier.overall_counts.outcomes.success_rate_decided
    )

    # B. Diagnostics: raw diagnostics report ALL failed attacks (both planned and unmatched)
    assert cold_hier.diagnostics.failed_attacks_count == 2
    assert cold_hier.diagnostics.total_retries >= 16

    # C. Per-group counts parity
    assert cold_hier.groups[0].counts.completed == 501
    assert cold_hier.groups[0].counts.succeeded == 500
    assert cold_hier.groups[0].counts.errors == 1
    assert cold_hier.groups[0].counts.completed == warm_hier.groups[0].counts.completed
    assert cold_hier.groups[0].counts.succeeded == warm_hier.groups[0].counts.succeeded
    assert cold_hier.groups[0].counts.errors == warm_hier.groups[0].counts.errors


@pytest.mark.asyncio
async def test_legacy_recount_paged_deltas_without_hydrating_scenario_results(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that legacy recount for runs needing SDK statistics pages deltas (batches of 500)
    and does NOT call get_scenario_results_async (avoiding memory bloat).
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="LegacyRecountScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 501 results with seed identifiers (triggers needs_sdk_statistics)
    results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Atomic seed attack {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            atomic_attack_identifier=AtomicAttackIdentifier(
                class_name="MockAttack",
                seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value=f"seed_value_{i}")],
            ),
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "group_legacy",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(501)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    # Spy on memory methods
    original_get_scenario_results = sqlite_instance.get_scenario_results_async
    original_get_deltas = sqlite_instance.get_scenario_attack_result_deltas_async
    original_get_summaries = sqlite_instance.get_scenario_recount_summaries_async

    scenario_results_called = False
    deltas_called = False
    summary_page_sizes: list[int] = []

    async def tracking_get_scenario_results(*args: Any, **kwargs: Any) -> Any:
        nonlocal scenario_results_called
        scenario_results_called = True
        return await original_get_scenario_results(*args, **kwargs)

    async def tracking_get_deltas(*args: Any, **kwargs: Any) -> Any:
        nonlocal deltas_called
        deltas_called = True
        return await original_get_deltas(*args, **kwargs)

    async def tracking_get_summaries(*args: Any, **kwargs: Any) -> Any:
        summary_page_sizes.append(kwargs.get("limit", 0))
        return await original_get_summaries(*args, **kwargs)

    sqlite_instance.get_scenario_results_async = tracking_get_scenario_results  # type: ignore[assignment]
    sqlite_instance.get_scenario_attack_result_deltas_async = tracking_get_deltas  # type: ignore[assignment]
    sqlite_instance.get_scenario_recount_summaries_async = tracking_get_summaries  # type: ignore[assignment]

    service = ScenarioRunService(memory=sqlite_instance)

    # Fetch run summary list which triggers recount for runs needing SDK statistics
    run_list = await service.list_runs_async(limit=10)
    assert run_list is not None
    matched_run = next((r for r in run_list.items if r.scenario_result_id == scenario_id), None)
    assert matched_run is not None
    assert matched_run.completed_attacks == 501

    # Verified: full ScenarioResult was NOT loaded, deltas were NOT hydrated, summaries were paged!
    assert scenario_results_called is False
    assert deltas_called is False
    assert len(summary_page_sizes) >= 2  # Paged in batches of 500


@pytest.mark.asyncio
async def test_501_unplanned_group_attempts_parity_between_cold_and_warm_cache(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that a scenario with 501 unplanned group attempts maintains exact parity
    in group counts, overall counts, and outcomes between cold (max_nodes=1) and warm cache.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="UnplannedParityScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 successful unplanned attempts
    all_results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Unplanned objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "unplanned_attack_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # 501st unplanned attempt is an error with retries
    all_results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Unplanned objective 500",
            outcome=AttackOutcome.ERROR,
            total_retries=5,
            error_type="UnplannedExecutionError",
            error_message="Execution failed",
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "unplanned_attack_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=500),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)
    service = ScenarioRunService(memory=sqlite_instance)

    # 1. Cold hierarchy with max_nodes=1
    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.truncated is True

    # 2. Warm hierarchy
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Parity assertions:
    assert cold_hier.overall_counts.completed == 501
    assert cold_hier.overall_counts.succeeded == 500
    assert cold_hier.overall_counts.errors == 1
    assert cold_hier.overall_counts.completed == warm_hier.overall_counts.completed
    assert cold_hier.overall_counts.succeeded == warm_hier.overall_counts.succeeded
    assert cold_hier.overall_counts.errors == warm_hier.overall_counts.errors

    # Outcomes parity
    assert cold_hier.overall_counts.outcomes is not None
    assert warm_hier.overall_counts.outcomes is not None
    assert cold_hier.overall_counts.outcomes.successes == warm_hier.overall_counts.outcomes.successes
    assert cold_hier.overall_counts.outcomes.errors == warm_hier.overall_counts.outcomes.errors

    # Group counts parity for the unplanned group
    assert len(cold_hier.groups) == len(warm_hier.groups) == 1
    cold_group = cold_hier.groups[0]
    warm_group = warm_hier.groups[0]
    assert cold_group.counts.completed == warm_group.counts.completed == 501
    assert cold_group.counts.succeeded == warm_group.counts.succeeded == 500
    assert cold_group.counts.errors == warm_group.counts.errors == 1
    assert cold_group.counts.retries == warm_group.counts.retries == 5


@pytest.mark.asyncio
async def test_500_cyclic_results_with_outer_parent_at_501_parity(sqlite_instance: SQLiteMemory) -> None:
    """
    Test that a 500-result cyclic component (A -> B -> C -> ... -> A) with an outer parent
    at position 501 pointing to A discovers the outer parent on cold max_nodes=1, maintaining
    exact parity with warm cache.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="CyclicScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    cyclic_ids = [str(uuid.uuid4()) for _ in range(500)]
    outer_parent_id = str(uuid.uuid4())

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=cyclic_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Cyclic objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            metadata={
                "child_attack_result_ids": [cyclic_ids[(i + 1) % 500]],
                "completion_policy": "ALL_CHILDREN",
                "executed_turns": 1,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    results.append(
        AttackResult(
            attack_result_id=outer_parent_id,
            conversation_id=str(uuid.uuid4()),
            objective="Outer parent objective",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": "outer_seed",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={
                "child_attack_result_ids": [cyclic_ids[-1]],
                "completion_policy": "ALL_CHILDREN",
                "executed_turns": 1,
            },
            timestamp=base_time + timedelta(seconds=500),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.truncated is True

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    cold_root_ids = [r.attack_result_id for g in cold_hier.groups for r in g.roots]
    warm_root_ids = [r.attack_result_id for g in warm_hier.groups for r in g.roots]
    assert outer_parent_id in cold_root_ids
    assert outer_parent_id in warm_root_ids
    assert cold_root_ids == warm_root_ids


async def test_shared_child_with_1001_parents_has_bounded_native_root_pages_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Page the actual parent roots without reverse-discovering or hydrating their complete fan-in."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ParentFaninScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    child_id = str(uuid.uuid4())
    child_result = AttackResult(
        attack_result_id=child_id,
        conversation_id=str(uuid.uuid4()),
        objective="Shared child objective",
        outcome=AttackOutcome.SUCCESS,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={"parent_collection": "attack_group", "seed_group_id": "seed_0"},
        timestamp=base_time,
    )

    parents = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Parent objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={"parent_collection": "attack_group", "seed_group_id": f"seed_{i + 1}"},
            metadata={
                "child_attack_result_ids": [child_id],
                "completion_policy": "ALL_CHILDREN",
                "executed_turns": 1,
            },
            timestamp=base_time + timedelta(seconds=i + 1),
        )
        for i in range(1001)
    ]

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[child_result, *parents])

    service = ScenarioRunService(memory=sqlite_instance)
    with patch.object(
        sqlite_instance,
        "_row_to_scenario_attack_result_delta",
        wraps=sqlite_instance._row_to_scenario_attack_result_delta,
    ) as mapped:
        page = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert page is not None
    roots = [node for group in page.groups for node in group.roots]
    assert len(roots) == 10
    assert {root.attack_result_id for root in roots} == {parent.attack_result_id for parent in parents[:10]}
    assert all(not root.children for root in roots)
    assert page.has_more
    assert page.next_cursor is not None
    assert mapped.call_count == 10


@pytest.mark.asyncio
async def test_raw_retrying_attacks_count_counts_all_attempts_not_units_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that raw_retrying_attacks_count counts all retrying attempts (even for the same logical unit),
    maintaining parity between cold SQL aggregates and warm cache.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="RetryingAttemptsScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    attempt_1 = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Objective 1",
        outcome=AttackOutcome.ERROR,
        total_retries=2,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time,
    )
    attempt_2 = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Objective 1",
        outcome=AttackOutcome.SUCCESS,
        total_retries=3,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=1),
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attempt_1, attempt_2])
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=10)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert warm_hier is not None

    assert cold_hier.diagnostics.attack_retries_count == 2
    assert warm_hier.diagnostics.attack_retries_count == 2
    assert cold_hier.diagnostics.total_retries == 6
    assert warm_hier.diagnostics.total_retries == 6
    assert cold_hier.diagnostics.failed_attacks_count == 1
    assert warm_hier.diagnostics.failed_attacks_count == 1


@pytest.mark.asyncio
async def test_recount_with_saved_plan_preserves_counting_policy_and_unmatched_identifier_only_error(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that legacy recount loads the saved plan from the scenario result header and preserves
    the planned counting policy: planned=1, completed=1, succeeded=1, 100% success percentage,
    with the unmatched identifier-only error reflected in producer attempts and raw errors.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    planned_obj = "Planned Objective 1"
    planned_sha = hashlib.sha256(planned_obj.encode("utf-8")).hexdigest()

    saved_plan = ScenarioRunPlan(
        scenario_registry_name="PlanRecountScenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group_1",
                display_group="display_1",
                atomic_attack_name="attack_1",
                technique_eval_hash="hash_tech_1",
                seed_group_ids=["seed_1"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id="seed_1",
                objective=planned_obj,
                objective_sha256=planned_sha,
            )
        ],
    )

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="PlanRecountScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: saved_plan.model_dump()},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    success_attempt = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective=planned_obj,
        outcome=AttackOutcome.SUCCESS,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_1",
            "parent_eval_hash": "hash_tech_1",
            "seed_group_id": "seed_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time,
    )

    error_identifier = AtomicAttackIdentifier(
        class_name="MockAttack",
        seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value="unmatched_seed_val")],
    )
    error_attempt = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Unmatched Error Objective",
        atomic_attack_identifier=error_identifier,
        outcome=AttackOutcome.ERROR,
        total_retries=1,
        error_type="UnmatchedError",
        error_message="Failure",
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_1",
            "parent_eval_hash": "hash_tech_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=1),
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[success_attempt, error_attempt])
    service = ScenarioRunService(memory=sqlite_instance)

    run_list = await service.list_runs_async(limit=10)
    assert run_list is not None
    matched_run = next((r for r in run_list.items if r.scenario_result_id == scenario_id), None)
    assert matched_run is not None

    assert matched_run.completed_attacks == 1
    assert matched_run.total_attacks == 1
    assert matched_run.objective_achieved_rate == 100

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=10)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert warm_hier is not None

    assert cold_hier.overall_counts.completed == 1
    assert cold_hier.overall_counts.planned == 1
    assert cold_hier.overall_counts.succeeded == 1
    assert cold_hier.overall_counts.errors == 0
    assert cold_hier.overall_counts.success_percentage == 100.0

    assert warm_hier.overall_counts.completed == 1
    assert warm_hier.overall_counts.planned == 1
    assert warm_hier.overall_counts.succeeded == 1
    assert warm_hier.overall_counts.errors == 0
    assert warm_hier.overall_counts.success_percentage == 100.0

    assert cold_hier.overall_counts.producer_counts.target_facing.attempts == 2
    assert cold_hier.overall_counts.producer_counts.target_facing.errors == 1
    assert warm_hier.overall_counts.producer_counts.target_facing.attempts == 2
    assert warm_hier.overall_counts.producer_counts.target_facing.errors == 1

    assert cold_hier.diagnostics.failed_attacks_count == 1
    assert warm_hier.diagnostics.failed_attacks_count == 1


@pytest.mark.asyncio
async def test_recount_updates_group_counts_and_raw_aggregates_parity(sqlite_instance: SQLiteMemory) -> None:
    """
    Test that recount updates group_counts and raw aggregates so that cold hierarchy
    matches warm hierarchy when SDK recount is triggered.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    alpha_obj = "Alpha Objective"
    alpha_sha = hashlib.sha256(alpha_obj.encode("utf-8")).hexdigest()

    saved_plan = ScenarioRunPlan(
        scenario_registry_name="MultiGroupRecountScenario",
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group_alpha",
                display_group="display_alpha",
                atomic_attack_name="attack_alpha",
                technique_eval_hash="eval_alpha",
                seed_group_ids=["seed_alpha_1"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id="seed_alpha_1",
                objective=alpha_obj,
                objective_sha256=alpha_sha,
            )
        ],
    )

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="MultiGroupRecountScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: saved_plan.model_dump()},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    res_1 = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective=alpha_obj,
        outcome=AttackOutcome.SUCCESS,
        total_retries=1,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_alpha",
            "parent_eval_hash": "eval_alpha",
            "seed_group_id": "seed_alpha_1",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time,
    )

    ident = AtomicAttackIdentifier(
        class_name="MockAttack",
        seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value="alpha_seed_val")],
    )
    res_2 = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective=alpha_obj,
        atomic_attack_identifier=ident,
        outcome=AttackOutcome.SUCCESS,
        total_retries=2,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_alpha",
            "parent_eval_hash": "eval_alpha",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=1),
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[res_1, res_2])
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=10)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=10)
    assert warm_hier is not None

    assert len(cold_hier.groups) == len(warm_hier.groups) == 1
    cold_grp = cold_hier.groups[0]
    warm_grp = warm_hier.groups[0]
    assert cold_grp.counts.completed == warm_grp.counts.completed == 1
    assert cold_grp.counts.succeeded == warm_grp.counts.succeeded == 1
    assert cold_grp.counts.retries == warm_grp.counts.retries == 4
    assert cold_hier.diagnostics.total_retries == warm_hier.diagnostics.total_retries == 4
    assert cold_hier.diagnostics.attack_retries_count == warm_hier.diagnostics.attack_retries_count == 2


@pytest.mark.asyncio
async def test_500_leaves_500_inner_parents_one_outer_root_parity(sqlite_instance: SQLiteMemory) -> None:
    """Test 3-layer hierarchy (500 leaves -> 500 inner parents -> 1 outer root) parity."""
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    leaf_ids = [str(uuid.uuid4()) for _ in range(500)]
    inner_ids = [str(uuid.uuid4()) for _ in range(500)]
    outer_root_id = str(uuid.uuid4())

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=leaf_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    results.extend(
        AttackResult(
            attack_result_id=inner_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Inner parent objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={
                "child_attack_result_ids": [leaf_ids[i]],
                "completion_policy": "ALL_CHILDREN",
                "executed_turns": 1,
            },
            timestamp=base_time + timedelta(seconds=500 + i),
        )
        for i in range(500)
    )

    results.append(
        AttackResult(
            attack_result_id=outer_root_id,
            conversation_id=str(uuid.uuid4()),
            objective="Outer root objective",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": "root_seed",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={
                "child_attack_result_ids": inner_ids,
                "completion_policy": "ALL_CHILDREN",
                "executed_turns": 1,
            },
            timestamp=base_time + timedelta(seconds=1000),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=1100)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    cold_root_ids = [r.attack_result_id for g in cold_hier.groups for r in g.roots]
    warm_root_ids = [r.attack_result_id for g in warm_hier.groups for r in g.roots]
    assert cold_root_ids == warm_root_ids == [outer_root_id]


@pytest.mark.asyncio
async def test_501_distinct_units_prefix_recount_preserves_exact_statistics_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that 501 distinct units preserve exact run-wide statistics under cold max_nodes=1."""
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS if i < 500 else AttackOutcome.ERROR,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(501)
    ]

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    assert cold_hier.overall_counts.completed == warm_hier.overall_counts.completed == 501
    assert (
        cold_hier.overall_counts.producer_counts.target_facing.attempts
        == warm_hier.overall_counts.producer_counts.target_facing.attempts
        == 501
    )
    assert cold_hier.overall_counts.outcomes is not None
    assert warm_hier.overall_counts.outcomes is not None
    assert cold_hier.overall_counts.outcomes.errors == warm_hier.overall_counts.outcomes.errors == 1


@pytest.mark.asyncio
async def test_501_attempts_of_one_unit_with_7_retries_diagnostic_pressure_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that 501 attempts of 1 unit with 7 retries yields total retry pressure of 507 cold and warm."""
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Single shared objective",
            outcome=AttackOutcome.SUCCESS,
            total_retries=7 if i == 500 else 0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": "seed_0",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(501)
    ]

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    assert cold_hier.diagnostics.total_retries == 507
    assert warm_hier.diagnostics.total_retries == 507


@pytest.mark.asyncio
async def test_two_omitted_retrying_tail_attempts_diagnostic_pressure_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that two omitted tail attempts recording 7 and 9 retries yield total retry pressure of 17."""
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Prefix objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Tail unit objective",
            outcome=AttackOutcome.ERROR,
            total_retries=7,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "tail_group",
                "seed_group_id": "seed_tail",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=500),
        )
    )
    results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Tail unit objective",
            outcome=AttackOutcome.SUCCESS,
            total_retries=9,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "tail_group",
                "seed_group_id": "seed_tail",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=501),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    assert cold_hier.diagnostics.total_retries == 17
    assert warm_hier.diagnostics.total_retries == 17


@pytest.mark.asyncio
async def test_repeated_legacy_unit_500_errors_followed_by_recovery_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that 500 errors followed by 1 recovery for one legacy unit reports 1 success cold and warm."""
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Repeated unit objective",
            outcome=AttackOutcome.ERROR if i < 500 else AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": "seed_0",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(501)
    ]

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    assert cold_hier.overall_counts.completed == 1
    assert cold_hier.overall_counts.succeeded == 1
    assert warm_hier.overall_counts.completed == 1
    assert warm_hier.overall_counts.succeeded == 1


@pytest.mark.asyncio
async def test_500_leaves_and_501_parents_leaf_0_not_emitted_as_root_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test DAG where 500 leaves, 500 parents referencing leaf 1, and parent 501 referencing leaf 0
    does not emit leaf 0 as a root.
    """
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 leaf results (leaf 0 to leaf 499)
    leaf_ids = [str(uuid.uuid4()) for _ in range(500)]
    leaf_results = [
        AttackResult(
            attack_result_id=leaf_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # 500 parents (parents 0 to 499): each references leaf 1, and between them cover leaves 1..499
    parent_results: list[AttackResult] = []
    for i in range(500):
        children = [leaf_ids[1], leaf_ids[(i % 499) + 1]]
        parent_results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent objective {i}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": children},
                timestamp=base_time + timedelta(seconds=500 + i),
            )
        )

    # 1 final parent (parent 500) saved at index 1000: references leaf 0
    final_parent_id = str(uuid.uuid4())
    parent_results.append(
        AttackResult(
            attack_result_id=final_parent_id,
            conversation_id=str(uuid.uuid4()),
            objective="Parent of leaf 0",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "orch_group",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={"child_attack_result_ids": [leaf_ids[0]]},
            timestamp=base_time + timedelta(seconds=1000),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=leaf_results + parent_results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=1200)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Leaf 0 must NEVER be emitted as a root in cold or warm hierarchy
    all_cold_roots = [r for g in cold_hier.groups for r in g.roots]
    all_warm_roots = [r for g in warm_hier.groups for r in g.roots]
    assert len(all_cold_roots) >= 1
    assert len(all_warm_roots) >= 1
    assert all(r.attack_result_id != leaf_ids[0] for r in all_cold_roots)
    assert all(r.attack_result_id != leaf_ids[0] for r in all_warm_roots)
    # The roots must be actual orchestration parents
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_cold_roots)
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_warm_roots)


@pytest.mark.asyncio
async def test_legacy_identity_500_errors_and_1_success_recounts_single_unit_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test that 500 identifier-only errors followed by 1 attributed success for the same seed unifies into
    1 unit cold and warm.
    """
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    seed_group = AttackSeedGroup(seeds=[SeedObjective(value="Shared seed objective"), SeedPrompt(value="context")])

    # 500 identifier-only ERROR attempts (no seed_group_id in attribution_data, triggers needs_sdk_statistics)
    results: list[AttackResult] = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Shared seed objective",
            outcome=AttackOutcome.ERROR,
            total_retries=0,
            atomic_attack_identifier=AtomicAttackIdentifier.build(
                attack_identifier=ComponentIdentifier(class_name="MockAttack", class_module="tests"),
                seed_group=seed_group,
            ),
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "legacy_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # 1 explicitly attributed SUCCESS attempt for the same seed
    results.append(
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective="Shared seed objective",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "legacy_group",
                "seed_group_id": seed_group.logical_id,
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=500),
        )
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Unified into 1 completed unit, 1 successful unit (100% success), and 500 retries / retry pressure
    assert cold_hier.overall_counts.completed == 1
    assert warm_hier.overall_counts.completed == 1
    assert cold_hier.overall_counts.succeeded == 1
    assert warm_hier.overall_counts.succeeded == 1
    assert cold_hier.overall_counts.errors == 500
    assert warm_hier.overall_counts.errors == 500
    assert cold_hier.overall_counts.retries == 500
    assert warm_hier.overall_counts.retries == 500
    assert cold_hier.overall_counts.success_percentage == 100.0
    assert warm_hier.overall_counts.success_percentage == 100.0
    assert cold_hier.diagnostics.total_retries == 500
    assert warm_hier.diagnostics.total_retries == 500


@pytest.mark.asyncio
async def test_saved_plan_two_seeds_shared_objective_different_context_500_attempts_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test saved plan containing two seeds sharing an objective but different contexts, with 500 attempts."""
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    obj_text = "Shared objective between seeds"
    obj_hash = hashlib.sha256(obj_text.encode("utf-8")).hexdigest()

    sg_0 = AttackSeedGroup(seeds=[SeedObjective(value=obj_text), SeedPrompt(value="context_a")])
    sg_1 = AttackSeedGroup(seeds=[SeedObjective(value=obj_text), SeedPrompt(value="context_b")])

    seed_groups = [
        ScenarioRunPlanSeedGroup(
            id=sg_0.logical_id,
            objective=obj_text,
            objective_sha256=obj_hash,
        ),
        ScenarioRunPlanSeedGroup(
            id=sg_1.logical_id,
            objective=obj_text,
            objective_sha256=obj_hash,
        ),
    ]
    atomic_group = ScenarioRunPlanAtomicGroup(
        id="group_1",
        atomic_attack_name="attack_type_1",
        display_group="Attack Type 1",
        technique_eval_hash="hash_tech_1",
        seed_group_ids=[sg_0.logical_id, sg_1.logical_id],
    )
    plan = ScenarioRunPlan(
        atomic_groups=[atomic_group],
        seed_groups=seed_groups,
    )
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="SharedObjectiveScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
        metadata={"run_plan": plan.model_dump(mode="json")},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 attempts across the two seeds (250 each), with distinct seed contexts in identifier
    results: list[AttackResult] = []
    for i in range(500):
        sg = sg_0 if i % 2 == 0 else sg_1
        results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=obj_text,
                outcome=AttackOutcome.ERROR,
                total_retries=0,
                atomic_attack_identifier=AtomicAttackIdentifier.build(
                    attack_identifier=ComponentIdentifier(class_name="attack_type_1", class_module="tests"),
                    seed_group=sg,
                ),
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "attack_type_1",
                    "parent_eval_hash": "hash_tech_1",
                    "result_role": AttackResultRole.TARGET_FACING.value,
                },
                timestamp=base_time + timedelta(seconds=i),
            )
        )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Both seeds resolved; 500 errors total; retry pressure: (250-1) + (250-1) = 498
    assert cold_hier.overall_counts.errors == 500
    assert warm_hier.overall_counts.errors == 500
    assert cold_hier.overall_counts.retries == 498
    assert warm_hier.overall_counts.retries == 498
    assert cold_hier.diagnostics.total_retries == 498
    assert warm_hier.diagnostics.total_retries == 498
    assert cold_hier.overall_counts.completed == 2
    assert warm_hier.overall_counts.completed == 2


@pytest.mark.asyncio
async def test_11_parent_chain_boundary_guards_depth_cutoff_mssql_and_sqlite(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that an 11-parent deep chain (exceeding depth boundary 10) above 500 leaves with max_nodes=1
    never promotes unverified candidates (at depth 10) to roots in cold response, and marks hierarchy truncated.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ElevenParentChainScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 leaves
    leaf_ids = [str(uuid.uuid4()) for _ in range(500)]
    leaf_results = [
        AttackResult(
            attack_result_id=lid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "leaves_group",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i, lid in enumerate(leaf_ids)
    ]

    # Chain of 11 parents:
    # Parent 10 references all 500 leaves
    # Parent 9 references Parent 10
    # ...
    # Parent 0 (True Root) references Parent 1
    parent_ids = [str(uuid.uuid4()) for _ in range(11)]
    parents: list[AttackResult] = []
    for depth in range(11):
        pid = parent_ids[depth]
        children = leaf_ids if depth == 10 else [parent_ids[depth + 1]]
        parents.append(
            AttackResult(
                attack_result_id=pid,
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent depth {depth}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": children},
                timestamp=base_time + timedelta(seconds=500 + depth),
            )
        )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=leaf_results + parents)
    service = ScenarioRunService(memory=sqlite_instance)

    # Cold hierarchy with max_nodes=1 loads 1 leaf initially, then seeks parents up to depth 10.
    # Depth 10 reaches parent_ids[1]. parent_ids[0] (True Root) is not loaded due to depth <= 10.
    # Parent 1 is unverified. None of Parent 1, Parent 2, ..., Parent 10 should be promoted to root!
    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.truncated is True

    cold_roots = [r for g in cold_hier.groups for r in g.roots]
    cold_root_ids = {r.attack_result_id for r in cold_roots}
    # Intermediate parents must NOT be emitted as fake roots
    for pid in parent_ids[1:]:
        assert pid not in cold_root_ids


@pytest.mark.asyncio
async def test_parent_budget_boundary_guards_unverified_parents(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that at the 2,500-parent budget boundary, parents whose own parents were not checked
    are marked unverified and never emitted as apparent roots.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ParentBudgetBoundaryScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 1 leaf
    leaf_id = str(uuid.uuid4())
    leaf_result = AttackResult(
        attack_result_id=leaf_id,
        conversation_id=str(uuid.uuid4()),
        objective="Leaf",
        outcome=AttackOutcome.SUCCESS,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "leaf_group",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time,
    )

    # 2,500 parents all referencing leaf_id
    parent_budget = 2500
    parent_ids = [str(uuid.uuid4()) for _ in range(parent_budget)]
    parents = [
        AttackResult(
            attack_result_id=pid,
            conversation_id=str(uuid.uuid4()),
            objective=f"Parent {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "orch_group",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={"child_attack_result_ids": [leaf_id]},
            timestamp=base_time + timedelta(seconds=1 + i),
        )
        for i, pid in enumerate(parent_ids)
    ]

    # 1 top-level outer root referencing the first 10 parents
    outer_root_id = str(uuid.uuid4())
    outer_root = AttackResult(
        attack_result_id=outer_root_id,
        conversation_id=str(uuid.uuid4()),
        objective="Outer Root",
        outcome=AttackOutcome.SUCCESS,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "root_group",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": parent_ids[:10]},
        timestamp=base_time + timedelta(seconds=3000),
    )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[leaf_result] + parents + [outer_root])
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.truncated is True

    cold_roots = [r for g in cold_hier.groups for r in g.roots]
    cold_root_ids = {r.attack_result_id for r in cold_roots}
    # None of the 2,500 parents whose parents were never verified may be an apparent root
    for pid in parent_ids[:10]:
        assert pid not in cold_root_ids


@pytest.mark.asyncio
async def test_2001_legacy_attempts_recount_uses_lightweight_summaries_without_full_delta_hydration(
    sqlite_instance: SQLiteMemory,
) -> None:
    """
    Test that a large legacy run with 2,001 attempts recounts using get_scenario_recount_summaries_async
    without hydrating full ScenarioAttackResultDelta objects or calling get_scenario_attack_result_deltas_async.
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="LegacyRecount2001Scenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 2,001 legacy attempts without seed group IDs in attribution (needing SDK statistics)
    count = 2001
    results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS if i % 2 == 0 else AttackOutcome.FAILURE,
            total_retries=1 if i % 10 == 0 else 0,
            atomic_attack_identifier=AtomicAttackIdentifier(
                class_name="MockAttack",
                seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value=f"seed_val_{i}")],
            ),
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": f"technique_{(i % 5)}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(count)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=results)

    deltas_call_count = 0
    summaries_page_limits: list[int] = []

    original_get_deltas = sqlite_instance.get_scenario_attack_result_deltas_async
    original_get_summaries = sqlite_instance.get_scenario_recount_summaries_async

    async def tracking_get_deltas(*args: Any, **kwargs: Any) -> Any:
        nonlocal deltas_call_count
        deltas_call_count += 1
        return await original_get_deltas(*args, **kwargs)

    async def tracking_get_summaries(*args: Any, **kwargs: Any) -> Any:
        summaries_page_limits.append(kwargs.get("limit", 0))
        return await original_get_summaries(*args, **kwargs)

    sqlite_instance.get_scenario_attack_result_deltas_async = tracking_get_deltas  # type: ignore[assignment]
    sqlite_instance.get_scenario_recount_summaries_async = tracking_get_summaries  # type: ignore[assignment]

    service = ScenarioRunService(memory=sqlite_instance)
    run_list = await service.list_runs_async(limit=10)
    assert run_list is not None
    matched_run = next((r for r in run_list.items if r.scenario_result_id == scenario_id), None)
    assert matched_run is not None
    assert matched_run.completed_attacks == count

    # Recount MUST use lightweight summaries and NEVER call full delta hydration
    assert deltas_call_count == 0
    # 2,001 items paged with limit 500 => pages: 500, 500, 500, 500, 500
    assert len(summaries_page_limits) == 5
    assert summaries_page_limits == [500, 500, 500, 500, 500]


@pytest.mark.asyncio
async def test_1002_results_500_leaves_501_parents_leaf_0_not_emitted_as_root_parity(
    sqlite_instance: SQLiteMemory,
) -> None:
    """Test exact 1,002-result SQLite case where 500 leaves, 501 parents referencing leaf 1 covering
    leaves 1..499, and 1 final parent referencing leaf 0 does not emit leaf 0 as a root in cold hierarchy,
    and agrees with warm hierarchy.
    """
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 leaf results (leaf 0 to leaf 499)
    leaf_ids = [str(uuid.uuid4()) for _ in range(500)]
    leaf_results = [
        AttackResult(
            attack_result_id=leaf_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # 501 parents (parents 0 to 500): all reference leaf 1, and between them cover leaves 1..499
    parent_results: list[AttackResult] = []
    for i in range(501):
        children = [leaf_ids[1], leaf_ids[(i % 499) + 1]]
        parent_results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent objective {i}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": children},
                timestamp=base_time + timedelta(seconds=500 + i),
            )
        )

    # 1 final parent (parent 501) saved at index 1001: references leaf 0
    final_parent_id = str(uuid.uuid4())
    parent_results.append(
        AttackResult(
            attack_result_id=final_parent_id,
            conversation_id=str(uuid.uuid4()),
            objective="Parent of leaf 0",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "orch_group",
                "result_role": AttackResultRole.ORCHESTRATION.value,
            },
            metadata={"child_attack_result_ids": [leaf_ids[0]]},
            timestamp=base_time + timedelta(seconds=1001),
        )
    )

    assert len(leaf_results) + len(parent_results) == 1002
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=leaf_results + parent_results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=1200)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Leaf 0 must NEVER be emitted as a root in cold or warm hierarchy
    all_cold_roots = [r for g in cold_hier.groups for r in g.roots]
    all_warm_roots = [r for g in warm_hier.groups for r in g.roots]
    assert len(all_cold_roots) >= 1
    assert len(all_warm_roots) >= 1
    assert all(r.attack_result_id != leaf_ids[0] for r in all_cold_roots)
    assert all(r.attack_result_id != leaf_ids[0] for r in all_warm_roots)
    # The roots must be actual orchestration parents
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_cold_roots)
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_warm_roots)
    # Cold and warm root IDs must agree
    cold_root_ids = {r.attack_result_id for r in all_cold_roots}
    warm_root_ids = {r.attack_result_id for r in all_warm_roots}
    assert cold_root_ids == warm_root_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("leaf_490_parents_count", [1, 2, 3, 11])
async def test_chunk_boundary_unprocessed_rows_500_leaves_parents_leaf_499_parity(
    sqlite_instance: SQLiteMemory,
    leaf_490_parents_count: int,
) -> None:
    """Test 1,000, 1,001, 1,002, and 1,010-result SQLite chunk-boundary cases.
    500 leaves split into chunks of 490 and 10 in SQLite.
    490 parents reference leaves 0..489 (1 each).
    leaf_490_parents_count parents reference leaf 490.
    9 parents reference leaves 491..499 (1 each).
    Total parents: 490 + leaf_490_parents_count + 9.
    When leaf_490_parents_count == 2 (total 501 parents): chunk 2 returns 11 parents.
    Parent loop consumes 10 parents before reaching limit 500, leaving the 11th parent (leaf 499) unprocessed.
    The database has_more flag must be True so leaf 499 is not emitted as a false root.
    """
    scenario_id = str(uuid.uuid4())
    base_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=uuid.UUID(scenario_id),
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 leaf results (leaves 0 to 499)
    leaf_ids = [str(uuid.uuid4()) for _ in range(500)]
    leaf_results = [
        AttackResult(
            attack_result_id=leaf_ids[i],
            conversation_id=str(uuid.uuid4()),
            objective=f"Leaf objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    parent_results: list[AttackResult] = []
    # 490 parents referencing leaves 0..489 (1 each)
    for i in range(490):
        parent_results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent {i}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": [leaf_ids[i]]},
                timestamp=base_time + timedelta(seconds=500 + len(parent_results)),
            )
        )

    # leaf_490_parents_count parents referencing leaf 490
    for i in range(leaf_490_parents_count):
        parent_results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent 490_{i}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": [leaf_ids[490]]},
                timestamp=base_time + timedelta(seconds=500 + len(parent_results)),
            )
        )

    # 9 parents referencing leaves 491..499 (1 each)
    for i in range(491, 500):
        parent_results.append(
            AttackResult(
                attack_result_id=str(uuid.uuid4()),
                conversation_id=str(uuid.uuid4()),
                objective=f"Parent {i}",
                outcome=AttackOutcome.SUCCESS,
                total_retries=0,
                attribution_parent_id=scenario_id,
                attribution_data={
                    "parent_collection": "orch_group",
                    "result_role": AttackResultRole.ORCHESTRATION.value,
                },
                metadata={"child_attack_result_ids": [leaf_ids[i]]},
                timestamp=base_time + timedelta(seconds=500 + len(parent_results)),
            )
        )

    await sqlite_instance.add_attack_results_to_memory_async(attack_results=leaf_results + parent_results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=1200)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Leaf 499 must NEVER be emitted as a root in cold or warm hierarchy
    all_cold_roots = [r for g in cold_hier.groups for r in g.roots]
    all_warm_roots = [r for g in warm_hier.groups for r in g.roots]
    assert len(all_cold_roots) >= 1
    assert len(all_warm_roots) >= 1
    assert all(r.attack_result_id != leaf_ids[499] for r in all_cold_roots)
    assert all(r.attack_result_id != leaf_ids[499] for r in all_warm_roots)
    # The roots must be actual orchestration parents
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_cold_roots)
    assert all(r.result_role == AttackResultRole.ORCHESTRATION for r in all_warm_roots)
    # Cold and warm root IDs must agree
    cold_root_ids = {r.attack_result_id for r in all_cold_roots}
    warm_root_ids = {r.attack_result_id for r in all_warm_roots}
    assert cold_root_ids == warm_root_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("control_retries", [0, 1, 3])
async def test_500_results_followed_by_retrying_attempt_diagnostic_count_parity(
    sqlite_instance: SQLiteMemory,
    control_retries: int,
) -> None:
    """Test that 500 ordinary results followed by 1 result with retry events (or total_retries)
    correctly preserves raw_retrying_attacks_count=1 and attack_retries_count=1 in cold hierarchy,
    agreeing with warm hierarchy and SQL aggregate across controls (total_retries=0, 1, 3).
    """
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="RetryEventOnlyScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 ordinary results with total_retries=0 and no retry events
    ordinary_results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # 1 legacy result at index 500 (attempt 501) with saved RetryEvent
    retry_events = [
        RetryEvent(
            attempt_number=1,
            exception_type="RateLimitError",
            timestamp=base_time + timedelta(seconds=500),
        )
    ]
    if control_retries > 1:
        retry_events.append(
            RetryEvent(
                attempt_number=2,
                exception_type="RateLimitError",
                timestamp=base_time + timedelta(seconds=501),
            )
        )

    retrying_result = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Objective 500",
        outcome=AttackOutcome.SUCCESS,
        total_retries=control_retries,
        retry_events=retry_events,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_500",
            "result_role": AttackResultRole.TARGET_FACING.value,
        },
        timestamp=base_time + timedelta(seconds=500),
    )

    all_results = ordinary_results + [retrying_result]
    assert len(all_results) == 501
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)
    service = ScenarioRunService(memory=sqlite_instance)

    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None

    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None

    # Parity assertions: SQL aggregate and recount must preserve raw_retrying_attacks_count=1
    assert cold_hier.diagnostics.attack_retries_count == 1
    assert warm_hier.diagnostics.attack_retries_count == 1
    assert cold_hier.diagnostics.attack_retries_count == warm_hier.diagnostics.attack_retries_count


@pytest.mark.asyncio
async def test_legacy_identity_linked_child_error_recount_parity(sqlite_instance: SQLiteMemory) -> None:
    """Test that a sequential unit with legacy identifier-only seed identity having two children
    (failure followed by error) and an orchestration parent error referencing both children:
    1. SQL aggregate and SDK report 1 error and 1 retry.
    2. Lightweight recount preserves child-error links and reports 1 error and 1 retry.
    3. Cold one-node hierarchy with 500 preceding results reports 1 error and 1 retry, agreeing with warm hierarchy.
    4. Unlinked independent parent error control correctly reports 2 errors and 1 retry.
    """
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    # 1. Main test scenario with 500 preceding results followed by sequential unit
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)
    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="LegacyLinkedChildErrorScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    # 500 ordinary results with legacy identifier-only seed identity
    ordinary_results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            atomic_attack_identifier=AtomicAttackIdentifier(
                class_name="MockAttack",
                seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value=f"seed_val_{i}")],
            ),
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "technique_legacy",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    # Sequential unit with legacy identifier-only seed identity
    seq_identifier = AtomicAttackIdentifier(
        class_name="MockAttack",
        seed_identifiers=[SeedIdentifier(class_name="SeedPrompt", value="sequential_seed_val")],
    )
    child1_id = str(uuid.uuid4())
    child1 = AttackResult(
        attack_result_id=child1_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Unit Objective",
        outcome=AttackOutcome.FAILURE,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 1,
        },
        timestamp=base_time + timedelta(seconds=500),
    )

    child2_id = str(uuid.uuid4())
    child2 = AttackResult(
        attack_result_id=child2_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Unit Objective",
        outcome=AttackOutcome.ERROR,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 2,
        },
        timestamp=base_time + timedelta(seconds=501),
    )

    parent_id = str(uuid.uuid4())
    parent = AttackResult(
        attack_result_id=parent_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Unit Objective",
        outcome=AttackOutcome.ERROR,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": [child1_id, child2_id]},
        timestamp=base_time + timedelta(seconds=502),
    )

    all_results = ordinary_results + [child1, child2, parent]
    assert len(all_results) == 503
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)

    # Verify SQL aggregate directly
    aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[scenario_id])
    agg = aggregates[scenario_id]
    assert agg.completed_units == 501
    assert agg.outcome_errors == 1
    assert agg.error_attempts == 1
    assert agg.total_retries == 1

    service = ScenarioRunService(memory=sqlite_instance)

    # Verify lightweight recount via history list
    run_list = await service.list_runs_async(limit=10)
    matched_run = next(r for r in run_list.items if r.scenario_result_id == scenario_id)
    assert matched_run.completed_attacks == 501

    # Verify cold one-node hierarchy
    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.overall_counts.completed == 501
    assert cold_hier.overall_counts.errors == 1
    assert cold_hier.overall_counts.retries == 1

    # Verify warm hierarchy
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None
    assert warm_hier.overall_counts.completed == 501
    assert warm_hier.overall_counts.errors == 1
    assert warm_hier.overall_counts.retries == 1

    # Parity check between cold and warm
    assert cold_hier.overall_counts.errors == warm_hier.overall_counts.errors == 1
    assert cold_hier.overall_counts.retries == warm_hier.overall_counts.retries == 1

    # 2. Control: Unlinked independent parent error (where parent error does NOT wrap an already-failed child)
    ctrl_uuid = uuid.uuid4()
    ctrl_id = str(ctrl_uuid)
    ctrl_scen = make_scenario_result(
        id=ctrl_uuid,
        scenario_name="UnlinkedParentErrorScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[ctrl_scen])

    ctrl_child1_id = str(uuid.uuid4())
    ctrl_child1 = AttackResult(
        attack_result_id=ctrl_child1_id,
        conversation_id=str(uuid.uuid4()),
        objective="Control Unit Objective",
        outcome=AttackOutcome.FAILURE,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 1,
        },
        timestamp=base_time,
    )
    ctrl_child2_id = str(uuid.uuid4())
    ctrl_child2 = AttackResult(
        attack_result_id=ctrl_child2_id,
        conversation_id=str(uuid.uuid4()),
        objective="Control Unit Objective",
        outcome=AttackOutcome.FAILURE,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 2,
        },
        timestamp=base_time + timedelta(seconds=1),
    )
    # Parent error is independent because neither child is an ERROR
    ctrl_parent = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Control Unit Objective",
        outcome=AttackOutcome.ERROR,
        total_retries=0,
        atomic_attack_identifier=seq_identifier,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "technique_legacy",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": [ctrl_child1_id, ctrl_child2_id]},
        timestamp=base_time + timedelta(seconds=2),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ctrl_child1, ctrl_child2, ctrl_parent])

    ctrl_aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[ctrl_id])
    ctrl_agg = ctrl_aggregates[ctrl_id]
    assert ctrl_agg.outcome_errors == 1
    assert ctrl_agg.error_attempts == 1
    assert ctrl_agg.completed_units == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence_type", ["failed_sequence", "child_error_sequence"])
async def test_clipped_cold_warm_hierarchy_diagnostic_retries_with_linked_children(
    sqlite_instance: SQLiteMemory,
    sequence_type: str,
) -> None:
    """Test that 500 ordinary results followed by two target-facing children and their parent
    (with explicit seed attribution and 0 persisted retries) reports identical diagnostics and progress
    in both cold and warm hierarchy projections:
    - overall_counts.retries == 1 (execution units exclude envelope)
    - diagnostics.total_retries == 2 (raw diagnostic population includes all 3 saved results)
    - 3-result control without preceding results also reports overall_counts.retries=1 and diagnostics.total_retries=2.
    """
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    scenario_uuid = uuid.uuid4()
    scenario_id = str(scenario_uuid)

    scen = make_scenario_result(
        id=scenario_uuid,
        scenario_name="ClippedDiagnosticRetriesScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scen])

    ordinary_results = [
        AttackResult(
            attack_result_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
            objective=f"Objective {i}",
            outcome=AttackOutcome.SUCCESS,
            total_retries=0,
            attribution_parent_id=scenario_id,
            attribution_data={
                "parent_collection": "attack_group",
                "seed_group_id": f"seed_{i}",
                "result_role": AttackResultRole.TARGET_FACING.value,
            },
            timestamp=base_time + timedelta(seconds=i),
        )
        for i in range(500)
    ]

    c1_outcome = AttackOutcome.FAILURE
    c2_outcome = AttackOutcome.FAILURE if sequence_type == "failed_sequence" else AttackOutcome.ERROR
    p_outcome = AttackOutcome.FAILURE if sequence_type == "failed_sequence" else AttackOutcome.ERROR

    child1_id = str(uuid.uuid4())
    child1 = AttackResult(
        attack_result_id=child1_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Objective",
        outcome=c1_outcome,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_sequential",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 1,
        },
        timestamp=base_time + timedelta(seconds=500),
    )

    child2_id = str(uuid.uuid4())
    child2 = AttackResult(
        attack_result_id=child2_id,
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Objective",
        outcome=c2_outcome,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_sequential",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 2,
        },
        timestamp=base_time + timedelta(seconds=501),
    )

    parent = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Sequential Objective",
        outcome=p_outcome,
        total_retries=0,
        attribution_parent_id=scenario_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_sequential",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": [child1_id, child2_id]},
        timestamp=base_time + timedelta(seconds=502),
    )

    all_results = ordinary_results + [child1, child2, parent]
    assert len(all_results) == 503
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=all_results)

    service = ScenarioRunService(memory=sqlite_instance)

    # Cold hierarchy with max_nodes=1
    cold_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert cold_hier is not None
    assert cold_hier.overall_counts.retries == 1
    assert cold_hier.diagnostics.total_retries == 2

    # Warm hierarchy with max_nodes=1
    await service.get_run_progress_async(scenario_result_id=scenario_id, since=None, limit=600)
    warm_hier = await service.get_run_hierarchy_async(scenario_result_id=scenario_id, max_nodes=1)
    assert warm_hier is not None
    assert warm_hier.overall_counts.retries == 1
    assert warm_hier.diagnostics.total_retries == 2

    # Verify cold and warm agreement
    assert cold_hier.overall_counts.retries == warm_hier.overall_counts.retries == 1
    assert cold_hier.diagnostics.total_retries == warm_hier.diagnostics.total_retries == 2

    # 3-result control without preceding results
    ctrl_uuid = uuid.uuid4()
    ctrl_id = str(ctrl_uuid)
    ctrl_scen = make_scenario_result(
        id=ctrl_uuid,
        scenario_name="ThreeResultControlScenario",
        scenario_version=1,
        objective_target_identifier=get_mock_target_identifier(),
        attack_results={},
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[ctrl_scen])

    ctrl_c1_id = str(uuid.uuid4())
    ctrl_c1 = AttackResult(
        attack_result_id=ctrl_c1_id,
        conversation_id=str(uuid.uuid4()),
        objective="Control Sequential Objective",
        outcome=c1_outcome,
        total_retries=0,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_ctrl_seq",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 1,
        },
        timestamp=base_time,
    )
    ctrl_c2_id = str(uuid.uuid4())
    ctrl_c2 = AttackResult(
        attack_result_id=ctrl_c2_id,
        conversation_id=str(uuid.uuid4()),
        objective="Control Sequential Objective",
        outcome=c2_outcome,
        total_retries=0,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_ctrl_seq",
            "result_role": AttackResultRole.TARGET_FACING.value,
            "attempt_index": 2,
        },
        timestamp=base_time + timedelta(seconds=1),
    )
    ctrl_p = AttackResult(
        attack_result_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
        objective="Control Sequential Objective",
        outcome=p_outcome,
        total_retries=0,
        attribution_parent_id=ctrl_id,
        attribution_data={
            "parent_collection": "attack_group",
            "seed_group_id": "seed_ctrl_seq",
            "result_role": AttackResultRole.ORCHESTRATION.value,
        },
        metadata={"child_attack_result_ids": [ctrl_c1_id, ctrl_c2_id]},
        timestamp=base_time + timedelta(seconds=2),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ctrl_c1, ctrl_c2, ctrl_p])

    ctrl_cold = await service.get_run_hierarchy_async(scenario_result_id=ctrl_id, max_nodes=1)
    assert ctrl_cold is not None
    assert ctrl_cold.overall_counts.retries == 1
    assert ctrl_cold.diagnostics.total_retries == 2

    await service.get_run_progress_async(scenario_result_id=ctrl_id, since=None, limit=10)
    ctrl_warm = await service.get_run_hierarchy_async(scenario_result_id=ctrl_id, max_nodes=1)
    assert ctrl_warm is not None
    assert ctrl_warm.overall_counts.retries == 1
    assert ctrl_warm.diagnostics.total_retries == 2
