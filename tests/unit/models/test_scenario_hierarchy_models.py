# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pyrit.models import (
    AttackOutcome,
    AttackResultRole,
    RetryEvent,
    ScenarioAttackTechniqueDetails,
    ScenarioConversationMessagePieceProjection,
    ScenarioConversationMessageProjection,
    ScenarioConversationProjection,
    ScenarioHierarchyDiagnostics,
    ScenarioHierarchyGroup,
    ScenarioHierarchyNode,
    ScenarioHierarchyProjection,
    ScenarioOrchestrationParentSummary,
    ScenarioProgressCounts,
    ScenarioProgressScore,
    ScenarioRunState,
)
from pyrit.models.catalog.scenario import AttackErrorSummary, AttackRetrySummary


def test_scenario_orchestration_parent_summary_defaults() -> None:
    now = datetime.now(UTC)
    summary = ScenarioOrchestrationParentSummary(
        attack_result_id="attack-123",
        outcome=AttackOutcome.SUCCESS,
        timestamp=now,
    )

    assert summary.attack_result_id == "attack-123"
    assert summary.result_role == AttackResultRole.UNKNOWN
    assert summary.outcome == AttackOutcome.SUCCESS
    assert summary.attempt_index is None
    assert summary.child_attack_result_ids == []
    assert summary.completion_policy is None
    assert summary.retries == []
    assert summary.retries_truncated is False


def test_scenario_orchestration_parent_summary_validation() -> None:
    now = datetime.now(UTC)
    # attempt_index must be >= 1 when provided
    with pytest.raises(ValidationError):
        ScenarioOrchestrationParentSummary(
            attack_result_id="attack-123",
            outcome=AttackOutcome.SUCCESS,
            timestamp=now,
            attempt_index=0,
        )

    summary = ScenarioOrchestrationParentSummary(
        attack_result_id="attack-123",
        result_role=AttackResultRole.ORCHESTRATION,
        outcome=AttackOutcome.ERROR,
        attempt_index=2,
        completion_policy="FIRST_SUCCESS",
        child_attack_result_ids=["child-1", "child-2"],
        error_type="TargetError",
        error_message="Connection timed out",
        timestamp=now,
        total_retries=3,
        retries=[RetryEvent(exception_type="RateLimit", attempt_number=1)],
        retries_truncated=True,
    )
    assert summary.attempt_index == 2
    assert summary.completion_policy == "FIRST_SUCCESS"
    assert summary.child_attack_result_ids == ["child-1", "child-2"]
    assert summary.retries_truncated is True


def test_scenario_conversation_projections() -> None:
    now = datetime.now(UTC)
    msg = ScenarioConversationMessageProjection(
        role="assistant",
        content="Response text",
        sequence=1,
        timestamp=now,
        scores=[
            ScenarioProgressScore(
                scorer_name="ObjectiveScorer",
                score_type="float_scale",
                status="complete",
                score_value="0.9",
            )
        ],
    )
    assert msg.role == "assistant"
    assert msg.content == "Response text"
    assert len(msg.scores) == 1

    conv = ScenarioConversationProjection(
        conversation_id="conv-1",
        conversation_type="main",
        description="Main objective target conversation",
        message_count=1,
        messages=[msg],
        messages_truncated=False,
    )
    assert conv.conversation_id == "conv-1"
    assert conv.conversation_type == "main"
    assert len(conv.messages) == 1


def test_scenario_hierarchy_node_recursive_tree() -> None:
    now = datetime.now(UTC)
    child1 = ScenarioHierarchyNode(
        attack_result_id="child-1",
        result_role=AttackResultRole.TARGET_FACING,
        attempt_index=1,
        atomic_attack_name="Crescendo",
        atomic_group_id="group-1",
        seed_group_id="seed-1",
        objective="test objective",
        outcome=AttackOutcome.FAILURE,
        timestamp=now,
    )
    child2 = ScenarioHierarchyNode(
        attack_result_id="child-2",
        result_role=AttackResultRole.TARGET_FACING,
        attempt_index=2,
        atomic_attack_name="PromptSending",
        atomic_group_id="group-1",
        seed_group_id="seed-1",
        objective="test objective",
        outcome=AttackOutcome.SUCCESS,
        timestamp=now,
    )
    parent = ScenarioHierarchyNode(
        attack_result_id="parent-1",
        result_role=AttackResultRole.ORCHESTRATION,
        atomic_attack_name="SequentialAttack",
        atomic_group_id="group-1",
        seed_group_id="seed-1",
        objective="test objective",
        outcome=AttackOutcome.SUCCESS,
        timestamp=now,
        child_attack_result_ids=["child-1", "child-2", "unresolved-3"],
        children=[child1, child2],
        unresolved_child_attack_result_ids=["unresolved-3"],
        orchestration=ScenarioOrchestrationParentSummary(
            attack_result_id="parent-1",
            result_role=AttackResultRole.ORCHESTRATION,
            outcome=AttackOutcome.SUCCESS,
            child_attack_result_ids=["child-1", "child-2", "unresolved-3"],
            completion_policy="FIRST_SUCCESS",
            timestamp=now,
        ),
    )

    assert len(parent.children) == 2
    assert parent.children[0].attack_result_id == "child-1"
    assert parent.children[0].attempt_index == 1
    assert parent.children[1].attack_result_id == "child-2"
    assert parent.children[1].attempt_index == 2
    assert parent.unresolved_child_attack_result_ids == ["unresolved-3"]
    assert parent.orchestration is not None
    assert parent.orchestration.completion_policy == "FIRST_SUCCESS"

    # Round trip serialization
    dumped = parent.model_dump(mode="json")
    reloaded = ScenarioHierarchyNode.model_validate(dumped)
    assert reloaded.attack_result_id == "parent-1"
    assert len(reloaded.children) == 2
    assert reloaded.unresolved_child_attack_result_ids == ["unresolved-3"]


