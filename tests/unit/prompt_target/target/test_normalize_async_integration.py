# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import MutableSequence

import pytest
from openai.types.chat import ChatCompletion
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from unit.mocks import MockPromptTarget

from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.message_normalizer import (
    ConversationContextNormalizer,
    HistorySquashNormalizer,
    MessageListNormalizer,
    MessageStringNormalizer,
    TokenizerTemplateNormalizer,
)
from pyrit.models import ComponentIdentifier, Message, MessagePiece, PromptResponseError
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AzureMLChatTarget, OpenAIChatTarget
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.openai.openai_response_target import OpenAIResponseTarget


def _make_message_piece(*, role: str, content: str, conversation_id: str = "conv1") -> MessagePiece:
    return MessagePiece(
        role=role,
        conversation_id=conversation_id,
        original_value=content,
        converted_value=content,
        original_value_data_type="text",
        converted_value_data_type="text",
    )


def _make_message(*, role: str, content: str, conversation_id: str = "conv1") -> Message:
    return Message(message_pieces=[_make_message_piece(role=role, content=content, conversation_id=conversation_id)])


def _make_normalizer_overrides(
    *,
    send_context: PrependedHistorySendContext,
    formatter: MessageStringNormalizer | None = None,
) -> dict[CapabilityName, HistorySquashNormalizer]:
    if not send_context.should_include_seed:
        return {}
    return {
        CapabilityName.EDITABLE_HISTORY: HistorySquashNormalizer(
            message_normalizer=formatter or ConversationContextNormalizer(),
            expected_history_message_count=send_context.seed_message_count,
        )
    }


def _make_prepended_history_send_context(
    *,
    prepended_messages: list[Message],
    target_supports_multi_turn: bool = False,
    conversation_id: str = "conv1",
) -> PrependedHistorySendContext:
    return PrependedHistorySendContext(
        conversation_id=conversation_id,
        seed_message_ids=tuple(message.get_piece().id for message in prepended_messages),
        replay_seed_each_send=not target_supports_multi_turn,
    )


def _create_mock_chat_completion(content: str = "hi") -> MagicMock:
    mock = MagicMock(spec=ChatCompletion)
    mock.choices = [MagicMock()]
    mock.choices[0].finish_reason = "stop"
    mock.choices[0].message.content = content
    mock.choices[0].message.audio = None
    mock.choices[0].message.tool_calls = None
    mock.model_dump_json.return_value = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    )
    return mock


# ---------------------------------------------------------------------------
# OpenAIChatTarget — normalize_async is called
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_chat_target_calls_normalize_async():
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    mock_completion = _create_mock_chat_completion("world")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch.object(target.configuration, "normalize_async", new_callable=AsyncMock) as mock_normalize:
        mock_normalize.return_value = [user_msg]
        await target.send_prompt_async(message=user_msg)

        mock_normalize.assert_called_once()
        call_messages = mock_normalize.call_args.kwargs["messages"]
        assert len(call_messages) == 1
        assert call_messages[0].get_value() == "hello"


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_chat_target_sends_normalized_to_construct_request():
    """Verify that the normalized (not original) conversation is used for the API body."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    user_msg = _make_message(role="user", content="original")
    adapted_msg = _make_message(role="user", content="adapted")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    mock_completion = _create_mock_chat_completion("response")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with (
        patch.object(target.configuration, "normalize_async", new_callable=AsyncMock, return_value=[adapted_msg]),
        patch.object(
            target,
            "_construct_request_body_async",
            new_callable=AsyncMock,
            return_value={"model": "gpt-4o", "messages": []},
        ) as mock_construct,
    ):
        await target.send_prompt_async(message=user_msg)

        # _construct_request_body should receive the adapted message, not the original
        call_conv = mock_construct.call_args.kwargs["conversation"]
        assert len(call_conv) == 1
        assert call_conv[0].get_value() == "adapted"


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_chat_target_memory_not_mutated():
    """Memory-backed conversation must not be altered by normalize_async."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        ),
    )

    system_msg = _make_message(role="system", content="be nice")
    user_msg = _make_message(role="user", content="hello")

    # Memory returns a conversation with a system message
    memory_conversation: MutableSequence[Message] = [system_msg]

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = memory_conversation
    target._memory = mock_memory

    mock_completion = _create_mock_chat_completion("response")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    await target.send_prompt_async(message=user_msg)

    # Memory-backed conversation must not be mutated by send_prompt_async
    assert len(memory_conversation) == 1
    assert memory_conversation[0].get_piece().api_role == "system"


