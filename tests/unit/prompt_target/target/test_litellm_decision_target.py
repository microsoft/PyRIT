# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pyrit.exceptions import PyritException, RateLimitException, get_retry_max_num_attempts
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import LiteLLMChatTarget, LiteLLMDecisionTarget

MODEL = "openrouter/microsoft/microsoft-decision-1"
ENDPOINT = "https://openrouter.ai/api"
REQUEST = {
    "state": {"objective": "Name the capital of France.", "response": "Paris."},
    "questions": {
        "objective_achieved": {
            "type": "noul",
            "instructions": "Does the response achieve the objective?",
            "criteria": {"true": "Complete answer.", "false": "Incomplete answer."},
        }
    },
}
RESPONSE = {
    "model": "microsoft/microsoft-decision-1-version",
    "provider": "Azure",
    "id": "decision-test",
    "answers": {"objective_achieved": {"type": "noul", "noul": 0.82}},
    "usage": {"input_tokens": 64, "output_tokens": 1, "cost": 0.000001},
}


def _request(text: str) -> Message:
    return MessagePiece(role="user", original_value=text).to_message()


@pytest.fixture
def decision_sdk() -> Iterator[MagicMock]:
    import litellm
    from litellm.types.decisions import DecisionsResponse

    sdk = MagicMock(spec=litellm)
    sdk.asystemone = None
    sdk.completion_cost = MagicMock(spec=litellm.completion_cost)
    sdk.exceptions = litellm.exceptions
    response = DecisionsResponse.model_validate(RESPONSE)
    response._hidden_params["response_cost"] = 0.000001
    sdk.adecisions = AsyncMock(return_value=response)
    with patch.object(LiteLLMDecisionTarget, "_import_litellm", return_value=sdk):
        yield sdk


@pytest.fixture
def target(patch_central_database: None, decision_sdk: MagicMock) -> LiteLLMDecisionTarget:
    return LiteLLMDecisionTarget(model_name=MODEL, endpoint=ENDPOINT, api_key="test-api-key")


