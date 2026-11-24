# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError
from tenacity import wait_none

from pyrit.exceptions import RateLimitException
from pyrit.models import JsonResponseConfig, Message, MessagePiece, ToolExecutionMetadata
from pyrit.prompt_target import (
    FunctionTool,
    LiteLLMChatTarget,
    MCPToolProvider,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    ToolProvider,
    collect_tools_async,
    tool,
)
from pyrit.prompt_target.common.tool_call_history import TOOL_CALL_INPUT_MODALITIES
from pyrit.registry import TargetRegistry


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


@pytest.mark.parametrize("arguments", [{"x": "two"}, {"y": 3}, {"x": 2, "unexpected": True}])
def test_function_tool_validate_arguments_rejects_mismatched_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        add.validate_arguments(arguments=arguments)


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
    assert result.output == 5
    assert result.invoked is True


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


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, OpenAIResponseTarget])
def test_openai_target_copy_keeps_tools_and_providers(target_class: type[OpenAIChatTarget]) -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    source = target_class(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[add],
        tool_providers=[provider],
        execute_tools=False,
        max_tool_rounds=3,
    )

    copy = TargetRegistry().recreate_instance(source=source, params={})

    assert list(copy._direct_tools) == [add]
    assert copy._tool_providers == [provider]
    assert copy._execute_tools is False
    assert copy._max_tool_rounds == 3
    assert copy.get_identifier().hash == source.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, OpenAIResponseTarget])
def test_openai_target_copy_adds_tools_to_a_tool_less_source(target_class: type[OpenAIChatTarget]) -> None:
    source = target_class(model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key")

    copy = TargetRegistry().recreate_instance(source=source, params={"tools": [add], "max_tool_rounds": 2})

    assert list(copy._direct_tools) == [add]
    assert copy._max_tool_rounds == 2
    assert not source._direct_tools
    assert copy.get_identifier().params["tools"]


def _chat_tool_target(
    target_class: type[OpenAIChatTarget | LiteLLMChatTarget], *, model: str = "gpt-4o", **kwargs: Any
) -> OpenAIChatTarget | LiteLLMChatTarget:
    if target_class is LiteLLMChatTarget:
        return LiteLLMChatTarget(model_name=f"openai/{model}", **kwargs)
    return OpenAIChatTarget(model_name=model, endpoint="https://mock.azure.com", api_key="mock-key", **kwargs)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, LiteLLMChatTarget])
@pytest.mark.parametrize(
    ("nested", "match"),
    [({"n": 2}, "n=1"), ({"tools": []}, r"extra_body_parameters\['tools'\]")],
)
def test_chat_tool_targets_reject_nested_settings_that_override_local_tools(
    *, target_class: type[OpenAIChatTarget | LiteLLMChatTarget], nested: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _chat_tool_target(target_class, tools=[add], extra_body_parameters={"extra_body": nested})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, LiteLLMChatTarget])
@pytest.mark.parametrize(
    ("settings", "nested"),
    [({"tools": [add], "execute_tools": False}, {"n": 2}), ({}, {"n": 2, "tools": []})],
)
def test_chat_tool_targets_accept_nested_settings_without_local_execution(
    *, target_class: type[OpenAIChatTarget | LiteLLMChatTarget], settings: dict[str, Any], nested: dict[str, Any]
) -> None:
    _chat_tool_target(target_class, **settings, extra_body_parameters={"extra_body": nested})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, LiteLLMChatTarget])
@pytest.mark.parametrize("with_tools", [False, True])
def test_chat_tool_targets_accept_tool_history_from_their_own_tools(
    *, target_class: type[OpenAIChatTarget | LiteLLMChatTarget], with_tools: bool
) -> None:
    call = {"type": "function", "id": "call-1", "function": {"name": "add", "arguments": '{"x": 1}'}}
    result = {"type": "function_call_output", "call_id": "call-1", "output": "2"}
    conversation = [
        Message.from_prompt(prompt="Add", role="user"),
        MessagePiece(
            role="assistant", original_value=json.dumps(call), original_value_data_type="function_call"
        ).to_message(),
        MessagePiece(
            role="tool", original_value=json.dumps(result), original_value_data_type="function_call_output"
        ).to_message(),
        Message.from_prompt(prompt="Again", role="user"),
    ]
    target = _chat_tool_target(target_class, model="my-deployment", tools=[add] if with_tools else None)

    assert not target.capabilities.input_modalities & TOOL_CALL_INPUT_MODALITIES
    if with_tools:
        target._validate_request(normalized_conversation=conversation)
    else:
        with pytest.raises(ValueError, match="tool-history modality 'function_call'"):
            target._validate_request(normalized_conversation=conversation)


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