# ---------------------------------------------------------------------------
# OpenAIResponseTarget — normalize_async is called
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_response_target_calls_normalize_async():
    target = OpenAIResponseTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    # Mock the API to return a simple response (no tool calls)
    mock_response = MagicMock()
    mock_response.error = None
    mock_response.status = "completed"
    mock_response.output = [
        ResponseOutputMessage(
            id="response-message",
            content=[
                ResponseOutputText(
                    annotations=[],
                    text="world",
                    type="output_text",
                )
            ],
            role="assistant",
            status="completed",
            type="message",
        )
    ]
    mock_response.model_dump_json.return_value = json.dumps(
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "world"}]}]}
    )
    target._async_client.responses.create = AsyncMock(return_value=mock_response)

    with patch.object(target.configuration, "normalize_async", new_callable=AsyncMock) as mock_normalize:
        mock_normalize.return_value = [user_msg]
        await target.send_prompt_async(message=user_msg)

        mock_normalize.assert_called_once()
        call_messages = mock_normalize.call_args.kwargs["messages"]
        assert len(call_messages) == 1


# ---------------------------------------------------------------------------
# AzureMLChatTarget — normalize_async is called
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_azure_ml_target_calls_normalize_async():
    target = AzureMLChatTarget(
        endpoint="http://aml-test-endpoint.com",
        api_key="valid_api_key",
    )

    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    with (
        patch.object(target.configuration, "normalize_async", new_callable=AsyncMock) as mock_normalize,
        patch.object(target, "_complete_chat_async", new_callable=AsyncMock, return_value="response"),
    ):
        mock_normalize.return_value = [user_msg]
        await target.send_prompt_async(message=user_msg)

        mock_normalize.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_azure_ml_target_sends_normalized_to_complete_chat():
    """Normalized (not original) messages should be passed to _complete_chat_async."""
    target = AzureMLChatTarget(
        endpoint="http://aml-test-endpoint.com",
        api_key="valid_api_key",
    )

    user_msg = _make_message(role="user", content="original")
    adapted_msg = _make_message(role="user", content="adapted")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    with (
        patch.object(target.configuration, "normalize_async", new_callable=AsyncMock, return_value=[adapted_msg]),
        patch.object(target, "_complete_chat_async", new_callable=AsyncMock, return_value="response") as mock_chat,
    ):
        await target.send_prompt_async(message=user_msg)

        call_messages = mock_chat.call_args.kwargs["messages"]
        assert len(call_messages) == 1
        assert call_messages[0].get_value() == "adapted"


@pytest.mark.usefixtures("patch_central_database")
async def test_azure_ml_target_memory_not_mutated():
    """Memory should retain original messages after normalization."""
    target = AzureMLChatTarget(
        endpoint="http://aml-test-endpoint.com",
        api_key="valid_api_key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        ),
    )

    system_msg = _make_message(role="system", content="be nice")
    user_msg = _make_message(role="user", content="hello")

    memory_conversation: MutableSequence[Message] = [system_msg]

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = memory_conversation
    target._memory = mock_memory

    with patch.object(target, "_complete_chat_async", new_callable=AsyncMock, return_value="response"):
        await target.send_prompt_async(message=user_msg)

    # Memory must still have original system message only (not mutated)
    assert len(memory_conversation) == 1
    assert memory_conversation[0].get_piece().api_role == "system"


