# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import os
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from openai import BadRequestError
from openai.types.chat import ChatCompletion
from unit.mocks import openai_chat_response_json_dict, openai_response_json_dict

from pyrit.exceptions import EmptyResponseException
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    CapabilityName,
    JsonResponseConfig,
    Message,
    MessagePiece,
    MessageScorable,
    RequestGenerationConfig,
    RequestGenerationInfo,
    ScoringExpectation,
    TargetCapabilities,
    WebSearchConfig,
    WebSearchMode,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import (
    OpenAIChatTarget,
    OpenAICompletionTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    RealtimeTarget,
    RoundRobinTarget,
    get_known_capabilities,
)
from pyrit.prompt_target.common import utils as target_utils
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.openai.openai_target import _OpenAIModelSampling
from pyrit.score import SelfAskTrueFalseScorer


@pytest.fixture
def target(patch_central_database: Any) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        model_name="gpt-4o", endpoint="https://example.test/v1", api_key="test-only-key", temperature=0.2
    )


def request(temperature: float | None = None) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="hello",
                prompt_metadata=RequestGenerationConfig(temperature=temperature).to_metadata(),
            )
        ]
    )


def completion() -> ChatCompletion:
    return ChatCompletion.model_validate({**openai_chat_response_json_dict(), "created": 0})


@pytest.fixture(params=[OpenAICompletionTarget, OpenAIResponseTarget])
def generation_target_type(
    request: pytest.FixtureRequest,
) -> type[OpenAICompletionTarget | OpenAIResponseTarget]:
    return request.param


def generation_response(path: str) -> dict[str, Any]:
    if path.endswith("/responses"):
        return openai_response_json_dict()
    assert path.endswith("/completions")
    return {
        "id": "cmpl-1",
        "object": "text_completion",
        "created": 0,
        "model": "gpt-4o",
        "choices": [{"index": 0, "text": "hi", "finish_reason": "stop", "logprobs": None}],
    }


@pytest.mark.parametrize(
    ("default", "temperature", "expected", "source"),
    [
        (0.2, None, 0.2, "target_default"),
        (None, None, None, "provider_default"),
        (0, None, 0, "target_default"),
        (0.2, 0, 0, "request"),
        (0.2, 0.9, 0.9, "request"),
        (0.2, 2, 2, "request"),
    ],
)
async def test_openai_adapters_persist_sent_temperature_async(
    *,
    generation_target_type: type[OpenAICompletionTarget | OpenAIResponseTarget],
    patch_central_database: Any,
    sqlite_instance: SQLiteMemory,
    default: float | None,
    temperature: float | None,
    expected: float | None,
    source: str,
) -> None:
    bodies: list[dict[str, Any]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=generation_response(http_request.url.path))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = generation_target_type(
            model_name="private-deployment",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=default,
            httpx_client_kwargs={"http_client": client},
        )
        assert target.capabilities.supports_temperature
        identifier = target.get_identifier()
        message = request(temperature)
        message.get_piece().prompt_metadata["_pyrit_generation"] = {"forged": True}
        result = await PromptNormalizer().send_prompt_async(message=message, target=target)
        saved = await sqlite_instance.get_conversation_messages_async(
            conversation_id=result.get_piece().conversation_id
        )
        assert len(bodies) == 1
        assert bodies[0].get("temperature") == expected
        assert ("temperature" in bodies[0]) is (expected is not None)
        assert len(saved) == 2
        for stored in saved:
            info = RequestGenerationInfo.from_metadata(metadata=stored.get_piece().prompt_metadata)
            assert info is not None
            assert info.temperature.requested == temperature
            assert info.temperature.resolved == expected
            assert info.temperature.source == source
            assert info.request_piece_ids == (str(saved[0].get_piece().id),)
        assert RequestGenerationConfig.METADATA_KEY not in result.get_piece().prompt_metadata
        assert target.get_identifier() is identifier
        assert target._temperature == default


@pytest.mark.parametrize("value", [True, "0.3", float("nan"), float("inf"), -0.1, 2.1, "conflict"])
async def test_openai_adapters_reject_invalid_temperature_before_io_async(
    *,
    generation_target_type: type[OpenAICompletionTarget | OpenAIResponseTarget],
    patch_central_database: Any,
    value: Any,
) -> None:
    send = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        target = generation_target_type(
            model_name="gpt-4o",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            httpx_client_kwargs={"http_client": client},
        )
        message = request(0)
        if value == "conflict":
            message.message_pieces.append(
                MessagePiece(
                    role="user",
                    original_value="second",
                    conversation_id=message.get_piece().conversation_id,
                    prompt_metadata=RequestGenerationConfig(temperature=1).to_metadata(),
                )
            )
        else:
            message.get_piece().prompt_metadata[RequestGenerationConfig.METADATA_KEY] = {"temperature": value}
        with pytest.raises(ValueError):
            await target.send_prompt_async(message=message)
        send.assert_not_called()
        assert RequestGenerationInfo.METADATA_KEY not in message.get_piece().prompt_metadata


async def test_openai_adapters_refresh_model_and_respect_explicit_capabilities_async(
    *,
    generation_target_type: type[OpenAICompletionTarget | OpenAIResponseTarget],
    patch_central_database: Any,
) -> None:
    target = generation_target_type(
        model_name="gpt-4o",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
    )
    try:
        for model, supported in [("o3", False), ("gpt-5", False), ("gpt-5.5", False), ("private-deployment", True)]:
            target.set_model_name(model_name=model)
            assert target.capabilities.supports_temperature is supported
            if not supported:
                with pytest.raises(ValueError, match="does not support runtime temperature"):
                    await target.send_prompt_async(message=request(0))
        target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": False}))
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            target.validate_request_generation_config(request(0))
        target.set_model_name(model_name="o3")
        target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": True}))
        assert target.validate_request_generation_config(request(0)).temperature == 0
    finally:
        await target.cleanup_target_async()


async def test_openai_adapters_isolate_concurrent_and_reused_requests_async(
    *,
    generation_target_type: type[OpenAICompletionTarget | OpenAIResponseTarget],
    patch_central_database: Any,
) -> None:
    temperatures: list[float] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        temperatures.append(json.loads(http_request.content)["temperature"])
        await asyncio.sleep(0)
        return httpx.Response(200, json=generation_response(http_request.url.path))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = generation_target_type(
            model_name="gpt-4o",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=0.2,
            httpx_client_kwargs={"http_client": client},
        )
        messages = [request(0), request(1.4), request()]
        await asyncio.gather(*(target.send_prompt_async(message=message) for message in messages))
        assert sorted(temperatures) == [0, 0.2, 1.4]
        messages[0].get_piece().prompt_metadata.update(RequestGenerationConfig(temperature=0.9).to_metadata())
        await target.send_prompt_async(message=messages[0])
        assert temperatures[-1] == 0.9
        assert target._temperature == 0.2


