# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from pydantic import ValidationError

from pyrit.models.chat_message import (
    ChatMessage,
    ChatMessageListDictContent,
    ChatMessagesDataset,
    ToolCall,
)


def test_tool_call_init():
    tc = ToolCall(id="call_1", type="function", function="get_weather")
    assert tc.id == "call_1"
    assert tc.type == "function"
    assert tc.function == "get_weather"


def test_tool_call_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ToolCall(id="call_1", type="function", function="get_weather", extra="bad")


def test_chat_message_init_with_string_content():
    msg = ChatMessage(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"
    assert msg.name is None
    assert msg.tool_calls is None
    assert msg.tool_call_id is None


def test_chat_message_init_with_list_content():
    parts = [{"type": "text", "text": "hello"}, {"type": "image_url", "url": "http://img.png"}]
    msg = ChatMessage(role="assistant", content=parts)
    assert msg.content == parts


def test_chat_message_init_with_all_fields():
    tc = ToolCall(id="call_1", type="function", function="lookup")
    msg = ChatMessage(
        role="assistant",
        content="result",
        name="helper",
        tool_calls=[tc],
        tool_call_id="call_1",
    )
    assert msg.name == "helper"
    assert msg.tool_calls == [tc]
    assert msg.tool_call_id == "call_1"


def test_chat_message_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="hi", extra_field="bad")


def test_chat_message_invalid_role():
    with pytest.raises(ValidationError):
        ChatMessage(role="invalid_role", content="hi")


def test_chat_message_to_json():
    msg = ChatMessage(role="user", content="test")
    json_str = msg.to_json()
    parsed = json.loads(json_str)
    assert parsed["role"] == "user"
    assert parsed["content"] == "test"


def test_chat_message_to_dict_excludes_none():
    msg = ChatMessage(role="user", content="test")
    d = msg.to_dict()
    assert "name" not in d
    assert "tool_calls" not in d
    assert "tool_call_id" not in d
    assert d["role"] == "user"
    assert d["content"] == "test"


def test_chat_message_from_json():
    original = ChatMessage(role="system", content="you are helpful")
    json_str = original.to_json()
    restored = ChatMessage.from_json(json_str)
    assert restored.role == original.role
    assert restored.content == original.content


def test_chat_message_from_json_roundtrip_with_tool_calls():
    tc = ToolCall(id="c1", type="function", function="fn")
    original = ChatMessage(role="assistant", content="ok", tool_calls=[tc], tool_call_id="c1")
    restored = ChatMessage.from_json(original.to_json())
    assert restored.tool_calls[0].id == "c1"
    assert restored.tool_call_id == "c1"


@pytest.mark.parametrize("role", ["system", "user", "assistant", "simulated_assistant", "tool", "developer"])
def test_chat_message_accepts_all_valid_roles(role):
    msg = ChatMessage(role=role, content="test")
    assert msg.role == role


def test_chat_message_list_dict_content_deprecated(capsys):
    msg = ChatMessageListDictContent(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_chat_messages_dataset_init():
    msgs = [[ChatMessage(role="user", content="hi"), ChatMessage(role="assistant", content="hello")]]
    dataset = ChatMessagesDataset(name="test_ds", description="A test dataset", list_of_chat_messages=msgs)
    assert dataset.name == "test_ds"
    assert dataset.description == "A test dataset"
    assert len(dataset.list_of_chat_messages) == 1
    assert len(dataset.list_of_chat_messages[0]) == 2


def test_chat_messages_dataset_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ChatMessagesDataset(
            name="ds",
            description="desc",
            list_of_chat_messages=[],
            extra="bad",
        )