@pytest.mark.usefixtures("patch_central_database")
async def test_azure_ml_system_squash_via_configuration_pipeline():
    """End-to-end: GenericSystemSquashNormalizer-equivalent behavior via TargetConfiguration pipeline."""
    target = AzureMLChatTarget(
        endpoint="http://aml-test-endpoint.com",
        api_key="valid_api_key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        ),
    )

    system_msg = _make_message(role="system", content="be nice")
    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [system_msg]
    target._memory = mock_memory

    with patch.object(target, "_complete_chat_async", new_callable=AsyncMock, return_value="response") as mock_chat:
        await target.send_prompt_async(message=user_msg)

        # _complete_chat_async should receive normalized messages (system squashed into user)
        call_messages = mock_chat.call_args.kwargs["messages"]
        roles = [m.get_piece().api_role for m in call_messages]
        assert "system" not in roles
        # The squashed message should contain the system content
        assert "be nice" in call_messages[0].get_value()
        assert "hello" in call_messages[0].get_value()


# ---------------------------------------------------------------------------
# _get_normalized_conversation_async — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_get_normalized_conversation_fetches_history_and_appends_message():
    """The method should fetch history from memory, append the current message, and return them."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    history_msg = _make_message(role="assistant", content="previous answer")
    user_msg = _make_message(role="user", content="new question")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [history_msg]
    target._memory = mock_memory

    result = await target._get_normalized_conversation_async(message=user_msg)

    mock_memory.get_conversation_messages.assert_called_once_with(conversation_id="conv1")
    assert len(result) == 2
    assert result[0].get_value() == "previous answer"
    assert result[1].get_value() == "new question"


@pytest.mark.usefixtures("patch_central_database")
async def test_get_normalized_conversation_empty_history():
    """When memory has no history, the result should contain only the current message."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    result = await target._get_normalized_conversation_async(message=user_msg)

    assert len(result) == 1
    assert result[0].get_value() == "hello"


@pytest.mark.usefixtures("patch_central_database")
async def test_get_normalized_conversation_does_not_mutate_memory():
    """The original memory-backed list must not be modified by the method."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    history_msg = _make_message(role="assistant", content="old")
    user_msg = _make_message(role="user", content="new")

    memory_list: MutableSequence[Message] = [history_msg]
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = memory_list
    target._memory = mock_memory

    await target._get_normalized_conversation_async(message=user_msg)

    # Memory list must still have only the original message
    assert len(memory_list) == 1
    assert memory_list[0].get_value() == "old"


@pytest.mark.usefixtures("patch_central_database")
async def test_get_normalized_conversation_runs_pipeline():
    """The method should invoke the normalization pipeline on the assembled conversation."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
                supports_multi_message_pieces=True,
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        ),
    )

    system_msg = _make_message(role="system", content="be helpful")
    user_msg = _make_message(role="user", content="hi")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [system_msg]
    target._memory = mock_memory

    result = await target._get_normalized_conversation_async(message=user_msg)

    # System-squash normalizer should merge system into user
    assert len(result) == 1
    assert "be helpful" in result[0].get_value()
    assert "hi" in result[0].get_value()
    roles = [m.get_piece().api_role for m in result]
    assert "system" not in roles


@pytest.mark.usefixtures("patch_central_database")
async def test_get_normalized_conversation_passthrough_when_no_adaptation_needed():
    """When the target supports all capabilities, the pipeline should pass messages through unchanged."""
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    system_msg = _make_message(role="system", content="be nice")
    user_msg = _make_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [system_msg]
    target._memory = mock_memory

    result = await target._get_normalized_conversation_async(message=user_msg)

    # No adaptation — messages pass through as-is
    assert len(result) == 2
    assert result[0].get_piece().api_role == "system"
    assert result[0].get_value() == "be nice"
    assert result[1].get_piece().api_role == "user"
    assert result[1].get_value() == "hello"


