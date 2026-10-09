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
from sqlalchemy.orm import Session

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
from pyrit.backend.models.message_sends import MessageSendRequest, MessageSendState
from pyrit.backend.models.targets import TargetListResponse
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.target_service import TargetService
from pyrit.memory import SQLiteMemory
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.memory.memory_models import PromptMemoryEntry
from pyrit.models import Conversation, MessagePiece, PromptDataType, Score
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.target.request_trace_context import RequestTraceContext
from pyrit.models.target.target_capabilities import TargetCapabilities
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from unit.backend.mocks import _settle_send_async, message_send_lifecycle_async
from unit.mocks import MockPromptTarget, get_mock_target_identifier, run_memory_session_async


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
    with (
        patch("pyrit.backend.services.attack_service.get_target_service", return_value=service),
        patch("pyrit.backend.services.message_send_service.get_target_service", return_value=service),
    ):
        yield target


@pytest.fixture
def response_target(patch_central_database: None) -> Iterator[OpenAIResponseTarget]:
    target = OpenAIResponseTarget(
        endpoint="https://example.invalid/v1/responses", model_name="unit-test", api_key="unit-test"
    )
    service = MagicMock(spec=TargetService)
    service.get_target_object.return_value = target
    service.get_target_async.return_value = target_object_to_instance("responses", target)
    service.list_targets_async.return_value = TargetListResponse(
        items=[target_object_to_instance("responses", target)],
        pagination=PaginationInfo(limit=50, has_more=False),
    )
    with patch("pyrit.backend.services.attack_service.get_target_service", return_value=service):
        yield target