@pytest.mark.parametrize("temperature", [None, 0])
async def test_openai_adapters_retry_once_resolved_async(
    *,
    generation_target_type: type[OpenAICompletionTarget | OpenAIResponseTarget],
    patch_central_database: Any,
    temperature: float | None,
) -> None:
    attempts: list[float] = []
    message = request(temperature)

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        attempts.append(json.loads(http_request.content)["temperature"])
        if len(attempts) == 1:
            target._temperature = 1.9
            return httpx.Response(
                429,
                json={"error": {"message": "Rate limit", "type": "rate_limit_error", "code": "rate_limit"}},
            )
        return httpx.Response(200, json=generation_response(http_request.url.path))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = generation_target_type(
            model_name="gpt-4o",
            endpoint="https://first.example.test/v1",
            api_key="test-only-key",
            temperature=0.2,
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        transport = (
            target._send_model_request_async
            if isinstance(target, OpenAIResponseTarget)
            else target._send_prompt_to_target_async
        )
        with (
            patch.dict(os.environ, {"RETRY_MAX_NUM_ATTEMPTS": "2"}),
            patch.object(transport.retry, "sleep", new_callable=AsyncMock),
            patch.object(target, "_resolve_request_temperature", wraps=target._resolve_request_temperature) as resolve,
        ):
            await target.send_prompt_async(message=message)
        expected = 0.2 if temperature is None else temperature
        assert attempts == [expected, expected]
        resolve.assert_called_once()
        info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
        assert info is not None
        assert info.temperature.resolved == expected


@pytest.mark.parametrize(
    ("model", "effort", "extras", "supported"),
    [
        ("gpt-4o", None, {}, True),
        ("gpt-5.5", None, {}, False),
        ("gpt-5.5", "none", {}, True),
        ("gpt-5.4", "high", {}, False),
        ("gpt-5.5", "high", {"reasoning": {"effort": "none"}}, True),
        ("gpt-5.5", "none", {"reasoning": None}, False),
        ("gpt-5.5", "high", {"extra_body": {"reasoning": {"effort": "none"}}}, True),
        ("gpt-5.5", "none", {"extra_body": {"reasoning": {"summary": "auto"}}}, False),
        ("gpt-5.5", "high", {"extra_body": {"reasoning": {"summary": "auto"}}}, False),
        ("gpt-5.4", "high", {"extra_body": {"reasoning": {"summary": "auto"}}}, True),
        ("gpt-5.5", "none", {"extra_body": {"reasoning": None}}, False),
        ("gpt-5.4", None, {"extra_body": {"reasoning": {"effort": "high"}}}, False),
        ("gpt-5", None, {"extra_body": {"model": "gpt-4o"}}, True),
        ("gpt-4o", None, {"extra_body": {"model": "gpt-5"}}, False),
        ("gpt-4o", None, {"extra_body": {"stream": True}}, False),
    ],
)
async def test_responses_sampling_matches_effective_http_payload_async(
    *, patch_central_database: Any, model: str, effort: Any, extras: dict[str, Any], supported: bool
) -> None:
    bodies: list[dict[str, Any]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=openai_response_json_dict())

    original = deepcopy(extras)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = OpenAIResponseTarget(
            model_name=model,
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            reasoning_effort=effort,
            extra_body_parameters=extras,
            httpx_client_kwargs={"http_client": client},
        )
        assert target.capabilities.supports_temperature is supported
        if supported:
            await target.send_prompt_async(message=request(0))
            assert bodies[0]["temperature"] == 0
            assert _OpenAIModelSampling.supports_temperature(
                model_name=bodies[0]["model"],
                reasoning_effort=(bodies[0].get("reasoning") or {}).get("effort"),
            )
        else:
            with pytest.raises(ValueError, match="does not support runtime temperature"):
                await target.send_prompt_async(message=request(0))
            assert not bodies
        assert extras == original


@pytest.mark.parametrize("temperature", [None, 0])
async def test_responses_tool_continuations_and_retries_keep_initial_temperature_async(
    *, patch_central_database: Any, sqlite_instance: SQLiteMemory, temperature: float | None
) -> None:
    bodies: list[dict[str, Any]] = []
    extras: dict[str, Any] = {
        "temperature": 1.1,
        "extra_body": {"temperature": 0.7, "metadata": {"custom": "preserved"}},
    }

    async def tool_async(arguments: dict[str, Any]) -> dict[str, str]:
        target._temperature = 1.5
        extras["extra_body"]["temperature"] = 1.8
        return {"result": "done"}

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        response = openai_response_json_dict()
        if len(bodies) == 1:
            response["output"] = [
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "lookup",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
        elif len(bodies) == 2:
            return httpx.Response(
                429,
                json={"error": {"message": "Rate limit", "type": "rate_limit_error", "code": "rate_limit"}},
            )
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = OpenAIResponseTarget(
            model_name="gpt-4o",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=0.2,
            extra_body_parameters=extras,
            custom_functions={"lookup": tool_async},
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        message = request(temperature)
        with (
            patch.object(target._send_model_request_async.retry, "sleep", new_callable=AsyncMock),
            patch.object(target, "_resolve_request_temperature", wraps=target._resolve_request_temperature) as resolve,
        ):
            result = await PromptNormalizer().send_prompt_async(message=message, target=target)
        resolve.assert_called_once()
        expected = 0.7 if temperature is None else temperature
        assert [body["temperature"] for body in bodies] == [expected, expected, expected]
        assert all(body["metadata"] == {"custom": "preserved"} for body in bodies)
        saved = await sqlite_instance.get_conversation_messages_async(
            conversation_id=result.get_piece().conversation_id
        )
        assert len(saved) == 4
        for stored in saved:
            info = RequestGenerationInfo.from_metadata(metadata=stored.get_piece().prompt_metadata)
            assert info is not None
            assert info.temperature.requested == temperature
            assert info.temperature.resolved == expected
            assert info.request_piece_ids == (str(saved[0].get_piece().id),)
        await target.send_prompt_async(message=request(0.9))
        assert bodies[-1]["temperature"] == 0.9
        assert extras["extra_body"]["temperature"] == 1.8


@pytest.mark.parametrize("passthrough", [None, 0.3])
async def test_responses_no_override_preserves_unsupported_mode_payload_async(
    *,
    patch_central_database: Any,
    passthrough: float | None,
) -> None:
    bodies: list[dict[str, Any]] = []
    extras = {"extra_body": {"temperature": passthrough}}

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=openai_response_json_dict())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = OpenAIResponseTarget(
            model_name="gpt-5.5",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=0.8,
            reasoning_effort="high",
            extra_body_parameters=extras,
            httpx_client_kwargs={"http_client": client},
        )
        assert not target.capabilities.supports_temperature
        message = request()
        await target.send_prompt_async(message=message)
        assert bodies[0]["temperature"] == passthrough
        assert bodies[0]["reasoning"] == {"effort": "high"}
        assert extras == {"extra_body": {"temperature": passthrough}}
        assert target._temperature == 0.8
        info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
        assert info is not None
        assert info.temperature.resolved == passthrough
        assert info.temperature.source == ("provider_default" if passthrough is None else "target_default")


@pytest.mark.parametrize("web_search", [False, True])
@pytest.mark.parametrize("mode", ["reasoning", "model"])
async def test_responses_revalidate_mode_after_async_preparation_async(
    *,
    patch_central_database: Any,
    web_search: bool,
    mode: str,
) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-5.4",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters={"tools": [{"type": "web_search_preview"}]} if web_search else None,
    )
    construct_body = target._construct_request_body_async

    async def prepare_async(**kwargs: Any) -> dict[str, Any]:
        body = await construct_body(**kwargs)
        if mode == "reasoning":
            target._reasoning_effort = "high"
        else:
            target._extra_body_parameters = {**(target._extra_body_parameters or {}), "model": "o3"}
        return body

    try:
        message = request(0)
        with (
            patch.object(target, "_construct_request_body_async", side_effect=prepare_async),
            patch.object(target._client.responses, "create", new_callable=AsyncMock) as send,
        ):
            with pytest.raises(ValueError, match="Runtime temperature is not supported"):
                await target.send_prompt_async(message=message)
            send.assert_not_called()
        assert RequestGenerationInfo.METADATA_KEY not in message.get_piece().prompt_metadata
    finally:
        await target.cleanup_target_async()


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4",
        "gpt-4-0314",
        "gpt-4-0613",
        "gpt-4-32k",
        "gpt-4-32k-0613",
        "gpt-4-turbo",
        "gpt-4-turbo-2024-04-09",
        "gpt-4-turbo-preview",
        "gpt-4-1106-preview",
        "gpt-4-0125-preview",
        "gpt-4-vision-preview",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4o-2024-08-06",
        "gpt-4o-mini-2024-07-18",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4.1-mini-2025-04-14",
    ],
)
def test_openai_non_reasoning_sampling_families(model: str) -> None:
    assert _OpenAIModelSampling.supports_temperature(model_name=model)
    assert not _OpenAIModelSampling.supports_temperature(model_name=model, declared_support=False)


