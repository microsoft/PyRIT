# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Paged hierarchy navigation, independent counts, and on-demand canonical conversations."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects import sqlite

from pyrit.backend.main import app
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.message_send_service import MessageSendService
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.memory import SQLiteMemory, set_message_piece_sha256_async
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    ConversationReference,
    ConversationType,
    MessagePiece,
    ScenarioHierarchyNode,
)
from unit.mocks import get_mock_target_identifier, make_scenario_result


async def _store_run_async(memory: SQLiteMemory) -> str:
    identifier = uuid.uuid4()
    await memory.add_scenario_results_to_memory_async(
        scenario_results=[
            make_scenario_result(
                id=identifier,
                scenario_name="Hierarchy pagination",
                scenario_version=1,
                objective_target_identifier=get_mock_target_identifier(),
                attack_results={},
            )
        ]
    )
    return str(identifier)


def _result(*, run_id: str, index: int, child_ids: list[Any] | None = None) -> AttackResult:
    return AttackResult(
        attack_result_id=str(uuid.UUID(int=index + 100)),
        conversation_id=str(uuid.UUID(int=index + 10000)),
        objective=f"Objective {index}",
        outcome=AttackOutcome.SUCCESS,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        attribution_parent_id=run_id,
        attribution_data={
            "parent_collection": "atomic",
            "seed_group_id": f"seed-{index}",
            "result_role": (
                AttackResultRole.ORCHESTRATION.value if child_ids is not None else AttackResultRole.TARGET_FACING.value
            ),
        },
        metadata={"child_attack_result_ids": child_ids} if child_ids is not None else {},
    )


def _flatten(nodes: list[ScenarioHierarchyNode]) -> list[ScenarioHierarchyNode]:
    return [descendant for node in nodes for descendant in [node, *_flatten(node.children)]]


async def test_root_continuation_reserves_every_returned_root_and_counts_emitted_dag_nodes_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    children = [_result(run_id=run_id, index=index) for index in range(6, 9)]
    child_ids = [child.attack_result_id for child in children]
    roots = [_result(run_id=run_id, index=index) for index in range(4)]
    roots += [
        _result(run_id=run_id, index=4, child_ids=child_ids),
        _result(run_id=run_id, index=5, child_ids=child_ids[:2]),
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[*roots, *children])
    service = ScenarioRunService(memory=sqlite_instance)
    with patch.object(service._progress_read_model, "get_snapshot_async", new_callable=AsyncMock) as progress:
        first = await service.get_run_hierarchy_async(scenario_result_id=run_id, max_nodes=4)
        assert first is not None
        assert first.has_more
        assert first.next_cursor is not None
        second = await service.get_run_hierarchy_async(scenario_result_id=run_id, max_nodes=4, cursor=first.next_cursor)
        assert second is not None
        progress.assert_not_awaited()
    first_roots = [node for group in first.groups for node in group.roots]
    second_roots = [node for group in second.groups for node in group.roots]
    assert [node.attack_result_id for node in first_roots + second_roots] == [
        result.attack_result_id for result in roots
    ]
    assert len(_flatten(second_roots)) == 4
    assert len(second_roots[0].children) == 2
    assert second_roots[1].attack_result_id == roots[-1].attack_result_id
    assert second_roots[1].children == []
    assert not second.has_more
    assert second.next_cursor is None
    assert second.overall_counts == first.overall_counts
    assert second.truncated


@pytest.mark.parametrize("form", ["uppercase", "hex", "braced", "urn"])
async def test_native_roots_and_node_mapping_agree_on_saved_uuid_forms_async(
    sqlite_instance: SQLiteMemory, form: str
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child = _result(run_id=run_id, index=1001)
    identifier = child.attack_result_id
    reference = {
        "uppercase": identifier.upper(),
        "hex": uuid.UUID(identifier).hex.upper(),
        "braced": "{" + identifier + "}",
        "urn": "URN:UUID:" + identifier,
    }[form]
    parent = _result(run_id=run_id, index=1000, child_ids=[reference])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent, child])
    hierarchy = await ScenarioRunService(memory=sqlite_instance).get_run_hierarchy_async(
        scenario_result_id=run_id, max_nodes=5
    )
    assert hierarchy is not None
    roots = [node for group in hierarchy.groups for node in group.roots]
    assert [node.attack_result_id for node in roots] == [parent.attack_result_id]
    assert roots[0].children[0].attack_result_id == child.attack_result_id
    assert roots[0].children[0].child_index == 0


