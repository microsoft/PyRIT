# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from pyrit.backend.main import app
from pyrit.backend.models.operations import FindingEvidenceCreateRequest
from pyrit.backend.services.operation_service import FindingNotFoundError, OperationService
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_models import AttackResultEntry, Base, PromptMemoryEntry
from pyrit.models import (
    AttackResult,
    ConversationReference,
    ConversationType,
    Finding,
    FindingEvidence,
    Message,
    MessagePiece,
    Operation,
)
from pyrit.models.harm_category import HarmCategory

pytestmark = pytest.mark.usefixtures("patch_central_database")

MISSING_ID = UUID(int=404)


def test_operations_create_list_and_get(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    created = client.post("/api/operations", json={"name": "  Red team / α% "})
    assert created.status_code == 201
    operation = created.json()
    assert operation["name"] == "Red team / α%"
    client.post("/api/operations", json={"name": "Alpha"})
    assert [item["name"] for item in client.get("/api/operations").json()["items"]] == ["Alpha", "Red team / α%"]
    assert all(item["finding_counts"] == {} for item in client.get("/api/operations").json()["items"])
    assert client.get(f"/api/operations/{operation['id']}").json() == operation
    assert client.get(f"/api/operations/{MISSING_ID}").status_code == 404


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
    assert client.get(f"/api/operations/{operation['id']}").json() == operation
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
        Finding(operation_id=operation.id, title="100%_Proof", severity="low")
    )
    attack = AttackResult(conversation_id="main", objective="Evidence", operation=operation.name)
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
    body = {"attack_result_id": attack.attack_result_id, "conversation_id": "main"}
    created = client.post(path, json=body)
    repeated = client.post(path, json=body)
    assert created.status_code == 201
    assert repeated.status_code == 200
    assert created.json()["created"] is True and repeated.json()["created"] is False
    assert created.json()["item"] == repeated.json()["item"]
    page = client.get(path, params={"limit": 1}).json()
    assert page["items"][0] == {"item": created.json()["item"], "availability": "available", "scenario_result_id": None}
    assert client.get(f"/api/operations/{operation.id}/findings").json()["items"][0]["evidence_count"] == 1
    evidence_id = created.json()["item"]["id"]
    assert client.delete(f"{path}/{evidence_id}").status_code == 204
    assert client.delete(f"{path}/{evidence_id}").status_code == 404


@pytest.mark.parametrize("attribution", [None, "Other", "exact Operation", "Exact Operation "])
async def test_finding_evidence_rejects_attribution_async(
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    attribution: str | None,
) -> None:
    operation, finding, attack = evidence_source_async
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id, update_fields={"operation": attribution}
    )
    response = TestClient(app, headers=compatibility_headers).post(
        f"/api/operations/{operation.id}/findings/{finding.id}/evidence",
        json={"attack_result_id": attack.attack_result_id, "conversation_id": "main"},
    )
    assert response.status_code == 409
    assert await sqlite_instance.get_finding_evidence_counts_async(finding_ids=[finding.id]) == {}


@pytest.mark.parametrize("missing", ["operation", "finding", "attack", "conversation"])
def test_finding_evidence_rejects_missing_sources(
    compatibility_headers: dict[str, str],
    evidence_source_async: tuple[Operation, Finding, AttackResult],
    missing: str,
) -> None:
    operation, finding, attack = evidence_source_async
    response = TestClient(app, headers=compatibility_headers).post(
        f"/api/operations/{MISSING_ID if missing == 'operation' else operation.id}/findings/"
        f"{MISSING_ID if missing == 'finding' else finding.id}/evidence",
        json={
            "attack_result_id": str(MISSING_ID) if missing == "attack" else attack.attack_result_id,
            "conversation_id": "other" if missing == "conversation" else "main",
        },
    )
    assert response.status_code == 404


async def test_finding_evidence_related_membership_and_unavailability_async(
    sqlite_instance: SQLiteMemory,
    evidence_source_async: tuple[Operation, Finding, AttackResult],
) -> None:
    operation, finding, attack = evidence_source_async
    attack.related_conversations = {
        ConversationReference(conversation_id="related", conversation_type=ConversationType.PRUNED),
    }
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id, update_fields={"pruned_conversation_ids": ["related"]}
    )
    await sqlite_instance.add_message_to_memory_async(
        request=Message(message_pieces=[MessagePiece(role="user", original_value="Related", conversation_id="related")])
    )
    service = OperationService()
    saved = await service.attach_finding_evidence_async(
        operation_id=operation.id,
        finding_id=finding.id,
        request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id, conversation_id="related"),
    )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id, update_fields={"pruned_conversation_ids": []}
    )
    page = await service.list_finding_evidence_async(
        operation_id=operation.id, finding_id=finding.id, limit=20, offset=0
    )
    assert page.items[0].item == saved.item
    assert page.items[0].availability == "unavailable"
    with patch.object(sqlite_instance, "get_attack_results_async", new_callable=AsyncMock) as mocked:
        mocked.side_effect = RuntimeError("storage failed")
        with pytest.raises(RuntimeError, match="storage failed"):
            await service.list_finding_evidence_async(
                operation_id=operation.id, finding_id=finding.id, limit=20, offset=0
            )


async def test_finding_search_is_literal_case_insensitive_async(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Search"))
    for title in ["100%_Proof", "100XXProof", "other"]:
        await sqlite_instance.add_finding_async(Finding(operation_id=operation.id, title=title, severity="low"))
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
            request = FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id, conversation_id="main")
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
    request = FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id, conversation_id="main")
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
                request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id, conversation_id="main"),
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
        request=FindingEvidenceCreateRequest(attack_result_id=attack.attack_result_id, conversation_id="main"),
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
        json={
            "attack_result_id": attack.attack_result_id,
            "conversation_id": "main",
        },
    ).json()["item"]
    other = await sqlite_instance.add_finding_async(Finding(operation_id=operation.id, title="Other", severity="low"))
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
        {"attack_result_id": "invalid", "conversation_id": "main"},
        {"attack_result_id": str(MISSING_ID), "conversation_id": ""},
        {"attack_result_id": str(MISSING_ID), "conversation_id": "x" * 129},
        {"attack_result_id": str(MISSING_ID), "conversation_id": "main", "operation": "forged"},
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
        evidence=FindingEvidence(
            finding_id=finding.id,
            attack_result_id=UUID(attack.attack_result_id),
            conversation_id="main",
        )
    )
    await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(
            finding_id=finding.id,
            attack_result_id=uuid4(),
            conversation_id="missing",
        )
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