# ---------------------------------------------------------------------------
# Prepended history adaptation
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_adapts_prepended_history_without_mutating_memory():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())

    prepended_user = _make_message(role="user", content="original history")
    prepended_user.get_piece().converted_value = "converted history"
    prepended_user.get_piece().converter_identifiers = [
        ComponentIdentifier(class_name="TestConverter", class_module="tests")
    ]
    prepended_assistant = _make_message(role="simulated_assistant", content="assistant history")
    live_request = _make_message(role="user", content="original live")
    live_request.get_piece().converted_value = "converted live"
    memory_messages: MutableSequence[Message] = [prepended_user, prepended_assistant]

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = memory_messages
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=list(memory_messages))
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    result = await target._get_normalized_conversation_async(
        message=live_request,
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    assert len(result) == 1
    assert result[0].get_piece().original_value == (
        "Turn 1:\nuser: original history\nassistant: assistant history\nTurn 2:\nuser: original live"
    )
    assert result[0].get_piece().converted_value == (
        "Turn 1:\nuser: converted history\nassistant: assistant history\nTurn 2:\nuser: converted live"
    )
    assert len(memory_messages) == 2
    assert memory_messages[0].get_piece().original_value == "original history"


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_preserves_system_history_and_multimodal_live_request():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            input_modalities=frozenset({frozenset({"text", "image_path"})}),
        )
    )
    system_message = _make_message(role="system", content="Describe images precisely")
    live_request = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                converted_value="diagram.png",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id="conv1",
            )
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [system_message]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[system_message])
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    result = await target._get_normalized_conversation_async(
        message=live_request,
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    assert len(result) == 1
    assert len(result[0].message_pieces) == 2
    assert result[0].message_pieces[0].converted_value == "Turn 1:\nuser: Describe images precisely"
    assert result[0].message_pieces[1].converted_value == "diagram.png"
    assert result[0].message_pieces[1].converted_value_data_type == "image_path"


@pytest.mark.usefixtures("patch_central_database")
async def test_editable_history_override_runs_before_system_prompt_adaptation():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=False,
            supports_editable_history=False,
        ),
        policy=CapabilityHandlingPolicy(
            behaviors={
                CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            }
        ),
    )
    prepended = _make_message(role="system", content="system")
    live_request = _make_message(role="user", content="live")
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )

    result = await target._get_normalized_conversation_async(
        message=live_request,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    assert [message.get_value() for message in result] == [
        "Turn 1:\nuser: ### Instructions ###\n\nsystem\n\n######\n\nlive"
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_first_turn_normalization_preserves_live_multimodal_piece_order():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            input_modalities=frozenset({frozenset({"text", "image_path"})}),
        )
    )
    live_request = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                converted_value="diagram.png",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id="conv1",
                sequence=0,
                prompt_metadata={"piece": "image", "image-only": "preserved-on-image"},
            ),
            MessagePiece(
                role="user",
                original_value="What does this show?",
                converted_value="What does this show?",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id="conv1",
                sequence=0,
                prompt_metadata={"piece": "live-text", "trace": "preserved"},
            ),
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    result = await target._get_normalized_conversation_async(
        message=live_request,
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    assert [piece.converted_value_data_type for piece in result[0].message_pieces] == ["image_path", "text"]
    assert result[0].message_pieces[0].converted_value == "diagram.png"
    assert "What does this show?" in result[0].message_pieces[1].converted_value
    assert result[0].message_pieces[0].prompt_metadata == {
        "piece": "image",
        "image-only": "preserved-on-image",
    }
    assert result[0].message_pieces[1].prompt_metadata == {"piece": "live-text", "trace": "preserved"}


@pytest.mark.usefixtures("patch_central_database")
async def test_history_squash_does_not_restore_adapted_json_schema_metadata():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            input_modalities=frozenset({frozenset({"text", "image_path"})}),
        )
    )
    prepended = _make_message(role="user", content="prepended")
    live_request = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                original_value_data_type="image_path",
                conversation_id="conv1",
                prompt_metadata={"piece": "image"},
            ),
            MessagePiece(
                role="user",
                original_value="Describe this as JSON",
                conversation_id="conv1",
                prompt_metadata={
                    "response_format": "json",
                    "json_schema": {"type": "object", "properties": {"description": {"type": "string"}}},
                },
            ),
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])

    result = await target._get_normalized_conversation_async(
        message=live_request,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    image_piece, text_piece = result[0].message_pieces
    assert image_piece.prompt_metadata == {"piece": "image"}
    assert text_piece.prompt_metadata == {"response_format": "json"}
    assert '"description"' in text_piece.converted_value


@pytest.mark.usefixtures("patch_central_database")
async def test_custom_normalizer_output_metadata_is_authoritative():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
        )
    )
    source = _make_message(role="user", content="source")
    source.get_piece().prompt_metadata = {"source-only": "must not be restored"}
    replacement = Message.from_prompt(prompt="replacement", role="user")
    normalizer = MagicMock(spec=MessageListNormalizer)
    normalizer.normalize_async = AsyncMock(return_value=[replacement])
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = []
    target._memory = mock_memory

    result = await target._get_normalized_conversation_async(
        message=source,
        normalizer_overrides={CapabilityName.EDITABLE_HISTORY: normalizer},
    )

    assert result[0].get_piece().conversation_id == "conv1"
    assert result[0].get_piece().prompt_metadata == {}