@pytest.mark.parametrize(
    ("model", "default_supported"),
    [
        ("gpt-5.1", True),
        ("gpt-5.1-2025-11-13", True),
        ("gpt-5.2", True),
        ("gpt-5.4", True),
        ("gpt-5.4-mini", True),
        ("gpt-5.4-nano-2026-03-17", True),
        ("gpt-5.5", False),
        ("gpt-5.6", False),
        ("gpt-5.6-sol", False),
        ("gpt-5.6-terra", False),
        ("gpt-5.6-luna", False),
    ],
)
@pytest.mark.parametrize("effort", [None, "none", "minimal", "low", "medium", "high", "xhigh", "max", ""])
def test_openai_conditional_sampling_uses_model_reasoning_default(
    model: str, default_supported: bool, effort: str | None
) -> None:
    expected = default_supported if effort is None else effort == "none"
    assert _OpenAIModelSampling.supports_temperature(model_name=model, reasoning_effort=effort) is expected


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano-2025-08-07",
        "gpt-5.2-pro",
        "gpt-5.4-pro",
        "gpt-5.1-codex",
        "gpt-5.3-codex",
        "o1",
        "o3",
        "o3-mini",
        "o4-mini",
        "gpt-4o-audio-preview-2024-12-17",
        "gpt-4o-mini-realtime-preview",
    ],
)
def test_explicit_sampling_declaration_overrides_model_inference(model: str) -> None:
    assert not _OpenAIModelSampling.supports_temperature(model_name=model, reasoning_effort="none")
    assert _OpenAIModelSampling.supports_temperature(model_name=model, reasoning_effort="none", declared_support=True)
    assert not _OpenAIModelSampling.supports_temperature(
        model_name=model, reasoning_effort="high", declared_support=True
    )


@pytest.mark.parametrize(
    "model",
    [
        "private-deployment",
        "gpt-3.5-turbo",
        "gpt-3.5-turbo-0125",
        "gpt-4o-custom",
        "gpt-4o-search-preview",
        "gpt-4.1-future",
        "gpt-5.4-custom-2026-01-01",
        "gpt-5.4-mini-extra",
        "gpt-5.99",
        "gpt-4o-2024-08-06-custom",
        "",
    ],
)
def test_unknown_sampling_variants_default_to_supported(model: str) -> None:
    assert _OpenAIModelSampling.supports_temperature(model_name=model)
    assert _OpenAIModelSampling.supports_temperature(model_name=model, declared_support=True)
    assert not _OpenAIModelSampling.supports_temperature(model_name=model, declared_support=False)
    assert not _OpenAIModelSampling.supports_temperature(
        model_name=model, declared_support=True, reasoning_effort="high"
    )


@pytest.mark.parametrize("temperature", [None, 0, 0.9, 2])
async def test_send_records_effective_temperature_without_changing_identity_async(
    target: OpenAIChatTarget, sqlite_instance: SQLiteMemory, temperature: float | None
) -> None:
    identifier = target.get_identifier()
    response = completion()
    message = request(temperature)
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock, return_value=response) as send:
        result = await PromptNormalizer().send_prompt_async(
            message=message, target=target, conversation_id=message.get_piece().conversation_id
        )
    expected = 0.2 if temperature is None else temperature
    assert send.call_args.kwargs["temperature"] == expected
    assert target.get_identifier() is identifier
    assert target._temperature == 0.2
    saved = await sqlite_instance.get_conversation_messages_async(conversation_id=result.get_piece().conversation_id)
    assert len(saved) == 2
    for stored in saved:
        assert "_pyrit_temperature" not in stored.get_piece().prompt_metadata
        assert "request_temperature" not in stored.get_piece().prompt_metadata
        info = RequestGenerationInfo.from_metadata(metadata=stored.get_piece().prompt_metadata)
        assert info is not None
        assert info.temperature.requested == temperature
        assert info.temperature.resolved == expected
        assert info.temperature.source == ("target_default" if temperature is None else "request")
        assert info.request_piece_ids == (str(saved[0].get_piece().id),)
    assert (
        RequestGenerationConfig.from_metadata(metadata=saved[0].get_piece().prompt_metadata).temperature == temperature
    )
    assert RequestGenerationConfig.METADATA_KEY not in result.get_piece().prompt_metadata


@pytest.mark.parametrize("value", [True, "0.3", float("nan"), float("inf"), -0.1, 2.1])
async def test_invalid_override_fails_before_io_async(target: OpenAIChatTarget, value: Any) -> None:
    message = request()
    message.get_piece().prompt_metadata.update({"request_generation_config": {"temperature": value}})
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError):
            await target.send_prompt_async(message=message)
    send.assert_not_called()
    assert RequestGenerationInfo.METADATA_KEY not in message.get_piece().prompt_metadata


@pytest.mark.parametrize("mode", [WebSearchMode.DEFAULT, WebSearchMode.DISABLED])
async def test_runtime_temperature_preserves_web_search_policy_async(
    target: OpenAIChatTarget, sqlite_instance: SQLiteMemory, mode: WebSearchMode
) -> None:
    message = request(0)
    policy = WebSearchConfig(mode=mode)
    message.get_piece().prompt_metadata.update(policy.to_metadata())
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        response = await PromptNormalizer().send_prompt_async(
            message=message, target=target, conversation_id=message.get_piece().conversation_id
        )
    assert send.call_args.kwargs["temperature"] == 0
    stored = await sqlite_instance.get_conversation_messages_async(conversation_id=response.get_piece().conversation_id)
    metadata = stored[0].get_piece().prompt_metadata
    assert WebSearchConfig.from_metadata(metadata=metadata) == policy
    info = RequestGenerationInfo.from_metadata(metadata=metadata)
    assert info is not None
    assert info.temperature.resolved == 0
    assert info.temperature.source == "request"


async def test_runtime_temperature_does_not_bypass_required_web_search_async(target: OpenAIChatTarget) -> None:
    message = request(0)
    message.get_piece().prompt_metadata.update(WebSearchConfig(mode=WebSearchMode.REQUIRED).to_metadata())
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="cannot enforce web search"):
            await target.send_prompt_async(message=message)
    send.assert_not_called()
    assert RequestGenerationInfo.METADATA_KEY not in message.get_piece().prompt_metadata


async def test_temperature_normalization_retains_web_search_policy_guard_async(target: OpenAIChatTarget) -> None:
    message = request(0)
    message.get_piece().prompt_metadata.update(WebSearchConfig(mode=WebSearchMode.DISABLED).to_metadata())
    with (
        patch.object(target, "_get_normalized_conversation_async", new_callable=AsyncMock, return_value=[request()]),
        patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send,
    ):
        with pytest.raises(ValueError, match="Normalization changed the request's web search policy"):
            await target.send_prompt_async(message=message)
    send.assert_not_called()


@pytest.mark.parametrize("model", ["o1", "o3", "gpt-5", "gpt-4o-audio-preview"])
async def test_unsupported_models_reject_override_async(patch_central_database: Any, model: str) -> None:
    target = OpenAIChatTarget(model_name=model, endpoint="https://example.test/v1", api_key="test-only-key")
    assert not target.capabilities.supports_temperature
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            await target.send_prompt_async(message=request(0))
    send.assert_not_called()


async def test_unimplemented_adapter_rejects_override_async(patch_central_database: Any) -> None:
    target = OpenAIImageTarget(model_name="gpt-image-1", endpoint="https://example.test/v1", api_key="test-only-key")
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        await target.send_prompt_async(message=request(0))


@pytest.mark.parametrize("custom", [False, True])
async def test_profile_or_custom_capability_cannot_enable_an_unimplemented_adapter_async(
    patch_central_database: Any, custom: bool
) -> None:
    target = OpenAIImageTarget(
        model_name="gpt-4o",
        underlying_model="gpt-4o",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=(
            TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True)) if custom else None
        ),
    )
    assert not target.capabilities.supports_temperature
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        await target.send_prompt_async(message=request(0))


def test_adapter_support_is_required_even_after_applying_capabilities(patch_central_database: Any) -> None:
    target = OpenAIImageTarget(model_name="gpt-image-1", endpoint="https://example.test/v1", api_key="test-only-key")
    target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": True}))
    assert not target.capabilities.supports_temperature
    with patch.object(target, "_validate_request_generation_config") as validate:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            target.validate_request_generation_config(request(0))
    validate.assert_not_called()


