# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Another user can't continue a chat through someone's private target."""

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.auth import caller_principal_scope
from pyrit.backend.models import AddMessageRequest, MessagePieceRequest
from pyrit.backend.models.message_sends import MessageSendRequest, MessageSendState
from pyrit.backend.services import TargetService
from pyrit.backend.services.manual_send_scheduler import ManualSendScheduler
from pyrit.backend.services.message_send_service import MessageSendService
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
)
from pyrit.models.component_spec import SourceInstanceSpec, TargetBinding
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.registry import Registry, TargetRegistry, owner_metadata
from unit.backend.mocks import _settle_send_async, message_send_lifecycle_async
from unit.mocks import MockPromptTarget

_TARGET_NAME = "alice-chat"


@dataclass
class _Chat:
    """An attack whose conversation uses alice's private target."""

    attack: AttackResult
    send: AsyncMock


@pytest.fixture
def isolated_registries(patch_central_database: Any) -> Iterator[None]:
    with (
        patch.dict(Registry._singletons, {}, clear=True),
        patch("pyrit.backend.services.message_send_service.get_target_service", side_effect=TargetService),
    ):
        yield


async def _store_attack_async(
    sqlite_instance: SQLiteMemory, *, objective_target: ComponentIdentifier, metadata: dict[str, Any]
) -> AttackResult:
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="Continue the chat",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack", class_module="pyrit.backend", objective_target=objective_target
            )
        ),
        metadata=metadata,
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    return attack


def _reply(*, normalized_conversation: list[Message]) -> list[Message]:
    conversation_id = normalized_conversation[-1].get_piece().conversation_id
    return [MessagePiece(role="assistant", original_value="reply", conversation_id=conversation_id).to_message()]


@pytest.fixture
async def chat_by_name(sqlite_instance: SQLiteMemory, isolated_registries: None) -> AsyncIterator[_Chat]:
    target = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(
        target, name=_TARGET_NAME, metadata=owner_metadata("alice", private=True)
    )
    attack = await _store_attack_async(
        sqlite_instance,
        objective_target=target.get_identifier(),
        metadata={"target_registry_name": _TARGET_NAME},
    )
    send = AsyncMock(side_effect=_reply)
    with patch.object(MockPromptTarget, "_send_prompt_to_target_async", send):
        yield _Chat(attack=attack, send=send)


@pytest.fixture
async def chat_by_binding(sqlite_instance: SQLiteMemory, isolated_registries: None) -> AsyncIterator[_Chat]:
    source = OpenAIChatTarget(endpoint="https://api.openai.com/v1", model_name="gpt-4o", api_key="alice-key")
    TargetRegistry.get_registry_singleton().instances.register(
        source, name=_TARGET_NAME, metadata=owner_metadata("alice", private=True)
    )
    spec = SourceInstanceSpec(
        source_name=_TARGET_NAME, source_hash=source.get_identifier().hash, params={"temperature": 0.2}
    )
    with caller_principal_scope("alice"):
        copy: PromptTarget = await TargetService().build_from_source_async(spec)
    binding = TargetBinding(
        source_name=_TARGET_NAME,
        source_hash=source.get_identifier().hash,
        temperature=0.2,
        effective_hash=copy.get_identifier().hash,
    )
    attack = await _store_attack_async(
        sqlite_instance, objective_target=copy.get_identifier(), metadata=binding.to_metadata()
    )
    await copy.cleanup_target_async()
    send = AsyncMock(side_effect=_reply)
    try:
        with patch.object(OpenAIChatTarget, "_send_prompt_to_target_async", send):
            yield _Chat(attack=attack, send=send)
    finally:
        await source.cleanup_target_async()


def _add_request(chat: _Chat) -> AddMessageRequest:
    return AddMessageRequest(
        pieces=[MessagePieceRequest(original_value="Hello")],
        target_conversation_id=chat.attack.conversation_id,
        target_registry_name=_TARGET_NAME,
        send=True,
    )


def _send_request(chat: _Chat) -> MessageSendRequest:
    return MessageSendRequest(**_add_request(chat).model_dump(), submission_id=str(uuid.uuid4()))


async def _add_message_async(chat: _Chat) -> None:
    await MessageSendService(scheduler=ManualSendScheduler()).add_message_async(
        attack_result_id=chat.attack.attack_result_id, request=_add_request(chat)
    )


async def _submit_and_settle_async(chat: _Chat) -> MessageSendState:
    service = MessageSendService(scheduler=ManualSendScheduler())
    async with message_send_lifecycle_async(service):
        status = await service.submit_async(attack_result_id=chat.attack.attack_result_id, request=_send_request(chat))
        return (await _settle_send_async(service=service, status=status)).state


@pytest.fixture(params=["chat_by_name", "chat_by_binding"])
def chat(request: pytest.FixtureRequest) -> _Chat:
    selected_chat = request.getfixturevalue(request.param)
    assert isinstance(selected_chat, _Chat)
    return selected_chat


async def test_another_user_cannot_add_a_message_through_a_private_target_async(chat: _Chat) -> None:
    with caller_principal_scope("bob"), pytest.raises(ValueError, match="not found|missing"):
        await _add_message_async(chat)

    chat.send.assert_not_awaited()


async def test_another_user_cannot_submit_a_send_through_a_private_target_async(chat: _Chat) -> None:
    with caller_principal_scope("bob"), pytest.raises(ValueError, match="not found|missing"):
        await _submit_and_settle_async(chat)

    chat.send.assert_not_awaited()


async def test_a_signed_out_request_cannot_send_through_a_private_target_async(chat: _Chat) -> None:
    with caller_principal_scope(None), pytest.raises(ValueError, match="not found|missing"):
        await _add_message_async(chat)

    chat.send.assert_not_awaited()


async def test_the_owner_can_add_a_message_through_her_private_target_async(chat: _Chat) -> None:
    with caller_principal_scope("alice"):
        await _add_message_async(chat)

    chat.send.assert_awaited_once()


async def test_the_owner_can_submit_a_send_through_her_private_target_async(chat: _Chat) -> None:
    with caller_principal_scope("alice"):
        state = await _submit_and_settle_async(chat)

    assert state == MessageSendState.COMPLETED
    chat.send.assert_awaited_once()