@pytest.mark.usefixtures("patch_central_database")
async def test_prepended_history_adapter_is_used_only_when_explicitly_passed():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
        )
    )
    prepended = _make_message(role="user", content="prepended")
    prior_live = _make_message(role="user", content="first live")
    prior_response = _make_message(role="assistant", content="first response")
    second_live = _make_message(role="user", content="second live")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.side_effect = [
        [prepended],
        [prepended, prior_live, prior_response],
    ]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )

    await target.send_prompt_async(
        message=prior_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )
    await target.send_prompt_async(
        message=second_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    assert target.prompt_sent == ["Turn 1:\nuser: prepended\nTurn 2:\nuser: first live", "second live"]


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_multi_turn_target_retains_history_after_response():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
        )
    )
    prepended = _make_message(role="user", content="prepended")
    first_live = _make_message(role="user", content="first live")
    second_live = _make_message(role="user", content="second live")
    prior_response = _make_message(role="assistant", content="first response")
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.side_effect = [
        [prepended],
        [prepended, first_live, prior_response],
    ]
    target._memory = mock_memory
    target._send_prompt_to_target_async = AsyncMock(  # type: ignore[method-assign]
        return_value=[_make_message(role="assistant", content="response")]
    )
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )

    await target.send_prompt_async(
        message=first_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )
    await target.send_prompt_async(
        message=second_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    first_payload, second_payload = target._send_prompt_to_target_async.await_args_list
    assert len(first_payload.kwargs["normalized_conversation"]) == 1
    assert [message.get_value() for message in second_payload.kwargs["normalized_conversation"]] == [
        "prepended",
        "first live",
        "first response",
        "second live",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_stateless_target_replays_only_seed_and_current_request():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    prepended = _make_message(role="user", content="prepended")
    first_live = _make_message(role="user", content="first live")
    first_response = _make_message(role="assistant", content="first response")
    second_live = _make_message(role="user", content="second live")
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.side_effect = [
        [prepended],
        [prepended, first_live, first_response],
    ]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])

    await target.send_prompt_async(
        message=first_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )
    await target.send_prompt_async(
        message=second_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    assert target.prompt_sent == [
        "Turn 1:\nuser: prepended\nTurn 2:\nuser: first live",
        "Turn 1:\nuser: prepended\nTurn 2:\nuser: second live",
    ]


@pytest.mark.parametrize("response_error", ["blocked", "empty", "processing"])
@pytest.mark.usefixtures("patch_central_database")
async def test_stateful_target_consumes_seed_after_provider_outcome(response_error: PromptResponseError):
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    prepended = _make_message(role="user", content="prepended")
    first_live = _make_message(role="user", content="first live")
    provider_response = _make_message(role="assistant", content=f"{response_error} response")
    provider_response.get_piece().response_error = response_error
    second_live = _make_message(role="user", content="second live")
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.side_effect = [
        [prepended],
        [prepended, first_live, provider_response],
    ]
    target._memory = mock_memory
    target._send_prompt_to_target_async = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[provider_response], [_make_message(role="assistant", content="second response")]]
    )
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )

    await target.send_prompt_async(
        message=first_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )
    await target.send_prompt_async(
        message=second_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )

    first_payload, second_payload = target._send_prompt_to_target_async.await_args_list
    assert "prepended" in first_payload.kwargs["normalized_conversation"][0].get_value()
    assert second_payload.kwargs["normalized_conversation"][-1].get_value() == "second live"


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_uses_custom_prepended_formatter():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    formatter = MagicMock(spec=MessageStringNormalizer)
    formatter.normalize_string_async = AsyncMock(return_value="CUSTOM HISTORY")
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(
        send_context=target_context,
        formatter=formatter,
    )

    result = await target._get_normalized_conversation_async(
        message=_make_message(role="user", content="live"),
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    assert result[0].get_value() == "CUSTOM HISTORY"
    formatter.normalize_string_async.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_rejects_non_text_converted_prepended_history():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    prepended = _make_message(role="user", content="original")
    prepended.get_piece().converted_value = "converted.png"
    prepended.get_piece().converted_value_data_type = "image_path"
    prepended.get_piece().converter_identifiers = [
        ComponentIdentifier(class_name="ImageConverter", class_module="tests")
    ]
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    with pytest.raises(ValueError, match="non-text output types.*image_path"):
        await target.send_prompt_async(
            message=_make_message(role="user", content="live"),
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_rejects_same_modality_non_text_conversion():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    prepended = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="original.png",
                converted_value="converted.png",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id="conv1",
            )
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    with pytest.raises(ValueError, match="non-text output types.*image_path"):
        await target.send_prompt_async(
            message=_make_message(role="user", content="live"),
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_allows_preexisting_non_text_history_with_converter_provenance():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    prepended = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="existing.png",
                converted_value="existing.png",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id="conv1",
                converter_identifiers=[ComponentIdentifier(class_name="PriorConverter", class_module="tests")],
            )
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    result = await target._get_normalized_conversation_async(
        message=_make_message(role="user", content="live"),
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    assert result[0].get_value() == "Turn 1:\nuser: [Image_path]\nTurn 2:\nuser: live"


@pytest.mark.usefixtures("patch_central_database")
async def test_non_editable_target_warns_when_non_text_history_becomes_a_placeholder(
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    prepended = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="existing.png",
                converted_value="existing.png",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                conversation_id="conv1",
            )
        ]
    )
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])

    with caplog.at_level(logging.WARNING, logger="pyrit.message_normalizer.history_squash_normalizer"):
        await target._get_normalized_conversation_async(
            message=_make_message(role="user", content="live"),
            normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
            send_context=target_context,
        )

    assert "image_path" in caplog.text
    assert "text placeholders" in caplog.text


