# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    ConversationMessageRequest,
    ConversationPieceRequest,
    CreateAttackRequest,
    SaveConversationRequest,
    UpdateAttackRequest,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import TargetListResponse
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.target_service import TargetService
from pyrit.memory import SQLiteMemory
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.models import Score
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.target.target_capabilities import TargetCapabilities
from pyrit.prompt_target import OpenAIResponseTarget
from unit.mocks import MockPromptTarget, get_mock_target_identifier


def draft(*, value: str = "Original prompt", operator: str = "owner") -> SaveConversationRequest:
    return SaveConversationRequest(
        save_id=uuid.uuid4(),
        destination="new_attack",
        objective="Original objective",
        operator=operator,
        messages=[
            ConversationMessageRequest(
                role="user",
                pieces=[ConversationPieceRequest(data_type="text", original_value=value)],
            )
        ],
    )


@pytest.fixture
def editor_target(patch_central_database: None) -> Iterator[MockPromptTarget]:
    target = MockPromptTarget()
    service = MagicMock(spec=TargetService)
    service.get_target_object.return_value = target

    async def instance_async(*, target_registry_name: str) -> TargetInstance:
        return target_object_to_instance(target_registry_name, target)

    async def list_async(*, cursor: str | None = None) -> TargetListResponse:
        return TargetListResponse(
            items=[target_object_to_instance("selected", target)],
            pagination=PaginationInfo(limit=50, has_more=False),
        )

    service.get_target_async.side_effect = instance_async
    service.list_targets_async.side_effect = list_async
    with patch("pyrit.backend.services.attack_service.get_target_service", return_value=service):
        yield target


@pytest.fixture
def response_target(patch_central_database: None) -> Iterator[OpenAIResponseTarget]:
    target = OpenAIResponseTarget(
        endpoint="https://example.invalid/v1/responses", model_name="unit-test", api_key="unit-test"
    )
    service = MagicMock(spec=TargetService)
    service.get_target_object.return_value = target
    service.get_target_async.return_value = target_object_to_instance("responses", target)
    with patch("pyrit.backend.services.attack_service.get_target_service", return_value=service):
        yield target


