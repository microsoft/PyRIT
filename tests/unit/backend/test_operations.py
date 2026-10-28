# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from pyrit.backend.main import app
from pyrit.backend.models.operations import EvidenceAvailability, FindingEvidenceCreateRequest
from pyrit.backend.services.operation_service import FindingNotFoundError, OperationNotFoundError, OperationService
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_models import AttackResultEntry, Base, PromptMemoryEntry
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    Finding,
    FindingEvidence,
    Message,
    MessagePiece,
    Operation,
    Score,
)
from pyrit.models.harm_category import HarmCategory

pytestmark = pytest.mark.usefixtures("patch_central_database")

MISSING_ID = UUID(int=404)
EMPTY_USAGE = {"findings": 0, "attacks": 0, "scenario_runs": 0}


def test_operations_create_list_and_get(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    created = client.post("/api/operations", json={"name": "  Red team / α% "})
    assert created.status_code == 201
    operation = created.json()
    assert operation["name"] == "Red team / α%"
    client.post("/api/operations", json={"name": "Alpha"})
    assert [item["name"] for item in client.get("/api/operations").json()["items"]] == ["Alpha", "Red team / α%"]
    assert all(item["finding_counts"] == {} for item in client.get("/api/operations").json()["items"])
    assert client.get(f"/api/operations/{operation['id']}").json() == {**operation, "usage": EMPTY_USAGE}
    assert client.get(f"/api/operations/{MISSING_ID}").status_code == 404


def test_operation_description_is_set_on_create_and_edited(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation = client.post("/api/operations", json={"name": "Scoped", "description": "Web app only"}).json()
    assert operation["description"] == "Web app only"
    path = f"/api/operations/{operation['id']}"
    updated = client.patch(path, json={"description": "Web app and API\nNo social engineering"})
    assert updated.status_code == 200
    assert updated.json() == {**operation, "description": "Web app and API\nNo social engineering"}
    assert client.get(path).json() == {**updated.json(), "usage": EMPTY_USAGE}
    assert client.patch(path, json={"description": ""}).json()["description"] == ""
    assert client.patch(path, json={"description": "x", "created_at": operation["created_at"]}).status_code == 422
    assert client.patch(f"/api/operations/{MISSING_ID}", json={"description": "x"}).status_code == 404
    assert TestClient(app).patch(path, json={"description": "x"}).status_code != 200
    assert client.get("/api/operations").json()["items"][0]["description"] == ""


def test_operation_rename_keeps_identity_and_rejects_taken_or_invalid_names(
    compatibility_headers: dict[str, str],
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation = client.post("/api/operations", json={"name": "Scoped", "description": "Web app"}).json()
    other = client.post("/api/operations", json={"name": "Other"}).json()
    path = f"/api/operations/{operation['id']}"
    recased = client.patch(path, json={"name": " SCOPED "})
    assert recased.status_code == 200
    assert recased.json() == {**operation, "name": "SCOPED"}
    conflict = client.patch(path, json={"name": "other", "description": "Lost"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["operation"] == other
    for name in [" ", "😀" * 65]:
        assert client.patch(path, json={"name": name}).status_code == 422
    assert client.get(path).json() == {**recased.json(), "usage": EMPTY_USAGE}
    assert client.patch(path, json={"name": "Engagement / α%"}).json() == {**operation, "name": "Engagement / α%"}
    assert [item["name"] for item in client.get("/api/operations").json()["items"]] == ["Engagement / α%", "Other"]


def test_empty_operation_is_deleted(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation = client.post("/api/operations", json={"name": "Typo"}).json()
    path = f"/api/operations/{operation['id']}"
    assert TestClient(app).delete(path).status_code != 204
    deleted = client.delete(path)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.patch(path, json={"name": "Fixed"}).status_code == 404
    assert client.get("/api/operations").json()["items"] == []


async def test_finding_for_an_operation_deleted_during_the_request_is_not_found_async(
    sqlite_instance: SQLiteMemory, compatibility_headers: dict[str, str]
) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Case"))
    original_get_async = OperationService.get_async

    async def get_then_delete_async(self: OperationService, operation_id: UUID) -> Operation:
        found = await original_get_async(self, operation_id)
        await sqlite_instance.delete_operation_async(operation_id=operation_id)
        return found

    with patch.object(OperationService, "get_async", get_then_delete_async):
        response = TestClient(app, headers=compatibility_headers).post(
            f"/api/operations/{operation.id}/findings", json={"title": "Late", "severity": "low"}
        )
    assert response.status_code == 404
    assert await sqlite_instance.get_findings_async() == []


def test_duplicate_operation_conflicts_with_existing_record(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    existing = client.post("/api/operations", json={"name": "Operation A"}).json()
    conflict = client.post("/api/operations", json={"name": " operation a "})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["operation"] == existing
    assert len(client.get("/api/operations").json()["items"]) == 1


def test_findings_are_created_and_listed_under_an_operation(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Case"}).json()["id"]
    other_id = client.post("/api/operations", json={"name": "Other"}).json()["id"]
    created = client.post(
        f"/api/operations/{operation_id}/findings", json={"title": "Assessment", "severity": "informational"}
    )
    assert created.status_code == 201
    finding = created.json()
    assert finding["operation_id"] == operation_id
    assert finding["description"] == ""
    client.post(f"/api/operations/{other_id}/findings", json={"title": "Elsewhere", "severity": "low"})
    listed = client.get(f"/api/operations/{operation_id}/findings", params={"limit": 1}).json()
    assert listed == {"items": [{**finding, "evidence_count": 0}], "has_more": False, "next_offset": None}
    counts = {item["name"]: item["finding_counts"] for item in client.get("/api/operations").json()["items"]}
    assert counts == {"Case": {"informational": 1}, "Other": {"low": 1}}


def test_finding_options_exposes_canonical_harm_types(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    response = client.get("/api/operations/finding-options")
    assert response.status_code == 200
    assert response.json() == {"harm_types": [category.value for category in HarmCategory]}
    assert response.json()["harm_types"].count("Other") == 1
    assert TestClient(app).get("/api/operations/finding-options").status_code != 200


def test_finding_api_round_trips_optional_and_custom_classifications(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Classifications"}).json()["id"]
    path = f"/api/operations/{operation_id}/findings"
    body = {
        "title": "Custom",
        "severity": "other",
        "severity_other": "  Team severity  ",
        "harm_type": "Other",
        "harm_type_other": "  Team harm  ",
    }
    response = client.post(path, json=body)
    assert response.status_code == 201
    finding = response.json()
    assert {key: finding[key] for key in body} == body
    assert client.get(path).json()["items"] == [{**finding, "evidence_count": 0}]
    updated = client.put(f"{path}/{finding['id']}", json={"title": "Edited", "severity": "low", "harm_type": "Malware"})
    assert updated.status_code == 200
    assert updated.json()["harm_type"] == "Malware"
    assert updated.json()["severity_other"] is None
    assert updated.json()["harm_type_other"] is None
    assert updated.json()["id"] == finding["id"]
    assert updated.json()["created_at"] == finding["created_at"]
    for invalid in [
        {"severity": "other"},
        {"severity": "low", "severity_other": "Stray"},
        {"severity": "low", "harm_type": "Other"},
        {"severity": "low", "harm_type": "Unknown"},
    ]:
        assert client.post(path, json={"title": "Invalid", **invalid}).status_code == 422
        assert client.put(f"{path}/{finding['id']}", json={"title": "Invalid", **invalid}).status_code == 422
    cleared = client.put(f"{path}/{finding['id']}", json={"title": "No harm type", "severity": "moderate"})
    assert cleared.json()["harm_type"] is None


def test_findings_require_an_existing_operation(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    body = {"title": "Assessment", "severity": "low"}
    assert client.post(f"/api/operations/{MISSING_ID}/findings", json=body).status_code == 404
    assert client.get(f"/api/operations/{MISSING_ID}/findings").status_code == 404


def test_finding_create_retried_with_the_same_id_returns_the_saved_finding(
    compatibility_headers: dict[str, str],
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Case"}).json()["id"]
    path = f"/api/operations/{operation_id}/findings"
    body = {"id": str(uuid4()), "title": "Assessment", "severity": "low"}
    first = client.post(path, json=body)
    retried = client.post(path, json=body)
    assert first.status_code == retried.status_code == 201
    assert retried.json() == first.json()
    assert first.json()["id"] == body["id"]
    assert client.post(path, json={**body, "title": "Edited"}).status_code == 409
    other_id = client.post("/api/operations", json={"name": "Other"}).json()["id"]
    assert client.post(f"/api/operations/{other_id}/findings", json=body).status_code == 409
    assert [item["title"] for item in client.get(path).json()["items"]] == ["Assessment"]


def test_finding_create_links_attack_evidence_in_the_same_request(
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, _, attack = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    path = f"/api/operations/{operation.id}/findings"
    body = {
        "id": str(uuid4()),
        "title": "With evidence",
        "severity": "low",
        "attack_result_id": attack.attack_result_id,
    }
    created = client.post(path, json=body)
    assert created.status_code == 201
    assert client.post(path, json=body).json() == created.json()
    evidence = client.get(f"{path}/{body['id']}/evidence").json()["items"]
    assert [(item["item"]["attack_result_id"], item["conversation_id"]) for item in evidence] == [
        (attack.attack_result_id, "main")
    ]


@pytest.mark.parametrize(
    ("source", "status_code"),
    [
        ({"attack_result_id": str(MISSING_ID)}, 404),
        ({"attack_result_id": "not-a-uuid"}, 422),
        ({"conversation_id": "main"}, 422),
    ],
)
def test_finding_create_saves_nothing_when_its_evidence_cannot_be_linked(
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    source: dict[str, object],
    status_code: int,
) -> None:
    operation, finding, _ = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    path = f"/api/operations/{operation.id}/findings"
    assert client.post(path, json={"title": "Unlinked", "severity": "low", **source}).status_code == status_code
    assert [item["id"] for item in client.get(path).json()["items"]] == [str(finding.id)]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/operations", {"name": " "}),
        ("/api/operations", {"name": "😀" * 65}),
        ("findings", {"title": "", "severity": "low"}),
        ("findings", {"title": "Assessment", "severity": "urgent"}),
        ("findings", {"title": "Assessment", "severity": "low", "operation": "Case"}),
    ],
)
def test_operations_and_findings_reject_invalid_input(
    path: str, body: dict[str, str], compatibility_headers: dict[str, str]
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Case"}).json()["id"]
    url = f"/api/operations/{operation_id}/findings" if path == "findings" else path
    assert client.post(url, json=body).status_code == 422
    assert client.get(f"/api/operations/{operation_id}/findings").json()["items"] == []


def test_operation_findings_validate_pagination_and_require_compatibility(
    compatibility_headers: dict[str, str],
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Case"}).json()["id"]
    for params in [{"offset": -1}, {"limit": 0}, {"limit": 101}]:
        assert client.get(f"/api/operations/{operation_id}/findings", params=params).status_code == 422
    assert TestClient(app).get("/api/operations").status_code != 200


def test_finding_update_and_delete_preserve_identity_and_operation(
    compatibility_headers: dict[str, str],
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation = client.post("/api/operations", json={"name": "Case"}).json()
    path = f"/api/operations/{operation['id']}/findings"
    original = client.post(path, json={"title": "Original", "severity": "low"}).json()
    finding_path = f"{path}/{original['id']}"
    edited = client.put(finding_path, json={"title": "Edited", "severity": "critical", "description": "Notes"})
    assert edited.status_code == 200
    assert edited.json() == {**original, "title": "Edited", "severity": "critical", "description": "Notes"}
    assert client.get(path).json()["items"] == [{**edited.json(), "evidence_count": 0}]
    deleted = client.delete(finding_path)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(path).json()["items"] == []
    assert client.get(f"/api/operations/{operation['id']}").json() == {**operation, "usage": EMPTY_USAGE}
    assert client.delete(finding_path).status_code == 404
    assert client.put(finding_path, json={"title": "Missing", "severity": "low"}).status_code == 404


def test_finding_mutations_reject_wrong_operations_and_invalid_fields(
    compatibility_headers: dict[str, str],
) -> None:
    client = TestClient(app, headers=compatibility_headers)
    operation_id = client.post("/api/operations", json={"name": "Case"}).json()["id"]
    other_id = client.post("/api/operations", json={"name": "Other"}).json()["id"]
    path = f"/api/operations/{operation_id}/findings"
    original = client.post(path, json={"title": "Original", "severity": "low"}).json()
    body = {"title": "Edited", "severity": "critical"}
    for requested_id in [other_id, str(MISSING_ID)]:
        wrong_path = f"/api/operations/{requested_id}/findings/{original['id']}"
        assert client.put(wrong_path, json=body).status_code == 404
        assert client.delete(wrong_path).status_code == 404
    finding_path = f"{path}/{original['id']}"
    for invalid in [
        {**body, "title": " "},
        {**body, "severity": "urgent"},
        {**body, "operation_id": other_id},
        {**body, "created_at": original["created_at"]},
    ]:
        assert client.put(finding_path, json=invalid).status_code == 422
    assert TestClient(app).put(finding_path, json=body).status_code != 200
    assert TestClient(app).delete(finding_path).status_code != 204
    assert client.get(path).json()["items"] == [{**original, "evidence_count": 0}]


@pytest.fixture
async def evidence_source_async(sqlite_instance: SQLiteMemory) -> tuple[Operation, Finding, AttackResult]:
    return await _store_evidence_source_async(sqlite_instance)


async def _store_evidence_source_async(sqlite_instance: SQLiteMemory) -> tuple[Operation, Finding, AttackResult]:
    operation = await sqlite_instance.add_operation_async(Operation(name="Exact Operation"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="100%_Proof", severity="low")
    )
    attack = AttackResult(conversation_id="main", objective="Evidence", operation_id=operation.id)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    await sqlite_instance.add_message_to_memory_async(
        request=Message(message_pieces=[MessagePiece(role="user", original_value="Evidence", conversation_id="main")])
    )
    return operation, finding, attack


def test_finding_evidence_attach_returns_created_then_already_attached(
    compatibility_headers: dict[str, str], evidence_source_async: tuple[Operation, Finding, AttackResult]
) -> None:
    operation, finding, attack = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    path = f"/api/operations/{operation.id}/findings/{finding.id}/evidence"
    body = {"attack_result_id": attack.attack_result_id}
    created = client.post(path, json=body)
    repeated = client.post(path, json=body)
    assert created.status_code == 201
    assert repeated.status_code == 200
    assert created.json()["created"] is True and repeated.json()["created"] is False
    assert created.json()["item"] == repeated.json()["item"]
    page = client.get(path, params={"limit": 1}).json()
    assert page["items"][0] == {
        "item": created.json()["item"],
        "availability": "available",
        "scenario_result_id": None,
        "source_operation_id": str(operation.id),
        "source_operation_name": operation.name,
        "objective": "Evidence",
        "outcome": "undetermined",
        "score": None,
        "conversation_id": "main",
    }
    assert client.get(f"/api/operations/{operation.id}/findings").json()["items"][0]["evidence_count"] == 1
    evidence_id = created.json()["item"]["id"]
    assert client.delete(f"{path}/{evidence_id}").status_code == 204
    assert client.delete(f"{path}/{evidence_id}").status_code == 404


def test_operation_in_use_is_not_deleted_and_id_linked_activity_follows_a_rename(
    compatibility_headers: dict[str, str], evidence_source_async: tuple[Operation, Finding, AttackResult]
) -> None:
    operation, finding, attack = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    path = f"/api/operations/{operation.id}"
    usage = {"findings": 1, "attacks": 1, "scenario_runs": 0}
    assert client.get(path).json()["usage"] == usage
    refused = client.delete(path)
    assert refused.status_code == 409
    assert refused.json()["detail"]["usage"] == usage
    assert client.patch(path, json={"name": "Renamed"}).status_code == 200
    evidence_path = f"{path}/findings/{finding.id}/evidence"
    client.post(evidence_path, json={"attack_result_id": attack.attack_result_id})
    assert client.get(evidence_path).json()["items"][0]["source_operation_name"] == "Renamed"
    assert client.get(path).json()["name"] == "Renamed"


@pytest.mark.parametrize(
    ("attribution", "source_name"),
    [("unassigned", None), ("other_operation", "Other"), ("legacy_exact_name", "Exact Operation")],
)
async def test_finding_evidence_accepts_any_saved_attack_without_changing_it_async(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    attribution: str,
    source_name: str | None,
) -> None:
    operation, finding, _ = evidence_source_async
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    attack = AttackResult(
        conversation_id="source",
        objective="Evidence",
        operation_id=other.id if attribution == "other_operation" else None,
        operation=operation.name if attribution == "legacy_exact_name" else None,
    )
    # Legacy name-only rows predate Operation IDs and can no longer be written through the memory API.
    with sqlite_instance._get_session() as session:
        session.add(AttackResultEntry(entry=attack))
        session.commit()
    await sqlite_instance.add_message_to_memory_async(
        request=Message(message_pieces=[MessagePiece(role="user", original_value="x", conversation_id="source")])
    )
    client = TestClient(app, headers=compatibility_headers)
    path = f"/api/operations/{operation.id}/findings/{finding.id}/evidence"
    response = client.post(path, json={"attack_result_id": attack.attack_result_id})
    assert response.status_code == 201
    listed = client.get(path).json()["items"][0]
    assert listed["source_operation_id"] == (str(other.id) if attribution == "other_operation" else None)
    assert listed["source_operation_name"] == source_name
    stored = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
    assert (stored.operation_id, stored.operation) == (attack.operation_id, attack.operation)


@pytest.mark.parametrize("missing", ["operation", "finding", "attack", "messages"])
async def test_finding_evidence_rejects_missing_sources_async(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    missing: str,
) -> None:
    operation, finding, attack = evidence_source_async
    attack_result_id = attack.attack_result_id
    if missing == "attack":
        attack_result_id = str(MISSING_ID)
    elif missing == "messages":
        empty = AttackResult(conversation_id="empty", objective="Nothing saved")
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[empty])
        attack_result_id = empty.attack_result_id
    response = TestClient(app, headers=compatibility_headers).post(
        f"/api/operations/{MISSING_ID if missing == 'operation' else operation.id}/findings/"
        f"{MISSING_ID if missing == 'finding' else finding.id}/evidence",
        json={"attack_result_id": attack_result_id},
    )
    assert response.status_code == 404


async def test_finding_evidence_opens_the_scored_conversation_else_the_main_one_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    branch = MessagePiece(role="assistant", original_value="Branch", conversation_id="branch")
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[branch])
    score = Score(
        score_value="true",
        score_type="true_false",
        score_rationale="Scored on the branch",
        scorer_class_identifier=ComponentIdentifier(class_name="SelfAskTrueFalseScorer", class_module="pyrit.score"),
        message_piece_id=branch.id,
    )
    await sqlite_instance.add_scores_to_memory_async(scores=[score])
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id,
        update_fields={"pruned_conversation_ids": ["branch"], "automated_score_id": str(score.id)},
    )
    service = OperationService()
    saved = await service.attach_finding_evidence_async(
        operation_id=operation.id,
        finding_id=finding.id,
        request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id),
    )

    async def opened_async() -> tuple[str | None, str]:
        page = await service.list_finding_evidence_async(
            operation_id=operation.id, finding_id=finding.id, limit=20, offset=0
        )
        assert page.items[0].item == saved.item
        return page.items[0].conversation_id, page.items[0].availability

    assert await opened_async() == ("branch", "available")
    # A score left on a conversation that is no longer part of the attack opens the main conversation.
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id, update_fields={"pruned_conversation_ids": []}
    )
    assert await opened_async() == ("main", "available")
    with patch.object(sqlite_instance, "get_attack_results_async", new_callable=AsyncMock) as mocked:
        mocked.side_effect = RuntimeError("storage failed")
        with pytest.raises(RuntimeError, match="storage failed"):
            await opened_async()


async def test_finding_evidence_shows_the_attacks_current_outcome_and_score_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Scores"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Scored", severity="low")
    )
    pieces = {
        conversation_id: MessagePiece(role="assistant", original_value=conversation_id, conversation_id=conversation_id)
        for conversation_id in ["main", "branch", "unscored"]
    }
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=list(pieces.values()))

    def make_score(conversation_id: str, scorer: str, value: str) -> Score:
        return Score(
            score_value=value,
            score_type="true_false",
            score_rationale=f"{scorer} on {conversation_id}",
            scorer_class_identifier=ComponentIdentifier(class_name=scorer, class_module="pyrit.score"),
            message_piece_id=pieces[conversation_id].id,
        )

    automated = make_score("main", "SelfAskTrueFalseScorer", "true")
    human = make_score("branch", "ManualScorer", "false")
    await sqlite_instance.add_scores_to_memory_async(scores=[automated, human])
    scored = AttackResult(
        conversation_id="main",
        objective="Scored objective",
        operation_id=operation.id,
        automated_score=automated,
        human_score=human,
        outcome=AttackOutcome.FAILURE,
        related_conversations={
            ConversationReference(conversation_id="branch", conversation_type=ConversationType.PRUNED)
        },
    )
    unscored = AttackResult(conversation_id="unscored", objective="Unscored objective")
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[scored, unscored])
    service = OperationService()
    for attack in [scored, unscored, scored]:
        await service.attach_finding_evidence_async(
            operation_id=operation.id,
            finding_id=finding.id,
            request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id),
        )

    page = await service.list_finding_evidence_async(
        operation_id=operation.id, finding_id=finding.id, limit=20, offset=0
    )

    shown = {
        evidence.objective: (
            evidence.outcome,
            evidence.score.score_rationale if evidence.score else None,
            evidence.score.is_objective_score if evidence.score else None,
            evidence.conversation_id,
        )
        for evidence in page.items
    }
    # The human score is the attack's current score, so the branch it was given in is the one to open.
    assert shown == {
        "Scored objective": (AttackOutcome.FAILURE, "ManualScorer on branch", True, "branch"),
        "Unscored objective": (AttackOutcome.UNDETERMINED, None, None, "unscored"),
    }


async def test_finding_search_is_literal_case_insensitive_async(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Search"))
    for title in ["100%_Proof", "100XXProof", "other"]:
        await sqlite_instance.add_finding_async(finding=Finding(operation_id=operation.id, title=title, severity="low"))
    page = await OperationService().list_findings_async(
        operation_id=operation.id, limit=20, offset=0, title_query="%_pROOF"
    )
    assert [f.title for f in page.items] == ["100%_Proof"]
    assert page.items[0].evidence_count == 0


async def test_concurrent_attach_preserves_one_association_async(
    tmp_path: Path,
) -> None:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "evidence.db", skip_schema_migration=True)
    try:
        await asyncio.to_thread(Base.metadata.create_all, memory.engine)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            operation, finding, attack = await _store_evidence_source_async(memory)
            request = FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id)
            results = await asyncio.gather(
                *[
                    OperationService().attach_finding_evidence_async(
                        operation_id=operation.id, finding_id=finding.id, request=request
                    )
                    for _ in range(2)
                ]
            )
            assert sorted(r.created for r in results) == [False, True]
            assert results[0].item == results[1].item
            assert await memory.get_finding_evidence_counts_async(finding_ids=[finding.id]) == {finding.id: 1}
    finally:
        await memory.dispose_engine_async()


async def test_unrelated_integrity_failure_propagates_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    service = OperationService()
    request = FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id)
    await service.attach_finding_evidence_async(operation_id=operation.id, finding_id=finding.id, request=request)
    failure = IntegrityError("insert", {}, RuntimeError("UNIQUE constraint failed: FindingEvidenceEntries.id"))
    with patch.object(sqlite_instance, "add_finding_evidence_async", side_effect=failure):
        with pytest.raises(IntegrityError) as raised:
            await service.attach_finding_evidence_async(
                operation_id=operation.id, finding_id=finding.id, request=request
            )
        assert raised.value is failure


async def test_attach_after_concurrent_finding_delete_is_not_found_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    add_evidence = sqlite_instance.add_finding_evidence_async

    async def delete_then_add_async(*, evidence: FindingEvidence) -> FindingEvidence:
        await sqlite_instance.delete_finding_async(operation_id=operation.id, finding_id=finding.id)
        return await add_evidence(evidence=evidence)

    with patch.object(sqlite_instance, "add_finding_evidence_async", side_effect=delete_then_add_async):
        with pytest.raises(FindingNotFoundError):
            await OperationService().attach_finding_evidence_async(
                operation_id=operation.id,
                finding_id=finding.id,
                request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id),
            )
    assert await sqlite_instance.get_finding_evidence_counts_async(finding_ids=[finding.id]) == {}


@pytest.mark.parametrize("table", [AttackResultEntry, PromptMemoryEntry])
async def test_evidence_retains_missing_source_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    table: type[Base],
) -> None:
    operation, finding, attack = evidence_source_async
    service = OperationService()
    saved = await service.attach_finding_evidence_async(
        operation_id=operation.id,
        finding_id=finding.id,
        request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id),
    )
    async with await sqlite_instance.get_session_async() as session, session.begin():
        await session.execute(delete(table))
    page = await service.list_finding_evidence_async(
        operation_id=operation.id, finding_id=finding.id, limit=1, offset=0
    )
    assert page.items[0].item == saved.item
    assert page.items[0].availability == "unavailable"