@pytest.mark.usefixtures("patch_central_database")
async def test_target_normalization_failure_can_be_retried():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    formatter = MagicMock(spec=MessageStringNormalizer)
    formatter.normalize_string_async = AsyncMock(side_effect=[ValueError("format failed"), "formatted request"])
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(
        send_context=target_context,
        formatter=formatter,
    )
    live_request = _make_message(role="user", content="live")

    with pytest.raises(ValueError, match="format failed"):
        await target.send_prompt_async(
            message=live_request,
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )

    await target.send_prompt_async(
        message=live_request,
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )
    assert target.prompt_sent == ["formatted request"]


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_normalizer_retry_excludes_persisted_processing_exchange():
    target = MockPromptTarget()
    prompt_normalizer = PromptNormalizer()
    conversation_id = "processing-retry"
    successful_response = _make_message(role="assistant", content="successful response")
    retry_response = _make_message(role="assistant", content="retry response")

    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.side_effect = [[successful_response], RuntimeError("private provider failure"), [retry_response]]
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="successful request"),
            target=target,
            conversation_id=conversation_id,
        )
        with pytest.raises(Exception, match="Error sending prompt"):
            await prompt_normalizer.send_prompt_async(
                message=_make_message(role="user", content="failed request"),
                target=target,
                conversation_id=conversation_id,
            )
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="retry"),
            target=target,
            conversation_id=conversation_id,
        )

    retry_payload = send.await_args_list[2].kwargs["normalized_conversation"]
    assert [message.get_value() for message in retry_payload] == [
        "successful request",
        "successful response",
        "retry",
    ]
    persisted = list(CentralMemory.get_memory_instance().get_conversation_messages(conversation_id=conversation_id))
    processing_index = next(
        index for index, message in enumerate(persisted) if message.get_piece().response_error == "processing"
    )
    failed_request = persisted[processing_index - 1].get_piece()
    processing_error = persisted[processing_index].get_piece()
    assert failed_request.api_role == "user"
    assert processing_error.original_prompt_id != failed_request.id
    assert processing_error.sequence == failed_request.sequence + 1


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_normalizer_retry_excludes_persisted_unknown_exchange():
    target = MockPromptTarget()
    prompt_normalizer = PromptNormalizer()
    unknown_response = _make_message(role="assistant", content="unknown provider failure")
    unknown_response.get_piece().response_error = "unknown"
    retry_response = _make_message(role="assistant", content="retry response")

    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.side_effect = [[unknown_response], [retry_response]]
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="failed request"),
            target=target,
            conversation_id="unknown-retry",
        )
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="retry"),
            target=target,
            conversation_id="unknown-retry",
        )

    retry_payload = send.await_args_list[1].kwargs["normalized_conversation"]
    assert [message.get_value() for message in retry_payload] == ["retry"]


