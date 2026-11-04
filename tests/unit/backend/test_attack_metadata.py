# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Initial Chat metadata is independent of complete saved-reference DTOs."""

import uuid
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.attacks import AttackMetadata, AttackSummary
from pyrit.backend.services.attack_service import AttackService
from pyrit.memory import SQLiteMemory, set_message_piece_sha256_async
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    MessagePiece,
)


@pytest.fixture(name="metadata_attack")
async def metadata_attack_async(sqlite_instance: SQLiteMemory) -> AttackResult:
    response = MessagePiece(
        conversation_id="main",
        role="assistant",
        original_value="Full saved response body",
        converted_value="Full saved response body",
    )
    await set_message_piece_sha256_async(response)
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[response])
    attack = AttackResult(
        conversation_id=response.conversation_id,
        objective="Saved objective",
        operator="original-owner",
        labels={"team": "red"},
        last_response=response,
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(class_name="SavedAttack", class_module="pyrit.executor.attack")
        ),
        related_conversations={
            ConversationReference(conversation_id=f"pruned-{index:03d}", conversation_type=ConversationType.PRUNED)
            for index in range(120)
        }
        | {
            ConversationReference(conversation_id=kind.value, conversation_type=kind)
            for kind in (ConversationType.PREPARATION, ConversationType.ADVERSARIAL)
        },
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    return attack


@pytest.mark.usefixtures("patch_central_database")
async def test_metadata_validates_off_page_membership_without_mapping_full_reference_arrays_async(
    sqlite_instance: SQLiteMemory, metadata_attack: AttackResult
) -> None:
    first_page = await sqlite_instance.get_attack_conversation_page_async(
        attack_result_id=metadata_attack.attack_result_id, limit=50
    )
    assert first_page is not None
    first_ids = {reference.conversation_id for reference in first_page.conversations}
    selected_id = next(
        reference.conversation_id
        for reference in metadata_attack.related_conversations
        if reference.conversation_type == ConversationType.PRUNED and reference.conversation_id not in first_ids
    )
    service = AttackService()
    with (
        patch.object(
            AttackSummary,
            "related_conversation_ids",
            new_callable=PropertyMock,
            side_effect=AssertionError("Metadata must not project complete reference IDs"),
        ),
        patch.object(service, "_summarize_attack_async", wraps=service._summarize_attack_async) as summarize,
        patch.object(sqlite_instance, "get_conversation_messages_async", new_callable=AsyncMock) as full_messages,
    ):
        metadata = await service.get_attack_metadata_async(
            attack_result_id=metadata_attack.attack_result_id, conversation_id=selected_id
        )
        assert metadata is not None
        data = metadata.model_dump(mode="json")
        assert data["related_conversation_count"] == 120
        assert data["selected_conversation_id"] == selected_id
        assert data["selected_conversation_valid"] is True
        assert data["execution_read_only"] is True
        assert data["last_response_message_piece_id"] == str(metadata_attack.last_response.id)
        assert data["operator"] == "original-owner"
        assert data["labels"] == {"team": "red"}
        assert not {"related_conversations", "related_conversation_ids", "last_response", "metadata"} & data.keys()
        mapped_result = summarize.call_args.kwargs["ar"]
        assert mapped_result.related_conversations == set()
        assert mapped_result.last_response is None
        full_messages.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("selected_id", ["unrelated", "preparation", "adversarial"])
async def test_metadata_rejects_non_active_selection_without_substituting_main_async(
    sqlite_instance: SQLiteMemory, metadata_attack: AttackResult, selected_id: str
) -> None:
    service = AttackService()
    metadata = await service.get_attack_metadata_async(
        attack_result_id=metadata_attack.attack_result_id, conversation_id=selected_id
    )
    assert metadata is not None
    assert metadata.related_conversation_count == 120
    assert metadata.selected_conversation_id == selected_id
    assert metadata.selected_conversation_valid is False
    assert metadata.conversation_id == "main"
    with pytest.raises(ValueError, match="not part of attack"):
        await service.get_conversation_messages_async(
            attack_result_id=metadata_attack.attack_result_id, conversation_id=selected_id, limit=100
        )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("module", ["pyrit.backend", "pyrit.executor.attack"])