async def test_evidence_detach_is_scoped_and_does_not_mutate_source_async(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    saved = client.post(
        f"/api/operations/{operation.id}/findings/{finding.id}/evidence",
        json={"attack_result_id": attack.attack_result_id},
    ).json()["item"]
    other = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Other", severity="low")
    )
    assert (
        client.delete(f"/api/operations/{operation.id}/findings/{other.id}/evidence/{saved['id']}").status_code == 404
    )
    assert client.delete(f"/api/operations/{uuid4()}/findings/{finding.id}/evidence/{saved['id']}").status_code == 404
    assert (
        client.delete(f"/api/operations/{operation.id}/findings/{finding.id}/evidence/{saved['id']}").status_code == 204
    )
    assert (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0] == attack
    assert (await sqlite_instance.get_conversation_stats_async(conversation_ids=["main"]))["main"].message_count == 1


@pytest.mark.parametrize(
    "body",
    [
        {"attack_result_id": "invalid"},
        {},
        {"attack_result_id": str(MISSING_ID), "conversation_id": "main"},
        {"attack_result_id": str(MISSING_ID), "operation": "forged"},
    ],
)
def test_evidence_input_is_strict(
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    body: dict[str, str],
) -> None:
    operation, finding, _ = evidence_source_async
    client = TestClient(app, headers=compatibility_headers)
    assert client.post(f"/api/operations/{operation.id}/findings/{finding.id}/evidence", json=body).status_code == 422


