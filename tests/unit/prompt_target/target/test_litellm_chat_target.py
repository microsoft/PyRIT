# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.exceptions.exception_classes import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
)
from pyrit.models import Message, MessagePiece


def _make_litellm_stub():
    """Install a fake litellm module so the target can be imported without the real package."""
    mod = types.ModuleType("litellm")
    mod.acompletion = AsyncMock(name="litellm.acompletion")

    exc_mod = types.ModuleType("litellm.exceptions")
    exc_mod.RateLimitError = type("RateLimitError", (Exception,), {"__module__": "litellm.exceptions"})
    exc_mod.APIConnectionError = type("APIConnectionError", (Exception,), {"__module__": "litellm.exceptions"})
    exc_mod.Timeout = type("Timeout", (Exception,), {"__module__": "litellm.exceptions"})
    exc_mod.AuthenticationError = type("AuthenticationError", (Exception,), {"__module__": "litellm.exceptions"})
    exc_mod.InternalServerError = type("InternalServerError", (Exception,), {"__module__": "litellm.exceptions"})
    exc_mod.ServiceUnavailableError = type(
        "ServiceUnavailableError", (Exception,), {"__module__": "litellm.exceptions"}
    )

    mod.exceptions = exc_mod
    sys.modules["litellm"] = mod
    sys.modules["litellm.exceptions"] = exc_mod
    return mod


LITELLM_STUB = _make_litellm_stub()

from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget  # noqa: E402