@pytest.mark.parametrize("target_type", [OpenAIImageTarget, OpenAITTSTarget, OpenAIVideoTarget, RealtimeTarget])
def test_capability_updates_mask_unimplemented_generation_without_mutating_input(
    patch_central_database: Any, target_type: type[OpenAITarget]
) -> None:
    target = target_type(model_name="gpt-4o", endpoint="https://example.test/v1", api_key="test-only-key")
    configuration = target.configuration
    capabilities = target.capabilities.model_copy(update={"supports_temperature": True, "supports_json_output": True})
    target.apply_capabilities(capabilities=capabilities)
    assert not target.capabilities.supports_temperature
    assert target.capabilities.supports_json_output
    assert capabilities.supports_temperature
    assert target.configuration.policy is configuration.policy
    assert target.configuration.normalizer_overrides == configuration.normalizer_overrides


@pytest.mark.parametrize("underlying_model", [None, "unknown-deployment"])
def test_default_configuration_masks_unimplemented_generation(
    patch_central_database: Any, underlying_model: str | None
) -> None:
    default = TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True))
    with patch.object(OpenAIImageTarget, "_DEFAULT_CONFIGURATION", default):
        configuration = OpenAIImageTarget.get_default_configuration(underlying_model)
    assert not configuration.capabilities.supports_temperature
    assert default.capabilities.supports_temperature
    assert configuration.policy is default.policy
    assert configuration.normalizer_overrides == default.normalizer_overrides


def test_send_validation_rejects_unimplemented_generation_even_if_configuration_is_unmasked(
    patch_central_database: Any,
) -> None:
    target = OpenAIImageTarget(model_name="gpt-image-1", endpoint="https://example.test/v1", api_key="test-only-key")
    target._configuration = TargetConfiguration(
        capabilities=target.capabilities.model_copy(update={"supports_temperature": True})
    )
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        target.validate_request_generation_config(request(0))


@pytest.mark.parametrize("supports_temperature", [False, True])
def test_capability_updates_preserve_opted_in_generation(target: OpenAIChatTarget, supports_temperature: bool) -> None:
    target.apply_capabilities(
        capabilities=target.capabilities.model_copy(update={"supports_temperature": supports_temperature})
    )
    assert target.capabilities.supports_temperature is supports_temperature
    if supports_temperature:
        assert target.validate_request_generation_config(request(0)).temperature == 0
    else:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            target.validate_request_generation_config(request(0))


@pytest.mark.parametrize("temperature", [None, 0])
def test_generation_validation_uses_native_capability_and_one_adapter_hook(
    target: OpenAIChatTarget, temperature: float | None
) -> None:
    with (
        patch.object(TargetCapabilities, "includes", autospec=True, return_value=True) as includes,
        patch.object(target, "_validate_request_generation_config") as validate,
    ):
        config = target.validate_request_generation_config(request(temperature))
    validate.assert_called_once_with(config)
    if temperature is None:
        includes.assert_not_called()
    else:
        includes.assert_called_once()
        assert includes.call_args.kwargs["capability"] == CapabilityName.TEMPERATURE


async def test_set_model_name_refreshes_temperature_support_async(target: OpenAIChatTarget) -> None:
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        for model, supported in (
            ("gpt-5", False),
            ("gpt-5.1", True),
            ("unknown", True),
            ("gpt-4o", True),
            ("gpt-4.1-nano-2025-04-14", True),
            ("gpt-5.5", False),
            ("gpt-5.4", True),
        ):
            target.set_model_name(model_name=model)
            assert target.capabilities.supports_temperature is supported
            if supported:
                await target.send_prompt_async(message=request(0))
                assert send.call_args.kwargs["model"] == model
                assert send.call_args.kwargs["temperature"] == 0
            else:
                with pytest.raises(ValueError, match="does not support runtime temperature"):
                    await target.send_prompt_async(message=request(0))
        assert send.await_count == 5
    assert target._temperature == 0.2


@pytest.mark.parametrize("enabled", [False, True])
def test_set_model_name_retains_explicit_generation_capabilities(patch_central_database: Any, enabled: bool) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=enabled)),
    )
    for model in ("unknown", "gpt-4o", "gpt-5", "gpt-5.5"):
        target.set_model_name(model_name=model)
        assert target.capabilities.supports_temperature is enabled


@pytest.mark.parametrize(
    ("underlying_model", "extras", "supported"),
    [
        ("gpt-4o", {}, True),
        ("gpt-5", {}, False),
        (None, {"model": "gpt-4o"}, True),
        ("gpt-4o", {"extra_body": {"model": "gpt-5"}}, False),
        (None, {"model": "gpt-5", "extra_body": {"model": "gpt-4o"}}, True),
        (None, {"reasoning_effort": "high"}, False),
        (None, {"reasoning_effort": "none", "extra_body": {"reasoning_effort": "high"}}, False),
        (None, {"modalities": ["text", "audio"]}, False),
        (None, {"stream": True}, False),
    ],
)
def test_set_model_name_preserves_effective_model_and_mode_precedence(
    patch_central_database: Any, underlying_model: str | None, extras: dict[str, Any], supported: bool
) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        underlying_model=underlying_model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters=extras,
    )
    target.set_model_name(model_name="gpt-5.1")
    assert target.capabilities.supports_temperature is supported


def test_adapter_hook_rechecks_mode_after_capability_declaration(target: OpenAIChatTarget) -> None:
    target._extra_body_parameters = {"reasoning_effort": "high"}
    with pytest.raises(ValueError, match="Runtime temperature is not supported"):
        target.validate_request_generation_config(request(0))


@pytest.mark.parametrize("extras", [{"model": "gpt-5"}, {"extra_body": {"model": "gpt-5.5"}}])
def test_inferred_support_does_not_override_changed_model_rules(
    target: OpenAIChatTarget, extras: dict[str, Any]
) -> None:
    target._extra_body_parameters = extras
    with pytest.raises(ValueError, match="Runtime temperature is not supported"):
        target.validate_request_generation_config(request(0))


@pytest.mark.parametrize(
    "extras",
    [
        {"reasoning_effort": "high"},
        {"reasoning_effort": "none", "extra_body": {"reasoning_effort": "high"}},
        {"modalities": ["text", "audio"]},
        {"extra_body": {"stream": True}},
    ],
)
def test_applying_capabilities_cannot_advertise_restricted_mode(
    patch_central_database: Any, extras: dict[str, Any]
) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-5.1",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters=extras,
    )
    target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": True}))
    assert not target.capabilities.supports_temperature
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        target.validate_request_generation_config(request(0))


@pytest.mark.parametrize("enabled", [False, True])
async def test_applied_capability_and_send_validation_agree_async(patch_central_database: Any, enabled: bool) -> None:
    target = OpenAIChatTarget(
        model_name="private-deployment", endpoint="https://example.test/v1", api_key="test-only-key"
    )
    target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": enabled}))
    assert target.capabilities.supports_temperature is enabled
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        if enabled:
            await target.send_prompt_async(message=request(0))
            assert send.call_args.kwargs["temperature"] == 0
        else:
            with pytest.raises(ValueError, match="does not support runtime temperature"):
                await target.send_prompt_async(message=request(0))
            send.assert_not_called()
    target.set_model_name(model_name="unknown")
    assert target.capabilities.supports_temperature


@pytest.mark.parametrize(
    "model",
    ["gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-nano", "gpt-5.1", "gpt-5.4-mini"],
)
def test_known_sampling_rule_enables_temperature(patch_central_database: Any, model: str) -> None:
    target = OpenAIChatTarget(model_name=model, endpoint="https://example.test/v1", api_key="test-only-key")
    assert target.capabilities.supports_temperature
    assert target.validate_request_generation_config(request(0)).temperature == 0


def test_deployment_uses_underlying_model_profile(patch_central_database: Any) -> None:
    target = OpenAIChatTarget(
        model_name="production-deployment",
        underlying_model="gpt-4o",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
    )
    assert target.capabilities.supports_temperature


