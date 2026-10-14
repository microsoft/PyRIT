# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit and parity tests for bounded Scenario result hierarchy projections."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.analytics import compute_outcome_statistics
from pyrit.backend.main import app
from pyrit.backend.services.scenario_progress_read_model import (
    ScenarioProgressReadModel,
    ScenarioProgressSnapshot,
)
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.memory import MemoryInterface, PromptMemoryEntry, SQLiteMemory
from pyrit.memory.storage.serializers import set_message_piece_sha256_async
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    MessagePiece,
    RetryEvent,
    ScenarioAtomicGroupProgress,
    ScenarioAttackResultDelta,
    ScenarioAttackTechniqueDetails,
    ScenarioConversationMessageProjection,
    ScenarioConversationProjection,
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
    effective_summary = summary or ScenarioProgressSummary(
        overall=ScenarioProgressCounts(
            completed=len([r for r in results if r.outcome != AttackOutcome.ERROR]),
            planned=1,
            succeeded=len([r for r in results if r.outcome == AttackOutcome.SUCCESS]),
            errors=len([r for r in results if r.outcome == AttackOutcome.ERROR]),
            retries=sum(r.total_retries for r in results),
        ),
        atomic_groups=atomic_groups or [],
    )
    return ScenarioProgressSnapshot(
        deltas=tuple(deltas),
        results=tuple(results),
        summary=effective_summary,
        plan=effective_plan,
    )


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
                seconds_offset=i,
            )
        )
    deltas.extend(
        _make_test_delta(
            attack_result_id=f"retry-{i}",
            outcome=AttackOutcome.SUCCESS,
            retries=[RetryEvent(exception_type="RateLimit", attempt_number=1)],
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
    memory.get_attack_result_conversation_projections_async = AsyncMock(
        return_value=[
            ScenarioConversationProjection(
                conversation_id="conv_1",
                conversation_type="main",
                message_count=1,
                messages=[
                    ScenarioConversationMessageProjection(
                        role="user",
                        content="Test prompt",
                        sequence=1,
                    )
                ],
            )
        ]
    )

    read_model = ScenarioProgressReadModel(memory=memory)
    service = ScenarioRunService(memory=memory, progress_read_model=read_model)

    node = await service.get_run_hierarchy_node_async(
        scenario_result_id="scenario-run-1",
        attack_result_id="parent-1",
    )

    assert node is not None
    assert node.attack_result_id == "parent-1"
    assert len(node.children) == 1
    assert node.children[0].attack_result_id == "child-1"
    assert len(node.conversations) == 1
    assert len(node.conversations[0].messages) == 1
    assert node.conversations[0].messages[0].content == "Test prompt"

    # Querying non-existent node returns None
    missing = await service.get_run_hierarchy_node_async(
        scenario_result_id="scenario-run-1",
        attack_result_id="non-existent",
    )
    assert missing is None


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

    # 2. Test conversation projections
    conv_projections = await sqlite_instance.get_attack_result_conversation_projections_async(
        attack_result_id=child_id,
        include_messages=True,
    )
    assert len(conv_projections) == 1
    proj = conv_projections[0]
    assert proj.conversation_id == child_conv_id
    assert proj.message_count == 2
    assert len(proj.messages) == 2
    assert proj.messages[0].role == "user"
    assert proj.messages[0].content == "Prompt to target"
    assert proj.messages[1].role == "assistant"
    assert proj.messages[1].content == "Response from target"

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
        attempt_index=1,
        seconds_offset=1,
    )
    delta2 = _make_test_delta(
        attack_result_id="unplanned-2",
        outcome=AttackOutcome.SUCCESS,
        role=AttackResultRole.TARGET_FACING,
        parent_collection="unplanned_group",
        seed_group_id="seed_u",
        attempt_index=2,
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


@pytest.mark.asyncio
async def test_sqlite_bounded_multimodal_conversation_projection(sqlite_instance: SQLiteMemory, tmp_path: Path) -> None:
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
    img_file.write_bytes(b"dummy_png_bytes")
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
    )
    p0_img = MessagePiece(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        role="user",
        original_value_data_type="image_path",
        original_value=img_path,
        converted_value=img_path,
        sequence=0,
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
    )

    for p in [p0_text, p0_img, p1, p2]:
        await set_message_piece_sha256_async(p)
        await sqlite_instance._run_database_operation_async(sqlite_instance._insert_entry, PromptMemoryEntry(entry=p))

    # Read with limit=2
    projs = await sqlite_instance.get_attack_result_conversation_projections_async(
        attack_result_id=ar_id,
        include_messages=True,
        limit_messages_per_conversation=2,
    )

    assert len(projs) == 1
    proj = projs[0]
    assert proj.conversation_id == conv_id
    assert proj.message_count == 3  # Exact count from COUNT query
    assert proj.messages_truncated is True
    assert len(proj.messages) == 2  # Bounded by SQL LIMIT

    # Verify multimodal pieces in message 0
    msg0 = proj.messages[0]
    assert msg0.role == "user"
    assert len(msg0.pieces) == 2
    assert msg0.pieces[0].data_type == "text"
    assert msg0.pieces[0].content == "Analyze blueprint image"
    assert msg0.pieces[1].data_type == "image_path"
    assert msg0.pieces[1].content == img_path
    assert msg0.pieces[1].converted_value == img_path

    # Verify raw custom role in message 1
    msg1 = proj.messages[1]
    assert msg1.role == "simulated_assistant"
    assert msg1.content == "I detect architectural blueprints."
