# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
    get_retry_max_num_attempts,
)
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import (
    OpenAIChatAudioConfig,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.prompt_target.common.json_response_config import _JsonResponseConfig
from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget

# ---------------------------------------------------------------------------
# LiteLLM stub
# ---------------------------------------------------------------------------


class _StubLiteLLMError(Exception):
    def __init__(self, message: str = "", **kwargs: object) -> None:
        super().__init__(message)
        self.status_code = kwargs.get("status_code")


_EXCEPTION_NAMES = [
    "RateLimitError",
    "APIConnectionError",
    "Timeout",
    "AuthenticationError",
    "InternalServerError",
    "ServiceUnavailableError",
    "BadRequestError",
    "ContentPolicyViolationError",
]


def _make_litellm_stub(
    *,
    supports_vision: bool = True,
    supports_response_schema: bool = False,
    supports_audio_input: bool = False,
    supports_audio_output: bool = False,
):
    mod = types.ModuleType("litellm")
    mod.acompletion = AsyncMock(name="litellm.acompletion")
    mod.supports_vision = MagicMock(return_value=supports_vision)
    mod.supports_response_schema = MagicMock(return_value=supports_response_schema)
    mod.supports_audio_input = MagicMock(return_value=supports_audio_input)
    mod.supports_audio_output = MagicMock(return_value=supports_audio_output)
    mod.get_supported_openai_params = MagicMock(
        return_value=["temperature", "top_p", "max_tokens", "response_format", "seed", "n", "stop"]
    )

    exc_mod = types.ModuleType("litellm.exceptions")
    for name in _EXCEPTION_NAMES:
        setattr(exc_mod, name, type(name, (_StubLiteLLMError,), {"__module__": "litellm.exceptions"}))
    mod.exceptions = exc_mod
    return mod, exc_mod


@pytest.fixture
def litellm_stub():
    mod, exc_mod = _make_litellm_stub()
    with patch.dict(sys.modules, {"litellm": mod, "litellm.exceptions": exc_mod}):
        yield mod


@pytest.fixture
def target(patch_central_database, litellm_stub) -> LiteLLMChatTarget:
    return LiteLLMChatTarget(model_name="anthropic/claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(content="hello", finish_reason="stop", model="anthropic/claude-sonnet-4-6"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = finish_reason
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.audio = None
    resp.model = model
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.total_tokens = 15
    resp.usage.cached_tokens = 0
    resp.model_dump_json = MagicMock(return_value=json.dumps({"finish_reason": finish_reason, "content": content}))
    return resp


def _mock_audio_response(transcript="hello there", model="openai/gpt-4o-audio-preview"):
    resp = _mock_response(content=None, model=model)
    resp.choices[0].message.content = None
    audio = MagicMock()
    audio.transcript = transcript
    audio.data = base64.b64encode(b"fake audio bytes").decode("utf-8")
    resp.choices[0].message.audio = audio
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


def _user_message(text="test prompt", conversation_id="convo"):
    piece = MessagePiece(
        role="user",
        conversation_id=conversation_id,
        original_value=text,
        original_value_data_type="text",
    )
    return piece.to_message()


def _disabled_json_config() -> _JsonResponseConfig:
    return _JsonResponseConfig.from_metadata(metadata={})


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_init_requires_model_name(patch_central_database, litellm_stub):
    with pytest.raises(ValueError, match="model_name is required"):
        LiteLLMChatTarget()


def test_init_reads_model_env_var(patch_central_database, litellm_stub):
    with patch.dict("os.environ", {"LITELLM_MODEL": "openai/gpt-4o"}):
        t = LiteLLMChatTarget()
    assert t._model_name == "openai/gpt-4o"


def test_init_explicit_model_overrides_env(patch_central_database, litellm_stub):
    with patch.dict("os.environ", {"LITELLM_MODEL": "openai/gpt-4o"}):
        t = LiteLLMChatTarget(model_name="anthropic/claude-haiku-4-5")
    assert t._model_name == "anthropic/claude-haiku-4-5"


def test_init_api_key_from_env(patch_central_database, litellm_stub):
    with patch.dict("os.environ", {"LITELLM_API_KEY": "sk-env"}):
        t = LiteLLMChatTarget(model_name="openai/gpt-4o")
    assert t._api_key == "sk-env"


def test_init_endpoint_from_env(patch_central_database, litellm_stub):
    with patch.dict("os.environ", {"LITELLM_ENDPOINT": "http://localhost:4000"}):
        t = LiteLLMChatTarget(model_name="openai/gpt-4o")
    assert t._endpoint == "http://localhost:4000"


def test_drop_params_defaults_to_true_in_body(target):
    body = target._construct_request_body(
        messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config()
    )
    assert body["drop_params"] is True


def test_drop_unsupported_params_false_sets_strict_body(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", drop_unsupported_params=False)
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["drop_params"] is False


def test_drop_params_can_be_overridden_via_extra_body(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", extra_body_parameters={"drop_params": False})
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["drop_params"] is False


def test_extra_body_drop_params_overrides_init_arg(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o",
        drop_unsupported_params=False,
        extra_body_parameters={"drop_params": True},
    )
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["drop_params"] is True


def test_num_retries_default_from_pyrit_convention(target):
    assert target._num_retries == max(get_retry_max_num_attempts() - 1, 0)


def test_init_rejects_out_of_range_temperature(patch_central_database, litellm_stub):
    with pytest.raises((ValueError, PyritException)):
        LiteLLMChatTarget(model_name="openai/gpt-4o", temperature=5)


# ---------------------------------------------------------------------------
# Capability derivation
# ---------------------------------------------------------------------------


def test_capabilities_vision_model_includes_image(target):
    supported = {t for combo in target.capabilities.input_modalities for t in combo}
    assert "image_path" in supported


def test_capabilities_text_only_model_excludes_image(patch_central_database):
    mod, exc_mod = _make_litellm_stub(supports_vision=False)
    with patch.dict(sys.modules, {"litellm": mod, "litellm.exceptions": exc_mod}):
        t = LiteLLMChatTarget(model_name="text/only-model")
    supported = {ty for combo in t.capabilities.input_modalities for ty in combo}
    assert supported == {"text"}


def test_capabilities_json_output_derived_from_supported_params(target):
    # stub get_supported_openai_params includes "response_format"
    assert target.capabilities.supports_json_output is True


def test_capabilities_audio_model_includes_audio_modalities(patch_central_database):
    mod, exc_mod = _make_litellm_stub(supports_audio_input=True, supports_audio_output=True)
    with patch.dict(sys.modules, {"litellm": mod, "litellm.exceptions": exc_mod}):
        t = LiteLLMChatTarget(model_name="openai/gpt-4o-audio-preview")
    input_types = {ty for combo in t.capabilities.input_modalities for ty in combo}
    output_types = {ty for combo in t.capabilities.output_modalities for ty in combo}
    assert "audio_path" in input_types
    assert "audio_path" in output_types


def test_capabilities_text_only_model_excludes_audio(target):
    output_types = {ty for combo in target.capabilities.output_modalities for ty in combo}
    input_types = {ty for combo in target.capabilities.input_modalities for ty in combo}
    assert output_types == {"text"}
    assert "audio_path" not in input_types


def test_custom_configuration_overrides_derivation(patch_central_database, litellm_stub):
    custom = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True))
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", custom_configuration=custom)
    supported = {ty for combo in t.capabilities.input_modalities for ty in combo}
    assert supported == {"text"}
    assert t.capabilities.supports_json_output is False


# ---------------------------------------------------------------------------
# Identifier
# ---------------------------------------------------------------------------


def test_identifier_includes_behavioral_params_and_excludes_key(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o",
        api_key="sk-secret",
        endpoint="http://localhost:4000",
        temperature=0.5,
    )
    params = t.get_identifier().params
    assert params["temperature"] == 0.5
    assert params["endpoint"] == "http://localhost:4000"
    assert not any("key" in key.lower() for key in params)
    assert "sk-secret" not in json.dumps(params)


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


def test_construct_request_body_basics(target):
    messages = [{"role": "user", "content": "hi"}]
    body = target._construct_request_body(messages=messages, json_config=_disabled_json_config())
    assert body["model"] == "anthropic/claude-sonnet-4-6"
    assert body["messages"] == messages
    assert body["drop_params"] is True
    assert body["num_retries"] == target._num_retries


def test_construct_request_body_forwards_api_key(target):
    body = target._construct_request_body(
        messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config(), api_key="sk-x"
    )
    assert body["api_key"] == "sk-x"


def test_construct_request_body_omits_none_values(target):
    body = target._construct_request_body(
        messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config()
    )
    assert "api_key" not in body
    assert "temperature" not in body
    assert "response_format" not in body


def test_construct_request_body_forwards_optional_params(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o",
        temperature=0.5,
        top_p=0.9,
        max_tokens=100,
        frequency_penalty=0.1,
        presence_penalty=0.2,
        seed=7,
        n=2,
        stop=["END"],
        endpoint="http://localhost:4000",
    )
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["temperature"] == 0.5
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 100
    assert body["frequency_penalty"] == 0.1
    assert body["presence_penalty"] == 0.2
    assert body["seed"] == 7
    assert body["n"] == 2
    assert body["stop"] == ["END"]
    assert body["api_base"] == "http://localhost:4000"


def test_max_completion_tokens_available_via_extra_body_passthrough(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/o1", extra_body_parameters={"max_completion_tokens": 512})
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["max_completion_tokens"] == 512
    assert "max_tokens" not in body


def test_construct_request_body_passthrough_extra_params(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o",
        extra_body_parameters={"tools": [{"type": "function"}], "tool_choice": "auto"},
    )
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["tools"] == [{"type": "function"}]
    assert body["tool_choice"] == "auto"


def test_construct_request_body_forwards_headers(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", headers={"X-Trace": "abc"})
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["extra_headers"] == {"X-Trace": "abc"}


# ---------------------------------------------------------------------------
# Send prompt (through the public API)
# ---------------------------------------------------------------------------


async def test_send_prompt_returns_text_response(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_response("The answer is 4."))

    result = await target.send_prompt_async(message=_user_message("What is 2+2?"))

    assert len(result) == 1
    assert result[0].message_pieces[0].converted_value == "The answer is 4."
    litellm_stub.acompletion.assert_awaited_once()
    call_kwargs = litellm_stub.acompletion.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert call_kwargs["num_retries"] == target._num_retries


async def test_send_prompt_handles_tool_calls(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_tool_call_response())

    result = await target.send_prompt_async(message=_user_message("What's the weather?"))

    piece = result[0].message_pieces[0]
    assert piece.converted_value_data_type == "function_call"
    parsed = json.loads(piece.converted_value)
    assert parsed["function"]["name"] == "get_weather"


async def test_send_prompt_captures_token_usage(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_response("ok"))

    result = await target.send_prompt_async(message=_user_message("hi"))

    metadata = result[0].message_pieces[0].prompt_metadata
    assert metadata["token_usage_input_tokens"] == 10
    assert metadata["token_usage_output_tokens"] == 5
    assert metadata["token_usage_total_tokens"] == 15


async def test_send_prompt_captures_response_cost_from_hidden_params(target, litellm_stub):
    response = _mock_response("ok")
    response._hidden_params = {"response_cost": 0.00042}
    litellm_stub.acompletion = AsyncMock(return_value=response)

    result = await target.send_prompt_async(message=_user_message("hi"))

    metadata = result[0].message_pieces[0].prompt_metadata
    assert float(metadata["token_usage_cost"]) == pytest.approx(0.00042)


async def test_send_prompt_captures_response_cost_falls_back_to_completion_cost(target, litellm_stub):
    response = _mock_response("ok")
    # No usable _hidden_params dict -> should recompute via litellm.completion_cost.
    response._hidden_params = None
    litellm_stub.completion_cost = MagicMock(return_value=0.0009)
    litellm_stub.acompletion = AsyncMock(return_value=response)

    result = await target.send_prompt_async(message=_user_message("hi"))

    metadata = result[0].message_pieces[0].prompt_metadata
    assert float(metadata["token_usage_cost"]) == pytest.approx(0.0009)
    litellm_stub.completion_cost.assert_called_once()


async def test_send_prompt_omits_cost_when_unavailable(target, litellm_stub):
    response = _mock_response("ok")
    response._hidden_params = None
    litellm_stub.completion_cost = MagicMock(side_effect=Exception("no pricing map"))
    litellm_stub.acompletion = AsyncMock(return_value=response)

    result = await target.send_prompt_async(message=_user_message("hi"))

    metadata = result[0].message_pieces[0].prompt_metadata
    assert "token_usage_cost" not in metadata


async def test_send_prompt_resolves_callable_api_key(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key=lambda: "token-abc")
    litellm_stub.acompletion = AsyncMock(return_value=_mock_response("ok"))

    await t.send_prompt_async(message=_user_message("hi"))

    assert litellm_stub.acompletion.call_args.kwargs["api_key"] == "token-abc"


# ---------------------------------------------------------------------------
# Multimodal message building
# ---------------------------------------------------------------------------


async def test_build_chat_messages_text_only_uses_string_content(target):
    messages = [_user_message("hello")]
    result = await target._build_chat_messages_async(messages)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "hello"


async def test_build_chat_messages_multimodal_text_and_image(target):
    text_piece = MessagePiece(
        role="user", conversation_id="c", original_value="describe", original_value_data_type="text"
    )
    image_piece = MessagePiece(
        role="user", conversation_id="c", original_value="x.png", original_value_data_type="image_path"
    )
    message = Message(message_pieces=[text_piece, image_piece])

    with patch(
        "pyrit.prompt_target.common.chat_completions_message_builder.build_image_content_entry_async",
        new=AsyncMock(return_value={"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}),
    ):
        result = await target._build_chat_messages_async([message])

    content_types = [part["type"] for part in result[0]["content"]]
    assert "text" in content_types
    assert "image_url" in content_types


async def test_build_chat_messages_rejects_unsupported_type(target):
    piece = MessagePiece(
        role="user", conversation_id="c", original_value="v.mp4", original_value_data_type="video_path"
    )
    text_piece = MessagePiece(role="user", conversation_id="c", original_value="t", original_value_data_type="text")
    message = Message(message_pieces=[text_piece, piece])
    with pytest.raises(ValueError, match="Multimodal data type video_path is not yet supported"):
        await target._build_chat_messages_async([message])


async def test_build_chat_messages_multimodal_text_and_audio(target):
    text_piece = MessagePiece(
        role="user", conversation_id="c", original_value="transcribe", original_value_data_type="text"
    )
    audio_piece = MessagePiece(
        role="user", conversation_id="c", original_value="clip.wav", original_value_data_type="audio_path"
    )
    message = Message(message_pieces=[text_piece, audio_piece])

    with patch(
        "pyrit.prompt_target.common.chat_completions_message_builder.build_audio_content_entry_async",
        new=AsyncMock(return_value={"type": "input_audio", "input_audio": {"data": "xxx", "format": "wav"}}),
    ):
        result = await target._build_chat_messages_async([message])

    content_types = [part["type"] for part in result[0]["content"]]
    assert "text" in content_types
    assert "input_audio" in content_types


def test_audio_response_config_adds_modalities_to_request_body(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(
        model_name="openai/gpt-4o-audio-preview",
        audio_response_config=OpenAIChatAudioConfig(voice="alloy", audio_format="wav"),
    )
    body = t._construct_request_body(messages=[{"role": "user", "content": "hi"}], json_config=_disabled_json_config())
    assert body["modalities"] == ["text", "audio"]
    assert body["audio"] == {"voice": "alloy", "format": "wav"}


async def test_send_prompt_parses_audio_response(patch_central_database, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_audio_response())
    target = LiteLLMChatTarget(
        model_name="openai/gpt-4o-audio-preview",
        audio_response_config=OpenAIChatAudioConfig(voice="alloy", audio_format="wav"),
    )
    piece = MessagePiece(role="user", conversation_id="c", original_value="say hi", original_value_data_type="text")

    with patch("pyrit.prompt_target.common.chat_completions_response_parser.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/audio.wav"
        mock_serializer.save_data_async = AsyncMock()
        mock_factory.return_value = mock_serializer

        responses = await target.send_prompt_async(message=piece.to_message())

    pieces = responses[0].message_pieces
    data_types = [p.converted_value_data_type for p in pieces]
    assert "text" in data_types
    assert "audio_path" in data_types
    transcript_piece = next(p for p in pieces if p.converted_value_data_type == "text")
    assert transcript_piece.prompt_metadata.get("transcription") == "audio"
    audio_piece = next(p for p in pieces if p.converted_value_data_type == "audio_path")
    assert audio_piece.converted_value == "/path/to/audio.wav"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


async def test_send_prompt_emits_response_format_for_json(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_response("{}"))
    piece = MessagePiece(
        role="user",
        conversation_id="c",
        original_value="give json",
        original_value_data_type="text",
        prompt_metadata={"response_format": "json"},
    )
    await target.send_prompt_async(message=piece.to_message())
    assert litellm_stub.acompletion.call_args.kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Content filtering (surfaced as error Message, not raised)
# ---------------------------------------------------------------------------


async def test_content_filter_finish_reason_returns_error_message(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(
        return_value=_mock_response(content="partial answer", finish_reason="content_filter")
    )

    result = await target.send_prompt_async(message=_user_message("bad prompt"))

    piece = result[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert piece.prompt_metadata.get("partial_content") == "partial answer"


async def test_content_policy_exception_returns_error_message(target, litellm_stub):
    exc = litellm_stub.exceptions.ContentPolicyViolationError("content_filter triggered")
    litellm_stub.acompletion = AsyncMock(side_effect=exc)

    result = await target.send_prompt_async(message=_user_message("bad prompt"))

    assert result[0].message_pieces[0].converted_value_data_type == "error"


# ---------------------------------------------------------------------------
# Empty / malformed responses
# ---------------------------------------------------------------------------


async def test_empty_response_raises(target, litellm_stub):
    empty = _mock_response(content=None)
    empty.choices[0].message.content = None
    empty.choices[0].message.tool_calls = None
    empty.choices[0].message.audio = None
    litellm_stub.acompletion = AsyncMock(return_value=empty)

    with pytest.raises(EmptyResponseException):
        await target.send_prompt_async(message=_user_message())


async def test_no_choices_raises_pyrit_exception(target, litellm_stub):
    bad = MagicMock()
    bad.choices = []
    litellm_stub.acompletion = AsyncMock(return_value=bad)

    with pytest.raises(PyritException, match="No choices"):
        await target.send_prompt_async(message=_user_message())


async def test_unknown_finish_reason_raises(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(return_value=_mock_response(finish_reason="banana"))

    with pytest.raises(PyritException, match="Unknown finish_reason"):
        await target.send_prompt_async(message=_user_message())


# ---------------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------------


async def test_rate_limit_error_translated(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(side_effect=litellm_stub.exceptions.RateLimitError("rate limited"))
    with pytest.raises(RateLimitException, match="Rate limited"):
        await target.send_prompt_async(message=_user_message())


async def test_auth_error_translated(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(side_effect=litellm_stub.exceptions.AuthenticationError("bad key"))
    with pytest.raises(PyritException, match="Authentication failed"):
        await target.send_prompt_async(message=_user_message())


async def test_connection_error_translated_to_transient(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(side_effect=litellm_stub.exceptions.APIConnectionError("reset"))
    with pytest.raises(RateLimitException, match="Transient"):
        await target.send_prompt_async(message=_user_message())


async def test_timeout_translated_to_transient(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(side_effect=litellm_stub.exceptions.Timeout("timed out"))
    with pytest.raises(RateLimitException, match="Transient"):
        await target.send_prompt_async(message=_user_message())


async def test_unknown_error_wrapped_in_pyrit_exception(target, litellm_stub):
    litellm_stub.acompletion = AsyncMock(side_effect=RuntimeError("something broke"))
    with pytest.raises(PyritException, match="LiteLLM error"):
        await target.send_prompt_async(message=_user_message())


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


async def test_resolve_api_key_string(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key="sk-x")
    assert await t._resolve_api_key_async() == "sk-x"


async def test_resolve_api_key_none(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o")
    assert await t._resolve_api_key_async() is None


async def test_resolve_api_key_sync_callable(patch_central_database, litellm_stub):
    t = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key=lambda: "tok")
    assert await t._resolve_api_key_async() == "tok"


async def test_resolve_api_key_async_callable(patch_central_database, litellm_stub):
    async def provider() -> str:
        return "atok"

    t = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key=provider)
    assert await t._resolve_api_key_async() == "atok"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_is_json_response_supported_reflects_capability(target):
    assert target.is_json_response_supported() is True
