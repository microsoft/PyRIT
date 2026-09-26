# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError
from tenacity import wait_none

from pyrit.exceptions import RateLimitException
from pyrit.models import JsonResponseConfig, Message, MessagePiece
from pyrit.prompt_target import (
    FunctionTool,
    MCPToolProvider,
    OpenAIResponseTarget,
    ToolProvider,
    collect_tools_async,
    tool,
)


@tool
async def add(x: int, y: int = 1) -> int:  # pyrit-async-suffix-exempt
    """
    Add two numbers.

    Args:
        x: The first number.
        y: The second number.

    Returns:
        The sum.
    """
    return x + y


def test_tool_derives_definition_from_function() -> None:
    assert isinstance(add, FunctionTool)
    assert add.name == "add"
    assert add.description.startswith("Add two numbers.")
    assert add.parameters == {
        "additionalProperties": False,
        "properties": {
            "x": {"title": "X", "type": "integer"},
            "y": {"default": 1, "title": "Y", "type": "integer"},
        },
        "required": ["x"],
        "type": "object",
    }


async def test_tool_validates_arguments_and_returns_scalar() -> None:
    assert await add.execute_async(arguments={"x": 2}) == 3

    with pytest.raises(ValidationError):
        await add.execute_async(arguments={"x": 2, "unexpected": True})


async def test_tool_preserves_validated_structured_argument_type() -> None:
    class Location(BaseModel):
        city: str

    async def get_weather(*, location: Location) -> str:
        assert isinstance(location, Location)
        return location.city

    weather_tool = FunctionTool(function=get_weather)

    assert await weather_tool.execute_async(arguments={"location": {"city": "Seattle"}}) == "Seattle"


async def test_openai_response_target_advertises_and_executes_tool(patch_central_database) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[add],
        extra_body_parameters={"tools": [{"type": "web_search_preview"}]},
    )

    body = await target._construct_request_body_async(
        conversation=[Message.from_prompt(prompt="Add 2 and 3", role="user")],
        json_config=JsonResponseConfig(enabled=False),
    )
    result = await target._execute_call_section_async(
        {
            "type": "function_call",
            "name": "add",
            "arguments": '{"x": 2, "y": 3}',
            "call_id": "call-1",
        }
    )

    assert body["tools"] == [
        {"type": "web_search_preview"},
        {
            "type": "function",
            "name": "add",
            "description": add.description,
            "parameters": add.parameters,
            "strict": False,
        },
    ]
    assert result == 5


def test_openai_response_target_without_tools_preserves_identifier(patch_central_database) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
    )

    identifier = target._build_identifier()

    assert "tools" not in identifier.params
    assert "tool_providers" not in identifier.params
    assert "execute_tools" not in identifier.params


def test_single_response_mode_has_distinct_identity(patch_central_database) -> None:
    kwargs = {"model_name": "gpt-4", "endpoint": "https://mock.azure.com", "api_key": "mock-key"}
    default = OpenAIResponseTarget(**kwargs).get_identifier()
    enabled = OpenAIResponseTarget(**kwargs, execute_tools=True).get_identifier()
    disabled = OpenAIResponseTarget(**kwargs, execute_tools=False).get_identifier()
    assert default.hash == enabled.hash
    assert disabled.hash != default.hash
    assert disabled.params["execute_tools"] is False


def test_openai_response_target_identifier_includes_advertised_tool_definition(patch_central_database) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[add],
    )

    identifier = target._build_identifier()

    assert identifier.params["tools"] == [
        {
            "type": "function",
            "name": "add",
            "description": add.description,
            "parameters": add.parameters,
            "strict": False,
        }
    ]


async def test_identifier_does_not_depend_on_tool_discovery(patch_central_database) -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])
    targets = [
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.azure.com",
            api_key="mock-key",
            tool_providers=[provider],
        )
        for _ in range(2)
    ]
    before = targets[0].get_identifier()
    for target in targets:
        await target._initialize_tools_async()

    assert before.hash == targets[0]._build_identifier().hash == targets[1].get_identifier().hash
    assert targets[1]._tools == [add]
    assert "tools" not in targets[1].get_identifier().params


@pytest.mark.parametrize("from_provider", [False, True])
@pytest.mark.parametrize("legacy_registration", ["callback", "declaration"])
async def test_send_rejects_conflicting_tool_registrations(
    patch_central_database, from_provider: bool, legacy_registration: str
) -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])
    callback = AsyncMock()
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[] if from_provider else [add],
        tool_providers=[provider] if from_provider else [],
        custom_functions={"add": callback} if legacy_registration == "callback" else None,
        extra_body_parameters=(
            {"tools": [{"type": "function", "name": "add", "parameters": add.parameters}]}
            if legacy_registration == "declaration"
            else None
        ),
    )
    with patch.object(target, "_handle_openai_request_async", new_callable=AsyncMock) as send:
        for _ in range(2):
            with pytest.raises(ValueError, match="Tools conflict.*add"):
                await target.send_prompt_async(message=Message.from_prompt(prompt="Add", role="user"))
        send.assert_not_awaited()
    callback.assert_not_awaited()
    assert not target._tools_initialized