async def test_evidence_page_batches_sources_and_counts_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=finding.id, attack_result_id=UUID(attack.attack_result_id))
    )
    await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=finding.id, attack_result_id=uuid4())
    )
    with (
        patch.object(
            sqlite_instance, "get_attack_results_async", wraps=sqlite_instance.get_attack_results_async
        ) as owners,
        patch.object(
            sqlite_instance, "get_conversation_stats_async", wraps=sqlite_instance.get_conversation_stats_async
        ) as stats,
        patch.object(
            sqlite_instance,
            "get_finding_evidence_counts_async",
            wraps=sqlite_instance.get_finding_evidence_counts_async,
        ) as counts,
    ):
        service = OperationService()
        page = await service.list_finding_evidence_async(
            operation_id=operation.id, finding_id=finding.id, limit=1, offset=0
        )
        assert page.has_more and page.next_offset == 1
        owners.assert_called_once()
        stats.assert_called_once()
        await service.list_findings_async(operation_id=operation.id, limit=20, offset=0)
        counts.assert_called_once()
        assert counts.call_args.kwargs["finding_ids"] == [finding.id]


@pytest.fixture
async def selected_findings_async(sqlite_instance: SQLiteMemory) -> tuple[Operation, list[Finding]]:
    operation = await sqlite_instance.add_operation_async(Operation(name="Selected"))
    findings = []
    for severity in ["low", "critical", "moderate"]:
        finding = await sqlite_instance.add_finding_async(
            finding=Finding(operation_id=operation.id, title=severity.title(), severity=severity)
        )
        for index in range(2):
            conversation_id = f"{severity}-{index}"
            attack = AttackResult(conversation_id=conversation_id, objective=conversation_id, operation_id=operation.id)
            await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
            await sqlite_instance.add_message_to_memory_async(
                request=Message(
                    message_pieces=[
                        MessagePiece(role="user", original_value=conversation_id, conversation_id=conversation_id)
                    ]
                )
            )
            await sqlite_instance.add_finding_evidence_async(
                evidence=FindingEvidence(finding_id=finding.id, attack_result_id=UUID(attack.attack_result_id))
            )
        findings.append(finding)
    return operation, findings


