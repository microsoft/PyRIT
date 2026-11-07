# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services.operation_service import OperationService
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackResult,
    DecompositionPath,
    Finding,
    FindingEvidence,
    Message,
    MessagePiece,
    Operation,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")

MISSING_ID = UUID(int=404)


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    return TestClient(app, headers=compatibility_headers)


@pytest.fixture
def findings_path(client: TestClient) -> str:
    return f"/api/operations/{client.post('/api/operations', json={'name': 'Decomposed'}).json()['id']}/findings"


def _create(client: TestClient, path: str, title: str, **fields: object) -> Any:
    response = client.post(path, json={"title": title, "severity": "moderate", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def _editable(finding: dict[str, Any], **changes: object) -> dict[str, Any]:
    keys = ("title", "description", "severity", "severity_other", "harm_type", "harm_type_other")
    return {key: finding[key] for key in (*keys, "parent_finding_id", "demonstrated")} | changes


def test_sub_findings_are_created_listed_and_counted(client: TestClient, findings_path: str) -> None:
    parent = _create(client, findings_path, "Data theft", severity="critical")
    recon = _create(client, findings_path, "Recon", parent_finding_id=parent["id"])
    creds = _create(client, findings_path, "Creds", parent_finding_id=parent["id"], demonstrated=False)
    _create(client, findings_path, "Nested", parent_finding_id=recon["id"])

    top_level = client.get(findings_path, params={"top_level": True}).json()["items"]
    children = client.get(findings_path, params={"parent_finding_id": parent["id"]}).json()["items"]
    everything = client.get(findings_path).json()["items"]

    assert parent["parent_finding_id"] is None and parent["demonstrated"] is True and parent["paths"] == []
    assert (creds["parent_finding_id"], creds["demonstrated"]) == (parent["id"], False)
    assert [(item["title"], item["child_count"]) for item in top_level] == [("Data theft", 2)]
    assert {(item["title"], item["child_count"]) for item in children} == {("Recon", 1), ("Creds", 0)}
    assert len(everything) == 4
    counts = {item["name"]: item["finding_counts"] for item in client.get("/api/operations").json()["items"]}
    assert counts == {"Decomposed": {"critical": 1, "moderate": 3}}


def test_list_filters_reject_a_missing_parent_and_conflicting_filters(client: TestClient, findings_path: str) -> None:
    parent = _create(client, findings_path, "Parent")

    assert client.get(findings_path, params={"parent_finding_id": str(MISSING_ID)}).status_code == 404
    both = client.get(findings_path, params={"parent_finding_id": parent["id"], "top_level": True})
    assert both.status_code == 422


def test_sub_finding_parent_must_be_in_the_operation(client: TestClient, findings_path: str) -> None:
    other_path = f"/api/operations/{client.post('/api/operations', json={'name': 'Other'}).json()['id']}/findings"
    foreign = _create(client, other_path, "Foreign")

    for parent_id in (foreign["id"], str(MISSING_ID)):
        response = client.post(
            findings_path, json={"title": "Orphan", "severity": "low", "parent_finding_id": parent_id}
        )
        assert response.status_code == 404
        assert "Parent finding" in response.json()["detail"]
    assert client.get(findings_path).json()["items"] == []


def test_paths_are_replaced_with_derived_completeness(client: TestClient, findings_path: str) -> None:
    parent = _create(client, findings_path, "Data theft")
    recon, creds, pivot = [
        _create(client, findings_path, title, parent_finding_id=parent["id"], demonstrated=title != "Creds")
        for title in ("Recon", "Creds", "Pivot")
    ]
    paths_path = f"{findings_path}/{parent['id']}/paths"

    replaced = client.put(
        paths_path,
        json={
            "paths": [
                {"name": "Credential theft", "steps": [recon["id"], creds["id"]]},
                {"name": "Lateral movement", "steps": [recon["id"], pivot["id"]]},
            ]
        },
    )

    assert replaced.status_code == 200
    body = replaced.json()
    assert [(path["name"], path["steps"], path["complete"]) for path in body["paths"]] == [
        ("Credential theft", [recon["id"], creds["id"]], False),
        ("Lateral movement", [recon["id"], pivot["id"]], True),
    ]
    assert (body["child_count"], body["evidence_count"]) == (3, 0)
    listed = client.get(findings_path, params={"top_level": True}).json()["items"][0]
    assert listed["paths"] == body["paths"]

    demonstrated = client.put(f"{findings_path}/{creds['id']}", json=_editable(creds, demonstrated=True))
    assert demonstrated.status_code == 200
    relisted = client.get(findings_path, params={"top_level": True}).json()["items"][0]
    assert [path["complete"] for path in relisted["paths"]] == [True, True]
    # Editing the parent's fields leaves its paths alone.
    renamed = client.put(f"{findings_path}/{parent['id']}", json=_editable(parent, title="Renamed"))
    assert [path["name"] for path in renamed.json()["paths"]] == ["Credential theft", "Lateral movement"]


def test_paths_reject_steps_that_are_not_direct_sub_findings(client: TestClient, findings_path: str) -> None:
    parent = _create(client, findings_path, "Parent")
    child = _create(client, findings_path, "Child", parent_finding_id=parent["id"])
    grandchild = _create(client, findings_path, "Grandchild", parent_finding_id=child["id"])
    paths_path = f"{findings_path}/{parent['id']}/paths"
    path_id = str(uuid4())

    not_direct = client.put(paths_path, json={"paths": [{"steps": [child["id"], grandchild["id"]]}]})
    repeated_step = client.put(paths_path, json={"paths": [{"steps": [child["id"], child["id"]]}]})
    repeated_path = client.put(
        paths_path, json={"paths": [{"id": path_id, "steps": [child["id"]]}, {"id": path_id, "steps": [child["id"]]}]}
    )
    empty = client.put(paths_path, json={"paths": [{"steps": []}]})
    missing = client.put(f"{findings_path}/{MISSING_ID}/paths", json={"paths": []})

    assert not_direct.status_code == 409
    assert "direct sub-findings" in not_direct.json()["detail"]
    assert (repeated_step.status_code, repeated_path.status_code, empty.status_code) == (422, 422, 422)
    assert missing.status_code == 404
    assert client.get(findings_path, params={"top_level": True}).json()["items"][0]["paths"] == []


def test_moving_a_finding_rejects_cycles_and_path_steps(client: TestClient, findings_path: str) -> None:
    first = _create(client, findings_path, "First")
    second = _create(client, findings_path, "Second")
    child = _create(client, findings_path, "Child", parent_finding_id=first["id"])
    grandchild = _create(client, findings_path, "Grandchild", parent_finding_id=child["id"])

    under_descendant = client.put(
        f"{findings_path}/{child['id']}", json=_editable(child, parent_finding_id=grandchild["id"])
    )
    missing_parent = client.put(
        f"{findings_path}/{child['id']}", json=_editable(child, parent_finding_id=str(MISSING_ID))
    )
    assert under_descendant.status_code == 409
    assert missing_parent.status_code == 404

    client.put(f"{findings_path}/{first['id']}/paths", json={"paths": [{"name": "Only way", "steps": [child["id"]]}]})
    step_moved = client.put(f"{findings_path}/{child['id']}", json=_editable(child, parent_finding_id=second["id"]))
    assert step_moved.status_code == 409
    assert "Only way" in step_moved.json()["detail"]

    client.put(f"{findings_path}/{first['id']}/paths", json={"paths": []})
    moved = client.put(f"{findings_path}/{child['id']}", json=_editable(child, parent_finding_id=second["id"]))
    assert moved.status_code == 200
    assert moved.json()["parent_finding_id"] == second["id"]
    nested = client.get(findings_path, params={"parent_finding_id": child["id"]}).json()["items"]
    assert [item["id"] for item in nested] == [grandchild["id"]]


async def test_delete_cascades_to_sub_findings_and_their_evidence(
    sqlite_instance: SQLiteMemory, client: TestClient, findings_path: str
) -> None:
    parent = _create(client, findings_path, "Parent")
    kept = _create(client, findings_path, "Kept")
    child = _create(client, findings_path, "Child", parent_finding_id=parent["id"])
    grandchild = _create(client, findings_path, "Grandchild", parent_finding_id=child["id"])
    attack = AttackResult(conversation_id="evidence", objective="Shared objective")
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    await sqlite_instance.add_message_to_memory_async(
        request=Message(message_pieces=[MessagePiece(role="user", original_value="x", conversation_id="evidence")])
    )
    for finding in (child, grandchild, kept):
        linked = client.post(
            f"{findings_path}/{finding['id']}/evidence", json={"attack_result_id": attack.attack_result_id}
        )
        assert linked.status_code == 201

    deleted = client.delete(f"{findings_path}/{parent['id']}")

    assert deleted.status_code == 204
    assert [item["id"] for item in client.get(findings_path).json()["items"]] == [kept["id"]]
    for finding_id in (child["id"], grandchild["id"]):
        assert await sqlite_instance.get_finding_evidence_async(finding_id=UUID(finding_id), limit=10, offset=0) == []
    kept_evidence = await sqlite_instance.get_finding_evidence_async(finding_id=UUID(kept["id"]), limit=10, offset=0)
    assert [item.attack_result_id for item in kept_evidence] == [UUID(attack.attack_result_id)]
    assert await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])