@pytest.mark.usefixtures("patch_central_database")
class TestConversationEditor:
    async def test_provider_preflight_rejects_before_media_write_async(
        self, *, response_target: OpenAIResponseTarget, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        request.target_registry_name = "responses"
        request.messages = [
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[
                    ConversationPieceRequest(
                        data_type="binary_path", original_value=base64.b64encode(b"media").decode()
                    ),
                    ConversationPieceRequest(data_type="tool_call", original_value='{"call_id":"web-1"}'),
                ],
            )
        ]
        service = AttackService()
        with (
            patch.object(service, "_persist_base64_pieces_async", new_callable=AsyncMock) as persist,
            patch.object(response_target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send,
        ):
            with pytest.raises(ValueError, match="type"):
                await service.save_conversation_async(request=request)
            persist.assert_not_awaited()
            send.assert_not_awaited()
        assert sqlite_instance.get_attack_results() == []
        assert sqlite_instance._get_conversation(conversation_id=str(request.save_id)) is None

    async def test_provider_preflight_failure_leaves_target_unbound_async(
        self, *, response_target: OpenAIResponseTarget, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        request.messages = [
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[
                    ConversationPieceRequest(data_type="tool_call", original_value='{"call_id":"web-1"}'),
                ],
            )
        ]
        service = AttackService()
        saved = await service.save_conversation_async(request=request)
        attack = sqlite_instance.get_attack_results(attack_result_ids=[saved.attack.attack_result_id])[0]
        with pytest.raises(ValueError, match="type"):
            await service._bind_manual_target_async(attack=attack, registry_name="responses")
        current = sqlite_instance.get_attack_results(attack_result_ids=[saved.attack.attack_result_id])[0]
        assert current.metadata["target_unbound"] is True
        assert current.atomic_attack_identifier == attack.atomic_attack_identifier
        assert (
            sqlite_instance._get_conversation(conversation_id=saved.messages.conversation_id).target_identifier is None
        )

    async def test_provider_tool_extensions_round_trip_without_execution_async(
        self, *, response_target: OpenAIResponseTarget, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        request.target_registry_name = "responses"
        payload = '{"type":"web_search_call","call_id":"web-1","query":"query","extension":{"keep":true}}'
        request.messages = [
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[
                    ConversationPieceRequest(data_type="tool_call", original_value=payload),
                ],
            )
        ]
        with patch.object(response_target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
            saved = await AttackService().save_conversation_async(request=request)
            send.assert_not_awaited()
        assert saved.messages.messages[0].message_pieces[0].converted_value == payload
        history = sqlite_instance.get_conversation_messages(conversation_id=saved.messages.conversation_id)
        response_target.validate_tool_history(history)

    @pytest.mark.parametrize(
        "capabilities",
        [
            TargetCapabilities(),
            TargetCapabilities(supports_multi_turn=True),
            TargetCapabilities(supports_editable_history=True),
        ],
    )
    async def test_save_requires_editable_history_async(
        self, *, sqlite_instance: SQLiteMemory, editor_target: MockPromptTarget, capabilities: TargetCapabilities
    ) -> None:
        editor_target.apply_capabilities(capabilities=capabilities)
        request = draft()
        request.target_registry_name = "selected"
        with pytest.raises(ValueError, match="editable history"):
            await AttackService().save_conversation_async(request=request)
        assert sqlite_instance.get_attack_results() == []
        assert editor_target.prompt_sent == []

    @pytest.mark.parametrize("data_type", ["function_call", "function_call_output", "tool_call"])
    async def test_save_requires_each_tool_input_async(
        self, *, editor_target: MockPromptTarget, data_type: str, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        request.target_registry_name = "selected"
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="text",
                original_value="Original",
                converted_value="{}",
                converted_value_data_type=data_type,
            )
        ]
        with pytest.raises(ValueError, match=data_type):
            await AttackService().save_conversation_async(request=request)
        assert sqlite_instance.get_attack_results() == []

    async def test_same_attack_checks_target_without_registry_name_async(
        self, *, editor_target: MockPromptTarget, sqlite_instance: SQLiteMemory
    ) -> None:
        service = AttackService()
        request = draft()
        request.target_registry_name = "selected"
        first = await service.save_conversation_async(request=request)
        editor_target.apply_capabilities(capabilities=TargetCapabilities())
        request = SaveConversationRequest(
            save_id=uuid.uuid4(),
            destination="same_attack",
            attack_result_id=first.attack.attack_result_id,
            objective=first.attack.objective,
            expected_objective=first.attack.objective,
            operator="owner",
        )
        with pytest.raises(ValueError, match="editable history"):
            await service.save_conversation_async(request=request)
        assert sqlite_instance._get_conversation(conversation_id=str(request.save_id)) is None

    async def test_tool_removal_allows_text_target_save_async(self, editor_target: MockPromptTarget) -> None:
        editor_target.apply_capabilities(
            capabilities=TargetCapabilities(
                supports_editable_history=True,
                supports_multi_turn=True,
                input_modalities=frozenset(
                    {frozenset({"text"}), frozenset({"function_call"}), frozenset({"function_call_output"})}
                ),
            )
        )
        request = draft()
        request.target_registry_name = "selected"
        request.messages = [
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[
                    ConversationPieceRequest(data_type="text", original_value="Hello"),
                    ConversationPieceRequest(
                        data_type="function_call",
                        original_value='{"call_id":"call-1","name":"lookup","arguments":"{}"}',
                    ),
                ],
            ),
            ConversationMessageRequest(
                role="tool",
                pieces=[
                    ConversationPieceRequest(
                        data_type="function_call_output", original_value='{"call_id":"call-1","output":"answer"}'
                    )
                ],
            ),
        ]
        service = AttackService()
        first = await service.save_conversation_async(request=request)
        editor_target.apply_capabilities(
            capabilities=TargetCapabilities(supports_editable_history=True, supports_multi_turn=True)
        )
        request.save_id = uuid.uuid4()
        request.source_attack_result_id = first.attack.attack_result_id
        request.source_conversation_id = first.messages.conversation_id
        request.messages[0].pieces = request.messages[0].pieces[:1]
        request.messages[0].pieces[0].source_piece_id = first.messages.messages[0].message_pieces[0].id
        request.messages[1] = ConversationMessageRequest(
            role="user", pieces=[ConversationPieceRequest(data_type="text", original_value="taews")]
        )
        saved = await service.save_conversation_async(request=request)
        assert [message.role for message in saved.messages.messages] == ["simulated_assistant", "user"]
        assert [message.message_pieces[0].original_value for message in saved.messages.messages] == ["Hello", "taews"]
        assert saved.attack.target is not None
        assert saved.attack.attack_result_id != first.attack.attack_result_id
        assert editor_target.prompt_sent == []

    @pytest.mark.parametrize("tool_history", [False, True])
    async def test_first_binding_checks_all_saved_conversations_async(
        self, *, editor_target: MockPromptTarget, sqlite_instance: SQLiteMemory, tool_history: bool
    ) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        related = draft()
        related.destination = "same_attack"
        related.attack_result_id = first.attack.attack_result_id
        related.expected_objective = first.attack.objective
        if tool_history:
            related.messages = [
                ConversationMessageRequest(
                    role="simulated_assistant",
                    pieces=[
                        ConversationPieceRequest(
                            data_type="function_call",
                            original_value='{"call_id":"call-1","name":"lookup","arguments":"{}"}',
                        )
                    ],
                )
            ]
        else:
            editor_target.apply_capabilities(capabilities=TargetCapabilities())
        await service.save_conversation_async(request=related)
        with patch.object(service, "_send_and_store_message_async", new_callable=AsyncMock) as send:
            with pytest.raises(ValueError, match="function_call" if tool_history else "editable history"):
                await service.add_message_async(
                    attack_result_id=first.attack.attack_result_id,
                    request=AddMessageRequest(
                        target_conversation_id=first.messages.conversation_id,
                        target_registry_name="selected",
                        pieces=[ConversationPieceRequest(data_type="text", original_value="Next")],
                    ),
                )
        send.assert_not_awaited()
        assert (await service.get_attack_async(attack_result_id=first.attack.attack_result_id)).target_unbound
        for conversation_id in (first.messages.conversation_id, str(related.save_id)):
            assert sqlite_instance._get_conversation(conversation_id=conversation_id).target_identifier is None

    async def test_targetless_save_and_retry_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        request = draft()
        with patch.object(service, "_get_save_target_async", new_callable=AsyncMock, return_value=None):
            first = await service.save_conversation_async(request=request)
            second = await service.save_conversation_async(request=request)
        assert first.attack.target_unbound
        assert first.attack.target is None
        assert first.attack.outcome.value == "undetermined"
        assert first.attack.attack_result_id == second.attack.attack_result_id
        assert first.messages.conversation_id == str(request.save_id)
        assert len(sqlite_instance.get_message_pieces(conversation_id=str(request.save_id))) == 1
        assert first.messages.messages[0].message_pieces[0].id == second.messages.messages[0].message_pieces[0].id

    async def test_same_attack_keeps_original_and_main_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        piece = first.messages.messages[0].message_pieces[0]
        request = SaveConversationRequest(
            save_id=uuid.uuid4(),
            destination="same_attack",
            attack_result_id=first.attack.attack_result_id,
            source_attack_result_id=first.attack.attack_result_id,
            source_conversation_id=first.messages.conversation_id,
            expected_objective=first.attack.objective,
            objective="Changed objective",
            operator="owner",
            messages=[
                ConversationMessageRequest(
                    role="simulated_assistant",
                    pieces=[
                        ConversationPieceRequest(data_type="text", original_value="Edited", source_piece_id=piece.id),
                        ConversationPieceRequest(data_type="text", original_value="Second piece"),
                    ],
                )
            ],
        )
        saved = await service.save_conversation_async(request=request)
        assert saved.attack.conversation_id == first.attack.conversation_id
        assert saved.attack.objective == "Changed objective"
        assert saved.attack.outcome.value == "undetermined"
        assert saved.messages.messages[0].role == "simulated_assistant"
        pieces = sqlite_instance.get_message_pieces(conversation_id=str(request.save_id))
        assert [piece.original_value for piece in pieces] == ["Edited", "Second piece"]
        assert pieces[0].original_prompt_id == piece.original_prompt_id
        assert pieces[0].id != piece.id
        original = sqlite_instance.get_message_pieces(conversation_id=first.messages.conversation_id)
        assert original[0].original_value == "Original prompt"
        assert original[0].id == piece.id
        assert (await service.save_conversation_async(request=request)).messages.conversation_id == str(request.save_id)

    async def test_operator_and_stale_objective_do_not_write_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        request = SaveConversationRequest(
            save_id=uuid.uuid4(),
            destination="same_attack",
            attack_result_id=first.attack.attack_result_id,
            expected_objective="Original objective",
            objective="Changed objective",
            operator="different",
        )
        with pytest.raises(PermissionError, match="another operator"):
            await service.save_conversation_async(request=request)
        request.operator = "owner"
        request.expected_objective = "Stale"
        with pytest.raises(AttackStateConflictError, match="objective"):
            await service.save_conversation_async(request=request)
        assert sqlite_instance._get_conversation(conversation_id=str(request.save_id)) is None
        assert (
            await service.get_attack_async(attack_result_id=first.attack.attack_result_id)
        ).objective == "Original objective"

    async def test_objective_only_draft_async(self) -> None:
        request = draft()
        request.messages = []
        saved = await AttackService().save_conversation_async(request=request)
        assert saved.messages.messages == []
        assert saved.attack.objective == request.objective
        assert saved.attack.target_unbound

    async def test_tool_payload_round_trip_async(self) -> None:
        call = {"type": "function", "id": "call-1", "function": {"name": "lookup", "arguments": '{"key": "value"}'}}
        output = {"type": "function_call_output", "call_id": "call-1", "output": {"result": "stored only"}}
        request = draft()
        request.messages = [
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[
                    ConversationPieceRequest(data_type="function_call", original_value=json.dumps(call)),
                ],
            ),
            ConversationMessageRequest(
                role="tool",
                pieces=[
                    ConversationPieceRequest(data_type="function_call_output", original_value=json.dumps(output)),
                ],
            ),
        ]
        saved = await AttackService().save_conversation_async(request=request)
        assert [message.role for message in saved.messages.messages] == ["simulated_assistant", "tool"]
        assert json.loads(saved.messages.messages[0].message_pieces[0].original_value) == call
        assert json.loads(saved.messages.messages[1].message_pieces[0].original_value) == output

    async def test_orphan_tool_response_does_not_write_async(self, sqlite_instance: SQLiteMemory) -> None:
        request = draft()
        request.messages = [
            ConversationMessageRequest(
                role="tool",
                pieces=[
                    ConversationPieceRequest(
                        data_type="function_call_output",
                        original_value=json.dumps({"call_id": "missing", "output": "value"}),
                    ),
                ],
            )
        ]
        with pytest.raises(ValueError, match="preceding"):
            await AttackService().save_conversation_async(request=request)
        assert sqlite_instance._get_conversation(conversation_id=str(request.save_id)) is None

    async def test_media_rollback_deletes_only_staged_files_async(self, sqlite_instance: SQLiteMemory) -> None:
        request = draft()
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="binary_path",
                original_value=base64.b64encode(b"draft media").decode(),
                mime_type="text/plain",
            )
        ]
        service = AttackService()
        with patch.object(
            sqlite_instance, "add_conversation_branches_to_attack", side_effect=AttackStateConflictError("conflict")
        ):
            with pytest.raises(AttackStateConflictError):
                await service.save_conversation_async(request=request)
        assert not list(Path(sqlite_instance.results_path).rglob("*.txt"))
        assert sqlite_instance.get_attack_results() == []

    async def test_message_only_save_keeps_current_objective_async(self, *, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        await service.update_attack_async(
            attack_result_id=first.attack.attack_result_id,
            request=UpdateAttackRequest(objective="Changed elsewhere", expected_objective=first.attack.objective),
        )
        saved = await service.save_conversation_async(
            request=SaveConversationRequest(
                save_id=uuid.uuid4(),
                destination="same_attack",
                attack_result_id=first.attack.attack_result_id,
                operator="owner",
                messages=draft().messages,
            )
        )
        assert saved.attack.objective == "Changed elsewhere"
        assert saved.messages.messages[0].message_pieces[0].original_value == "Original prompt"

    async def test_cross_attack_source_preserves_lineage_async(self, *, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        source = await service.save_conversation_async(request=draft(operator="another operator"))
        destination = await service.save_conversation_async(request=draft(value="Destination"))
        source_piece = source.messages.messages[0].message_pieces[0]
        saved = await service.save_conversation_async(
            request=SaveConversationRequest(
                save_id=uuid.uuid4(),
                destination="same_attack",
                attack_result_id=destination.attack.attack_result_id,
                source_attack_result_id=source.attack.attack_result_id,
                source_conversation_id=source.messages.conversation_id,
                operator="owner",
                messages=[
                    ConversationMessageRequest(
                        role="user",
                        pieces=[ConversationPieceRequest(original_value="Copy", source_piece_id=source_piece.id)],
                    )
                ],
            )
        )
        assert saved.attack.attack_result_id == destination.attack.attack_result_id
        copied = sqlite_instance.get_message_pieces(conversation_id=saved.messages.conversation_id)[0]
        assert copied.original_prompt_id == source_piece.original_prompt_id
        assert copied.id != source_piece.id
        assert (
            sqlite_instance.get_message_pieces(conversation_id=source.messages.conversation_id)[0].id == source_piece.id
        )

    async def test_normal_create_rolls_back_all_rows_and_media_async(self, *, sqlite_instance: SQLiteMemory) -> None:
        request = CreateAttackRequest(
            prepended_conversation=[
                ConversationMessageRequest(
                    role="user",
                    pieces=[
                        ConversationPieceRequest(
                            data_type="binary_path",
                            original_value=base64.b64encode(b"staged media").decode(),
                            mime_type="text/plain",
                        )
                    ],
                )
            ]
        )
        service = AttackService()
        insert_pieces = sqlite_instance._add_message_pieces_to_session
        conversation_ids: list[str] = []

        def fail_after_pieces(**kwargs: Any) -> None:
            insert_pieces(**kwargs)
            kwargs["session"].flush()
            conversation_ids.extend(piece.conversation_id for piece in kwargs["message_pieces"])
            raise AttackStateConflictError("injected failure")

        with patch.object(sqlite_instance, "_add_message_pieces_to_session", side_effect=fail_after_pieces):
            with pytest.raises(AttackStateConflictError, match="injected failure"):
                await service.create_attack_async(request=request)
        assert sqlite_instance.get_attack_results() == []
        assert conversation_ids
        for conversation_id in conversation_ids:
            assert sqlite_instance._get_conversation(conversation_id=conversation_id) is None
            assert sqlite_instance.get_message_pieces(conversation_id=conversation_id) == []
        assert not list(Path(sqlite_instance.results_path).rglob("*.txt"))

    async def test_create_appends_after_copied_history_async(self, *, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        request = draft()
        request.messages.append(
            ConversationMessageRequest(role="assistant", pieces=[ConversationPieceRequest(original_value="Reply")])
        )
        original = await service.create_attack_async(
            request=CreateAttackRequest(prepended_conversation=request.messages)
        )
        original_pieces = sqlite_instance.get_message_pieces(conversation_id=original.conversation_id)
        created = await service.create_attack_async(
            request=CreateAttackRequest(
                source_conversation_id=original.conversation_id,
                cutoff_index=1,
                prepended_conversation=[
                    ConversationMessageRequest(role="user", pieces=[ConversationPieceRequest(original_value="Next")])
                ],
            )
        )
        pieces = sqlite_instance.get_message_pieces(conversation_id=created.conversation_id)
        assert [piece.sequence for piece in pieces] == [0, 1, 2]
        assert [piece.role for piece in pieces] == ["user", "simulated_assistant", "user"]
        assert [piece.original_value for piece in pieces] == ["Original prompt", "Reply", "Next"]
        assert pieces[0].original_prompt_id == original_pieces[0].original_prompt_id

    async def test_save_id_cannot_be_reused_for_different_content_async(self) -> None:
        service = AttackService()
        request = draft()
        await service.save_conversation_async(request=request)
        request.objective = "Different"
        with pytest.raises(AttackStateConflictError, match="identity"):
            await service.save_conversation_async(request=request)

    async def test_first_send_binds_all_conversations_and_keeps_receipts_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        related = await service.save_conversation_async(
            request=SaveConversationRequest(
                save_id=uuid.uuid4(),
                destination="same_attack",
                attack_result_id=first.attack.attack_result_id,
                expected_objective=first.attack.objective,
                objective=first.attack.objective,
                operator="owner",
            )
        )
        target = get_mock_target_identifier()
        with (
            patch.object(service, "_get_save_target_async", new_callable=AsyncMock, return_value=target),
            patch.object(service, "_validate_editor_target_async", new_callable=AsyncMock, return_value=None),
            patch.object(service, "_validate_target_match"),
            patch.object(service, "_send_and_store_message_async", new_callable=AsyncMock) as send,
        ):
            result = await service.add_message_async(
                attack_result_id=first.attack.attack_result_id,
                request=AddMessageRequest(
                    target_conversation_id=related.messages.conversation_id,
                    target_registry_name="selected",
                    pieces=[ConversationPieceRequest(data_type="text", original_value="Next")],
                ),
            )
        send.assert_awaited_once()
        assert result.attack.target_unbound is False
        assert result.attack.target.identifier_hash == target.hash
        for conversation_id in (first.messages.conversation_id, related.messages.conversation_id):
            conversation = sqlite_instance._get_conversation(conversation_id=conversation_id)
            assert conversation.target_identifier.hash == target.hash
        attack = sqlite_instance.get_attack_results(attack_result_ids=[first.attack.attack_result_id])[0]
        assert f"conversation_save:{first.messages.conversation_id}" in attack.metadata
        assert f"conversation_save:{related.messages.conversation_id}" in attack.metadata

    async def test_invalid_conversation_does_not_bind_target_async(self) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        with patch.object(service, "_bind_manual_target_async", new_callable=AsyncMock) as bind:
            with pytest.raises(ValueError, match="not part"):
                await service.add_message_async(
                    attack_result_id=saved.attack.attack_result_id,
                    request=AddMessageRequest(
                        target_conversation_id=str(uuid.uuid4()),
                        target_registry_name="selected",
                        pieces=[ConversationPieceRequest(data_type="text", original_value="Next")],
                    ),
                )
        bind.assert_not_awaited()
        assert (await service.get_attack_async(attack_result_id=saved.attack.attack_result_id)).target_unbound

    async def test_competing_binding_cannot_replace_target_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        original = sqlite_instance.get_attack_results(attack_result_ids=[saved.attack.attack_result_id])[0]
        with (
            patch.object(service, "_get_save_target_async", new_callable=AsyncMock) as resolve,
            patch.object(service, "_validate_editor_target_async", new_callable=AsyncMock, return_value=None),
        ):
            resolve.return_value = get_mock_target_identifier("First")
            await service._bind_manual_target_async(attack=original, registry_name="first")
            resolve.return_value = get_mock_target_identifier("Second")
            with pytest.raises(AttackStateConflictError):
                await service._bind_manual_target_async(attack=original, registry_name="second")
        current = await service.get_attack_async(attack_result_id=saved.attack.attack_result_id)
        assert current.target.identifier_hash == get_mock_target_identifier("First").hash

    @pytest.mark.parametrize("objective", ["New objective", ""])
    @pytest.mark.parametrize("in_conversation_save", [False, True])
    async def test_objective_change_keeps_score_evidence_async(
        self, *, sqlite_instance: SQLiteMemory, objective: str, in_conversation_save: bool
    ) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        score = Score(score_type="true_false", score_value="True", score_rationale="Original evidence")
        sqlite_instance.add_scores_to_memory(scores=[score])
        sqlite_instance.update_attack_result_by_id(
            attack_result_id=saved.attack.attack_result_id,
            update_fields={"automated_score_id": score.id, "human_score_id": score.id, "outcome": "success"},
        )
        unchanged = await service.update_attack_async(
            attack_result_id=saved.attack.attack_result_id,
            request=UpdateAttackRequest(objective=saved.attack.objective, expected_objective=saved.attack.objective),
        )
        assert unchanged.outcome.value == "success"
        if in_conversation_save:
            updated = (
                await service.save_conversation_async(
                    request=SaveConversationRequest(
                        save_id=uuid.uuid4(),
                        destination="same_attack",
                        attack_result_id=saved.attack.attack_result_id,
                        operator="owner",
                        objective=objective,
                        expected_objective=saved.attack.objective,
                    )
                )
            ).attack
        else:
            updated = await service.update_attack_async(
                attack_result_id=saved.attack.attack_result_id,
                request=UpdateAttackRequest(objective=objective, expected_objective=saved.attack.objective),
            )
        assert updated.objective == objective
        assert updated.outcome.value == "undetermined"
        assert updated.automated_score is None
        assert updated.human_score is None
        assert sqlite_instance.get_scores(score_ids=[str(score.id)])[0].score_rationale == "Original evidence"

    async def test_transaction_failure_rolls_back_attack_and_conversation_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        with patch.object(sqlite_instance, "_add_message_pieces_to_session", side_effect=ValueError("write failed")):
            with pytest.raises(ValueError, match="write failed"):
                await AttackService().save_conversation_async(request=request)
        assert sqlite_instance._get_conversation(conversation_id=str(request.save_id)) is None
        assert sqlite_instance.get_attack_results() == []

    async def test_concurrent_retries_leave_one_conversation_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        request = draft()
        first, second = await asyncio.gather(
            service.save_conversation_async(request=request),
            service.save_conversation_async(request=request),
        )
        assert first.messages == second.messages
        assert len(sqlite_instance.get_attack_results()) == 1
        assert len(sqlite_instance.get_message_pieces(conversation_id=str(request.save_id))) == 1

    @pytest.mark.parametrize("cancel", [False, True])
    async def test_failed_media_write_cleans_partial_file_async(
        self, *, sqlite_instance: SQLiteMemory, cancel: bool
    ) -> None:
        request = draft()
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="binary_path",
                original_value=base64.b64encode(b"partial").decode(),
                mime_type="text/plain",
            )
        ]
        storage = sqlite_instance.results_storage_io
        write = storage.write_file_async
        started, finish = asyncio.Event(), asyncio.Event()

        async def interrupted_write_async(*args: Any, **kwargs: Any) -> None:
            await write(*args, **kwargs)
            started.set()
            if cancel:
                await finish.wait()
            else:
                raise OSError("write interrupted")

        with patch.object(storage, "write_file_async", side_effect=interrupted_write_async):
            task = asyncio.create_task(AttackService().save_conversation_async(request=request))
            await asyncio.wait_for(started.wait(), timeout=5)
            if cancel:
                task.cancel()
                finish.set()
            with pytest.raises(asyncio.CancelledError if cancel else OSError):
                await task
        assert not list(Path(sqlite_instance.results_path).rglob("*.txt"))
        assert sqlite_instance.get_attack_results() == []