async def test_findings_with_evidence_read_in_constant_queries_async(
    sqlite_instance: SQLiteMemory,
    selected_findings_async: tuple[Operation, list[Finding]],
) -> None:
    operation, findings = selected_findings_async
    service = OperationService()
    with (
        patch.object(
            sqlite_instance, "get_attack_results_async", wraps=sqlite_instance.get_attack_results_async
        ) as owners,
        patch.object(
            sqlite_instance, "get_conversation_stats_async", wraps=sqlite_instance.get_conversation_stats_async
        ) as stats,
        patch.object(
            sqlite_instance, "get_finding_evidence_async", wraps=sqlite_instance.get_finding_evidence_async
        ) as evidence_pages,
        patch.object(sqlite_instance, "get_findings_async", wraps=sqlite_instance.get_findings_async) as finding_pages,
    ):
        selected = await service.get_findings_with_evidence_async(
            operation_id=operation.id, finding_ids=[finding.id for finding in findings]
        )
    owners.assert_called_once()
    stats.assert_called_once()
    evidence_pages.assert_not_called()
    finding_pages.assert_not_called()
    assert [finding.title for finding, _ in selected] == ["Critical", "Moderate", "Low"]
    for finding, items in selected:
        page = await service.list_finding_evidence_async(
            operation_id=operation.id, finding_id=finding.id, limit=10, offset=0
        )
        assert len(items) == 2
        assert items == page.items