def test_deleting_a_path_step_is_rejected_until_the_paths_change(client: TestClient, findings_path: str) -> None:
    parent = _create(client, findings_path, "Parent")
    step = _create(client, findings_path, "Step", parent_finding_id=parent["id"])
    client.put(f"{findings_path}/{parent['id']}/paths", json={"paths": [{"steps": [step["id"]]}]})

    rejected = client.delete(f"{findings_path}/{step['id']}")

    assert rejected.status_code == 409
    assert "Remove it from those paths first" in rejected.json()["detail"]
    assert len(client.get(findings_path).json()["items"]) == 2
    client.put(f"{findings_path}/{parent['id']}/paths", json={"paths": []})
    assert client.delete(f"{findings_path}/{step['id']}").status_code == 204


def test_create_retry_still_matches_a_finding_that_gained_paths(client: TestClient, findings_path: str) -> None:
    finding_id = str(uuid4())
    body = {"id": finding_id, "title": "Parent", "severity": "low"}
    saved = client.post(findings_path, json=body).json()
    child = _create(client, findings_path, "Child", parent_finding_id=finding_id)
    client.put(f"{findings_path}/{finding_id}/paths", json={"paths": [{"steps": [child["id"]]}]})

    retried = client.post(findings_path, json=body)

    assert retried.status_code == 201
    assert retried.json()["id"] == saved["id"]
    assert len(retried.json()["paths"]) == 1


async def test_list_item_completeness_uses_real_findings(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Mapped"))
    parent = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Parent", severity="low")
    )
    step = await sqlite_instance.add_finding_async(
        finding=Finding(
            operation_id=operation.id, title="Step", severity="low", parent_finding_id=parent.id, demonstrated=False
        )
    )
    path = DecompositionPath(name="Only", steps=(step.id,))
    await sqlite_instance.replace_finding_paths_async(operation_id=operation.id, finding_id=parent.id, paths=(path,))
    await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=parent.id, attack_result_id=uuid4())
    )

    page = await OperationService().list_findings_async(
        operation_id=operation.id, limit=10, offset=0, top_level_only=True
    )

    (item,) = page.items
    assert (item.id, item.child_count, item.evidence_count) == (parent.id, 1, 1)
    assert [(entry.id, entry.steps, entry.complete) for entry in item.paths] == [(path.id, (step.id,), False)]