async def test_native_roots_ignore_non_array_metadata_and_cross_run_parents_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    other_run = await _store_run_async(sqlite_instance)
    results = [_result(run_id=run_id, index=index) for index in range(5)]
    results[3].metadata = {"child_attack_result_ids": results[0].attack_result_id}
    results[4].metadata = {"child_attack_result_ids": {"child": results[1].attack_result_id}}
    foreign_parent = _result(run_id=other_run, index=20, child_ids=[results[2].attack_result_id])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[*results, foreign_parent])
    roots, has_more = await sqlite_instance.get_scenario_root_deltas_async(scenario_result_id=run_id, limit=10)
    assert {root.attack_result_id for root in roots} == {result.attack_result_id for result in results}
    assert not has_more


@pytest.mark.parametrize("reference", [None, True, 7, {}, [], "not-a-uuid", "00000000-0000-0000-0000-000000000064 "])
async def test_malformed_references_cannot_hide_saved_roots_async(
    sqlite_instance: SQLiteMemory, reference: Any
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child = _result(run_id=run_id, index=0)
    parent = _result(run_id=run_id, index=1, child_ids=[reference])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[child, parent])
    roots, has_more = await sqlite_instance.get_scenario_root_deltas_async(scenario_result_id=run_id, limit=2)
    assert {root.attack_result_id for root in roots} == {child.attack_result_id, parent.attack_result_id}
    assert not has_more


def test_native_root_query_expands_saved_references_once_not_once_per_candidate(sqlite_instance: SQLiteMemory) -> None:
    statement = sqlite_instance._build_scenario_root_deltas_statement(scenario_result_id=str(uuid.uuid4()), limit=2)
    compiled = statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    with sqlite_instance._get_session() as session:
        plan = session.execute(text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    details = " ".join(str(row[-1]).upper() for row in plan)
    assert "LIST SUBQUERY" in details
    assert "CORRELATED" not in details


async def test_empty_run_has_an_empty_page_and_zero_exact_counts_async(sqlite_instance: SQLiteMemory) -> None:
    run_id = await _store_run_async(sqlite_instance)
    page = await ScenarioRunService(memory=sqlite_instance).get_run_hierarchy_async(scenario_result_id=run_id)
    assert page is not None
    assert page.groups == []
    assert page.overall_counts.completed == 0
    assert page.diagnostics.total_retries == 0
    assert not page.has_more
    assert page.next_cursor is None
    assert not page.truncated


async def test_child_pages_preserve_malformed_positions_and_continue_past_empty_pages_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child = _result(run_id=run_id, index=1)
    missing_id = str(uuid.uuid4())
    parent = _result(
        run_id=run_id,
        index=0,
        child_ids=[child.attack_result_id.upper(), 7, None, {}, missing_id, child.attack_result_id],
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent, child])
    service = ScenarioRunService(memory=sqlite_instance)
    first = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id, attack_result_id=parent.attack_result_id, max_immediate_children=2
    )
    assert first is not None
    assert first.children_count == 6
    assert first.children[0].child_index == 0
    assert first.unresolved_child_attack_result_ids == ["<invalid child reference at position 1>"]
    assert first.next_children_cursor is not None
    second = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id,
        attack_result_id=parent.attack_result_id,
        max_immediate_children=2,
        children_cursor=first.next_children_cursor,
    )
    assert second is not None
    assert second.child_offset == 2
    assert second.children == []
    assert len(second.unresolved_child_attack_result_ids) == 2
    assert second.next_children_cursor is not None
    third = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id,
        attack_result_id=parent.attack_result_id,
        max_immediate_children=2,
        children_cursor=second.next_children_cursor,
    )
    assert third is not None
    assert third.child_offset == 4
    assert third.children == []
    assert third.unresolved_child_attack_result_ids == [missing_id, child.attack_result_id]
    assert third.next_children_cursor is None


