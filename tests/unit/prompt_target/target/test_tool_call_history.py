# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tool history must survive target serialization, not just response parsing."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from openai.types.chat import ChatCompletionMessage

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.models import Conversation, JsonResponseConfig, Message, MessagePiece
from pyrit.prompt_target import (
    CapabilityHandlingPolicy,
    CapabilityName,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
    TargetRequirements,
    TextTarget,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.chat_completions_message_builder import build_multimodal_chat_messages_async
from pyrit.prompt_target.common.chat_completions_response_parser import _build_tool_pieces
from pyrit.prompt_target.common.tool_call_history import (
    TOOL_CALL_INPUT_MODALITIES,
    parse_function_call,
    parse_function_call_output,
)
from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _call_piece(*, call_id: str = "call_1", chat_shape: bool = False) -> MessagePiece:
    payload = (
        {"type": "function", "id": call_id, "function": {"name": "lookup", "arguments": '{"key":"new"}'}}
        if chat_shape
        else {"type": "function_call", "call_id": call_id, "name": "lookup", "arguments": '{"key":"new"}'}
    )
    return MessagePiece(
        role="simulated_assistant",
        original_value='{"old":"must not be sent"}',
        converted_value=json.dumps(payload),
        original_value_data_type="function_call",
        conversation_id="history",
    )


def _output_piece(*, call_id: str = "call_1") -> MessagePiece:
    return MessagePiece(
        role="simulated_tool",
        original_value='{"old":"must not be sent"}',
        converted_value=json.dumps(
            {"type": "function_call_output", "call_id": call_id, "output": {"value": "synthetic"}}
        ),
        original_value_data_type="function_call_output",
        conversation_id="history",
    )


def _target(target_type: type[PromptTarget], *, enabled: bool = True) -> PromptTarget:
    return target_type(
        model_name="unknown",
        endpoint="https://example.invalid",
        api_key="not-a-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_multi_message_pieces=True,
                supports_editable_history=True,
                input_modalities=frozenset({frozenset({"text"})})
                | (TOOL_CALL_INPUT_MODALITIES if enabled else frozenset()),
            )
        ),
    )


@pytest.mark.parametrize("chat_shape", [False, True])
@pytest.mark.parametrize("target_type", [OpenAIChatTarget, LiteLLMChatTarget, OpenAIResponseTarget])
async def test_tool_history_provider_payload(target_type: type[PromptTarget], chat_shape: bool) -> None:
    target = _target(target_type)
    call = _call_piece(chat_shape=chat_shape)
    output = _output_piece()
    conversation = [call.to_message(), output.to_message(), Message.from_prompt(prompt="Continue.", role="user")]
    target._validate_request(normalized_conversation=conversation)
    if isinstance(target, OpenAIResponseTarget):
        body = await target._construct_request_body_async(
            conversation=conversation, json_config=JsonResponseConfig.from_metadata(metadata={})
        )
        assert body["input"][0] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"key":"new"}',
        }
        assert body["input"][1] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"value":"synthetic"}',
        }
    else:
        assert isinstance(target, (OpenAIChatTarget, LiteLLMChatTarget))
        messages = await target._build_chat_messages_async(conversation)
        assert messages[0] == {
            "role": "assistant",
            "tool_calls": [
                {"type": "function", "id": "call_1", "function": {"name": "lookup", "arguments": '{"key":"new"}'}}
            ],
        }
        assert messages[1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"value":"synthetic"}'}
    assert call.original_value == '{"old":"must not be sent"}'
    assert output.original_value == '{"old":"must not be sent"}'


async def test_chat_history_groups_multiple_calls_and_separates_outputs() -> None:
    text = MessagePiece(role="simulated_assistant", original_value="Looking up values.", conversation_id="history")
    messages = await build_multimodal_chat_messages_async(
        [
            Message(message_pieces=[text, _call_piece(), _call_piece(call_id="call_2")]),
            Message(message_pieces=[_output_piece(), _output_piece(call_id="call_2")]),
        ]
    )
    assert messages[0]["content"] == [{"type": "text", "text": "Looking up values."}]
    assert [call["id"] for call in messages[0]["tool_calls"]] == ["call_1", "call_2"]
    assert [message["tool_call_id"] for message in messages[1:]] == ["call_1", "call_2"]


@pytest.mark.parametrize("piece", [_call_piece(), _output_piece()])
async def test_tool_history_rejected_before_provider_send(piece: MessagePiece) -> None:
    target = _target(OpenAIChatTarget, enabled=False)
    target._memory.add_conversation_to_memory(
        conversation=Conversation(conversation_id="history", target_identifier=target.get_identifier())
    )
    target._memory.add_message_to_memory(request=piece.to_message())
    request = MessagePiece(role="user", original_value="Continue.", conversation_id="history").to_message()
    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="does not support tool-history modality"):
            await target.send_prompt_async(message=request)
    send.assert_not_called()