@pytest.mark.parametrize("model", ["private-deployment", "new-model-in-catalog", "gpt-3.5-turbo"])
@pytest.mark.parametrize("declared", [False, True])
async def test_unknown_models_support_runtime_temperature_async(
    patch_central_database: Any, model: str, declared: bool
) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=(
            TargetConfiguration(
                capabilities=OpenAIChatTarget.get_default_configuration().capabilities.model_copy(
                    update={"supports_temperature": True}
                )
            )
            if declared
            else None
        ),
    )
    assert target.capabilities.supports_temperature
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=request(0))
    assert send.call_args.kwargs["temperature"] == 0


@pytest.mark.parametrize(
    ("model", "extras", "supported"),
    [
        ("gpt-4.1-nano", {}, True),
        ("gpt-5.5", {}, False),
        ("gpt-5.5", {"reasoning_effort": "none"}, True),
        ("gpt-5.5", {"reasoning_effort": "medium"}, False),
    ],
)
def test_sampling_support_is_resolved_without_a_full_model_profile(
    patch_central_database: Any, model: str, extras: dict[str, Any], supported: bool
) -> None:
    assert get_known_capabilities(model) is None
    target = OpenAIChatTarget(
        model_name="production-deployment",
        underlying_model=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters=extras,
    )
    assert target.capabilities == OpenAIChatTarget._DEFAULT_CONFIGURATION.capabilities.model_copy(
        update={"supports_temperature": supported}
    )
    if supported:
        assert target.validate_request_generation_config(request(0)).temperature == 0
    else:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            target.validate_request_generation_config(request(0))


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-5.1", "gpt-5.4"])
def test_sampling_resolution_does_not_modify_shared_model_profiles(patch_central_database: Any, model: str) -> None:
    profile = get_known_capabilities(model)
    assert profile is not None
    assert not profile.supports_temperature
    target = OpenAIChatTarget(
        model_name="production-deployment",
        underlying_model=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
    )
    assert target.capabilities.supports_temperature
    assert target.capabilities == profile.model_copy(update={"supports_temperature": True})
    assert get_known_capabilities(model) is profile
    assert not profile.supports_temperature


def test_explicit_capability_disable_overrides_known_profile(patch_central_database: Any) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=False)),
    )
    assert not target.capabilities.supports_temperature


@pytest.mark.parametrize(
    "extras",
    [
        {"reasoning_effort": "none"},
        {"reasoning_effort": "high", "extra_body": {"reasoning_effort": "none"}},
        {"model": "unknown", "extra_body": {"model": "gpt-5.1", "reasoning_effort": "none"}},
    ],
)
async def test_effective_non_reasoning_mode_allows_temperature_async(
    patch_central_database: Any, extras: dict[str, Any]
) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-5.1",
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters=extras,
    )
    assert target.capabilities.supports_temperature
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=request(0.7))
    assert send.call_args.kwargs["temperature"] == 0.7


@pytest.mark.parametrize(
    ("model", "extras", "supported"),
    [
        ("gpt-4-turbo-2024-04-09", {}, True),
        ("gpt-4o-mini-2024-07-18", {}, True),
        ("gpt-4.1-nano-2025-04-14", {}, True),
        ("gpt-5.2", {}, True),
        ("gpt-5.4-mini", {}, True),
        ("gpt-5.4-nano", {"reasoning_effort": "high"}, False),
        ("gpt-5-mini", {"reasoning_effort": "none"}, False),
        ("gpt-5.5", {}, False),
        ("gpt-5.5", {"reasoning_effort": "none"}, True),
        ("gpt-5.6-sol", {"reasoning_effort": "high", "extra_body": {"reasoning_effort": "none"}}, True),
        ("gpt-5.6-luna", {"reasoning_effort": "none", "extra_body": {"reasoning_effort": None}}, False),
        ("gpt-5.4", {"extra_body": {"model": "gpt-5.5"}}, False),
        ("gpt-5.5", {"extra_body": {"model": "gpt-5.4"}}, True),
        ("gpt-4o", {"model": "gpt-5.5", "extra_body": {"model": "gpt-4.1"}}, True),
        ("gpt-5.4-pro", {"reasoning_effort": "none"}, False),
        ("gpt-4o-search-preview", {}, True),
        ("gpt-5.99", {"reasoning_effort": "none"}, True),
    ],
)
async def test_sampling_rules_agree_with_advertised_support_and_provider_payload_async(
    patch_central_database: Any, model: str, extras: dict[str, Any], supported: bool
) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        temperature=0.2,
        extra_body_parameters=extras,
    )
    identifier = target.get_identifier()
    message = request(0)
    assert target.capabilities.supports_temperature is supported
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        if supported:
            await target.send_prompt_async(message=message)
            body = {**send.call_args.kwargs, **send.call_args.kwargs.get("extra_body", {})}
            assert body["temperature"] == 0
            assert body["model"] == extras.get("extra_body", {}).get("model", extras.get("model", model))
            assert body.get("reasoning_effort") == extras.get("extra_body", {}).get(
                "reasoning_effort", extras.get("reasoning_effort")
            )
            info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
            assert info is not None
            assert info.temperature.resolved == body["temperature"]
        else:
            with pytest.raises(ValueError, match="does not support runtime temperature"):
                await target.send_prompt_async(message=message)
            send.assert_not_called()
    assert target.get_identifier() is identifier
    assert target._temperature == 0.2
    assert target._extra_body_parameters == extras


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5.5", "gpt-5.4-pro", "gpt-4o-audio-preview"])
@pytest.mark.parametrize("constructor", [False, True])
async def test_explicit_declaration_overrides_known_model_inference_async(
    patch_central_database: Any, model: str, constructor: bool
) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=(
            TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True)) if constructor else None
        ),
    )
    if not constructor:
        assert not target.capabilities.supports_temperature
        target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": True}))
    assert target.capabilities.supports_temperature
    message = request(0)
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=message)
    assert send.call_args.kwargs["temperature"] == 0
    assert "reasoning_effort" not in send.call_args.kwargs
    info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
    assert info is not None
    assert info.temperature.requested == info.temperature.resolved == 0


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5.5", "gpt-5.99"])
async def test_no_override_preserves_payload_on_unsupported_sampling_models_async(
    patch_central_database: Any, model: str
) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        extra_body_parameters={"reasoning_effort": "high", "extra_body": {"temperature": 0.3}},
    )
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=request())
    assert send.call_args.kwargs["extra_body"]["temperature"] == 0.3
    assert send.call_args.kwargs["reasoning_effort"] == "high"
    assert "temperature" not in send.call_args.kwargs


async def test_nested_passthrough_precedence_at_http_boundary_async(patch_central_database: Any) -> None:
    bodies: list[dict[str, Any]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=openai_chat_response_json_dict())

    extras = {"temperature": 1.1, "extra_body": {"temperature": 1.7, "metadata": {"custom": "preserved"}}}
    original = deepcopy(extras)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = OpenAIChatTarget(
            model_name="gpt-4o",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=0.2,
            extra_body_parameters=extras,
            httpx_client_kwargs={"http_client": client},
        )
        for temperature in (None, 0):
            message = request(temperature)
            await target.send_prompt_async(message=message)
            info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
            assert info is not None
            assert info.temperature.resolved == bodies[-1]["temperature"]
    assert [body["temperature"] for body in bodies] == [1.7, 0]
    assert all(body["metadata"] == {"custom": "preserved"} for body in bodies)
    assert extras == original