async def test_openai_response_target_reports_advertised_function_tools(patch_central_database) -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])
    declaration = {
        "type": "function",
        "name": "send_email",
        "description": "Send an email.",
        "parameters": {"type": "object", "properties": {"to": {"type": "string"}}},
    }
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tool_providers=[provider],
        custom_functions={"send_email": AsyncMock()},
        extra_body_parameters={"tools": [declaration, {"type": "web_search_preview"}]},
    )

    definitions = await target.get_tool_definitions_async()

    assert [definition.model_dump() for definition in definitions] == [
        {"name": "send_email", "description": "Send an email.", "parameters": declaration["parameters"]},
        {"name": "add", "description": add.description, "parameters": add.parameters},
    ]
    provider.get_tools_async.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "build_target",
    [
        lambda body: OpenAIChatTarget(
            model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", extra_body_parameters=body
        ),
        lambda body: LiteLLMChatTarget(model_name="custom/model", extra_body_parameters=body),
    ],
    ids=["openai_chat", "litellm"],
)
async def test_chat_targets_report_declared_function_tools(build_target) -> None:
    parameters = {"type": "object", "properties": {"to": {"type": "string"}}}
    declaration = {
        "type": "function",
        "function": {"name": "send_email", "description": "Send.", "parameters": parameters},
    }
    target = build_target({"tools": [declaration, {"type": "web_search_preview"}]})

    definitions = await target.get_tool_definitions_async()

    assert [definition.model_dump() for definition in definitions] == [
        {"name": "send_email", "description": "Send.", "parameters": parameters}
    ]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "build_target",
    [
        lambda body: OpenAIChatTarget(
            model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", extra_body_parameters=body
        ),
        lambda body: LiteLLMChatTarget(model_name="custom/model", extra_body_parameters=body),
    ],
    ids=["openai_chat", "litellm"],
)
async def test_null_tool_declarations_mean_no_tools(build_target) -> None:
    target = build_target({"tools": None})

    assert await target.get_tool_definitions_async() == []
    assert not target.may_advertise_tools()


async def test_litellm_target_reports_configured_tools(patch_central_database) -> None:
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[add])

    target = LiteLLMChatTarget(model_name="custom/model", tool_providers=[provider])

    definitions = await target.get_tool_definitions_async()

    assert [definition.name for definition in definitions] == ["add"]
    assert target.may_advertise_tools()


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
    assert result.output == {"value": 3}
    assert result.invoked is True
    callback.assert_awaited_once_with({"x": 2})