async def _store_shared_conversation_attacks_async(
    sqlite_instance: SQLiteMemory,
) -> tuple[Operation, list[AttackResult]]:
    operation = await sqlite_instance.add_operation_async(Operation(name="Shared conversation"))
    attacks = [
        AttackResult(
            conversation_id="shared",
            objective=objective,
            operation_id=operation.id,
            timestamp=datetime(2026, 1, day, tzinfo=UTC),
        )
        for day, objective in [(1, "First run"), (2, "Second run")]
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=attacks)
    await sqlite_instance.add_message_to_memory_async(
        request=Message(message_pieces=[MessagePiece(role="user", original_value="Shared", conversation_id="shared")])
    )
    return operation, attacks


async def test_findings_with_evidence_keep_an_older_attack_sharing_a_conversation_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    operation, attacks = await _store_shared_conversation_attacks_async(sqlite_instance)
    findings = []
    for attack in attacks:
        finding = await sqlite_instance.add_finding_async(
            finding=Finding(operation_id=operation.id, title=attack.objective, severity="low")
        )
        await sqlite_instance.add_finding_evidence_async(
            evidence=FindingEvidence(finding_id=finding.id, attack_result_id=UUID(attack.attack_result_id))
        )
        findings.append(finding)
    service = OperationService()

    selected = await service.get_findings_with_evidence_async(
        operation_id=operation.id, finding_ids=[finding.id for finding in findings]
    )

    assert {finding.title: [(item.objective, item.availability) for item in items] for finding, items in selected} == {
        "First run": [("First run", EvidenceAvailability.AVAILABLE)],
        "Second run": [("Second run", EvidenceAvailability.AVAILABLE)],
    }
    for finding, items in selected:
        page = await service.list_finding_evidence_async(
            operation_id=operation.id, finding_id=finding.id, limit=10, offset=0
        )
        assert items == page.items