def test_scenario_hierarchy_diagnostics_bounding() -> None:
    diagnostics = ScenarioHierarchyDiagnostics(
        failed_attacks=[
            AttackErrorSummary(
                atomic_attack_name="attack",
                objective="obj",
                error_type="Error",
                error_message="msg",
                total_retries=1,
            )
        ],
        failed_attacks_count=105,
        failed_attacks_truncated=True,
        attack_retries=[
            AttackRetrySummary(
                attack_result_id="att-1",
                atomic_attack_name="attack",
                retries=[],
            )
        ],
        attack_retries_count=120,
        attack_retries_truncated=True,
        total_retries=250,
    )

    assert len(diagnostics.failed_attacks) == 1
    assert diagnostics.failed_attacks_count == 105
    assert diagnostics.failed_attacks_truncated is True
    assert len(diagnostics.attack_retries) == 1
    assert diagnostics.attack_retries_count == 120
    assert diagnostics.attack_retries_truncated is True
    assert diagnostics.total_retries == 250


def test_scenario_hierarchy_projection_envelope() -> None:
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
        scenario_result_id="scen-123",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        groups=[group],
        overall_counts=ScenarioProgressCounts(
            completed=1, planned=1, succeeded=1, success_percentage=100, errors=0, retries=0
        ),
        diagnostics=ScenarioHierarchyDiagnostics(),
    )

    assert projection.scenario_result_id == "scen-123"
    assert len(projection.groups) == 1
    assert projection.groups[0].roots[0].attack_result_id == "node-1"
    assert projection.overall_counts.completed == 1
    assert projection.truncated is False


def test_scenario_conversation_multimodal_pieces() -> None:
    now = datetime.now(UTC)
    pieces = [
        ScenarioConversationMessagePieceProjection(
            data_type="text",
            content="Look at this image:",
            converted_value="Look at this image:",
        ),
        ScenarioConversationMessagePieceProjection(
            data_type="image_path",
            content="/path/to/img.png",
            converted_value="/path/to/img.png",
        ),
    ]
    msg = ScenarioConversationMessageProjection(
        role="simulated_assistant",
        content="Look at this image:",
        sequence=1,
        timestamp=now,
        pieces=pieces,
    )
    assert msg.role == "simulated_assistant"
    assert len(msg.pieces) == 2
    assert msg.pieces[0].data_type == "text"
    assert msg.pieces[1].data_type == "image_path"


def test_attack_retry_summary_bounded_fields() -> None:
    summary = AttackRetrySummary(
        attack_result_id="att-1",
        atomic_attack_name="attack",
        total_retries=250,
        retries=[RetryEvent(exception_type="RateLimit", attempt_number=i) for i in range(100)],
        retries_truncated=True,
    )
    assert summary.total_retries == 250
    assert len(summary.retries) == 100
    assert summary.retries_truncated is True


def test_scenario_hierarchy_projection_truncated_flag() -> None:
    projection = ScenarioHierarchyProjection(
        scenario_result_id="scen-123",
        scenario_name="TestScenario",
        scenario_version=1,
        status=ScenarioRunState.COMPLETED,
        groups=[],
        overall_counts=ScenarioProgressCounts(
            completed=0, planned=0, succeeded=0, success_percentage=0, errors=0, retries=0
        ),
        diagnostics=ScenarioHierarchyDiagnostics(),
        truncated=True,
    )
    assert projection.truncated is True