async def test_non_object_arguments_record_no_invocation_async(patch_central_database) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[add],
        fail_on_missing_function=False,
    )
    with patch.object(add, "execute_async", new_callable=AsyncMock) as execute:
        result = await target._execute_call_section_async({"name": "add", "arguments": "[]"})

    execute.assert_not_awaited()
    assert result.invoked is False
    assert result.output == {"error": "malformed_arguments", "function": "add", "raw_arguments": "[]"}


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
    async def scope_async() -> AsyncGenerator[None, None]:
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


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("arguments", ["{not-json", "[]", "null"])
@pytest.mark.parametrize("strict", [False, True])
async def test_malformed_arguments_reach_model_as_tool_error(*, arguments: str, strict: bool) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://example.invalid",
        api_key="not-a-key",
        tools=[add],
        fail_on_missing_function=strict,
    )
    call = MessagePiece(
        role="assistant",
        original_value=json.dumps(
            {"type": "function_call", "call_id": "call-1", "name": "add", "arguments": arguments}
        ),
        original_value_data_type="function_call",
    ).to_message()
    final = Message.from_prompt(prompt="Please supply valid arguments.", role="assistant")
    with (
        patch.object(target, "_handle_openai_request_async", new_callable=AsyncMock, side_effect=[call, final]) as send,
        patch.object(target._client.responses, "create", new_callable=AsyncMock) as create,
        patch.object(add, "execute_async", new_callable=AsyncMock) as execute,
    ):
        if strict:
            with pytest.raises(ValueError, match="arguments|Arguments"):
                await target.send_prompt_async(message=Message.from_prompt(prompt="Add", role="user"))
            send.assert_awaited_once()
        else:
            result = await target.send_prompt_async(message=Message.from_prompt(prompt="Add", role="user"))
            assert [message.api_role for message in result] == ["assistant", "tool", "assistant"]
            assert send.await_count == 2
            await send.call_args.kwargs["api_call"]()
            inputs = create.call_args.kwargs["input"]
            assert inputs[-2]["arguments"] == arguments
            assert json.loads(inputs[-1]["output"])["error"] == "malformed_arguments"
            with pytest.raises(ValueError):
                target.validate_tool_history(result)
        execute.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("arguments", ['{"value": "one"}', "{}", '{"value": 1, "unexpected": true}'])
@pytest.mark.parametrize("strict", [False, True])
async def test_mismatched_arguments_reach_model_as_tool_error(*, arguments: str, strict: bool) -> None:
    executed: list[int] = []

    async def record_async(*, value: int) -> int:
        executed.append(value)
        return value

    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://example.invalid",
        api_key="not-a-key",
        tools=[FunctionTool(function=record_async)],
        fail_on_missing_function=strict,
    )
    call = MessagePiece(
        role="assistant",
        original_value=json.dumps(
            {"type": "function_call", "call_id": "call-1", "name": "record_async", "arguments": arguments}
        ),
        original_value_data_type="function_call",
    ).to_message()
    final = Message.from_prompt(prompt="Please supply valid arguments.", role="assistant")
    with (
        patch.object(target, "_handle_openai_request_async", new_callable=AsyncMock, side_effect=[call, final]) as send,
        patch.object(target._client.responses, "create", new_callable=AsyncMock) as create,
    ):
        if strict:
            with pytest.raises(ValidationError):
                await target.send_prompt_async(message=Message.from_prompt(prompt="Record", role="user"))
            send.assert_awaited_once()
        else:
            result = await target.send_prompt_async(message=Message.from_prompt(prompt="Record", role="user"))
            assert [message.api_role for message in result] == ["assistant", "tool", "assistant"]
            assert send.await_count == 2
            await send.call_args.kwargs["api_call"]()
            output = json.loads(create.call_args.kwargs["input"][-1]["output"])
            assert output.pop("message")
            assert output == {
                "error": "malformed_arguments",
                "function": "record_async",
                "raw_arguments": arguments,
            }
            execution = ToolExecutionMetadata.from_metadata(metadata=result[1].get_piece().prompt_metadata)
            assert execution == ToolExecutionMetadata(invoked=False)
            target.validate_tool_history(result)
    assert executed == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("strict", [False, True])
async def test_value_error_raised_by_tool_function_propagates(*, strict: bool) -> None:
    async def lookup_async(*, key: str) -> str:
        raise ValueError(f"no entry for {key}")

    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://example.invalid",
        api_key="not-a-key",
        tools=[FunctionTool(function=lookup_async)],
        fail_on_missing_function=strict,
    )

    with pytest.raises(ValueError, match="no entry for missing"):
        await target._execute_call_section_async({"name": "lookup_async", "arguments": '{"key": "missing"}'})


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


def test_decision_target_does_not_run_tools_locally() -> None:
    from pyrit.prompt_target.litellm_decision_target import LiteLLMDecisionTarget

    assert LiteLLMChatTarget.RUNS_TOOLS_LOCALLY
    assert not LiteLLMDecisionTarget.RUNS_TOOLS_LOCALLY
