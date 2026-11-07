# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import patch

import httpx
import pytest
from unit.mocks import openai_chat_response_json_dict

from pyrit.auth import (
    CallerCredentialRequiredError,
    CallerCredentialStore,
    caller_principal_scope,
    create_caller_credential_provider,
)
from pyrit.models import Message
from pyrit.prompt_target import LiteLLMChatTarget, OpenAIChatTarget, PromptTarget, RealtimeTarget
from pyrit.registry import TargetRegistry


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
        await transport.first_request_received.wait()
        bob = asyncio.create_task(_send_as_async(target=target, principal="bob"))
        await asyncio.sleep(0)
        transport.release_first_request.set()
        await asyncio.gather(alice, bob)

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
