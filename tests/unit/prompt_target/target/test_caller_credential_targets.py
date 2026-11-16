# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest
from unit.async_utils import get_defined_tasks, settle_tasks_async, wait_for_completion_async, wait_for_event_async
from unit.mocks import openai_chat_response_json_dict

from pyrit.auth import CallerCredential, CallerCredentialRequiredError, caller_principal_scope
from pyrit.models import Message
from pyrit.prompt_target import (
    AzureBlobStorageTarget,
    AzureMLChatTarget,
    LiteLLMChatTarget,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    PromptShieldTarget,
    PromptTarget,
    RealtimeTarget,
)
from pyrit.registry import TargetRegistry

_SETTINGS = {"model_name": "test-model", "endpoint": "https://example.test/v1"}


async def _entra_style_token_provider() -> str:
    return "entra-token"


def _caller_target(
    *, http_client: httpx.AsyncClient | None = None, target_cls: type[OpenAIChatTarget] = OpenAIChatTarget
) -> OpenAIChatTarget:
    target = target_cls(
        **_SETTINGS,
        auth_mode="caller",
        httpx_client_kwargs={"http_client": http_client} if http_client is not None else None,
    )
    assert target.caller_credential is not None
    target.caller_credential.set_credential(principal="alice", credential="alice-key")
    target.caller_credential.set_credential(principal="bob", credential="bob-key")
    return target


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


async def _send_as_async(*, target: PromptTarget, principal: str | None) -> None:
    with caller_principal_scope(principal):
        await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))


def test_supported_auth_modes_declare_caller_keys() -> None:
    assert "caller" in OpenAIChatTarget.supported_auth_modes
    assert "caller" in OpenAIResponseTarget.supported_auth_modes
    assert "caller" in LiteLLMChatTarget.supported_auth_modes
    assert "caller" not in PromptTarget.supported_auth_modes
    # Realtime reuses a websocket authenticated when the conversation opened, not per request.
    assert "caller" not in RealtimeTarget.supported_auth_modes


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "build",
    [
        lambda: RealtimeTarget(**_SETTINGS, auth_mode="caller"),
        lambda: AzureMLChatTarget(endpoint="https://example.inference.ml.azure.com/score", auth_mode="caller"),
        lambda: PromptShieldTarget(endpoint="https://example.cognitiveservices.azure.com", auth_mode="caller"),
        lambda: AzureBlobStorageTarget(
            container_url="https://example.blob.core.windows.net/container", auth_mode="caller"
        ),
    ],
    ids=["realtime", "azure_ml", "prompt_shield", "blob_storage"],
)
def test_targets_without_caller_support_reject_caller_mode(build: Callable[[], PromptTarget]) -> None:
    with pytest.raises(ValueError, match="does not support auth_mode='caller'"):
        build()


@pytest.mark.usefixtures("patch_central_database")
def test_caller_mode_keeps_no_key_and_ignores_environment_keys() -> None:
    with patch.dict("os.environ", {"OPENAI_CHAT_KEY": "server-key"}):
        target = OpenAIChatTarget(**_SETTINGS, auth_mode="caller")

    assert isinstance(target.caller_credential, CallerCredential)
    assert target._api_key == target.caller_credential.get_credential_async


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("api_key", ["a-key", _entra_style_token_provider])
def test_caller_mode_rejects_an_explicit_key(api_key: object) -> None:
    with pytest.raises(ValueError, match="cannot be combined with an explicit api_key"):
        OpenAIChatTarget(**_SETTINGS, auth_mode="caller", api_key=api_key)  # type: ignore[arg-type]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "header",
    ["Authorization", "aUtHoRiZaTiOn", "api-key", "X-API-Key", "x-goog-api-key", "Ocp-Apim-Subscription-Key", "Cookie"],
)
@pytest.mark.parametrize("source", ["headers", "default_headers"])
def test_caller_mode_rejects_static_authentication_headers(header: str, source: str) -> None:
    with pytest.raises(ValueError, match="authentication headers"):
        OpenAIChatTarget(
            **_SETTINGS,
            auth_mode="caller",
            headers=json.dumps({header: "static-key"}) if source == "headers" else None,
            httpx_client_kwargs={"default_headers": {header: "static-key"}} if source == "default_headers" else None,
        )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("header", ["api-key", "Proxy-Authorization"])
async def test_direct_token_provider_keeps_static_gateway_headers_async(header: str) -> None:
    # A token provider passed in Python keeps working with a gateway's own auth header, as before caller keys.
    target = OpenAIChatTarget(
        **_SETTINGS,
        api_key=_entra_style_token_provider,
        headers=json.dumps({header: "gateway-key"}),
        extra_body_parameters={"extra_headers": {header: "gateway-key"}},
    )
    message = Message.from_prompt(prompt="hello", role="user")
    try:
        assert target.caller_credential is None
        body = await target._construct_request_body_async(
            conversation=[message],
            json_config=target._get_json_response_config(message_piece=message.message_pieces[0]),
        )
        assert body["extra_headers"] == {header: "gateway-key"}
    finally:
        await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_target_sends_each_callers_own_key_concurrently_async() -> None:
    transport = _RecordingTransport()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
        target = _caller_target(http_client=http_client)

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
async def test_openai_target_uses_replaced_key_on_next_request_async() -> None:
    transport = _RecordingTransport()
    transport.release_first_request.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
        target = _caller_target(http_client=http_client)

        await _send_as_async(target=target, principal="alice")
        assert target.caller_credential is not None
        target.caller_credential.set_credential(principal="alice", credential="alice-new-key")
        await _send_as_async(target=target, principal="alice")

    assert transport.authorizations == ["Bearer alice-key", "Bearer alice-new-key"]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("principal", [None, "carol"])
