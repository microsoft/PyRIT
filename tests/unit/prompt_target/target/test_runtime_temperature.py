# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai.types.chat import ChatCompletion
from unit.mocks import openai_chat_response_json_dict

from pyrit.exceptions import EmptyResponseException
from pyrit.memory import SQLiteMemory
from pyrit.models import (
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
from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget
from pyrit.prompt_target.common import utils as target_utils
from pyrit.prompt_target.common.target_capabilities import get_known_capabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
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


@pytest.mark.parametrize("temperature", [None, 0, 0.9, 2])
@pytest.mark.parametrize("legacy", [False, True])
async def test_send_records_effective_temperature_without_changing_identity_async(
    target: OpenAIChatTarget, sqlite_instance: SQLiteMemory, temperature: float | None, legacy: bool
) -> None:
    identifier = target.get_identifier()
    response = completion()
    message = request(temperature)
    if legacy:
        message.get_piece().prompt_metadata = {"request_temperature": temperature}
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


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("value", [True, "0.3", float("nan"), float("inf"), -0.1, 2.1])
async def test_invalid_override_fails_before_io_async(target: OpenAIChatTarget, value: Any, legacy: bool) -> None:
    message = request()
    message.get_piece().prompt_metadata.update(
        {"request_temperature": value} if legacy else {"request_generation_config": {"temperature": value}}
    )
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


@pytest.mark.parametrize("model", ["o1", "o3", "gpt-5", "gpt-4o-audio-preview", "unknown"])
async def test_unsupported_models_reject_override_async(patch_central_database: Any, model: str) -> None:
    target = OpenAIChatTarget(model_name=model, endpoint="https://example.test/v1", api_key="test-only-key")
    assert not target.capabilities.supports_temperature
    with patch.object(target._client.chat.completions, "create", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="does not support runtime temperature"):
            await target.send_prompt_async(message=request(0))
    send.assert_not_called()


async def test_unimplemented_adapter_rejects_override_async(patch_central_database: Any) -> None:
    target = OpenAIResponseTarget(model_name="gpt-4o", endpoint="https://example.test/v1", api_key="test-only-key")
    with pytest.raises(ValueError, match="does not support runtime temperature"):
        await target.send_prompt_async(message=request(0))


@pytest.mark.parametrize("custom", [False, True])
async def test_profile_or_custom_capability_cannot_enable_an_unimplemented_adapter_async(
    patch_central_database: Any, custom: bool
) -> None:
    target = OpenAIResponseTarget(
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


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-5.1"])
def test_known_profile_enables_temperature(patch_central_database: Any, model: str) -> None:
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


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4.1", "gpt-4o-2024-08-06", "private-deployment"])
async def test_explicit_capability_enables_unlisted_models_async(patch_central_database: Any, model: str) -> None:
    target = OpenAIChatTarget(
        model_name=model,
        endpoint="https://example.test/v1",
        api_key="test-only-key",
        custom_configuration=TargetConfiguration(
            capabilities=OpenAIChatTarget.get_default_configuration().capabilities.model_copy(
                update={"supports_temperature": True}
            )
        ),
    )
    assert target.capabilities.supports_temperature
    with patch.object(
        target._client.chat.completions, "create", new_callable=AsyncMock, return_value=completion()
    ) as send:
        await target.send_prompt_async(message=request(0))
    assert send.call_args.kwargs["temperature"] == 0


def test_temperature_support_comes_from_profile_not_model_name(patch_central_database: Any) -> None:
    profile = get_known_capabilities("gpt-4o")
    with patch("pyrit.prompt_target.openai.openai_chat_target.get_known_capabilities", return_value=profile):
        target = OpenAIChatTarget(
            model_name="new-model-in-catalog", endpoint="https://example.test/v1", api_key="test-only-key"
        )
    assert target.capabilities.supports_temperature


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
    normalized.get_piece().prompt_metadata["request_temperature"] = 1.8
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
    message.get_piece().prompt_metadata["_pyrit_temperature"] = {"resolved_temperature": 99}
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
    assert "_pyrit_temperature" not in message.get_piece().prompt_metadata


def test_new_capability_does_not_change_existing_identifier_projection() -> None:
    before = TargetConfiguration(capabilities=TargetCapabilities())
    after = TargetConfiguration(capabilities=TargetCapabilities(supports_temperature=True))
    assert after.as_identifier_params() == before.as_identifier_params()
    assert "supports_temperature" not in after.as_identifier_params()["capabilities"]


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


async def test_conflicting_legacy_and_current_options_fail_before_normalization_async(target: OpenAIChatTarget) -> None:
    message = request(0)
    message.get_piece().prompt_metadata["request_temperature"] = 1
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


@pytest.mark.parametrize("extras", [{"reasoning_effort": "high"}, {"extra_body": {"reasoning_effort": "high"}}])
async def test_explicit_capability_does_not_bypass_mode_validation_async(
    patch_central_database: Any, extras: dict[str, Any]
) -> None:
    target = OpenAIChatTarget(
        model_name="private-deployment",
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