@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
async def test_round_robin_runtime_temperature_rotation_preserves_evidence_async(
    *,
    patch_central_database: Any,
    sqlite_instance: SQLiteMemory,
    target_type: type[OpenAIChatTarget | OpenAIResponseTarget],
) -> None:
    attempts: list[tuple[str, float]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        attempts.append((http_request.url.host, json.loads(http_request.content)["temperature"]))
        response = (
            openai_response_json_dict() if target_type is OpenAIResponseTarget else openai_chat_response_json_dict()
        )
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        children = [
            target_type(
                model_name="gpt-4o",
                endpoint=f"https://{host}.example.test/v1",
                api_key="test-only-key",
                temperature=0.2,
                extra_body_parameters={"extra_body": {"temperature": default}},
                httpx_client_kwargs={"http_client": client},
            )
            for host, default in [("first", 0.3), ("second", 0.8)]
        ]
        target = RoundRobinTarget(targets=children)
        identifier = target.get_identifier()
        child_identifiers = [child.get_identifier() for child in children]
        assert target.capabilities.supports_temperature
        for temperature, expected in [(0, 0), (1.1, 1.1), (None, 0.3)]:
            result = await PromptNormalizer().send_prompt_async(message=request(temperature), target=target)
            saved = await sqlite_instance.get_conversation_messages_async(
                conversation_id=result.get_piece().conversation_id
            )
            assert len(saved) == 2
            for stored in saved:
                info = RequestGenerationInfo.from_metadata(metadata=stored.get_piece().prompt_metadata)
                assert info is not None
                assert info.temperature.requested == temperature
                assert info.temperature.resolved == expected
                assert info.temperature.source == ("target_default" if temperature is None else "request")
                assert info.request_piece_ids == (str(saved[0].get_piece().id),)
            assert target.get_identifier() is identifier
            assert all(child._temperature == 0.2 for child in children)
            assert all(
                child.get_identifier() is expected_id
                for child, expected_id in zip(children, child_identifiers, strict=True)
            )
        assert attempts == [
            ("first.example.test", 0),
            ("second.example.test", 1.1),
            ("first.example.test", 0.3),
        ]


@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
@pytest.mark.parametrize("disabled_child", [0, 1])
async def test_round_robin_temperature_requires_every_child_async(
    *,
    patch_central_database: Any,
    target_type: type[OpenAIChatTarget | OpenAIResponseTarget],
    disabled_child: int,
) -> None:
    send = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        children = [
            target_type(
                model_name="gpt-4o",
                endpoint="https://example.test/v1",
                api_key="test-only-key",
                httpx_client_kwargs={"http_client": client},
            )
            for _ in range(2)
        ]
        child = children[disabled_child]
        child.apply_capabilities(capabilities=child.capabilities.model_copy(update={"supports_temperature": False}))
        target = RoundRobinTarget(targets=children)
        target.apply_capabilities(capabilities=target.capabilities.model_copy(update={"supports_temperature": True}))
        assert not target.capabilities.supports_temperature
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            await target.send_prompt_async(message=request(0))
        assert target._counter == 0
        send.assert_not_called()


@pytest.mark.parametrize("change", ["capability", "mode", "range"])
async def test_round_robin_validates_all_children_before_selection_async(
    *, patch_central_database: Any, change: str
) -> None:
    send = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        children = [
            OpenAIChatTarget(
                model_name="gpt-4o",
                endpoint="https://example.test/v1",
                api_key="test-only-key",
                httpx_client_kwargs={"http_client": client},
            )
            for _ in range(2)
        ]
        target = RoundRobinTarget(targets=children)
        assert target.capabilities.supports_temperature
        if change == "capability":
            children[1].apply_capabilities(
                capabilities=children[1].capabilities.model_copy(update={"supports_temperature": False})
            )
            assert not target.capabilities.supports_temperature
        elif change == "mode":
            children[1]._extra_body_parameters = {"reasoning_effort": "high"}
        message = request(2.1 if change == "range" else 0)
        with pytest.raises(ValueError, match="temperature"):
            await target.send_prompt_async(message=message)
        assert target._counter == 0
        send.assert_not_called()
        assert RequestGenerationInfo.METADATA_KEY not in message.get_piece().prompt_metadata


@pytest.mark.parametrize("temperature", [None, 0, 1.1])
@pytest.mark.parametrize("fallback_temperature", [0, 0.8, None])
@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
async def test_round_robin_fallback_resolves_its_own_temperature_async(
    *,
    patch_central_database: Any,
    sqlite_instance: SQLiteMemory,
    fallback_temperature: float | None,
    temperature: float | None,
    target_type: type[OpenAIChatTarget | OpenAIResponseTarget],
) -> None:
    attempts: list[tuple[str, dict[str, Any]]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        host = http_request.url.host
        attempts.append((host, json.loads(http_request.content)))
        if host == "first.example.test":
            return httpx.Response(
                429,
                json={"error": {"message": "Rate limit exhausted", "type": "rate_limit_error", "code": "rate_limit"}},
            )
        response = (
            openai_response_json_dict() if target_type is OpenAIResponseTarget else openai_chat_response_json_dict()
        )
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        first = target_type(
            model_name="gpt-4o",
            endpoint="https://first.example.test/v1",
            api_key="test-only-key",
            extra_body_parameters={"extra_body": {"temperature": 0.3}},
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        second = target_type(
            model_name="gpt-4o",
            endpoint="https://second.example.test/v1",
            api_key="test-only-key",
            extra_body_parameters=(
                {"extra_body": {"temperature": fallback_temperature}} if fallback_temperature is not None else {}
            ),
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        target = RoundRobinTarget(targets=[first, second])
        message = request(temperature)
        message.get_piece().prompt_metadata["custom"] = "preserved"
        transport = (
            first._send_model_request_async
            if isinstance(first, OpenAIResponseTarget)
            else first._send_prompt_to_target_async
        )
        with (
            patch.dict(os.environ, {"RETRY_MAX_NUM_ATTEMPTS": "2"}),
            patch.object(transport.retry, "sleep", new_callable=AsyncMock),
            patch.object(
                first, "_resolve_request_temperature", wraps=first._resolve_request_temperature
            ) as first_resolve,
            patch.object(
                second, "_resolve_request_temperature", wraps=second._resolve_request_temperature
            ) as second_resolve,
        ):
            result = await PromptNormalizer().send_prompt_async(
                message=message, target=target, conversation_id=message.get_piece().conversation_id
            )
        expected_first = temperature if temperature is not None else 0.3
        expected_fallback = temperature if temperature is not None else fallback_temperature
        assert [(host, body.get("temperature")) for host, body in attempts] == [
            ("first.example.test", expected_first),
            ("first.example.test", expected_first),
            ("second.example.test", expected_fallback),
        ]
        first_resolve.assert_called_once()
        second_resolve.assert_called_once()
        if expected_fallback is None:
            assert "temperature" not in attempts[-1][1]
        assert result.get_piece().prompt_metadata["inner_target_identifier"] == second.get_identifier().hash
        saved = await sqlite_instance.get_conversation_messages_async(
            conversation_id=result.get_piece().conversation_id
        )
        assert len(saved) == 2
        assert saved[0].get_piece().prompt_metadata["custom"] == "preserved"
        for stored in saved:
            info = RequestGenerationInfo.from_metadata(metadata=stored.get_piece().prompt_metadata)
            assert info is not None
            assert info.temperature.requested == temperature
            assert info.temperature.resolved == expected_fallback
            assert info.temperature.source == (
                "request"
                if temperature is not None
                else "target_default"
                if fallback_temperature is not None
                else "provider_default"
            )
            assert info.request_piece_ids == (str(saved[0].get_piece().id),)


async def test_unknown_model_provider_rejection_preserves_evidence_async(
    patch_central_database: Any, sqlite_instance: SQLiteMemory
) -> None:
    bodies: list[dict[str, Any]] = []

    async def reject_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Temperature is not supported with this model.",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "unsupported_parameter",
                }
            },
        )

    conversation_id = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_async)) as client:
        target = OpenAIChatTarget(
            model_name="private-deployment",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            httpx_client_kwargs={"http_client": client},
        )
        assert target.capabilities.supports_temperature
        with pytest.raises(Exception, match="Error sending prompt") as error:
            await PromptNormalizer().send_prompt_async(
                message=request(0), target=target, conversation_id=conversation_id
            )
    assert isinstance(error.value.__cause__, BadRequestError)
    assert error.value.__cause__.status_code == 400
    assert len(bodies) == 1
    assert bodies[0]["temperature"] == 0
    saved = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    assert len(saved) == 2
    assert saved[1].get_piece().response_error == "processing"
    assert "Temperature is not supported with this model." in saved[1].get_piece().original_value
    for message in saved:
        info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
        assert info is not None
        assert info.temperature.requested == info.temperature.resolved == 0
        assert info.temperature.source == "request"