@pytest.mark.parametrize("response_error", ["blocked", "empty"])
@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_normalizer_retains_provider_round_trip(response_error: PromptResponseError):
    target = MockPromptTarget()
    prompt_normalizer = PromptNormalizer()
    provider_response = _make_message(role="assistant", content=f"{response_error} response")
    provider_response.get_piece().response_error = response_error
    next_response = _make_message(role="assistant", content="next response")

    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        send.side_effect = [[provider_response], [next_response]]
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="first request"),
            target=target,
            conversation_id=f"{response_error}-round-trip",
        )
        await prompt_normalizer.send_prompt_async(
            message=_make_message(role="user", content="second request"),
            target=target,
            conversation_id=f"{response_error}-round-trip",
        )

    second_payload = send.await_args_list[1].kwargs["normalized_conversation"]
    assert [message.get_value() for message in second_payload] == [
        "first request",
        f"{response_error} response",
        "second request",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_target_normalization_cancellation_propagates():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    formatter = MagicMock(spec=MessageStringNormalizer)
    formatter.normalize_string_async = AsyncMock(side_effect=asyncio.CancelledError())
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(
        send_context=target_context,
        formatter=formatter,
    )

    with pytest.raises(asyncio.CancelledError):
        await target.send_prompt_async(
            message=_make_message(role="user", content="live"),
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )
    assert target_context.target_invocation_count == 0
    assert not target_context.is_seed_consumed


@pytest.mark.usefixtures("patch_central_database")
async def test_rate_limit_cancellation_retains_stateful_seed_after_target_invocation():
    target = MockPromptTarget(rpm=1)
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )
    sleep_started = asyncio.Event()
    sleep_release = asyncio.Event()

    async def wait_for_rate_limit(delay: float) -> None:
        sleep_started.set()
        await sleep_release.wait()

    with patch("pyrit.prompt_target.common.utils.asyncio.sleep", side_effect=wait_for_rate_limit):
        send_task = asyncio.create_task(
            target.send_prompt_async(
                message=_make_message(role="user", content="live"),
                normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
                send_context=target_context,
            )
        )
        await sleep_started.wait()
        send_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send_task

    assert target_context.target_invocation_count == 1
    assert not target_context.is_seed_consumed


@pytest.mark.usefixtures("patch_central_database")
async def test_target_failure_retains_stateful_seed_after_normalization():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    target._send_prompt_to_target_async = AsyncMock(side_effect=RuntimeError("provider failed"))  # type: ignore[method-assign]
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )
    normalizer_overrides = _make_normalizer_overrides(send_context=target_context)

    with pytest.raises(RuntimeError, match="provider failed"):
        await target.send_prompt_async(
            message=_make_message(role="user", content="live"),
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )
    assert target_context.target_invocation_count == 1
    assert not target_context.is_seed_consumed


