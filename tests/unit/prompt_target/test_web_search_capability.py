# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import copy
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from openai.types.responses import Response
from unit.assert_mocks import make_assert_config, make_assert_systematization
from unit.mocks import MockPromptTarget

from pyrit.datasets import AssertProvider
from pyrit.exceptions import BadRequestException
from pyrit.executor.promptgen import AssertGenerator
from pyrit.memory import MemoryInterface
from pyrit.models import CapabilityName, JsonResponseConfig, Message, TargetCapabilities, WebSearchConfig, WebSearchMode
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import OpenAIResponseTarget, TargetConfiguration
from pyrit.prompt_target.common.discover_target_capabilities import discover_target_capabilities_async


def _response_with_search(*, search_status: str | None, text: str = '{"answer": "A policy."}') -> Response:
    search = (
        []
        if search_status is None
        else [
            {
                "id": "search-1",
                "type": "web_search_call",
                "status": search_status,
                "action": {"type": "search", "query": "published return policy"},
            }
        ]
    )
    return Response.model_validate(
        {
            "id": "response-1",
            "object": "response",
            "created_at": 1,
            "model": "test",
            "parallel_tool_calls": False,
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "status": "completed",
            "output": [
                *search,
                {
                    "id": "message-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
        }
    )


def _search_target(*, extra: dict[str, object] | None = None) -> OpenAIResponseTarget:
    return OpenAIResponseTarget(
        endpoint="https://example.org/openai",
        model_name="test",
        api_key="test-key",
        extra_body_parameters={"tools": [{"type": "web_search"}], **(extra or {})},
    )


def _search_request(mode: WebSearchMode) -> Message:
    message = Message.from_prompt(prompt="Find the published policy.", role="user")
    message.get_piece().prompt_metadata = {
        **WebSearchConfig(mode=mode).to_metadata(),
        **JsonResponseConfig(enabled=True).to_metadata(),
    }
    return message


@pytest.mark.usefixtures("patch_central_database")
class TestWebSearchCapability:
    @pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
    def test_responses_target_declares_configured_search(self, tool_type: str) -> None:
        target = OpenAIResponseTarget(
            endpoint="https://example.org/openai",
            model_name="test",
            api_key="test-key",
            extra_body_parameters={"tools": [{"type": tool_type}]},
        )
        assert target.capabilities.includes(capability=CapabilityName.WEB_SEARCH)
        assert TargetCapabilities.model_validate(target.capabilities.model_dump(mode="json")).supports_web_search

    @pytest.mark.parametrize(
        "parameters", [None, {"tools": []}, {"tools": [{"type": "web_search"}], "tool_choice": "none"}]
    )
    def test_search_is_not_declared_without_an_enabled_tool(self, parameters: dict[str, object] | None) -> None:
        target = OpenAIResponseTarget(
            endpoint="https://example.org/openai",
            model_name="test",
            api_key="test-key",
            extra_body_parameters=parameters,
        )
        assert not target.capabilities.supports_web_search

    def test_custom_capabilities_override_automatic_search_declaration(self) -> None:
        target = OpenAIResponseTarget(
            endpoint="https://example.org/openai",
            model_name="test",
            api_key="test-key",
            extra_body_parameters={"tools": [{"type": "web_search"}]},
            custom_configuration=TargetConfiguration(capabilities=TargetCapabilities()),
        )
        assert not target.capabilities.supports_web_search

    @pytest.mark.parametrize("enabled", [False, True])
    async def test_discovery_preserves_search_without_running_a_search_probe_async(self, enabled: bool) -> None:
        target = OpenAIResponseTarget(
            endpoint="https://example.org/openai",
            model_name="test",
            api_key="test-key",
            extra_body_parameters={"tools": [{"type": "web_search"}]} if enabled else None,
        )
        with (
            patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send,
            patch(
                "pyrit.prompt_target.common.discover_target_capabilities._discover_input_modalities_async",
                new_callable=AsyncMock,
                return_value=target.capabilities.input_modalities,
            ),
        ):
            discovered = await discover_target_capabilities_async(
                target=target, capabilities=[CapabilityName.WEB_SEARCH], apply=True
            )
        send.assert_not_called()
        assert discovered.supports_web_search is enabled
        assert target.capabilities.supports_web_search is enabled

    @pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
    async def test_required_search_selects_the_configured_tool_async(self, tool_type: str) -> None:
        target = _search_target(extra={"tools": [{"type": tool_type}]})
        request = _search_request(WebSearchMode.REQUIRED)
        body = await target._construct_request_body_async(conversation=[request], json_config=JsonResponseConfig())
        assert body["tool_choice"] == {"type": tool_type}

    async def test_disabled_search_filters_hosted_search_without_mutating_the_target_async(self) -> None:
        parameters = {
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "get_policy", "parameters": {"type": "object", "properties": {}}},
            ],
            "tool_choice": {"type": "web_search"},
        }
        original = copy.deepcopy(parameters)
        target = _search_target(extra=parameters)
        body = await target._construct_request_body_async(
            conversation=[_search_request(WebSearchMode.DISABLED)], json_config=JsonResponseConfig()
        )
        assert body["tools"] == original["tools"][1:]
        assert body["tool_choice"] == "auto"
        assert parameters == original
        assert target.capabilities.supports_web_search
        subsequent = await target._construct_request_body_async(
            conversation=[_search_request(WebSearchMode.DEFAULT)], json_config=JsonResponseConfig()
        )
        assert subsequent["tools"] == original["tools"]
        assert subsequent["tool_choice"] == original["tool_choice"]

    async def test_required_search_retains_provider_evidence_async(self, sqlite_instance: MemoryInterface) -> None:
        target = _search_target()
        conversation_id = str(uuid4())
        with patch.object(
            target._async_client.responses,
            "create",
            new_callable=AsyncMock,
            return_value=_response_with_search(search_status="completed"),
        ) as create:
            response = await PromptNormalizer().send_prompt_async(
                message=_search_request(WebSearchMode.REQUIRED), target=target, conversation_id=conversation_id
            )
        assert json.loads(response.get_value()) == {"answer": "A policy."}
        assert create.call_args.kwargs["tool_choice"] == {"type": "web_search"}
        stored = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
        tool_calls = [piece for piece in stored if piece.converted_value_data_type == "tool_call"]
        assert len(tool_calls) == 1
        assert json.loads(tool_calls[0].converted_value)["status"] == "completed"

    @pytest.mark.parametrize("search_status", [None, "in_progress"])
    async def test_required_search_does_not_accept_unverified_json_async(self, search_status: str | None) -> None:
        target = _search_target()
        with patch.object(
            target._async_client.responses,
            "create",
            new_callable=AsyncMock,
            return_value=_response_with_search(search_status=search_status),
        ) as create:
            with pytest.raises(BadRequestException, match="no completed required"):
                await target.send_prompt_async(message=_search_request(WebSearchMode.REQUIRED))
        create.assert_called_once()

    async def test_disabled_search_rejects_unexpected_provider_search_async(self) -> None:
        target = _search_target()
        with patch.object(
            target._async_client.responses,
            "create",
            new_callable=AsyncMock,
            return_value=_response_with_search(search_status="completed"),
        ) as create:
            with pytest.raises(BadRequestException, match="search was disabled"):
                await target.send_prompt_async(message=_search_request(WebSearchMode.DISABLED))
        assert not create.call_args.kwargs["tools"]

    async def test_plain_target_does_not_silently_accept_required_search_async(self) -> None:
        target = MockPromptTarget()
        with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
            with pytest.raises(ValueError, match="cannot enforce"):
                await target.send_prompt_async(message=_search_request(WebSearchMode.REQUIRED))
        send.assert_not_called()

    async def test_false_search_declaration_is_not_enough_to_enforce_policy_async(self) -> None:
        target = MockPromptTarget(
            custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_web_search=True))
        )
        with pytest.raises(ValueError, match="cannot enforce"):
            target.validate_web_search_config(WebSearchConfig(mode=WebSearchMode.REQUIRED))
        with pytest.raises(ValueError, match="cannot enforce"):
            target.validate_web_search_config(WebSearchConfig(mode=WebSearchMode.DISABLED))

    async def test_normalization_must_not_drop_explicit_search_policy_async(self) -> None:
        target = _search_target()
        with (
            patch.object(
                target,
                "_get_normalized_conversation_async",
                new_callable=AsyncMock,
                return_value=[Message.from_prompt(prompt="Find a policy.", role="user")],
            ),
            patch.object(target._async_client.responses, "create", new_callable=AsyncMock) as create,
        ):
            with pytest.raises(ValueError, match="Normalization changed"):
                await target.send_prompt_async(message=_search_request(WebSearchMode.REQUIRED))
        create.assert_not_called()

    async def test_generation_cannot_succeed_without_required_search_evidence_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        target = _search_target()
        provider = AssertProvider(
            dataset_name="required_search",
            generator=AssertGenerator(target=target),
            config=make_assert_config(web_search=True),
        )
        response = _response_with_search(search_status=None, text=make_assert_systematization().model_dump_json())
        with patch.object(
            target._async_client.responses, "create", new_callable=AsyncMock, return_value=response
        ) as create:
            with pytest.raises(RuntimeError):
                await provider.fetch_dataset_async()
        create.assert_called_once()
        assert create.call_args.kwargs["tool_choice"] == {"type": "web_search"}
        assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