def test_current_tool_result_supported_by_modalities() -> None:
    target = _target(OpenAIChatTarget)
    target._validate_request(normalized_conversation=[_output_piece().to_message()])
    assert "function_call_output" in target.capabilities.supported_input_modalities
    TargetRequirements(required_input_modalities=TOOL_CALL_INPUT_MODALITIES).validate(target=target)
    with pytest.raises(ValueError, match="input modality"):
        TargetRequirements(required_input_modalities=TOOL_CALL_INPUT_MODALITIES).validate(
            target=_target(OpenAIChatTarget, enabled=False)
        )


def test_tool_capability_defaults_and_model_adapter_intersection() -> None:
    assert "function_call" not in TargetCapabilities().supported_input_modalities
    assert "function_call" in OpenAIResponseTarget.get_default_configuration().capabilities.supported_input_modalities
    assert "function_call" not in OpenAIChatTarget.get_default_configuration().capabilities.supported_input_modalities
    assert (
        "function_call" in OpenAIChatTarget.get_default_configuration("gpt-5").capabilities.supported_input_modalities
    )
    assert "function_call" not in TextTarget.get_default_configuration("gpt-5").capabilities.supported_input_modalities
    target = OpenAIChatTarget(model_name="gpt-4o", endpoint="https://example.invalid", api_key="not-a-key")
    assert "function_call" in target.capabilities.supported_input_modalities
    target.apply_capabilities(capabilities=TargetCapabilities())
    assert "function_call" not in target.capabilities.supported_input_modalities
    assert "function_call" not in _target(OpenAIResponseTarget, enabled=False).capabilities.supported_input_modalities


def test_backend_snapshot_preserves_tool_capability() -> None:
    target = _target(OpenAIChatTarget)
    result = target_object_to_instance("tool-target", target)
    assert "function_call_output" in result.model_dump()["capabilities"]["supported_input_modalities"]
    assert "supports_tool_calls" not in result.model_dump()["capabilities"]


async def test_tool_history_can_adapt_without_changing_stored_evidence() -> None:
    target = _target(OpenAIChatTarget, enabled=False)
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(),
        policy=CapabilityHandlingPolicy(behaviors={CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT}),
    )
    call = _call_piece()
    output = _output_piece()
    for piece in (call, output):
        piece.original_value = piece.converted_value
    target._memory.add_conversation_to_memory(
        conversation=Conversation(conversation_id="history", target_identifier=target.get_identifier())
    )
    for piece in (call, output):
        target._memory.add_message_to_memory(request=piece.to_message())
    request = MessagePiece(role="user", original_value="Continue.", conversation_id="history").to_message()
    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=[]) as send:
        await target.send_prompt_async(message=request)
    normalized = send.call_args.kwargs["normalized_conversation"]
    assert len(normalized) == 1
    assert "[Function_call_output]" in normalized[0].get_piece().converted_value
    stored = target._memory.get_conversation_messages(conversation_id="history")
    assert stored[1].get_piece().role == "simulated_tool"
    assert stored[1].get_piece().converted_value == output.converted_value
    with pytest.raises(ValueError, match="input modality"):
        TargetRequirements(
            native_required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY}),
            required_input_modalities=TOOL_CALL_INPUT_MODALITIES,
        ).validate(target=target)


@pytest.mark.parametrize("value", ["[]", "null", "{"])
def test_malformed_call_rejected(value: str) -> None:
    piece = _call_piece()
    piece.converted_value = value
    with pytest.raises(ValueError):
        parse_function_call(piece)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "function_call", "call_id": "", "name": "lookup", "arguments": "{}"},
        {"type": "function_call", "call_id": "c", "name": "lookup", "arguments": {}},
        {"type": "function", "id": "c", "function": "lookup"},
    ],
)
def test_call_requires_structured_fields(payload: dict[str, object]) -> None:
    piece = _call_piece()
    piece.converted_value = json.dumps(payload)
    with pytest.raises(ValueError):
        parse_function_call(piece)


def test_missing_tool_output_rejected() -> None:
    piece = _output_piece()
    piece.converted_value = '{"type":"function_call_output","call_id":"c"}'
    with pytest.raises(ValueError, match="output field"):
        parse_function_call_output(piece)


async def test_chat_parser_output_can_be_replayed() -> None:
    payload = {"type": "function", "id": "call_1", "function": {"name": "lookup", "arguments": "{}"}}
    response = ChatCompletionMessage.model_validate({"role": "assistant", "tool_calls": [payload]})
    pieces = _build_tool_pieces(message=response, request=MessagePiece(role="user", original_value="Look up a value."))
    messages = await build_multimodal_chat_messages_async([Message(message_pieces=pieces)])
    assert messages == [{"role": "assistant", "tool_calls": [payload]}]


async def test_tool_result_cannot_be_mixed_with_plain_text() -> None:
    result = _output_piece()
    text = MessagePiece(role="simulated_tool", original_value="extra", conversation_id="history")
    with pytest.raises(ValueError, match="only function_call_output"):
        await build_multimodal_chat_messages_async([Message(message_pieces=[result, text])])