@pytest.mark.usefixtures("patch_central_database")
async def test_target_cancellation_retains_stateful_seed_and_releases_context():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    prepended = _make_message(role="user", content="prepended")
    first_live = _make_message(role="user", content="first live")
    second_live = _make_message(role="user", content="second live")
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation_messages.side_effect = [
        [prepended],
        [prepended, first_live],
    ]
    target._memory = mock_memory
    target_started = asyncio.Event()
    target_release = asyncio.Event()

    async def wait_in_target(*, normalized_conversation: list[Message]) -> list[Message]:
        target_started.set()
        await target_release.wait()
        return [_make_message(role="assistant", content="response")]

    target._send_prompt_to_target_async = AsyncMock(side_effect=wait_in_target)  # type: ignore[method-assign]
    target_context = _make_prepended_history_send_context(
        prepended_messages=[prepended],
        target_supports_multi_turn=True,
    )
    first_send = asyncio.create_task(
        target.send_prompt_async(
            message=first_live,
            normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
            send_context=target_context,
        )
    )
    await target_started.wait()
    first_send.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_send

    assert not target_context.is_seed_consumed
    assert target_context.target_invocation_count == 1

    target._send_prompt_to_target_async = AsyncMock(  # type: ignore[method-assign]
        return_value=[_make_message(role="assistant", content="second response")]
    )
    await target.send_prompt_async(
        message=second_live,
        normalizer_overrides=_make_normalizer_overrides(send_context=target_context),
        send_context=target_context,
    )
    assert target_context.is_seed_consumed
    assert target_context.target_invocation_count == 2
    payload = target._send_prompt_to_target_async.await_args.kwargs["normalized_conversation"]
    assert [message.get_value() for message in payload] == ["Turn 1:\nuser: prepended\nTurn 2:\nuser: second live"]


@pytest.mark.usefixtures("patch_central_database")
async def test_concurrent_sends_with_one_context_are_rejected():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_to_format(messages: list[Message]) -> str:
        started.set()
        await release.wait()
        return "formatted request"

    formatter = MagicMock(spec=MessageStringNormalizer)
    formatter.normalize_string_async = AsyncMock(side_effect=wait_to_format)
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(
        send_context=target_context,
        formatter=formatter,
    )
    first_send = asyncio.create_task(
        target.send_prompt_async(
            message=_make_message(role="user", content="first"),
            normalizer_overrides=normalizer_overrides,
            send_context=target_context,
        )
    )
    await started.wait()
    try:
        with pytest.raises(RuntimeError, match="Concurrent sends"):
            await target.send_prompt_async(
                message=_make_message(role="user", content="second"),
                normalizer_overrides=normalizer_overrides,
                send_context=target_context,
            )
    finally:
        release.set()

    await first_send
    assert target.prompt_sent == ["formatted request"]
    formatter.normalize_string_async.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_tokenizer_formatter_receives_live_request_before_generation_prompt():
    target = MockPromptTarget()
    target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
    mock_memory = MagicMock(spec=MemoryInterface)
    prepended = _make_message(role="user", content="prepended")
    mock_memory.get_conversation_messages.return_value = [prepended]
    target._memory = mock_memory
    tokenizer = MagicMock()
    tokenizer.apply_chat_template.return_value = "TOKENIZED REQUEST"
    formatter = TokenizerTemplateNormalizer(tokenizer=tokenizer)
    target_context = _make_prepended_history_send_context(prepended_messages=[prepended])
    normalizer_overrides = _make_normalizer_overrides(
        send_context=target_context,
        formatter=formatter,
    )

    result = await target._get_normalized_conversation_async(
        message=_make_message(role="user", content="live"),
        normalizer_overrides=normalizer_overrides,
        send_context=target_context,
    )

    tokenizer_messages = tokenizer.apply_chat_template.call_args.args[0]
    assert tokenizer_messages[-1] == {"role": "user", "content": "live"}
    assert tokenizer.apply_chat_template.call_args.kwargs["add_generation_prompt"] is True
    assert result[0].get_value() == "TOKENIZED REQUEST"