@pytest.mark.parametrize(
    ("default", "extras"),
    [(None, {}), (0.2, {"extra_body": {"temperature": None}}), (0.2, {"extra_body": {"temperature": 1.7}})],
)
async def test_retry_preserves_default_payload_at_http_boundary_async(
    patch_central_database: Any, default: float | None, extras: dict[str, Any]
) -> None:
    bodies: list[dict[str, Any]] = []

    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(http_request.content))
        response = openai_chat_response_json_dict()
        if len(bodies) == 1:
            response["choices"][0]["message"]["content"] = ""
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_async)) as client:
        target = OpenAIChatTarget(
            model_name="gpt-4o",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=default,
            extra_body_parameters=extras,
            httpx_client_kwargs={"http_client": client},
        )
        with patch.object(target._send_prompt_to_target_async.retry, "sleep", new_callable=AsyncMock):
            await target.send_prompt_async(message=request())
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


async def test_unknown_provider_default_is_omitted_and_recorded_async(target: OpenAIChatTarget) -> None:
    target._temperature = None
    message = request()
    with patch.object(
        target._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=completion(),
    ) as send:
        await target.send_prompt_async(message=message)
    assert "temperature" not in send.call_args.kwargs
    info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
    assert info is not None
    assert info.temperature.source == "provider_default"
    assert info.temperature.resolved is None


async def test_current_piece_option_survives_normalization_without_history_leak_async(target: OpenAIChatTarget) -> None:
    current = request()
    current.message_pieces.append(
        MessagePiece(
            role="user",
            original_value="second",
            conversation_id=current.get_piece().conversation_id,
            prompt_metadata=RequestGenerationConfig(temperature=0).to_metadata(),
        )
    )
    normalized = request(1.9)
    response = completion()
    with (
        patch.object(target, "_get_normalized_conversation_async", new_callable=AsyncMock, return_value=[normalized]),
        patch.object(target._client.chat.completions, "create", new_callable=AsyncMock, return_value=response) as send,
    ):
        await target.send_prompt_async(message=current)
        assert send.call_args.kwargs["temperature"] == 0
        await target.send_prompt_async(message=request())
        assert send.call_args.kwargs["temperature"] == 0.2
    for piece in current.message_pieces:
        info = RequestGenerationInfo.from_metadata(metadata=piece.prompt_metadata)
        assert info is not None
        assert info.temperature.resolved == 0


async def test_concurrent_sends_are_isolated_async(target: OpenAIChatTarget) -> None:
    response = completion()
    messages = [request(0), request(1.4), request()]
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock, return_value=response) as send:
        await asyncio.gather(*(target.send_prompt_async(message=message) for message in messages))
    assert sorted(call.kwargs["temperature"] for call in send.call_args_list) == [0, 0.2, 1.4]
    assert target._temperature == 0.2


async def test_caller_cannot_supply_temperature_evidence_async(target: OpenAIChatTarget) -> None:
    message = request()
    message.get_piece().prompt_metadata["_pyrit_generation"] = {"temperature": {"resolved": 98}}
    with patch.object(
        target._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        return_value=completion(),
    ):
        await target.send_prompt_async(message=message)
    info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
    assert info is not None
    assert info.temperature.resolved == 0.2


@pytest.mark.parametrize("empty_response", [False, True])
async def test_normalizer_error_response_retains_evidence_without_request_options_async(
    target: OpenAIChatTarget, sqlite_instance: SQLiteMemory, empty_response: bool
) -> None:
    message = request(0)
    conversation_id = str(uuid4())
    failure = EmptyResponseException(message="No content") if empty_response else RuntimeError("Transport failed")
    with (
        patch.object(target._client.chat.completions, "create", new_callable=AsyncMock, side_effect=failure),
        patch.object(target._send_prompt_to_target_async.retry, "sleep", new_callable=AsyncMock),
    ):
        if empty_response:
            response = await PromptNormalizer().send_prompt_async(
                message=message, target=target, conversation_id=conversation_id
            )
            assert response.get_piece().response_error == "empty"
        else:
            with pytest.raises(Exception, match="Error sending prompt"):
                await PromptNormalizer().send_prompt_async(
                    message=message, target=target, conversation_id=conversation_id
                )
    saved = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    assert len(saved) == 2
    assert RequestGenerationConfig.from_message(saved[0]).temperature == 0
    assert RequestGenerationConfig.METADATA_KEY not in saved[1].get_piece().prompt_metadata
    request_info = RequestGenerationInfo.from_metadata(metadata=saved[0].get_piece().prompt_metadata)
    response_info = RequestGenerationInfo.from_metadata(metadata=saved[1].get_piece().prompt_metadata)
    assert request_info is not None
    assert request_info.temperature.requested == request_info.temperature.resolved == 0
    assert request_info.request_piece_ids == (str(saved[0].get_piece().id),)
    assert response_info == request_info


@pytest.mark.parametrize("temperature_range", [(0, 1), (0, 2)])
def test_new_capability_does_not_change_existing_identifier_projection(temperature_range: tuple[float, float]) -> None:
    before = TargetConfiguration(capabilities=TargetCapabilities())
    after = TargetConfiguration(
        capabilities=TargetCapabilities(supports_temperature=True, temperature_range=temperature_range)
    )
    assert after.as_identifier_params() == before.as_identifier_params()
    assert "supports_temperature" not in after.as_identifier_params()["capabilities"]
    assert "temperature_range" not in after.as_identifier_params()["capabilities"]


@pytest.mark.parametrize("temperature_range", [(1, 0), (float("nan"), 1), (0, float("inf"))])
def test_temperature_capabilities_reject_invalid_ranges(temperature_range: tuple[float, float]) -> None:
    with pytest.raises(ValueError):
        TargetCapabilities(temperature_range=temperature_range)


def test_temperature_capability_range_round_trip() -> None:
    capabilities = TargetCapabilities(supports_temperature=True, temperature_range=(0, 1))
    assert TargetCapabilities.model_validate_json(capabilities.model_dump_json()) == capabilities
    assert TargetCapabilities.model_validate({"supports_temperature": True}).temperature_range == (0, 2)


@pytest.mark.parametrize("model", ["Mistral-Large-3", "mistral-large-3", "Mistral-Large-3-2026-01-01"])
def test_mistral_temperature_range(model: str) -> None:
    assert _OpenAIModelSampling.supports_temperature(model_name=model)
    assert _OpenAIModelSampling.temperature_range(model) == (0, 1)
    assert _OpenAIModelSampling.temperature_range("private-deployment") == (0, 2)


@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAICompletionTarget, OpenAIResponseTarget])
@pytest.mark.parametrize("temperature", [None, 0, 0.7, 1, 1.01, 2])
async def test_mistral_range_is_advertised_and_enforced_before_io_async(
    *,
    patch_central_database: Any,
    target_type: type[OpenAIChatTarget | OpenAICompletionTarget | OpenAIResponseTarget],
    temperature: float | None,
) -> None:
    async def respond_async(http_request: httpx.Request) -> httpx.Response:
        assert json.loads(http_request.content)["temperature"] == (1.5 if temperature is None else temperature)
        response = (
            openai_chat_response_json_dict()
            if target_type is OpenAIChatTarget
            else generation_response(http_request.url.path)
        )
        return httpx.Response(200, json=response)

    send = AsyncMock(side_effect=respond_async)
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        target = target_type(
            model_name="Mistral-Large-3",
            endpoint="https://example.test/v1",
            api_key="test-only-key",
            temperature=1.5,
            httpx_client_kwargs={"http_client": client},
        )
        assert target.capabilities.supports_temperature
        assert target.capabilities.temperature_range == (0, 1)
        identifier = target.get_identifier()
        if temperature is not None and temperature > 1:
            with pytest.raises(ValueError, match="between 0 and 1"):
                await target.send_prompt_async(message=request(temperature))
            send.assert_not_called()
        else:
            await target.send_prompt_async(message=request(temperature))
            send.assert_called_once()
        assert target.get_identifier() is identifier
        assert target._temperature == 1.5