async def test_legacy_declaration_and_callback_remain_compatible(patch_central_database) -> None:
    callback = AsyncMock(return_value={"value": 3})
    declaration = {"type": "function", "name": "legacy_add", "parameters": add.parameters}
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[add],
        custom_functions={"legacy_add": callback},
        extra_body_parameters={"tools": [declaration]},
    )
    body = await target._construct_request_body_async(
        conversation=[Message.from_prompt(prompt="Add", role="user")],
        json_config=JsonResponseConfig(enabled=False),
    )
    result = await target._execute_call_section_async({"name": "legacy_add", "arguments": '{"x": 2}'})
    assert body["tools"][0] == declaration
    assert len(body["tools"]) == 2
    assert result == {"value": 3}
    callback.assert_awaited_once_with({"x": 2})


async def test_model_retries_pace_each_request_without_repeating_tools(patch_central_database) -> None:
    executed: list[int] = []

    async def record_async(*, value: int) -> int:
        executed.append(value)
        return value

    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[FunctionTool(function=record_async)],
        max_requests_per_minute=30,
    )
    calls = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=json.dumps(
                    {
                        "type": "function_call",
                        "call_id": f"call-{value}",
                        "name": "record_async",
                        "arguments": json.dumps({"value": value}),
                    }
                ),
                original_value_data_type="function_call",
            )
            for value in (1, 2)
        ]
    )
    final = Message.from_prompt(prompt="Done", role="assistant")
    with (
        patch("pyrit.prompt_target.common.utils.asyncio.sleep", new_callable=AsyncMock) as sleep,
        patch.object(target._send_model_request_async.retry, "wait", wait_none()),
        patch.object(
            target,
            "_handle_openai_request_async",
            new_callable=AsyncMock,
            side_effect=[calls, RateLimitException(message="retry continuation"), final],
        ) as send,
    ):
        result = await target.send_prompt_async(message=Message.from_prompt(prompt="Record both", role="user"))

    assert executed == [1, 2]
    assert [message.api_role for message in result] == ["assistant", "tool", "tool", "assistant"]
    assert [json.loads(message.get_piece().original_value)["call_id"] for message in result[1:3]] == [
        "call-1",
        "call-2",
    ]
    assert send.await_count == 3
    assert sum(call.args == (2,) for call in sleep.await_args_list) == 3
    target.validate_tool_history(result)
    replay = await target._build_input_for_multi_modal_async(result)
    assert [item["call_id"] for item in replay if item.get("type") == "function_call_output"] == [
        "call-1",
        "call-2",
    ]


async def test_single_response_advertises_tools_without_executing_calls(patch_central_database) -> None:
    lifecycle: list[str] = []
    provider = MagicMock(spec=MCPToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])

    @asynccontextmanager
    async def scope_async() -> AsyncIterator[None]:
        lifecycle.append("enter")
        try:
            yield
        finally:
            lifecycle.append("exit")

    provider.execution_scope_async = scope_async
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tool_providers=[provider],
        execute_tools=False,
        extra_body_parameters={"tools": [{"type": "web_search_preview"}]},
    )
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=json.dumps(
                    {"type": "function_call", "call_id": f"call-{index}", "name": "add", "arguments": '{"x":2}'}
                ),
                original_value_data_type="function_call",
            )
            for index in (1, 2)
        ]
    )
    with (
        patch.object(target, "_handle_openai_request_async", new_callable=AsyncMock, return_value=response) as send,
        patch.object(add, "execute_async", new_callable=AsyncMock) as execute,
        patch.object(target._client.responses, "create", new_callable=AsyncMock) as create,
    ):
        result = await target.send_prompt_async(message=Message.from_prompt(prompt="Add", role="user"))
        await send.call_args.kwargs["api_call"]()
    assert result == [response]
    assert len(result[0].message_pieces) == 2
    send.assert_awaited_once()
    execute.assert_not_called()
    provider.get_tools_async.assert_awaited_once()
    assert lifecycle == ["enter", "exit"]
    assert [item["type"] for item in create.call_args.kwargs["tools"]] == ["web_search_preview", "function"]


async def test_collect_tools_async_preserves_direct_and_provider_order() -> None:
    async def subtract(*, x: int, y: int) -> int:  # pyrit-async-suffix-exempt
        return x - y

    discovered_tool = FunctionTool(function=subtract)
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[discovered_tool])
    collected = await collect_tools_async(tools=[add], providers=[provider])

    assert [configured_tool.name for configured_tool in collected] == ["add", "subtract"]
    provider.get_tools_async.assert_awaited_once()
    assert await collected[1].execute_async(arguments={"x": 5, "y": 3}) == 2


async def test_collect_tools_async_rejects_provider_name_conflict() -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])
    with pytest.raises(ValueError, match="Duplicate tool name: add"):
        await collect_tools_async(tools=[add], providers=[provider])


def test_function_tool_rejects_sync_function() -> None:
    def sync_function(value: int) -> int:
        return value

    with pytest.raises(TypeError, match="must be async"):
        FunctionTool(function=sync_function)  # type: ignore[arg-type]


def test_function_tool_rejects_untyped_parameter() -> None:
    async def untyped_async(value):  # pyrit-async-suffix-exempt
        return value

    with pytest.raises(TypeError, match="type annotation"):
        FunctionTool(function=untyped_async)


def test_function_tool_rejects_unresolvable_local_annotation() -> None:
    class LocalArgument(BaseModel):
        value: str

    async def local_tool(*, argument: "LocalArgument") -> str:
        return argument.value

    with pytest.raises(TypeError, match="Move locally defined annotation types to module scope"):
        FunctionTool(function=local_tool)