async def test_openai_target_without_callers_key_never_falls_back_async(principal: str | None) -> None:
    transport = _RecordingTransport()
    environment = {"OPENAI_API_KEY": "server-key", "OPENAI_CHAT_KEY": "server-key"}
    with patch.dict("os.environ", environment):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport.handle_async)) as http_client:
            target = _caller_target(http_client=http_client)

            with pytest.raises(CallerCredentialRequiredError):
                await _send_as_async(target=target, principal=principal)

    assert transport.authorizations == []


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_reconstructed_copy_shares_the_callers_keys_async() -> None:
    source = _caller_target()
    copy = TargetRegistry.get_registry_singleton().recreate_instance(source=source, params={"temperature": 0.5})
    try:
        assert copy.caller_credential is source.caller_credential
        with caller_principal_scope("bob"):
            assert await copy._client._refresh_api_key() == "bob-key"
        with pytest.raises(CallerCredentialRequiredError):
            await copy._client._refresh_api_key()
    finally:
        await copy.cleanup_target_async()
        await source.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_openai_reconstructed_copy_rejects_static_authentication_async() -> None:
    source = _caller_target()
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
@pytest.mark.parametrize("header", ["Authorization", "aPi-KeY", "Ocp-Apim-Subscription-Key"])
async def test_openai_request_authentication_headers_are_rejected_at_construction_async(
    target_cls: type[OpenAIChatTarget] | type[OpenAIResponseTarget],
    reconstruct: bool,
    header: str,
) -> None:
    options = {"extra_headers": {header: "static-key"}}
    if not reconstruct:
        with pytest.raises(ValueError, match="authentication headers"):
            target_cls(**_SETTINGS, auth_mode="caller", extra_body_parameters=options)
        return
    source = target_cls(**_SETTINGS, auth_mode="caller")
    try:
        with pytest.raises(ValueError, match="authentication headers"):
            TargetRegistry.get_registry_singleton().recreate_instance(
                source=source, params={"extra_body_parameters": options}
            )
    finally:
        await source.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_caller_mode_ignores_the_servers_extra_request_headers_async() -> None:
    # A server header such as a gateway subscription key would let callers ride on the server's account.
    transport = _RecordingTransport()
    transport.release_first_request.set()
    seen: list[httpx.Headers] = []
    original = transport.handle_async

    async def record_async(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return await original(request)

    environment = {"OPENAI_ADDITIONAL_REQUEST_HEADERS": json.dumps({"Ocp-Apim-Subscription-Key": "server-key"})}
    with patch.dict("os.environ", environment):
        async with httpx.AsyncClient(transport=httpx.MockTransport(record_async)) as http_client:
            target = _caller_target(http_client=http_client)
            await _send_as_async(target=target, principal="alice")

    assert seen[0]["Authorization"] == "Bearer alice-key"
    assert "Ocp-Apim-Subscription-Key" not in seen[0]


@pytest.mark.usefixtures("patch_central_database")
async def test_concurrency_check_reports_early_send_failure_async() -> None:
    with patch(__name__ + "._send_as_async", side_effect=CallerCredentialRequiredError("early send failure")):
        with pytest.raises(CallerCredentialRequiredError, match="early send failure"):
            await test_openai_target_sends_each_callers_own_key_concurrently_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_class", [OpenAIChatTarget, OpenAIResponseTarget])
def test_caller_mode_refuses_server_header_variables(
    target_class: type[OpenAIChatTarget] | type[OpenAIResponseTarget],
) -> None:
    with patch.dict("os.environ", {"OPENAI_CUSTOM_HEADERS": "Authorization: Bearer server-key"}):
        with pytest.raises(ValueError, match="OPENAI_CUSTOM_HEADERS"):
            target_class(**_SETTINGS, auth_mode="caller")
        direct = target_class(**_SETTINGS, api_key=lambda: "own-key")

    assert callable(direct._api_key)


@pytest.mark.usefixtures("patch_central_database")
def test_caller_mode_is_part_of_the_identity() -> None:
    caller = OpenAIChatTarget(**_SETTINGS, auth_mode="caller")
    direct = OpenAIChatTarget(**_SETTINGS, api_key=lambda: "own-key")
    shared = OpenAIChatTarget(**_SETTINGS, api_key="server-key")

    assert caller.get_identifier().attributes == {"caller_credential": True}
    assert caller.get_identifier().params == shared.get_identifier().params
    assert caller.get_identifier().hash != shared.get_identifier().hash
    assert direct.get_identifier().hash == shared.get_identifier().hash