@pytest.mark.parametrize("source", ["model", "underlying_model", "passthrough"])
async def test_mistral_bounds_use_effective_model_async(*, patch_central_database: Any, source: str) -> None:
    target = OpenAIChatTarget(
        model_name="Mistral-Large-3" if source == "model" else "private-deployment",
        underlying_model="Mistral-Large-3" if source == "underlying_model" else None,
        extra_body_parameters={"extra_body": {"model": "Mistral-Large-3"}} if source == "passthrough" else None,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
    )
    try:
        assert target.capabilities.temperature_range == (0, 1)
        with pytest.raises(ValueError, match="between 0 and 1"):
            target.validate_request_generation_config(request(2))
        if source == "model":
            target.set_model_name(model_name="gpt-4o")
            assert target.capabilities.temperature_range == (0, 2)
            assert target.validate_request_generation_config(request(2)).temperature == 2
    finally:
        await target.cleanup_target_async()


async def test_round_robin_exposes_intersected_child_ranges_async(patch_central_database: Any) -> None:
    async with httpx.AsyncClient() as client:
        children = [
            OpenAIChatTarget(
                model_name="shared-model",
                endpoint="https://example.test/v1",
                api_key="test-only-key",
                extra_body_parameters={"extra_body": {"model": model}},
                httpx_client_kwargs={"http_client": client},
            )
            for model in ["gpt-4o", "Mistral-Large-3"]
        ]
        target = RoundRobinTarget(targets=children)
        assert target.capabilities.supports_temperature
        assert target.capabilities.temperature_range == (0, 1)
        assert target.validate_request_generation_config(request(1)).temperature == 1
        with pytest.raises(ValueError, match="between 0 and 1"):
            target.validate_request_generation_config(request(2))


async def test_no_override_preserves_request_body_async(target: OpenAIChatTarget) -> None:
    body = await target._construct_request_body_async(conversation=[request()], json_config=JsonResponseConfig())
    original = deepcopy(body)
    target._resolve_request_temperature(body=body, config=RequestGenerationConfig())
    assert body == original


@pytest.mark.parametrize(
    ("requested", "default", "passthrough", "expected"),
    [(0, 0.2, 1.7, 0), (None, 0.2, 1.7, 1.7), (None, 0.2, None, 0.2), (None, None, None, None)],
)
async def test_transport_retry_retains_resolved_value_and_paces_each_attempt_async(
    target: OpenAIChatTarget,
    requested: float | None,
    default: float | None,
    passthrough: float | None,
    expected: float | None,
) -> None:
    message = request(requested)
    target._temperature = default
    target._extra_body_parameters = {"extra_body": {"temperature": passthrough}} if passthrough is not None else {}
    target._max_requests_per_minute = 60
    attempts = 0

    async def respond_async(**kwargs: Any) -> ChatCompletion:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message.get_piece().prompt_metadata.update(RequestGenerationConfig(temperature=1.9).to_metadata())
            target._temperature = 1.8
            target._extra_body_parameters = {"extra_body": {"temperature": 1.6}}
            raise EmptyResponseException(message="Retry this empty response")
        return completion()

    with (
        patch.object(target, "_get_normalized_conversation_async", new_callable=AsyncMock, return_value=[message]),
        patch.object(target, "_resolve_request_temperature", wraps=target._resolve_request_temperature) as resolve,
        patch.object(
            target._client.chat.completions, "create", new_callable=AsyncMock, side_effect=respond_async
        ) as send,
        patch.object(target._send_prompt_to_target_async.retry, "sleep", new_callable=AsyncMock),
        patch.object(target_utils.asyncio, "sleep", new_callable=AsyncMock) as pace,
    ):
        await target.send_prompt_async(message=message)
    assert [
        call.kwargs.get("extra_body", {}).get("temperature", call.kwargs.get("temperature"))
        for call in send.call_args_list
    ] == [expected, expected]
    assert send.call_args.kwargs["extra_body"].get("temperature") == expected
    resolve.assert_called_once()
    assert pace.await_count == 2
    assert all(call.args == (1,) for call in pace.await_args_list)
    info = RequestGenerationInfo.from_metadata(metadata=message.get_piece().prompt_metadata)
    assert info is not None
    assert info.temperature.requested == requested
    assert info.temperature.resolved == expected
    if expected is None:
        assert info.temperature.source == "provider_default"
        assert all("temperature" not in call.kwargs for call in send.call_args_list)


async def test_reusing_request_starts_a_new_temperature_resolution_async(target: OpenAIChatTarget) -> None:
    message = request(0)
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=message)
        message.get_piece().prompt_metadata.update(RequestGenerationConfig(temperature=1.3).to_metadata())
        await target.send_prompt_async(message=message)
    assert [call.kwargs["temperature"] for call in send.call_args_list] == [0, 1.3]


async def test_scorer_keeps_default_and_does_not_copy_attack_temperature_async(
    target: OpenAIChatTarget, sqlite_instance: SQLiteMemory
) -> None:
    scorer = SelfAskTrueFalseScorer(chat_target=target)
    identifier = scorer.get_identifier()
    judge_response = completion()
    judge_response.choices[0].message.content = json.dumps(
        {"score_value": True, "description": "Objective achieved", "rationale": "Confirmed by the judge."}
    )
    with patch.object(
        target._client.chat.completions,
        "create",
        new_callable=AsyncMock,
        side_effect=[completion(), judge_response],
    ) as send:
        response = await PromptNormalizer().send_prompt_async(message=request(0), target=target)
        response.get_piece().prompt_metadata.update(RequestGenerationConfig(temperature=1.9).to_metadata())
        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(response),
            expectation=ScoringExpectation(objective="Answer the request"),
        )
    assert [call.kwargs["temperature"] for call in send.call_args_list] == [0, 0.2]
    assert scorer.get_identifier().hash == identifier.hash
    stored = await sqlite_instance.get_scores_async(score_ids=[str(scores[0].id)])
    assert len(stored) == 1
    assert stored[0].get_value() is True
    assert stored[0].score_rationale == "Confirmed by the judge."
    assert stored[0].scorer_class_identifier.hash == identifier.hash


async def test_conflicting_current_pieces_fail_before_normalization_async(target: OpenAIChatTarget) -> None:
    message = request(0)
    message.message_pieces.append(
        MessagePiece(
            role="user",
            original_value="second",
            conversation_id=message.get_piece().conversation_id,
            prompt_metadata=RequestGenerationConfig(temperature=1).to_metadata(),
        )
    )
    with patch.object(target, "_get_normalized_conversation_async", new_callable=AsyncMock) as normalize:
        with pytest.raises(ValueError, match="Conflicting"):
            await target.send_prompt_async(message=message)
    normalize.assert_not_called()


@pytest.mark.parametrize(
    "extras",
    [
        {"reasoning_effort": "high"},
        {"reasoning_effort": "none", "extra_body": {"reasoning_effort": "high"}},
        {"extra_body": {"model": "o3"}},
        {"extra_body": {"modalities": ["text", "audio"]}},
        {"stream": True},
    ],
)
def test_model_passthrough_restrictions_disable_capability(patch_central_database: Any, extras: dict[str, Any]) -> None:
    target = OpenAIChatTarget(
        model_name="gpt-4o", endpoint="https://example.test/v1", api_key="test-only-key", extra_body_parameters=extras
    )
    assert not target.capabilities.supports_temperature
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        target.validate_request_generation_config(request(0))


@pytest.mark.parametrize(
    "extras",
    [
        {"reasoning_effort": "high"},
        {"extra_body": {"reasoning_effort": "high"}},
        {"stream": True},
        {"extra_body": {"stream": True}},
        {"modalities": ["text", "audio"]},
        {"extra_body": {"modalities": ["text", "audio"]}},
    ],
)
@pytest.mark.parametrize("model", ["private-deployment", "gpt-5", "gpt-5.5"])
async def test_explicit_capability_does_not_bypass_mode_validation_async(
    patch_central_database: Any, extras: dict[str, Any], model: str
) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True)),
        extra_body_parameters=extras,
    )
    assert not target.capabilities.supports_temperature
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            await target.send_prompt_async(message=request(0))
    send.assert_not_called()