async def test_metadata_preserves_canonical_execution_policy_without_scenario_provenance_async(
    sqlite_instance: SQLiteMemory, module: str
) -> None:
    attack = AttackResult(
        conversation_id="main",
        objective="Manual or automated",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack" if module == "pyrit.backend" else "SavedAttack",
                class_module=module,
                children={
                    "objective_target": ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")
                },
            )
        ),
        metadata={"target_registry_name": "original-target"},
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    service = AttackService()
    metadata = await service.get_attack_metadata_async(attack_result_id=attack.attack_result_id)
    legacy = await service.get_attack_async(attack_result_id=attack.attack_result_id)
    assert metadata is not None and legacy is not None
    assert metadata.execution_read_only is (module != "pyrit.backend")
    assert metadata.selected_conversation_id == "main"
    assert metadata.selected_conversation_valid is True
    assert metadata.related_conversation_count == 0
    assert metadata.target == legacy.target
    assert metadata.target is not None
    assert metadata.target.target_registry_name == "original-target"


@pytest.mark.usefixtures("patch_central_database")
async def test_metadata_missing_attack_returns_none_async(sqlite_instance: SQLiteMemory) -> None:
    assert await AttackService().get_attack_metadata_async(attack_result_id=str(uuid.uuid4())) is None


@pytest.mark.usefixtures("patch_central_database")
async def test_legacy_unpaged_attack_and_conversation_reads_keep_complete_data_async(
    sqlite_instance: SQLiteMemory, metadata_attack: AttackResult
) -> None:
    service = AttackService()
    with (
        patch.object(sqlite_instance, "get_attack_conversation_page_async", new_callable=AsyncMock) as reference_pages,
        patch.object(sqlite_instance, "get_conversation_message_page_async", new_callable=AsyncMock) as message_pages,
    ):
        legacy = await service.get_attack_async(attack_result_id=metadata_attack.attack_result_id)
        messages = await service.get_conversation_messages_async(
            attack_result_id=metadata_attack.attack_result_id, conversation_id="main"
        )
        conversations = await service.get_conversations_async(attack_result_id=metadata_attack.attack_result_id)
        reference_pages.assert_not_awaited()
        message_pages.assert_not_awaited()
    assert legacy is not None
    data = legacy.model_dump(mode="json")
    assert len(data["related_conversation_ids"]) == 122
    assert len(data["related_conversations"]) == 122
    assert data["last_response"]["converted_value"] == "Full saved response body"
    assert messages is not None and messages.pagination is None
    assert messages.messages[0].message_pieces[0].converted_value == "Full saved response body"
    assert conversations is not None and conversations.pagination is None
    assert len(conversations.conversations) == 121


@pytest.mark.parametrize("selected_id", [None, "off-page"])
def test_metadata_route_returns_only_metadata_and_forwards_selected_membership(
    compatibility_headers: dict[str, str], selected_id: str | None
) -> None:
    metadata = AttackMetadata(
        attack_result_id="attack",
        conversation_id="main",
        objective="Saved",
        outcome="undetermined",
        execution_read_only=True,
        related_conversation_count=700,
        selected_conversation_id=selected_id or "main",
        selected_conversation_valid=True,
    )
    service = MagicMock(spec=AttackService)
    service.get_attack_metadata_async = AsyncMock(return_value=metadata)
    with patch("pyrit.backend.routes.attacks.get_attack_service", return_value=service):
        response = TestClient(app, headers=compatibility_headers).get(
            "/api/attacks/attack/metadata", params={"conversation_id": selected_id} if selected_id is not None else {}
        )
    assert response.status_code == 200
    assert response.json() == metadata.model_dump(mode="json")
    assert service.get_attack_metadata_async.call_args.kwargs == {
        "attack_result_id": "attack",
        "conversation_id": selected_id,
    }
    service.get_attack_async.assert_not_called()


@pytest.mark.parametrize("failure, expected_status", [(None, 404), (ValueError("Invalid attack ID"), 400)])
def test_metadata_route_preserves_not_found_and_request_errors(
    compatibility_headers: dict[str, str], failure: ValueError | None, expected_status: int
) -> None:
    service = MagicMock(spec=AttackService)
    service.get_attack_metadata_async = AsyncMock(return_value=None, side_effect=failure)
    with patch("pyrit.backend.routes.attacks.get_attack_service", return_value=service):
        response = TestClient(app, headers=compatibility_headers).get("/api/attacks/missing/metadata")
    assert response.status_code == expected_status
