# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from pyrit.models import JsonResponseConfig, Message
from pyrit.prompt_target import (
    FunctionTool,
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