async def test_finding_evidence_page_keeps_attacks_sharing_a_conversation_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    operation, attacks = await _store_shared_conversation_attacks_async(sqlite_instance)
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Shared", severity="low")
    )
    for attack in attacks:
        await sqlite_instance.add_finding_evidence_async(
            evidence=FindingEvidence(finding_id=finding.id, attack_result_id=UUID(attack.attack_result_id))
        )

    page = await OperationService().list_finding_evidence_async(
        operation_id=operation.id, finding_id=finding.id, limit=10, offset=0
    )

    assert len(page.items) == 2
    assert {(item.objective, item.availability) for item in page.items} == {
        ("First run", EvidenceAvailability.AVAILABLE),
        ("Second run", EvidenceAvailability.AVAILABLE),
    }


async def test_findings_with_evidence_resolve_in_batches_async(
    sqlite_instance: SQLiteMemory,
    selected_findings_async: tuple[Operation, list[Finding]],
) -> None:
    operation, findings = selected_findings_async
    service = OperationService()
    finding_ids = [finding.id for finding in findings]
    unbatched = await service.get_findings_with_evidence_async(operation_id=operation.id, finding_ids=finding_ids)
    with (
        patch("pyrit.backend.services.operation_service._EVIDENCE_BATCH_SIZE", 2),
        patch.object(
            sqlite_instance, "get_attack_results_async", wraps=sqlite_instance.get_attack_results_async
        ) as owners,
    ):
        batched = await service.get_findings_with_evidence_async(operation_id=operation.id, finding_ids=finding_ids)
    assert batched == unbatched
    assert owners.call_count == 3


async def test_findings_with_evidence_reject_missing_and_foreign_findings_async(
    sqlite_instance: SQLiteMemory,
    selected_findings_async: tuple[Operation, list[Finding]],
) -> None:
    operation, findings = selected_findings_async
    other = await sqlite_instance.add_operation_async(Operation(name="Other"))
    foreign = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=other.id, title="Foreign", severity="low")
    )
    unknown = uuid4()
    service = OperationService()
    for finding_ids, named in [([findings[0].id, foreign.id, unknown], foreign.id), ([unknown, foreign.id], unknown)]:
        with pytest.raises(FindingNotFoundError, match=f"Finding '{named}' not found in operation '{operation.id}'"):
            await service.get_findings_with_evidence_async(operation_id=operation.id, finding_ids=finding_ids)
    with pytest.raises(OperationNotFoundError):
        await service.get_findings_with_evidence_async(operation_id=MISSING_ID, finding_ids=[findings[0].id])