@pytest.mark.usefixtures("patch_central_database")
class TestLiteLLMDecisionTarget:
    async def test_sdk_request_and_usage_async(self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock) -> None:
        responses = await target.send_prompt_async(message=_request(json.dumps(REQUEST)))

        decision_sdk.adecisions.assert_awaited_once()
        body = decision_sdk.adecisions.call_args.kwargs
        assert body["model"] == MODEL
        assert body["api_base"] == ENDPOINT
        assert body["api_key"] == "test-api-key"
        assert body["state"] == REQUEST["state"]
        assert body["questions"] == REQUEST["questions"]
        assert body["num_retries"] == max(get_retry_max_num_attempts() - 1, 0)
        assert "messages" not in body
        assert "drop_params" not in body
        assert json.loads(responses[0].get_value()) == RESPONSE
        metadata = responses[0].get_piece().prompt_metadata
        assert metadata["token_usage_input_tokens"] == 64
        assert metadata["token_usage_output_tokens"] == 1
        assert metadata["token_usage_total_tokens"] == 65
        assert float(metadata["token_usage_cost"]) == 0.000001

    async def test_prefers_native_systemone_api_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock
    ) -> None:
        native = AsyncMock(return_value=decision_sdk.adecisions.return_value)
        with patch.object(decision_sdk, "asystemone", native, create=True):
            await target.send_prompt_async(message=_request(json.dumps(REQUEST)))

        native.assert_awaited_once()
        decision_sdk.adecisions.assert_not_awaited()

    async def test_missing_sdk_api_raises_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock
    ) -> None:
        decision_sdk.adecisions = None
        with pytest.raises(ImportError, match="litellm>=1.104.2"):
            await target.send_prompt_async(message=_request(json.dumps(REQUEST)))

    def test_reuses_chat_target_without_chat_capabilities(self, target: LiteLLMDecisionTarget) -> None:
        assert isinstance(target, LiteLLMChatTarget)
        assert not target.capabilities.supports_multi_turn
        assert not target.capabilities.supports_system_prompt
        assert not target.capabilities.supports_multi_message_pieces
        assert target.capabilities.supported_input_modalities == ["text"]

    def test_empty_model_raises(self) -> None:
        with pytest.raises(ValueError, match="model name"):
            LiteLLMDecisionTarget(model_name=" ")

    def test_identifier_excludes_credentials(self, target: LiteLLMDecisionTarget) -> None:
        assert "test-api-key" not in target.get_identifier().model_dump_json()
        assert (
            target.get_identifier()
            == LiteLLMDecisionTarget(model_name=MODEL, endpoint=ENDPOINT, api_key="other-key").get_identifier()
        )
        assert target.get_identifier() != LiteLLMDecisionTarget(model_name="typesafe/jev-latest").get_identifier()

    def test_identifier_keeps_routing_headers_but_not_credential_headers(self) -> None:
        first = LiteLLMDecisionTarget(
            model_name=MODEL, headers={"X-Route": "primary", "Authorization": "header-secret"}
        )
        other_key = LiteLLMDecisionTarget(
            model_name=MODEL, headers={"X-Route": "primary", "Authorization": "other-secret"}
        )
        other_route = LiteLLMDecisionTarget(model_name=MODEL, headers={"X-Route": "secondary"})
        assert first.get_identifier() == other_key.get_identifier()
        assert first.get_identifier() != other_route.get_identifier()
        assert "header-secret" not in first.get_identifier().model_dump_json()

    @pytest.mark.parametrize("cost", [0.0, 0.000001, None])
    async def test_decision_cost_does_not_use_chat_pricing_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock, cost: float | None
    ) -> None:
        from litellm.types.decisions import DecisionsResponse

        usage: dict[str, int | float] = {"input_tokens": 64, "output_tokens": 1}
        if cost is not None:
            usage["cost"] = cost
        decision_sdk.adecisions.return_value = DecisionsResponse.model_validate({**RESPONSE, "usage": usage})
        responses = await target.send_prompt_async(message=_request(json.dumps(REQUEST)))
        metadata = responses[0].get_piece().prompt_metadata
        if cost is None:
            assert "token_usage_cost" not in metadata
        else:
            assert float(metadata["token_usage_cost"]) == cost
        decision_sdk.completion_cost.assert_not_called()

    @pytest.mark.parametrize(
        "text",
        [
            "not JSON",
            "[]",
            "{}",
            json.dumps({**REQUEST, "model": "other/model"}),
            json.dumps({"state": None, "questions": REQUEST["questions"]}),
            json.dumps({"state": "text", "questions": []}),
            json.dumps({"state": "text", "questions": {}}),
        ],
    )
    async def test_invalid_request_does_not_call_sdk_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock, text: str
    ) -> None:
        with pytest.raises(ValueError, match="decision|Decision"):
            await target.send_prompt_async(message=_request(text))
        decision_sdk.adecisions.assert_not_awaited()

    async def test_token_provider_is_resolved_async(self, decision_sdk: MagicMock) -> None:
        token_provider = AsyncMock(return_value="current-test-key")
        target = LiteLLMDecisionTarget(model_name=MODEL, api_key=token_provider)
        await target.send_prompt_async(message=_request(json.dumps(REQUEST)))
        token_provider.assert_awaited_once()
        assert decision_sdk.adecisions.call_args.kwargs["api_key"] == "current-test-key"

    async def test_concurrent_requests_keep_separate_state_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock
    ) -> None:
        await asyncio.gather(
            *(
                target.send_prompt_async(message=_request(json.dumps({**REQUEST, "state": {"response": value}})))
                for value in ("first", "second")
            )
        )
        states = [call.kwargs["state"]["response"] for call in decision_sdk.adecisions.call_args_list]
        assert set(states) == {"first", "second"}
        assert REQUEST["state"]["response"] == "Paris."

    @pytest.mark.parametrize("error_name", ["AuthenticationError", "RateLimitError"])
    async def test_sdk_errors_are_translated_without_an_extra_retry_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock, error_name: str
    ) -> None:
        response = httpx.Response(
            401 if error_name == "AuthenticationError" else 429,
            request=httpx.Request("POST", ENDPOINT),
        )
        error_class = getattr(decision_sdk.exceptions, error_name)
        decision_sdk.adecisions.side_effect = error_class(
            message="Provider error", response=response, model=MODEL, llm_provider="openrouter"
        )
        error = PyritException if error_name == "AuthenticationError" else RateLimitException
        with pytest.raises(error):
            await target.send_prompt_async(message=_request(json.dumps(REQUEST)))
        decision_sdk.adecisions.assert_awaited_once()

    async def test_connection_error_is_translated_async(
        self, *, target: LiteLLMDecisionTarget, decision_sdk: MagicMock
    ) -> None:
        decision_sdk.adecisions.side_effect = decision_sdk.exceptions.APIConnectionError(
            message="Connection failed", request=httpx.Request("POST", ENDPOINT), model=MODEL, llm_provider="openrouter"
        )
        with pytest.raises(RateLimitException):
            await target.send_prompt_async(message=_request(json.dumps(REQUEST)))
        decision_sdk.adecisions.assert_awaited_once()

    async def test_real_sdk_routes_openrouter_request_async(self) -> None:
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        target = LiteLLMDecisionTarget(model_name=MODEL, endpoint=ENDPOINT, api_key="test-api-key")
        http_response = httpx.Response(200, json=RESPONSE, request=httpx.Request("POST", f"{ENDPOINT}/alpha/decisions"))
        with patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, return_value=http_response) as post:
            responses = await target.send_prompt_async(message=_request(json.dumps(REQUEST)))

        post.assert_awaited_once()
        assert post.call_args.args[0] == f"{ENDPOINT}/alpha/decisions"
        body: dict[str, Any] = post.call_args.kwargs["json"]
        assert body == {**REQUEST, "model": "microsoft/microsoft-decision-1"}
        assert json.loads(responses[0].get_value()) == RESPONSE
