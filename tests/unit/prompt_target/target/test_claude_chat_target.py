# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import sys
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from unit.prompt_target.target.test_litellm_chat_target import _make_litellm_stub, _mock_response, _user_message

from pyrit.models import JsonResponseConfig, Message, MessagePiece
from pyrit.prompt_target import ClaudeChatTarget, TargetCapabilities, TargetConfiguration, tool
from pyrit.registry import TargetRegistry


@pytest.fixture
def claude_sdk() -> Generator[Any, None, None]:
    sdk, exceptions = _make_litellm_stub()
    with patch.dict(sys.modules, {"litellm": sdk, "litellm.exceptions": exceptions}):
        yield sdk


@pytest.mark.usefixtures("patch_central_database")
class TestClaudeChatTarget:
    @pytest.mark.parametrize("model", ["claude-sonnet-4-6", "anthropic/claude-sonnet-4-6"])
    async def test_native_route_and_identity_async(self, *, claude_sdk: Any, model: str) -> None:
        target = ClaudeChatTarget(model_name=model)
        claude_sdk.acompletion.return_value = _mock_response()
        await target.send_prompt_async(message=_user_message())
        assert claude_sdk.acompletion.call_args.kwargs["model"] == "anthropic/claude-sonnet-4-6"
        assert target.get_identifier().params["model_name"] == "anthropic/claude-sonnet-4-6"
        assert target.get_identifier().params["underlying_model_name"] == "claude-sonnet-4-6"
        assert target.get_identifier() == ClaudeChatTarget(model_name="claude-sonnet-4-6").get_identifier()

    @pytest.mark.parametrize(
        "model",
        ["", "claude-", "claude- a", "gpt-4o", "openai/claude-sonnet-4-6", "bedrock/claude", "claude-a/b", "claude-a "],
    )
    def test_invalid_route(self, model: str) -> None:
        with pytest.raises(ValueError, match="Claude name"):
            ClaudeChatTarget(model_name=model)

    @pytest.mark.parametrize(
        "extra", [{"model": "openai/gpt-4o"}, {"custom_llm_provider": "openai"}, {"api_key": "secret"}]
    )
    def test_conflicting_passthrough(self, extra: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            ClaudeChatTarget(model_name="claude-sonnet-4-6", extra_body_parameters=extra)

    def test_capabilities_work_without_metadata(self, claude_sdk: Any) -> None:
        claude_sdk.supports_vision.return_value = False
        target = ClaudeChatTarget(model_name="claude-sonnet-4-6")
        assert target.capabilities.supports_multi_turn
        assert target.capabilities.supports_system_prompt
        assert target.capabilities.supports_editable_history
        assert {"text", "image_path", "function_call", "function_call_output"} <= set(
            target.capabilities.supported_input_modalities
        )
        assert target.capabilities.supports_json_output
        assert not target.capabilities.supports_json_schema

    def test_unknown_model_and_custom_configuration(self, claude_sdk: Any) -> None:
        claude_sdk.supports_vision.return_value = False
        claude_sdk.supports_function_calling.return_value = False
        claude_sdk.get_supported_openai_params.return_value = []
        unknown = ClaudeChatTarget(model_name="claude-future")
        assert set(unknown.capabilities.supported_input_modalities) == {"text"}
        assert not unknown.capabilities.supports_json_output
        configuration = TargetConfiguration(capabilities=TargetCapabilities())
        target = ClaudeChatTarget(model_name="claude-sonnet-4-6", custom_configuration=configuration)
        assert target.configuration is configuration

    async def test_credentials_and_endpoint_ignore_generic_environment_async(self, claude_sdk: Any) -> None:
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "anthropic-test",
                "LITELLM_API_KEY": "wrong-key",
                "LITELLM_ENDPOINT": "https://wrong.invalid",
            },
        ):
            target = ClaudeChatTarget(model_name="claude-sonnet-4-6")
            claude_sdk.acompletion.return_value = _mock_response()
            await target.send_prompt_async(message=_user_message())
        body = claude_sdk.acompletion.call_args.kwargs
        assert body["api_key"] == "anthropic-test"
        assert "api_base" not in body

    @pytest.mark.parametrize("async_provider", [False, True])
    async def test_explicit_token_providers_and_headers_async(self, *, claude_sdk: Any, async_provider: bool) -> None:
        provider = AsyncMock(return_value="token") if async_provider else lambda: "token"
        headers = {"X-Route": "test", "Authorization": "private"}
        target = ClaudeChatTarget(
            model_name="claude-sonnet-4-6", api_key=provider, endpoint="https://anthropic.invalid", headers=headers
        )
        headers["X-Route"] = "changed"
        claude_sdk.acompletion.return_value = _mock_response()
        await target.send_prompt_async(message=_user_message())
        assert claude_sdk.acompletion.call_args.kwargs["api_key"] == "token"
        assert claude_sdk.acompletion.call_args.kwargs["api_base"] == "https://anthropic.invalid"
        assert claude_sdk.acompletion.call_args.kwargs["extra_headers"]["X-Route"] == "test"
        assert target.get_identifier().params["headers"] == {"X-Route": "test"}
        assert "private" not in str(target.get_identifier())

    def test_discovery_and_forwarded_constructor(self, claude_sdk: Any) -> None:
        registry = TargetRegistry()
        assert registry.get_class("ClaudeChatTarget") is ClaudeChatTarget
        target = registry.create_instance("ClaudeChatTarget", model_name="claude-sonnet-4-6", max_tokens=256)
        assert isinstance(target, ClaudeChatTarget)
        assert target._max_tokens == 256

    async def test_optional_import_error_is_actionable_async(self) -> None:
        with patch.dict(sys.modules, {"litellm": None}):
            target = ClaudeChatTarget(model_name="claude-sonnet-4-6")
            with pytest.raises(ImportError, match=r"pip install pyrit\[litellm\]"):
                await target.send_prompt_async(message=_user_message())

    @pytest.mark.parametrize("json_output", [False, True])
    async def test_real_anthropic_adapter_system_image_tools_and_replay_async(
        self, *, sqlite_instance: Any, json_output: bool
    ) -> None:
        pytest.importorskip("litellm")
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        @tool
        async def add_async(*, value: int) -> int:
            return value + 1

        target = ClaudeChatTarget(model_name="claude-sonnet-4-6", api_key="test-key", tools=[add_async], max_tokens=256)
        from pyrit.prompt_normalizer import PromptNormalizer

        normalizer = PromptNormalizer()
        await target.set_system_prompt_async(system_prompt="Be concise.", conversation_id="native")
        content = [
            [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "use_1", "name": "add_async", "input": {"value": 2}},
            ],
            [
                {
                    "type": "tool_use",
                    "id": "format_1",
                    "name": ClaudeChatTarget._JSON_OUTPUT_TOOL_NAME,
                    "input": {"answer": 3},
                }
            ]
            if json_output
            else [{"type": "text", "text": "3"}],
            [{"type": "text", "text": "Again."}],
        ]
        if json_output:
            content[0].append(
                {
                    "type": "tool_use",
                    "id": "format_preview",
                    "name": ClaudeChatTarget._JSON_OUTPUT_TOOL_NAME,
                    "input": {"pending": True},
                }
            )
        replies = [
            httpx.Response(
                200,
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                json={
                    "id": f"msg_{i}",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": blocks,
                    "stop_reason": "tool_use" if i == 0 else "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                },
            )
            for i, blocks in enumerate(content)
        ]
        from pyrit.common.path import HOME_PATH

        image = MessagePiece(
            role="user",
            original_value=str(HOME_PATH / "assets" / "pyrit_architecture.png"),
            original_value_data_type="image_path",
        )
        text = MessagePiece(
            role="user",
            original_value="Use add_async for 2.",
            prompt_metadata=JsonResponseConfig(enabled=json_output).to_metadata(),
        )
        with patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, side_effect=replies) as post:
            result = await normalizer.send_prompt_async(
                target=target, message=Message(message_pieces=[text, image]), conversation_id="native"
            )
            if json_output:
                assert json.loads(result.get_value()) == {"answer": 3}
            else:
                assert result.get_value() == "3"
            await normalizer.send_prompt_async(target=target, message=_user_message("Again"), conversation_id="native")
        assert post.await_count == 3
        assert all(call.args[0] == "https://api.anthropic.com/v1/messages" for call in post.call_args_list)
        bodies = [call.kwargs["json"] for call in post.call_args_list]
        assert bodies[0]["model"] == "claude-sonnet-4-6"
        assert bodies[0]["system"][0]["text"] == "Be concise."
        assert bodies[0]["messages"][0]["content"][1]["type"] == "image"
        assert any(definition["name"] == "add_async" for definition in bodies[0]["tools"])
        assert bodies[1]["messages"][-1]["content"][0]["type"] == "tool_result"
        assert bodies[1]["messages"][-1]["content"][0]["tool_use_id"] == "use_1"
        assert any(
            block.get("type") == "tool_result" for message in bodies[2]["messages"] for block in message["content"]
        )
        persisted = await sqlite_instance.get_conversation_messages_async(conversation_id="native")
        assert [message.api_role for message in persisted] == [
            "system",
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
            "assistant",
        ]
        if json_output:
            assert any(
                definition["name"] == ClaudeChatTarget._JSON_OUTPUT_TOOL_NAME for definition in bodies[0]["tools"]
            )
            assert bodies[0]["tool_choice"]["type"] == "auto"

    @pytest.mark.parametrize("include_text", [False, True])
    async def test_real_tool_based_json_adapter_does_not_dispatch_formatting_tool_async(
        self, include_text: bool
    ) -> None:
        pytest.importorskip("litellm")
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        @tool
        async def benign_async() -> str:
            return "ok"

        target = ClaudeChatTarget(model_name="claude-sonnet-4-5", api_key="test-key", tools=[benign_async])
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            json={
                "id": "msg_json",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [
                    *([{"type": "text", "text": "Here is the result."}] if include_text else []),
                    {
                        "type": "tool_use",
                        "id": "json_1",
                        "name": ClaudeChatTarget._JSON_OUTPUT_TOOL_NAME,
                        "input": {"answer": "ok"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )
        with (
            patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, return_value=response) as post,
            patch.object(target, "_extract_response_cost", return_value=0.25),
        ):
            replies = await target.send_prompt_async(
                message=MessagePiece(
                    role="user",
                    original_value="Return JSON.",
                    prompt_metadata={
                        **JsonResponseConfig(enabled=True).to_metadata(),
                        "token_usage_input_tokens": 999,
                        "token_usage_cost": "999",
                        "finish_reason": "stale",
                        "status": "stale",
                        "incomplete_reason": "stale",
                        "caller_tag": "keep",
                    },
                ).to_message()
            )
        assert json.loads(replies[-1].get_value()) == {"answer": "ok"}
        assert all(piece.converted_value_data_type == "text" for reply in replies for piece in reply.message_pieces)
        pieces = replies[-1].message_pieces
        assert len(pieces) == (2 if include_text else 1)
        assert pieces[0].prompt_metadata["token_usage_input_tokens"] == 1
        assert pieces[0].prompt_metadata["token_usage_output_tokens"] == 2
        assert pieces[0].prompt_metadata["token_usage_cost"] == "0.25"
        assert pieces[0].prompt_metadata["finish_reason"] == "tool_calls"
        assert all(piece.prompt_metadata["caller_tag"] == "keep" for piece in pieces)
        assert all(
            "status" not in piece.prompt_metadata and "incomplete_reason" not in piece.prompt_metadata
            for piece in pieces
        )
        for piece in pieces[1:]:
            assert piece.converted_value == "Here is the result."
            assert not any(key.startswith("token_usage_") or key == "finish_reason" for key in piece.prompt_metadata)
        assert post.await_count == 1

    async def test_real_anthropic_explicit_schema_async(self) -> None:
        pytest.importorskip("litellm")
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        target = ClaudeChatTarget(model_name="claude-sonnet-4-6", api_key="test-key")
        target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_json_schema": True}))
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            json={
                "id": "msg_schema",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": '{"answer":8}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )
        with patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, return_value=response) as post:
            replies = await target.send_prompt_async(
                message=MessagePiece(
                    role="user",
                    original_value="Return JSON.",
                    prompt_metadata=JsonResponseConfig(json_schema=schema).to_metadata(),
                ).to_message()
            )
        assert json.loads(replies[-1].get_value()) == {"answer": 8}
        body = post.call_args.kwargs["json"]
        assert body["output_format"]["schema"] == schema
        assert "tools" not in body

    @pytest.mark.parametrize("value", ["not JSON", "[]"])
    async def test_json_final_response_must_be_an_object_async(self, *, claude_sdk: Any, value: str) -> None:
        target = ClaudeChatTarget(model_name="claude-sonnet-4-6")
        claude_sdk.acompletion.return_value = _mock_response(content=value)
        with pytest.raises(ValueError):
            await target.send_prompt_async(
                message=MessagePiece(
                    role="user",
                    original_value="Return JSON.",
                    prompt_metadata=JsonResponseConfig(enabled=True).to_metadata(),
                ).to_message()
            )

    def test_formatter_name_is_reserved(self) -> None:
        @tool
        async def benign_async() -> str:
            return "ok"

        benign_async.name = ClaudeChatTarget._JSON_OUTPUT_TOOL_NAME
        target = ClaudeChatTarget(model_name="claude-sonnet-4-6", tools=[benign_async])
        with pytest.raises(ValueError, match="reserved"):
            target._validate_tool_definitions([benign_async])