async def test_children_beyond_the_old_500_reference_cap_are_accessible_async(sqlite_instance: SQLiteMemory) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child_ids = [str(uuid.UUID(int=index + 100)) for index in range(503)]
    parent = _result(run_id=run_id, index=1000, child_ids=child_ids)
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[parent, _result(run_id=run_id, index=0), _result(run_id=run_id, index=502)]
    )
    service = ScenarioRunService(memory=sqlite_instance)
    first = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id, attack_result_id=parent.attack_result_id, max_immediate_children=500
    )
    assert first is not None
    assert len(first.children) == 1
    assert len(first.child_attack_result_ids) == 500
    assert first.next_children_cursor is not None
    last = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id,
        attack_result_id=parent.attack_result_id,
        max_immediate_children=500,
        children_cursor=first.next_children_cursor,
    )
    assert last is not None
    assert last.child_offset == 500
    assert last.children[0].child_index == 502
    assert last.children[0].attack_result_id == child_ids[502]
    assert last.next_children_cursor is None


async def test_rootless_cycles_are_diagnostic_samples_not_invented_roots_async(sqlite_instance: SQLiteMemory) -> None:
    run_id = await _store_run_async(sqlite_instance)
    first = _result(run_id=run_id, index=0, child_ids=[str(uuid.UUID(int=101))])
    second = _result(run_id=run_id, index=1, child_ids=[first.attack_result_id])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[first, second])
    service = ScenarioRunService(memory=sqlite_instance)
    hierarchy = await service.get_run_hierarchy_async(scenario_result_id=run_id, max_nodes=1)
    assert hierarchy is not None
    assert not any(group.roots for group in hierarchy.groups)
    assert hierarchy.truncated
    assert not hierarchy.has_more
    assert hierarchy.diagnostics.rootless_results_count == 2
    assert hierarchy.diagnostics.rootless_result_ids == [first.attack_result_id]
    assert hierarchy.diagnostics.rootless_results_truncated
    node = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id, attack_result_id=hierarchy.diagnostics.rootless_result_ids[0]
    )
    assert node is not None
    assert node.children[0].attack_result_id == second.attack_result_id


@pytest.mark.parametrize("has_resolvable_tail", [False, True])
async def test_preview_does_not_probe_every_missing_reference_to_fill_the_node_budget_async(
    sqlite_instance: SQLiteMemory, has_resolvable_tail: bool
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    tail = _result(run_id=run_id, index=1)
    parent = _result(
        run_id=run_id, index=0, child_ids=[*[str(uuid.uuid4()) for _ in range(499)], tail.attack_result_id]
    )
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[parent, tail] if has_resolvable_tail else [parent]
    )
    service = ScenarioRunService(memory=sqlite_instance)
    with patch.object(
        sqlite_instance,
        "get_scenario_attack_result_deltas_by_ids_async",
        wraps=sqlite_instance.get_scenario_attack_result_deltas_by_ids_async,
    ) as descendants:
        hierarchy = await service.get_run_hierarchy_async(scenario_result_id=run_id, max_nodes=2)
    assert hierarchy is not None
    descendants.assert_awaited_once()
    assert len(descendants.call_args.kwargs["attack_result_ids"]) == 1
    root = hierarchy.groups[0].roots[0]
    assert len(root.unresolved_child_attack_result_ids) == 500
    assert root.children_truncated is has_resolvable_tail
    if has_resolvable_tail:
        node = await service.get_run_hierarchy_node_async(
            scenario_result_id=run_id, attack_result_id=parent.attack_result_id, max_immediate_children=500
        )
        assert node is not None
        assert node.children[0].attack_result_id == tail.attack_result_id
        assert node.children[0].child_index == 499


async def test_child_cursors_reject_other_nodes_and_reordered_references_async(sqlite_instance: SQLiteMemory) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    parent = _result(run_id=run_id, index=0, child_ids=child_ids)
    other = _result(run_id=run_id, index=1, child_ids=child_ids)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent, other])
    service = ScenarioRunService(memory=sqlite_instance)
    first = await service.get_run_hierarchy_node_async(
        scenario_result_id=run_id, attack_result_id=parent.attack_result_id, max_immediate_children=1
    )
    assert first is not None
    assert first.next_children_cursor is not None
    with pytest.raises(ValueError, match="cursor"):
        await service.get_run_hierarchy_node_async(
            scenario_result_id=run_id,
            attack_result_id=other.attack_result_id,
            children_cursor=first.next_children_cursor,
        )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=parent.attack_result_id,
        update_fields={"attack_metadata": {"child_attack_result_ids": child_ids[::-1]}, "timestamp": parent.timestamp},
    )
    with pytest.raises(ValueError, match="cursor"):
        await service.get_run_hierarchy_node_async(
            scenario_result_id=run_id,
            attack_result_id=parent.attack_result_id,
            children_cursor=first.next_children_cursor,
        )