def _mock_response(content="hello", finish_reason="stop", model="anthropic/claude-sonnet-4-6"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = finish_reason
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.model = model
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    return resp


def _mock_tool_call_response():
    resp = _mock_response(content=None)
    resp.choices[0].message.content = None
    tool_call = MagicMock()
    tool_call.id = "call_123"
    tool_call.function.name = "get_weather"
    tool_call.function.arguments = '{"location": "SF"}'
    resp.choices[0].message.tool_calls = [tool_call]
    return resp


def _make_message(text="test prompt", role="user"):
    piece = MessagePiece(
        role=role,
        conversation_id="test_convo",
        original_value=text,
        converted_value=text,
        original_value_data_type="text",
        converted_value_data_type="text",
    )
    return Message(message_pieces=[piece])


@pytest.fixture
def target(patch_central_database) -> LiteLLMChatTarget:
    return LiteLLMChatTarget(model_name="anthropic/claude-sonnet-4-6")


# ── constructor tests ─────────────────────────────────────────────────

def test_init_requires_model_name(patch_central_database):
    with pytest.raises(ValueError, match="model_name is required"):
        LiteLLMChatTarget()


def test_init_reads_env_var(patch_central_database, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/gpt-4o")
    t = LiteLLMChatTarget()
    assert t._model_name == "openai/gpt-4o"


def test_init_explicit_overrides_env(patch_central_database, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/gpt-4o")
    t = LiteLLMChatTarget(model_name="anthropic/claude-haiku-4-5")
    assert t._model_name == "anthropic/claude-haiku-4-5"


def test_drop_params_defaults_true(target):
    assert target._drop_params is True


def test_drop_params_can_be_disabled(patch_central_database):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", drop_params=False)
    assert t._drop_params is False


# ── request body tests ────────────────────────────────────────────────

def test_construct_request_body_includes_drop_params(target):
    messages = [{"role": "user", "content": "hi"}]
    body = target._construct_request_body(messages)
    assert body["drop_params"] is True
    assert body["model"] == "anthropic/claude-sonnet-4-6"
    assert body["messages"] == messages


def test_construct_request_body_forwards_api_key(patch_central_database):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key="sk-test")
    body = t._construct_request_body([{"role": "user", "content": "hi"}])
    assert body["api_key"] == "sk-test"


def test_construct_request_body_omits_api_key_when_blank(target):
    body = target._construct_request_body([{"role": "user", "content": "hi"}])
    assert "api_key" not in body


def test_construct_request_body_forwards_optional_params(patch_central_database):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o",
        temperature=0.5,
        top_p=0.9,
        max_tokens=100,
        api_base="http://localhost:4000",
    )
    body = t._construct_request_body([{"role": "user", "content": "hi"}])
    assert body["temperature"] == 0.5
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 100
    assert body["api_base"] == "http://localhost:4000"


# ── send prompt tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_prompt_returns_text_response(target):
    LITELLM_STUB.acompletion = AsyncMock(return_value=_mock_response("The answer is 4."))
    conversation = [_make_message("What is 2+2?")]

    result = await target._send_prompt_to_target_async(normalized_conversation=conversation)

    assert len(result) == 1
    assert result[0].message_pieces[0].converted_value == "The answer is 4."
    LITELLM_STUB.acompletion.assert_awaited_once()
    call_kwargs = LITELLM_STUB.acompletion.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert call_kwargs["drop_params"] is True


@pytest.mark.asyncio
async def test_send_prompt_handles_tool_calls(target):
    LITELLM_STUB.acompletion = AsyncMock(return_value=_mock_tool_call_response())
    conversation = [_make_message("What's the weather?")]

    result = await target._send_prompt_to_target_async(normalized_conversation=conversation)

    assert len(result) == 1
    piece = result[0].message_pieces[0]
    assert piece.converted_value_data_type == "function_call"
    parsed = json.loads(piece.converted_value)
    assert parsed["function"]["name"] == "get_weather"


@pytest.mark.asyncio
async def test_send_prompt_captures_token_usage(target):
    LITELLM_STUB.acompletion = AsyncMock(return_value=_mock_response("ok"))
    conversation = [_make_message("hi")]

    result = await target._send_prompt_to_target_async(normalized_conversation=conversation)

    metadata = result[0].message_pieces[0].prompt_metadata
    assert metadata["token_usage_prompt_tokens"] == 10
    assert metadata["token_usage_completion_tokens"] == 5
    assert metadata["token_usage_total_tokens"] == 15


# ── empty/malformed response tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_empty_response_raises(target):
    empty_resp = _mock_response(content=None)
    empty_resp.choices[0].message.content = None
    empty_resp.choices[0].message.tool_calls = None
    LITELLM_STUB.acompletion = AsyncMock(return_value=empty_resp)

    with pytest.raises(EmptyResponseException):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


@pytest.mark.asyncio
async def test_no_choices_raises(target):
    bad_resp = MagicMock()
    bad_resp.choices = []
    LITELLM_STUB.acompletion = AsyncMock(return_value=bad_resp)

    with pytest.raises(EmptyResponseException):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


# ── exception translation tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_translated(target):
    exc = LITELLM_STUB.exceptions.RateLimitError("rate limited")
    LITELLM_STUB.acompletion = AsyncMock(side_effect=exc)

    with pytest.raises(RateLimitException, match="Rate limited"):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


@pytest.mark.asyncio
async def test_auth_error_translated(target):
    exc = LITELLM_STUB.exceptions.AuthenticationError("bad key")
    LITELLM_STUB.acompletion = AsyncMock(side_effect=exc)

    with pytest.raises(PyritException, match="Authentication failed"):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


@pytest.mark.asyncio
async def test_connection_error_translated_to_retryable(target):
    exc = LITELLM_STUB.exceptions.APIConnectionError("connection reset")
    LITELLM_STUB.acompletion = AsyncMock(side_effect=exc)

    with pytest.raises(RateLimitException, match="Transient"):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


@pytest.mark.asyncio
async def test_timeout_translated_to_retryable(target):
    exc = LITELLM_STUB.exceptions.Timeout("timed out")
    LITELLM_STUB.acompletion = AsyncMock(side_effect=exc)

    with pytest.raises(RateLimitException, match="Transient"):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


@pytest.mark.asyncio
async def test_unknown_error_wrapped_in_pyrit_exception(target):
    LITELLM_STUB.acompletion = AsyncMock(side_effect=RuntimeError("something broke"))

    with pytest.raises(PyritException, match="LiteLLM error"):
        await target._send_prompt_to_target_async(normalized_conversation=[_make_message()])


# ── validation edge cases ────────────────────────────────────────────

def test_unexpected_finish_reason_raises(target):
    resp = _mock_response(finish_reason="unexpected_value")
    with pytest.raises(PyritException, match="Unexpected finish_reason"):
        target._validate_response(resp)


def test_valid_finish_reasons_accepted(target):
    for reason in ("stop", "length", "tool_calls", "content_filter"):
        resp = _mock_response(finish_reason=reason)
        target._validate_response(resp)


# ── message building tests ───────────────────────────────────────────

def test_build_chat_messages_preserves_roles(target):
    msgs = [_make_message("hello", "user"), _make_message("hi there", "assistant")]
    result = target._build_chat_messages(msgs)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "hello"
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == "hi there"
