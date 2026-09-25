# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models import ChatMessageRole, Message, MessagePiece, PromptDataType
from pyrit.models.messages.tool_content import (
    FunctionCallContent,
    FunctionOutputContent,
    validate_tool_conversation,
)


def _message(*, role: ChatMessageRole, data_type: PromptDataType, payload: object) -> Message:
    return MessagePiece(
        role=role,
        original_value="original must not be parsed",
        converted_value=json.dumps(payload),
        original_value_data_type=data_type,
    ).to_message()


def _call(*, call_id: str = "call-1", role: ChatMessageRole = "simulated_assistant") -> Message:
    return _message(
        role=role,
        data_type="function_call",
        payload={"call_id": call_id, "name": "lookup", "arguments": "{}"},
    )


def _output(*, call_id: str = "call-1", role: ChatMessageRole = "simulated_tool") -> Message:
    return _message(
        role=role,
        data_type="function_call_output",
        payload={"call_id": call_id, "output": {"value": 1}},
    )


@pytest.mark.parametrize("nested", [True, False])
def test_function_content_retains_arguments_and_extensions(nested: bool) -> None:
    function = {"name": "lookup", "arguments": '{ "key": "value" }', "provider_field": 1}
    payload = {"id": "call-1", "function": function} if nested else {"call_id": "call-1", **function}
    call = FunctionCallContent.model_validate(payload)
    assert call.validated_call_id() == "call-1"
    assert call.validated_function().arguments == '{ "key": "value" }'
    assert call.model_dump(exclude_unset=True) == payload


@pytest.mark.parametrize("arguments", ["{", "[]", "null", '"text"', "1"])
@pytest.mark.parametrize("nested", [True, False])
def test_function_content_rejects_invalid_arguments(arguments: str, nested: bool) -> None:
    function = {"name": "lookup", "arguments": arguments}
    payload = {"id": "call-1", "function": function} if nested else {"call_id": "call-1", **function}
    with pytest.raises(ValueError):
        FunctionCallContent.model_validate(payload).validated_call_id()


@pytest.mark.parametrize(
    "fields",
    [
        {"call_id": ""},
        {"call_id": " "},
        {"call_id": 1},
        {"name": " "},
        {"arguments": {}},
        {"type": "custom"},
    ],
)
def test_function_content_rejects_invalid_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FunctionCallContent.model_validate(
            {"call_id": "call-1", "name": "lookup", "arguments": "{}", **fields}
        ).validated_call_id()


@pytest.mark.parametrize("payload", [{}, {"call_id": " "}, {"call_id": "call-1"}])
def test_function_output_requires_id_and_output(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FunctionOutputContent.model_validate(payload)


def test_tool_conversation_preserves_synthetic_values_and_pending_calls() -> None:
    history = [_call(), _call(call_id="call-2"), _output(), _output(call_id="call-2"), _call(call_id="pending")]
    original = [message.model_dump() for message in history]
    validate_tool_conversation([])
    validate_tool_conversation(history)
    assert [message.model_dump() for message in history] == original


@pytest.mark.parametrize(
    "history",
    [
        [_output()],
        [_output(), _call()],
        [_call(), _output(call_id="unknown")],
        [_call(), _output(), _output()],
    ],
)
def test_tool_conversation_requires_preceding_unanswered_call(history: list[Message]) -> None:
    with pytest.raises(ValueError, match="preceding, unanswered"):
        validate_tool_conversation(history)


def test_tool_conversation_rejects_duplicate_calls() -> None:
    with pytest.raises(ValueError, match="Duplicate function call ID"):
        validate_tool_conversation([_call(), _call()])


@pytest.mark.parametrize(
    "history",
    [
        [_call(role="user")],
        [_call(), _output(role="assistant")],
        [_message(role="tool", data_type="tool_call", payload={})],
    ],
)
def test_tool_conversation_checks_api_roles(history: list[Message]) -> None:
    with pytest.raises(ValueError, match="role"):
        validate_tool_conversation(history)


def test_tool_conversation_accepts_real_roles() -> None:
    validate_tool_conversation([_call(role="assistant"), _output(role="tool")])


@pytest.mark.parametrize("payload", [None, [], "text"])
def test_provider_tool_content_requires_json_object(payload: object) -> None:
    message = _message(role="simulated_assistant", data_type="tool_call", payload=payload)
    with pytest.raises(ValueError, match="JSON object"):
        validate_tool_conversation([message])