async def test_api_journey_loads_only_the_selected_saved_conversation_async(
    sqlite_instance: SQLiteMemory, compatibility_headers: dict[str, str]
) -> None:
    run_id = await _store_run_async(sqlite_instance)
    child = _result(run_id=run_id, index=1)
    related = {
        kind: str(uuid.uuid4())
        for kind in (ConversationType.PRUNED, ConversationType.ADVERSARIAL, ConversationType.PREPARATION)
    }
    child.related_conversations = {
        ConversationReference(conversation_id=identifier, conversation_type=kind)
        for kind, identifier in related.items()
    }
    parent = _result(run_id=run_id, index=0, child_ids=[child.attack_result_id])
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[parent, child])
    pieces = [
        MessagePiece(
            conversation_id=child.conversation_id,
            original_value=f"Saved piece {index}",
            role="user",
            sequence=0,
        )
        for index in range(3)
    ]
    context_pieces = [
        MessagePiece(conversation_id=identifier, original_value=f"Saved {kind.value}", role="user", sequence=0)
        for kind, identifier in related.items()
    ]
    for piece in pieces + context_pieces:
        await set_message_piece_sha256_async(piece)
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces + context_pieces)
    scenario_service = ScenarioRunService(memory=sqlite_instance)
    sends = MagicMock(spec=MessageSendService)
    attack_service = AttackService(memory=sqlite_instance, message_send_service=sends)
    client = TestClient(app, headers=compatibility_headers)
    with (
        patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=scenario_service),
        patch("pyrit.backend.routes.attacks.get_attack_service", return_value=attack_service),
    ):
        roots = client.get(f"/api/scenarios/runs/{run_id}/hierarchy", params={"max_nodes": 1})
        assert roots.status_code == 200
        assert roots.json()["groups"][0]["roots"][0]["attack_result_id"] == parent.attack_result_id
        node = client.get(f"/api/scenarios/runs/{run_id}/hierarchy/nodes/{child.attack_result_id}")
        assert node.status_code == 200
        assert "messages" not in node.json()["conversations"][0]
        assert node.json()["conversations_count"] == 2
        assert node.json()["conversations_truncated"] is False
        conversations_url = f"/api/attacks/{child.attack_result_id}/conversations"
        unpaged = client.get(conversations_url)
        assert unpaged.status_code == 200
        assert {conversation["conversation_id"] for conversation in unpaged.json()["conversations"]} == {
            child.conversation_id,
            related[ConversationType.PRUNED],
        }
        references = client.get(conversations_url, params={"limit": 1})
        assert references.status_code == 200
        reference_body = references.json()
        assert reference_body["conversations"][0]["message_count"] == 1
        assert reference_body["total_count"] == 2
        remaining = client.get(
            conversations_url, params={"limit": 1, "cursor": reference_body["pagination"]["next_cursor"]}
        )
        assert remaining.status_code == 200
        assert remaining.json()["pagination"]["has_more"] is False
        assert node.json()["conversations"] == reference_body["conversations"] + remaining.json()["conversations"]
        messages_url = f"/api/attacks/{child.attack_result_id}/messages"
        for limit in (None, 2):
            for kind, identifier in related.items():
                params: dict[str, str | int] = {"conversation_id": identifier}
                if limit is not None:
                    params["limit"] = limit
                response = client.get(messages_url, params=params)
                if kind == ConversationType.PRUNED:
                    assert response.status_code == 200
                    assert response.json()["messages"][0]["message_pieces"][0]["original_value"] == "Saved pruned"
                else:
                    assert response.status_code == 400
        first = client.get(messages_url, params={"conversation_id": child.conversation_id, "limit": 2})
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["partial_message_sequences"] == [0]
        second = client.get(
            messages_url,
            params={
                "conversation_id": child.conversation_id,
                "limit": 2,
                "cursor": first_body["pagination"]["next_cursor"],
            },
        )
        assert second.status_code == 200
        all_pieces = first_body["messages"][0]["message_pieces"] + second.json()["messages"][0]["message_pieces"]
        assert len(all_pieces) == 3
        assert {piece["id"] for piece in all_pieces} == {str(piece.id) for piece in pieces}
        bad_cursor = client.get(f"/api/scenarios/runs/{run_id}/hierarchy", params={"cursor": "malformed"})
        assert bad_cursor.status_code == 400
    assert sends.mock_calls == []
