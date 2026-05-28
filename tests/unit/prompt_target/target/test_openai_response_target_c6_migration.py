# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""C6 additions to the Response target function-chaining suite.

Covers the migration onto @tool_loop + LocalToolBackend.
"""

from __future__ import annotations

import json
import uuid
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.tools import LocalToolBackend, ToolEventBehavior, ToolEventPolicy


def _mock_function_call_response(call_id: str, function_name: str, arguments: dict) -> MagicMock:
    """Build a fake Responses-API response containing a function_call section."""
    mock_response = MagicMock()
    mock_response.status = "completed"
    mock_response.error = None
    section = MagicMock()
    section.type = "function_call"
    section.call_id = call_id
    section.name = function_name
    section.arguments = json.dumps(arguments)
    section.model_dump.return_value = {
        "type": "function_call",
        "call_id": call_id,
        "name": function_name,
        "arguments": json.dumps(arguments),
    }
    mock_response.output = [section]
    return mock_response


def _mock_text_response(text: str) -> MagicMock:
    """Build a fake Responses-API response containing a message section."""
    mock_response = MagicMock()
    mock_response.status = "completed"
    mock_response.error = None
    section = MagicMock()
    section.type = "message"
    section.content = [MagicMock(text=text)]
    mock_response.output = [section]
    return mock_response


def _user_msg(text: str, conversation_id: str | None = None) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value=text,
                conversation_id=conversation_id or str(uuid.uuid4()),
            )
        ]
    )


class TestCustomFunctionsDeprecation:
    """custom_functions still works but emits DeprecationWarning."""

    def test_custom_functions_kwarg_emits_deprecation_warning(self, patch_central_database):
        async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
            return {"t": 72}

        with pytest.warns(DeprecationWarning, match="custom_functions"):
            OpenAIResponseTarget(
                model_name="gpt-4",
                endpoint="https://mock.example.com",
                api_key="mock-key",
                custom_functions={"get_weather": get_weather},
            )

    @pytest.mark.asyncio
    async def test_custom_functions_kwarg_still_dispatches(self, patch_central_database):
        async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
            return {"temperature": 72, "condition": "sunny"}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            target = OpenAIResponseTarget(
                model_name="gpt-4",
                endpoint="https://mock.example.com",
                api_key="mock-key",
                custom_functions={"get_weather": get_weather},
            )

        responses = [
            _mock_function_call_response("call_1", "get_weather", {"location": "NYC"}),
            _mock_text_response("72F and sunny."),
        ]
        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return responses[len(seen) - 1]

        with patch.object(target._async_client.responses, "create", new_callable=AsyncMock) as mc:
            mc.side_effect = mock_create
            result = await target.send_prompt_async(message=_user_msg("weather?"))

        assert len(seen) == 2
        assert result[-1].message_pieces[0].original_value == "72F and sunny."
        second_input = seen[1]["input"]
        assert any(item.get("type") == "function_call_output" for item in second_input)


def _config_with_backend(backend: LocalToolBackend) -> TargetConfiguration:
    """Build a TargetConfiguration wired for the modern tool-backend path."""
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        supports_editable_history=True,
        supports_json_output=True,
        supports_system_prompt=True,
        supports_tool_use=True,
        input_modalities=frozenset(
            {
                frozenset(["text"]),
                frozenset(["text", "image_path"]),
                frozenset(["function_call"]),
                frozenset(["tool_call"]),
                frozenset(["function_call_output"]),
                frozenset(["reasoning"]),
            }
        ),
    )
    return TargetConfiguration(
        capabilities=caps,
        tool_event_policy=ToolEventPolicy(behavior=ToolEventBehavior.EXECUTE, max_tool_iterations=5),
        tool_backend=backend,
    )


class TestToolBackendDispatch:
    """The modern path: pass tool_backend via TargetConfiguration."""

    @pytest.mark.asyncio
    async def test_local_backend_dispatches_through_tool_loop(self, patch_central_database):
        async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
            return {"temperature": 72, "condition": "sunny"}

        backend = LocalToolBackend(
            callables={"get_weather": get_weather},
            schemas=[
                {
                    "name": "get_weather",
                    "description": "Weather lookup.",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                }
            ],
        )
        target = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.example.com",
            api_key="mock-key",
            custom_configuration=_config_with_backend(backend),
        )

        responses = [
            _mock_function_call_response("call_1", "get_weather", {"location": "NYC"}),
            _mock_text_response("72F and sunny in NYC."),
        ]
        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return responses[len(seen) - 1]

        with patch.object(target._async_client.responses, "create", new_callable=AsyncMock) as mc:
            mc.side_effect = mock_create
            result = await target.send_prompt_async(message=_user_msg("weather?"))

        assert len(seen) == 2
        assert result[-1].message_pieces[0].original_value == "72F and sunny in NYC."
        second_input = seen[1]["input"]
        assert any(item.get("type") == "function_call_output" for item in second_input)


class TestToolSchemasInjection:
    """_construct_request_body injects backend schemas when present."""

    @pytest.mark.asyncio
    async def test_backend_schemas_injected_into_tools(self, patch_central_database):
        async def get_weather(args: dict[str, Any]) -> dict[str, Any]:
            return {"t": 1}

        backend = LocalToolBackend(
            callables={"get_weather": get_weather},
            schemas=[{"name": "get_weather", "description": "x", "parameters": {"type": "object"}}],
        )
        target = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.example.com",
            api_key="mock-key",
            custom_configuration=_config_with_backend(backend),
        )
        body = await target._construct_request_body(
            conversation=[_user_msg("hi")],
            json_config=MagicMock(enabled=False, schema=None),
        )
        assert "tools" in body
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_extra_body_tools_take_precedence(self, patch_central_database):
        async def f(args: dict[str, Any]) -> dict[str, Any]:
            return {}

        backend = LocalToolBackend(
            callables={"f": f},
            schemas=[{"name": "f", "parameters": {"type": "object"}}],
        )
        legacy = [{"type": "function", "name": "legacy_tool", "description": "x"}]
        config = _config_with_backend(backend)
        target = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.example.com",
            api_key="mock-key",
            extra_body_parameters={"tools": legacy},
            custom_configuration=config,
        )
        body = await target._construct_request_body(
            conversation=[_user_msg("hi")],
            json_config=MagicMock(enabled=False, schema=None),
        )
        assert body["tools"] == legacy

    @pytest.mark.asyncio
    async def test_no_backend_no_tools_key(self, patch_central_database):
        target = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.example.com",
            api_key="mock-key",
        )
        body = await target._construct_request_body(
            conversation=[_user_msg("hi")],
            json_config=MagicMock(enabled=False, schema=None),
        )
        assert "tools" not in body


class TestNonFunctionCallPiecesPassThrough:
    """Reasoning / mcp_call / web_search_call sections must NOT be dispatched.

    The Response target's parser populates pieces for these types so they can
    be persisted to Memory and round-tripped on subsequent requests. The
    CanonicalEnvelopeParser only extracts function_call pieces; the tool loop
    must therefore see an empty parse and exit cleanly.
    """

    @pytest.mark.asyncio
    async def test_reasoning_only_response_exits_loop(self, patch_central_database):
        target = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.example.com",
            api_key="mock-key",
            reasoning_effort="medium",
        )
        # Reasoning section + final text section in one response
        mock_response = MagicMock()
        mock_response.status = "completed"
        mock_response.error = None
        reasoning_section = MagicMock()
        reasoning_section.type = "reasoning"
        reasoning_section.model_dump.return_value = {"type": "reasoning", "summary": "thinking..."}
        text_section = MagicMock()
        text_section.type = "message"
        text_section.content = [MagicMock(text="The answer is 42.")]
        mock_response.output = [reasoning_section, text_section]

        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return mock_response

        with patch.object(target._async_client.responses, "create", new_callable=AsyncMock) as mc:
            mc.side_effect = mock_create
            result = await target.send_prompt_async(message=_user_msg("question?"))

        # Exactly one API call -- reasoning is not a tool call so the loop exits
        assert len(seen) == 1
        # Response message contains both pieces
        assert len(result) == 1
        piece_types = [p.original_value_data_type for p in result[0].message_pieces]
        assert "reasoning" in piece_types
        assert "text" in piece_types