@pytest.mark.usefixtures("patch_central_database")
class TestConversationEditor:
    @pytest.mark.parametrize("data_type", ["audio_path", "video_path", "binary_path"])
    @pytest.mark.parametrize("same_attack", [False, True])
    @pytest.mark.parametrize("converted", [False, True])
    async def test_save_rejects_unsupported_history_before_writing_async(
        self,
        *,
        response_target: OpenAIResponseTarget,
        sqlite_instance: SQLiteMemory,
        data_type: PromptDataType,
        same_attack: bool,
        converted: bool,
    ) -> None:
        service = AttackService()
        initial = draft()
        initial.target_registry_name = "responses"
        source = await service.save_conversation_async(request=initial) if same_attack else None
        before = await sqlite_instance.get_attack_results_async()
        request = draft()
        request.target_registry_name = "responses"
        if source:
            request.destination = "same_attack"
            request.attack_result_id = source.attack.attack_result_id
            request.target_registry_name = None
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="text" if converted else data_type,
                original_value="original",
                converted_value="media" if converted else None,
                converted_value_data_type=data_type if converted else None,
            )
        ]
        request.messages.append(
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[ConversationPieceRequest(original_value="reply")],
            )
        )
        with (
            patch.object(service, "_persist_base64_pieces_async", new_callable=AsyncMock) as persist,
            patch.object(response_target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send,
        ):
            with pytest.raises(ValueError, match=data_type):
                await service.save_conversation_async(request=request)
            persist.assert_not_awaited()
            send.assert_not_awaited()
        assert await sqlite_instance.get_attack_results_async() == before
        assert await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id)) is None
        assert await sqlite_instance.get_message_pieces_async(conversation_id=str(request.save_id)) == []

    @pytest.mark.parametrize("data_type", ["audio_path", "video_path", "binary_path"])
    @pytest.mark.parametrize("related", [False, True])
    async def test_targetless_media_is_saved_but_incompatible_binding_writes_nothing_async(
        self,
        *,
        response_target: OpenAIResponseTarget,
        sqlite_instance: SQLiteMemory,
        managed_media_path: Path,
        data_type: PromptDataType,
        related: bool,
    ) -> None:
        media = managed_media_path.with_name("history.bin")
        await asyncio.to_thread(media.write_bytes, b"history bytes")
        service = AttackService()
        source = await service.save_conversation_async(request=draft())
        request = draft()
        request.destination = "same_attack"
        request.attack_result_id = source.attack.attack_result_id
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="text",
                original_value="original",
                converted_value=str(media),
                converted_value_data_type=data_type,
            )
        ]
        request.messages.append(
            ConversationMessageRequest(
                role="simulated_assistant",
                pieces=[ConversationPieceRequest(original_value="reply")],
            )
        )
        saved = await service.save_conversation_async(request=request)
        attack_id = saved.attack.attack_result_id
        before = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack_id]))[0]
        history = await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id)
        assert history[0].converted_value_data_type == data_type
        assert saved.attack.target_unbound
        with patch.object(response_target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
            with pytest.raises(ValueError, match=data_type):
                await service.add_message_async(
                    attack_result_id=attack_id,
                    request=AddMessageRequest(
                        target_conversation_id=source.messages.conversation_id
                        if related
                        else saved.messages.conversation_id,
                        target_registry_name="responses",
                        pieces=[ConversationPieceRequest(original_value="next")],
                    ),
                )
            send.assert_not_awaited()
        assert (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack_id]))[0] == before
        for conversation_id in before.get_active_conversation_ids():
            assert (
                await sqlite_instance.get_conversation_metadata_async(
                    conversation_id=conversation_id,
                )
            ).target_identifier is None
        assert await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id) == history

    async def test_supported_converted_history_can_save_and_bind_async(
        self,
        *,
        response_target: OpenAIResponseTarget,
        sqlite_instance: SQLiteMemory,
        managed_media_path: Path,
    ) -> None:
        media = managed_media_path.with_name("original.wav")
        await asyncio.to_thread(media.write_bytes, b"original audio bytes")
        service = AttackService()
        request = draft()
        request.messages[0].pieces = [
            ConversationPieceRequest(
                data_type="audio_path",
                original_value=str(media),
                converted_value="transcript",
                converted_value_data_type="text",
            )
        ]
        targetless = await service.save_conversation_async(request=request)
        attack = (
            await sqlite_instance.get_attack_results_async(
                attack_result_ids=[targetless.attack.attack_result_id],
            )
        )[0]
        bound = await service._bind_manual_target_async(attack=attack, registry_name="responses")
        assert bound.metadata["target_unbound"] is False
        request.save_id = uuid.uuid4()
        request.target_registry_name = "responses"
        saved = await service.save_conversation_async(request=request)
        assert saved.attack.target_unbound is False
        assert saved.messages.messages[0].message_pieces[0].converted_value == "transcript"

    @pytest.mark.parametrize("same_attack", [False, True])
    async def test_save_long_conversation_async(self, *, sqlite_instance: SQLiteMemory, same_attack: bool) -> None:
        service = AttackService()
        source = await service.save_conversation_async(request=draft())
        request = SaveConversationRequest(
            save_id=uuid.uuid4(),
            destination="same_attack" if same_attack else "new_attack",
            attack_result_id=source.attack.attack_result_id if same_attack else None,
            source_attack_result_id=source.attack.attack_result_id,
            source_conversation_id=source.messages.conversation_id,
            operator="owner",
            messages=[
                ConversationMessageRequest(
                    role="system" if index == 0 else "user" if index % 2 else "simulated_assistant",
                    pieces=[ConversationPieceRequest(data_type="text", original_value=f"Message {index}")],
                )
                for index in range(201)
            ],
        )

        saved = await service.save_conversation_async(request=request)

        assert len(saved.messages.messages) == 201
        stored = await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id)
        assert [piece.original_value for piece in stored] == [f"Message {index}" for index in range(201)]

    @pytest.mark.parametrize("change", ["original", "converted", "role", "unchanged"])
    async def test_source_scores_do_not_follow_edited_content_async(
        self, *, sqlite_instance: SQLiteMemory, change: str
    ) -> None:
        service = AttackService()
        source = await service.save_conversation_async(request=draft())
        piece = (await sqlite_instance.get_message_pieces_async(conversation_id=source.messages.conversation_id))[0]
        source_score = Score(score_value="true", score_type="true_false", message_piece_id=piece.id)
        (await sqlite_instance.add_scores_to_memory_async(scores=[source_score]))
        request = SaveConversationRequest(
            save_id=uuid.uuid4(),
            destination="same_attack",
            attack_result_id=source.attack.attack_result_id,
            source_attack_result_id=source.attack.attack_result_id,
            source_conversation_id=source.messages.conversation_id,
            operator="owner",
            messages=[
                ConversationMessageRequest(
                    role="developer" if change == "role" else "user",
                    pieces=[
                        ConversationPieceRequest(
                            source_piece_id=piece.id,
                            data_type="text",
                            original_value="Edited original" if change == "original" else piece.original_value,
                            converted_value="Edited converted" if change == "converted" else piece.converted_value,
                        )
                    ],
                )
            ],
        )
        saved = await service.save_conversation_async(request=request)
        edited = (await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id))[0]
        assert edited.prompt_metadata["source_piece_id"] == str(piece.id)
        if change == "unchanged":
            assert edited.original_prompt_id == piece.original_prompt_id
            assert [score.id for score in (await sqlite_instance.get_prompt_scores_async(prompt_ids=[edited.id]))] == [
                source_score.id
            ]
            return
        assert edited.original_prompt_id == edited.id
        assert (await sqlite_instance.get_prompt_scores_async(prompt_ids=[edited.id])) == []
        edited_score = Score(score_value="false", score_type="true_false", message_piece_id=edited.id)
        (await sqlite_instance.add_scores_to_memory_async(scores=[edited_score]))
        assert [score.id for score in (await sqlite_instance.get_prompt_scores_async(prompt_ids=[edited.id]))] == [
            edited_score.id
        ]
        assert [score.id for score in (await sqlite_instance.get_prompt_scores_async(prompt_ids=[piece.id]))] == [
            source_score.id
        ]

    @pytest.mark.parametrize("change", ["related", "append", "edit", "delete"])
    @pytest.mark.parametrize("asynchronous", [False, True])
    async def test_binding_rejects_history_changed_after_validation_async(
        self, *, sqlite_instance: SQLiteMemory, editor_target: MockPromptTarget, change: str, asynchronous: bool
    ) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        conversation_id = saved.messages.conversation_id
        validate = service._validate_editor_target_async

        async def change_history_async(**kwargs: Any) -> None:
            await validate(**kwargs)
            if change == "related":
                (
                    await sqlite_instance.add_conversation_branches_to_attack_async(
                        attack_result_id=saved.attack.attack_result_id,
                        conversations=[Conversation(conversation_id=str(uuid.uuid4()))],
                        message_pieces=[],
                    )
                )
            elif change == "append":
                (
                    await sqlite_instance.add_message_pieces_to_memory_async(
                        message_pieces=[
                            MessagePiece(role="user", original_value="New", conversation_id=conversation_id)
                        ]
                    )
                )
            else:

                def change_entry(session: Session) -> None:
                    entry = session.query(PromptMemoryEntry).filter_by(conversation_id=conversation_id).one()
                    if change == "delete":
                        session.delete(entry)
                    else:
                        entry.converted_value = "Changed after validation"
                    session.commit()

                await run_memory_session_async(memory=sqlite_instance, operation=change_entry)

        with (
            patch.object(service, "_validate_editor_target_async", side_effect=change_history_async) as check,
            patch.object(
                service._message_send_service, "_send_and_store_message_async", new_callable=AsyncMock
            ) as send,
        ):
            with pytest.raises(AttackStateConflictError, match="changed"):
                request = AddMessageRequest(
                    target_conversation_id=conversation_id,
                    target_registry_name="selected",
                    pieces=[ConversationPieceRequest(data_type="text", original_value="Next")],
                )
                if asynchronous:
                    await service.submit_message_send_async(
                        attack_result_id=saved.attack.attack_result_id,
                        request=MessageSendRequest(**request.model_dump(), submission_id="history-check"),
                    )
                else:
                    await service.add_message_async(attack_result_id=saved.attack.attack_result_id, request=request)
            send.assert_not_awaited()
        check.assert_awaited_once()
        assert (await service.get_attack_async(attack_result_id=saved.attack.attack_result_id)).target_unbound
        assert (
            await sqlite_instance.get_conversation_metadata_async(conversation_id=conversation_id)
        ).target_identifier is None

    @pytest.mark.parametrize("role", ["assistant", "tool"])
    def test_authored_real_response_roles_are_rejected(self, role: str) -> None:
        with pytest.raises(ValueError, match="simulated_assistant or simulated_tool"):
            SaveConversationRequest.model_validate(
                {
                    "save_id": str(uuid.uuid4()),
                    "destination": "new_attack",
                    "operator": "owner",
                    "messages": [{"role": role, "pieces": [{"data_type": "text", "original_value": "Authored"}]}],
                }
            )

    @pytest.mark.parametrize("role", ["assistant", "tool"])
    async def test_store_only_responses_remain_simulated_async(
        self, *, sqlite_instance: SQLiteMemory, role: str
    ) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        request = AddMessageRequest.model_validate(
            {
                "target_conversation_id": saved.messages.conversation_id,
                "role": role,
                "send": False,
                "pieces": [
                    {
                        "data_type": "text",
                        "original_value": "Authored response",
                        "prompt_metadata": {RequestTraceContext.METADATA_KEY: "not-live"},
                    }
                ],
            }
        )
        await service.add_message_async(attack_result_id=saved.attack.attack_result_id, request=request)
        piece = (await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id))[-1]
        assert piece.role == f"simulated_{role}"
        assert piece.prompt_metadata["prepended_history"] is True
        assert RequestTraceContext.METADATA_KEY not in piece.prompt_metadata

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
                        data_type="image_path", original_value=base64.b64encode(b"media").decode()
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
        assert (await sqlite_instance.get_attack_results_async()) == []
        assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id))) is None

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
        attack = (await sqlite_instance.get_attack_results_async(attack_result_ids=[saved.attack.attack_result_id]))[0]
        with pytest.raises(ValueError, match="type"):
            await service._bind_manual_target_async(attack=attack, registry_name="responses")
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[saved.attack.attack_result_id]))[0]
        assert current.metadata["target_unbound"] is True
        assert current.atomic_attack_identifier == attack.atomic_attack_identifier
        assert (
            await sqlite_instance.get_conversation_metadata_async(conversation_id=saved.messages.conversation_id)
        ).target_identifier is None

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
        history = await sqlite_instance.get_conversation_messages_async(conversation_id=saved.messages.conversation_id)
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
        assert (await sqlite_instance.get_attack_results_async()) == []
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
        assert (await sqlite_instance.get_attack_results_async()) == []

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
        assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id))) is None

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
                role="simulated_tool",
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
        with patch.object(
            service._message_send_service, "_send_and_store_message_async", new_callable=AsyncMock
        ) as send:
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
            assert (
                await sqlite_instance.get_conversation_metadata_async(conversation_id=conversation_id)
            ).target_identifier is None

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
        assert len(await sqlite_instance.get_message_pieces_async(conversation_id=str(request.save_id))) == 1
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
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=str(request.save_id))
        assert [piece.original_value for piece in pieces] == ["Edited", "Second piece"]
        assert pieces[0].original_prompt_id == pieces[0].id
        assert pieces[0].prompt_metadata["source_piece_id"] == str(piece.id)
        assert pieces[0].id != piece.id
        original = await sqlite_instance.get_message_pieces_async(conversation_id=first.messages.conversation_id)
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
        assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id))) is None
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

    async def test_tool_payload_round_trip_async(self, sqlite_instance: SQLiteMemory) -> None:
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
                role="simulated_tool",
                pieces=[
                    ConversationPieceRequest(
                        data_type="function_call_output",
                        original_value=json.dumps(output),
                        prompt_metadata={
                            RequestTraceContext.METADATA_KEY: "not-live",
                            RequestTraceContext.REQUEST_METADATA_KEY: "not-live",
                        },
                    ),
                ],
            ),
        ]
        saved = await AttackService().save_conversation_async(request=request)
        assert [message.role for message in saved.messages.messages] == ["simulated_assistant", "simulated_tool"]
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id)
        for piece in pieces:
            assert piece.is_simulated
            assert piece.prompt_metadata["prepended_history"] is True
            assert RequestTraceContext.METADATA_KEY not in piece.prompt_metadata
            assert RequestTraceContext.REQUEST_METADATA_KEY not in piece.prompt_metadata
            assert not ScorerPromptValidator().is_role_supported(piece)
        _, copies = await AttackService()._prepare_conversation_up_to_async(
            source_conversation_id=saved.messages.conversation_id, cutoff_index=1
        )
        assert [piece.role for piece in copies] == ["simulated_assistant", "simulated_tool"]
        assert all(piece.prompt_metadata["prepended_history"] is True for piece in copies)
        assert json.loads(saved.messages.messages[0].message_pieces[0].original_value) == call
        assert json.loads(saved.messages.messages[1].message_pieces[0].original_value) == output

    async def test_orphan_tool_response_does_not_write_async(self, sqlite_instance: SQLiteMemory) -> None:
        request = draft()
        request.messages = [
            ConversationMessageRequest(
                role="simulated_tool",
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
        assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id))) is None

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
            sqlite_instance,
            "add_conversation_branches_to_attack_async",
            side_effect=AttackStateConflictError("conflict"),
        ):
            with pytest.raises(AttackStateConflictError):
                await service.save_conversation_async(request=request)
        assert not list(Path(sqlite_instance.results_path).rglob("*.txt"))
        assert (await sqlite_instance.get_attack_results_async()) == []

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
        copied = (await sqlite_instance.get_message_pieces_async(conversation_id=saved.messages.conversation_id))[0]
        assert copied.original_prompt_id == copied.id
        assert copied.prompt_metadata["source_piece_id"] == str(source_piece.id)
        assert copied.id != source_piece.id
        assert (await sqlite_instance.get_message_pieces_async(conversation_id=source.messages.conversation_id))[
            0
        ].id == source_piece.id

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
        assert (await sqlite_instance.get_attack_results_async()) == []
        assert conversation_ids
        for conversation_id in conversation_ids:
            assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=conversation_id)) is None
            assert (await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)) == []
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
        original_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=original.conversation_id)
        created = await service.create_attack_async(
            request=CreateAttackRequest(
                source_conversation_id=original.conversation_id,
                cutoff_index=1,
                prepended_conversation=[
                    ConversationMessageRequest(role="user", pieces=[ConversationPieceRequest(original_value="Next")])
                ],
            )
        )
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=created.conversation_id)
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
            patch.object(service._message_send_service, "_add_message_async", new_callable=AsyncMock) as send,
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
            conversation = await sqlite_instance.get_conversation_metadata_async(conversation_id=conversation_id)
            assert conversation.target_identifier.hash == target.hash
        attack = (await sqlite_instance.get_attack_results_async(attack_result_ids=[first.attack.attack_result_id]))[0]
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

    async def test_async_first_send_binds_all_saved_conversations_async(
        self, *, sqlite_instance: SQLiteMemory, editor_target: MockPromptTarget
    ) -> None:
        service = AttackService()
        first = await service.save_conversation_async(request=draft())
        related = await service.save_conversation_async(
            request=SaveConversationRequest(
                save_id=uuid.uuid4(),
                destination="same_attack",
                attack_result_id=first.attack.attack_result_id,
                operator="owner",
            )
        )
        with patch.object(service._message_send_service, "_send_and_store_message_async", new_callable=AsyncMock):
            async with message_send_lifecycle_async(service._message_send_service):
                accepted = await service.submit_message_send_async(
                    attack_result_id=first.attack.attack_result_id,
                    request=MessageSendRequest(
                        submission_id="first-send",
                        target_conversation_id=related.messages.conversation_id,
                        target_registry_name="selected",
                        pieces=[ConversationPieceRequest(original_value="Next")],
                    ),
                )
                progress = await _settle_send_async(service=service._message_send_service, status=accepted)
                assert progress.state == MessageSendState.COMPLETED
                current = await service.get_attack_async(attack_result_id=first.attack.attack_result_id)
                assert current is not None
                assert not current.target_unbound
                owned = await sqlite_instance.get_attack_result_conversations_async(
                    attack_result_id=first.attack.attack_result_id
                )
                assert {conversation.conversation_id for conversation in owned} == {
                    first.messages.conversation_id,
                    related.messages.conversation_id,
                }
                assert all(conversation.target_identifier == editor_target.get_identifier() for conversation in owned)

    async def test_competing_binding_cannot_replace_target_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        saved = await service.save_conversation_async(request=draft())
        original = (await sqlite_instance.get_attack_results_async(attack_result_ids=[saved.attack.attack_result_id]))[
            0
        ]
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
        (await sqlite_instance.add_scores_to_memory_async(scores=[score]))
        (
            await sqlite_instance.update_attack_result_by_id_async(
                attack_result_id=saved.attack.attack_result_id,
                update_fields={"automated_score_id": score.id, "human_score_id": score.id, "outcome": "success"},
            )
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
        assert (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[
            0
        ].score_rationale == "Original evidence"

    async def test_transaction_failure_rolls_back_attack_and_conversation_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        request = draft()
        with patch.object(sqlite_instance, "_add_message_pieces_to_session", side_effect=ValueError("write failed")):
            with pytest.raises(ValueError, match="write failed"):
                await AttackService().save_conversation_async(request=request)
        assert (await sqlite_instance.get_conversation_metadata_async(conversation_id=str(request.save_id))) is None
        assert (await sqlite_instance.get_attack_results_async()) == []

    async def test_concurrent_retries_leave_one_conversation_async(self, sqlite_instance: SQLiteMemory) -> None:
        service = AttackService()
        request = draft()
        first, second = await asyncio.gather(
            service.save_conversation_async(request=request),
            service.save_conversation_async(request=request),
        )
        assert first.messages == second.messages
        assert len(await sqlite_instance.get_attack_results_async()) == 1
        assert len(await sqlite_instance.get_message_pieces_async(conversation_id=str(request.save_id))) == 1

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
        assert (await sqlite_instance.get_attack_results_async()) == []
