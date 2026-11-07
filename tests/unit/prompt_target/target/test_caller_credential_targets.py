# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from unit.async_utils import get_defined_tasks, settle_tasks_async, wait_for_completion_async, wait_for_event_async
from unit.mocks import openai_chat_response_json_dict

from pyrit.auth import (
    CallerCredentialRequiredError,
    CallerCredentialStore,
    caller_principal_scope,
    create_caller_credential_provider,
)
from pyrit.models import Message
from pyrit.prompt_target import LiteLLMChatTarget, OpenAIChatTarget, OpenAIResponseTarget, PromptTarget, RealtimeTarget
from pyrit.registry import TargetRegistry


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("header", ["Authorization", "aUtHoRiZaTiOn", "api-key", "X-API-Key", "x-goog-api-key"])
@pytest.mark.parametrize("source", ["headers", "default_headers"])
def test_token_provider_rejects_static_authentication_headers(
    store: CallerCredentialStore, header: str, source: str
) -> None:
    provider = create_caller_credential_provider(store=store, credential_id="shared", component_name="shared")
    with pytest.raises(ValueError, match="authentication headers"):
        OpenAIChatTarget(
            model_name="test-model",
            endpoint="https://example.test/v1",
            api_key=provider,
            headers=json.dumps({header: "static-key"}) if source == "headers" else None,
            httpx_client_kwargs={"default_headers": {header: "static-key"}} if source == "default_headers" else None,
        )


@pytest.fixture
def store() -> CallerCredentialStore:
    store = CallerCredentialStore()
    store.set_credential(principal="alice", credential_id="shared", credential="alice-key")
    store.set_credential(principal="bob", credential_id="shared", credential="bob-key")
    return store


class _RecordingTransport:
    def __init__(self) -> None:
        self.authorizations: list[str | None] = []
        self.first_request_received = asyncio.Event()
        self.release_first_request = asyncio.Event()

    async def handle_async(self, request: httpx.Request) -> httpx.Response:
        self.authorizations.append(request.headers.get("Authorization"))
        if len(self.authorizations) == 1:
            self.first_request_received.set()
            await self.release_first_request.wait()
        return httpx.Response(200, json=openai_chat_response_json_dict())


def _target(*, store: CallerCredentialStore, http_client: httpx.AsyncClient) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key=create_caller_credential_provider(store=store, credential_id="shared", component_name="shared"),
        httpx_client_kwargs={"http_client": http_client},
    )


async def _send_as_async(*, target: PromptTarget, principal: str | None) -> None:
    with caller_principal_scope(principal):
        await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))


def test_supported_targets_declare_caller_credential_parameter() -> None:
    assert OpenAIChatTarget.caller_credential_parameter == "api_key"
    assert LiteLLMChatTarget.caller_credential_parameter == "api_key"
    assert PromptTarget.caller_credential_parameter is None
    # Realtime reuses a websocket authenticated when the conversation opened, not per request.
    assert RealtimeTarget.caller_credential_parameter is None


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_target_sends_each_callers_own_key_concurrently_async(store: CallerCredentialStore) -> None:
    transport = _RecordingTransport()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
        target = _target(store=store, http_client=http_client)

        alice = asyncio.create_task(_send_as_async(target=target, principal="alice"))
        bob = None
        try:
            await wait_for_event_async(event=transport.first_request_received, tasks=[alice])
            bob = asyncio.create_task(_send_as_async(target=target, principal="bob"))
            await asyncio.sleep(0)
            transport.release_first_request.set()
            await wait_for_completion_async(future=asyncio.gather(alice, bob))
        finally:
            transport.release_first_request.set()
            await settle_tasks_async(*get_defined_tasks(alice, bob))

    assert transport.authorizations == ["Bearer alice-key", "Bearer bob-key"]


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_target_uses_replaced_key_on_next_request_async(store: CallerCredentialStore) -> None:
    transport = _RecordingTransport()
    transport.release_first_request.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
        target = _target(store=store, http_client=http_client)

        await _send_as_async(target=target, principal="alice")
        store.set_credential(principal="alice", credential_id="shared", credential="alice-new-key")
        await _send_as_async(target=target, principal="alice")

    assert transport.authorizations == ["Bearer alice-key", "Bearer alice-new-key"]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("principal", [None, "carol"])
async def test_openai_target_without_callers_key_never_falls_back_async(
    store: CallerCredentialStore, principal: str | None
) -> None:
    transport = _RecordingTransport()
    environment = {"OPENAI_API_KEY": "server-key", "OPENAI_CHAT_KEY": "server-key"}
    with patch.dict("os.environ", environment):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
            target = _target(store=store, http_client=http_client)

            with pytest.raises(CallerCredentialRequiredError):
                await _send_as_async(target=target, principal=principal)

    assert transport.authorizations == []


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_reconstructed_copy_resolves_the_current_caller_async(store: CallerCredentialStore) -> None:
    source = OpenAIChatTarget(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key=create_caller_credential_provider(store=store, credential_id="shared", component_name="shared"),
    )
    copy = TargetRegistry.get_registry_singleton().recreate_instance(source=source, params={"temperature": 0.5})
    try:
        assert copy._api_key is source._api_key
        with caller_principal_scope("bob"):
            assert await copy._client._refresh_api_key() == "bob-key"
        with pytest.raises(CallerCredentialRequiredError):
            await copy._client._refresh_api_key()
    finally:
        await copy.cleanup_target_async()
        await source.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_reconstructed_copy_rejects_static_authentication_async(store: CallerCredentialStore) -> None:
    source = OpenAIChatTarget(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key=create_caller_credential_provider(store=store, credential_id="shared", component_name="shared"),
    )
    try:
        with pytest.raises(ValueError, match="authentication headers"):
            TargetRegistry.get_registry_singleton().recreate_instance(
                source=source, params={"headers": json.dumps({"Authorization": "static-key"})}
            )
    finally:
        await source.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_cls", [OpenAIChatTarget, OpenAIResponseTarget])
@pytest.mark.parametrize("reconstruct", [False, True])
@pytest.mark.parametrize("header", ["Authorization", "aPi-KeY"])
async def test_openai_request_authentication_headers_are_rejected_async(
    store: CallerCredentialStore,
    target_cls: type[OpenAIChatTarget] | type[OpenAIResponseTarget],
    reconstruct: bool,
    header: str,
) -> None:
    options = {"extra_headers": {header: "static-key"}}
    source = target_cls(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key=create_caller_credential_provider(store=store, credential_id="shared", component_name="shared"),
        extra_body_parameters=None if reconstruct else options,
    )
    target = (
        TargetRegistry.get_registry_singleton().recreate_instance(
            source=source, params={"extra_body_parameters": options}
        )
        if reconstruct
        else source
    )
    message = Message.from_prompt(prompt="hello", role="user")
    try:
        with pytest.raises(ValueError, match="authentication headers"):
            await target._construct_request_body_async(
                conversation=[message],
                json_config=target._get_json_response_config(message_piece=message.message_pieces[0]),
            )
    finally:
        await target.cleanup_target_async()
        if target is not source:
            await source.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_concurrency_check_reports_early_send_failure_async(store: CallerCredentialStore) -> None:
    with patch(__name__ + "._send_as_async", side_effect=CallerCredentialRequiredError("early send failure")):
        with pytest.raises(CallerCredentialRequiredError, match="early send failure"):
            await test_openai_target_sends_each_callers_own_key_concurrently_async(store)
