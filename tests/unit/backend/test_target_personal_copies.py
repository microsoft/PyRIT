# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Personal copies of existing targets, such as initializer-configured ones, with caller credentials."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import Request, status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pyrit.auth import CallerCredentialRequiredError, caller_principal_scope
from pyrit.backend.main import app
from pyrit.backend.models import CreateTargetRequest
from pyrit.backend.services import TargetService, get_target_service
from pyrit.backend.services.target_service import (
    clear_caller_credentials,
    get_caller_credential_store,
)
from pyrit.models.catalog import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec, TargetBinding
from pyrit.prompt_target import LiteLLMChatTarget, OpenAIChatTarget, PromptTarget
from pyrit.registry import TargetRegistry

_ALICE = "entra:tenant:alice"
_BOB = "entra:tenant:bob"
_ALICE_KEY = "fake-alice-secret-marker"
_BOB_KEY = "fake-bob-secret-marker"
_SERVER_KEY = "fake-server-secret-marker"
_ENDPOINT = "https://example.test/v1"


@pytest.fixture(autouse=True)
def isolated_targets() -> Iterator[None]:
    TargetRegistry.reset_registry_singleton()
    get_target_service.cache_clear()
    clear_caller_credentials()
    yield
    TargetRegistry.reset_registry_singleton()
    get_target_service.cache_clear()
    clear_caller_credentials()


@pytest.fixture
def service(patch_central_database: object) -> TargetService:
    return TargetService()


def _register_source(service: TargetService, target: PromptTarget, *, name: str = "configured") -> str:
    service._registry.instances.register(target, name=name)
    return target.get_identifier().hash


def _openai_source(**kwargs: object) -> OpenAIChatTarget:
    return OpenAIChatTarget(**{"model_name": "test-model", "endpoint": _ENDPOINT, "api_key": _SERVER_KEY, **kwargs})


async def _copy_async(
    service: TargetService,
    *,
    source_hash: str,
    caller: str | None = _ALICE,
    target_type: str = "OpenAIChatTarget",
    source_name: str = "configured",
    params: dict[str, object] | None = None,
    is_admin: bool = False,
) -> TargetInstance:
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name="mine",
            type=target_type,
            requires_caller_credential=True,
            source=SourceInstanceSpec(source_name=source_name, source_hash=source_hash, params=params or {}),
        ),
        caller=caller,
        is_admin=is_admin,
    )
    assert isinstance(created, TargetInstance)
    return created


async def _openai_key_as_async(target: PromptTarget, *, caller: str | None) -> str:
    assert isinstance(target, OpenAIChatTarget)
    with caller_principal_scope(caller):
        return await target._client._refresh_api_key()


async def test_personal_copy_keeps_source_settings_but_not_its_credential_async(service: TargetService) -> None:
    source = _openai_source(headers='{"x-team": "red"}', underlying_model="gpt-4o", max_requests_per_minute=7)
    source_hash = _register_source(service, source)

    created = await _copy_async(service, source_hash=source_hash, params={"api_key": _ALICE_KEY})

    copy = service.get_target_object(target_registry_name="mine")
    assert copy is not source
    assert copy._headers == {"x-team": "red"}
    assert copy._underlying_model == "gpt-4o"
    assert copy._max_requests_per_minute == 7
    # Same settings, but the identity records caller credentials, so the copy never matches its source.
    assert created.identifier.params == source.get_identifier().params
    assert created.identifier.attributes == {"caller_credential": True}
    assert created.identifier.hash != source_hash
    assert created.requires_caller_credential
    assert created.caller_credential_configured
    assert _ALICE_KEY not in created.model_dump_json()
    assert await _openai_key_as_async(copy, caller=_ALICE) == _ALICE_KEY
    with pytest.raises(CallerCredentialRequiredError):
        await _openai_key_as_async(copy, caller=_BOB)
    assert source._api_key == _SERVER_KEY


async def test_personal_copy_never_calls_the_source_token_provider_async(service: TargetService) -> None:
    server_provider = AsyncMock(return_value=_SERVER_KEY)
    source_hash = _register_source(service, _openai_source(api_key=server_provider))

    await _copy_async(service, source_hash=source_hash)
    copy = service.get_target_object(target_registry_name="mine")
    await service.set_caller_credential_async(target_registry_name="mine", caller=_BOB, credential=_BOB_KEY)

    assert await _openai_key_as_async(copy, caller=_BOB) == _BOB_KEY
    with pytest.raises(CallerCredentialRequiredError):
        await _openai_key_as_async(copy, caller=_ALICE)
    server_provider.assert_not_awaited()


async def test_personal_copy_of_no_key_source_uses_caller_credentials_async(service: TargetService) -> None:
    source_hash = _register_source(service, _openai_source(api_key=None, auth_mode="none"))

    await _copy_async(service, source_hash=source_hash, params={"api_key": _ALICE_KEY})

    copy = service.get_target_object(target_registry_name="mine")
    assert await _openai_key_as_async(copy, caller=_ALICE) == _ALICE_KEY


async def test_personal_copy_allows_routing_and_generation_overrides_async(service: TargetService) -> None:
    source_hash = _register_source(service, _openai_source())

    created = await _copy_async(
        service,
        source_hash=source_hash,
        params={"endpoint": "https://other.test/v1", "temperature": 0.3},
        is_admin=True,
    )

    copy = service.get_target_object(target_registry_name="mine")
    assert copy._endpoint == "https://other.test/v1"
    assert created.identifier.params["temperature"] == 0.3
    with pytest.raises(CallerCredentialRequiredError):
        await _openai_key_as_async(copy, caller=_ALICE)


@pytest.mark.parametrize(
    ("source_factory", "target_type"),
    [
        (
            lambda: _openai_source(headers='{"Ocp-Apim-Subscription-Key": "fake-gateway-secret-marker"}'),
            "OpenAIChatTarget",
        ),
        (
            lambda: LiteLLMChatTarget(
                model_name="openai/test-model",
                endpoint=_ENDPOINT,
                api_key=_SERVER_KEY,
                headers={"Ocp-Apim-Subscription-Key": "fake-gateway-secret-marker"},
            ),
            "LiteLLMChatTarget",
        ),
    ],
)
async def test_personal_copy_keeps_source_destination_for_non_admins_async(
    service: TargetService, source_factory: object, target_type: str
) -> None:
    source_hash = _register_source(service, source_factory())  # type: ignore[operator]

    with pytest.raises(PermissionError, match="'endpoint' requires administrator access"):
        await _copy_async(
            service,
            source_hash=source_hash,
            target_type=target_type,
            params={"endpoint": "https://attacker.test/v1", "api_key": _ALICE_KEY},
        )

    assert service.get_target_object(target_registry_name="mine") is None
    assert len(get_caller_credential_store()) == 0


async def test_personal_copy_allows_non_admin_overrides_that_keep_the_destination_async(
    service: TargetService,
) -> None:
    source_hash = _register_source(service, _openai_source())

    created = await _copy_async(service, source_hash=source_hash, params={"endpoint": _ENDPOINT, "temperature": 0.3})

    assert service.get_target_object(target_registry_name="mine")._endpoint == _ENDPOINT
    assert created.identifier.params["temperature"] == 0.3


async def test_binding_to_deleted_personal_copy_does_not_fall_back_to_its_source_async(
    service: TargetService,
) -> None:
    source_hash = _register_source(service, _openai_source())
    created = await _copy_async(service, source_hash=source_hash, params={"api_key": _ALICE_KEY})
    bound = await service.build_from_source_async(
        SourceInstanceSpec(source_name="mine", source_hash=created.identifier.hash, params={"temperature": 0.5})
    )
    binding = TargetBinding(
        source_name="mine",
        source_hash=created.identifier.hash,
        temperature=0.5,
        effective_hash=bound.get_identifier().hash,
    )

    assert await service.delete_target_async(target_registry_name="mine", caller=_ALICE)
    with pytest.raises(ValueError, match="missing or ambiguous"):
        await service.resolve_binding_async(binding)


async def test_personal_copy_of_a_personal_target_keeps_credentials_separate_async(service: TargetService) -> None:
    alice_target = await service.create_target_async(
        request=CreateTargetRequest(
            name="alice-target",
            type="OpenAIChatTarget",
            requires_caller_credential=True,
            params={"model_name": "test-model", "endpoint": _ENDPOINT, "api_key": _ALICE_KEY},
        ),
        caller=_ALICE,
    )
    assert isinstance(alice_target, TargetInstance)

    await service.create_target_async(
        request=CreateTargetRequest(
            name="bob-copy",
            type="OpenAIChatTarget",
            requires_caller_credential=True,
            source=SourceInstanceSpec(
                source_name="alice-target", source_hash=alice_target.identifier.hash, params={"api_key": _BOB_KEY}
            ),
        ),
        caller=_BOB,
    )

    copy = service.get_target_object(target_registry_name="bob-copy")
    assert await _openai_key_as_async(copy, caller=_BOB) == _BOB_KEY
    with pytest.raises(CallerCredentialRequiredError):
        await _openai_key_as_async(copy, caller=_ALICE)


async def test_personal_copy_of_litellm_source_uses_caller_credentials_async(service: TargetService) -> None:
    source = LiteLLMChatTarget(model_name="openai/test-model", endpoint=_ENDPOINT, api_key=_SERVER_KEY)
    source_hash = _register_source(service, source)

    await _copy_async(service, source_hash=source_hash, target_type="LiteLLMChatTarget", params={"api_key": _ALICE_KEY})

    copy = service.get_target_object(target_registry_name="mine")
    assert isinstance(copy, LiteLLMChatTarget)
    with caller_principal_scope(_ALICE):
        assert await copy._resolve_api_key_async() == _ALICE_KEY
    with caller_principal_scope(_BOB), pytest.raises(CallerCredentialRequiredError):
        await copy._resolve_api_key_async()


async def test_personal_copy_rejects_litellm_routes_that_cannot_use_a_token_provider_async(
    service: TargetService,
) -> None:
    source_hash = _register_source(service, LiteLLMChatTarget(model_name="anthropic/claude-test", api_key="k"))

    with pytest.raises(ValueError, match="openai/ model"):
        await _copy_async(service, source_hash=source_hash, target_type="LiteLLMChatTarget")

    assert service.get_target_object(target_registry_name="mine") is None
    assert len(get_caller_credential_store()) == 0


async def test_personal_copy_rejects_static_authentication_headers_async(service: TargetService) -> None:
    source_hash = _register_source(service, _openai_source(headers='{"api-key": "server-header-key"}'))

    with pytest.raises(ValueError, match="Static authentication headers"):
        await _copy_async(service, source_hash=source_hash)

    assert service.get_target_object(target_registry_name="mine") is None


async def test_personal_copy_rejects_sources_that_cannot_be_rebuilt_async(service: TargetService) -> None:
    async with httpx.AsyncClient() as http_client:
        source_hash = _register_source(service, _openai_source(httpx_client_kwargs={"http_client": http_client}))

        with pytest.raises(ValueError, match="custom HTTP client"):
            await _copy_async(service, source_hash=source_hash)

    assert service.get_target_object(target_registry_name="mine") is None


async def test_personal_copy_rejects_auth_mode_override_async(service: TargetService) -> None:
    source_hash = _register_source(service, _openai_source())

    with pytest.raises(ValueError, match="omit auth_mode"):
        await _copy_async(service, source_hash=source_hash, params={"auth_mode": "identity"})


async def test_personal_copy_requires_registration_and_a_caller_async(service: TargetService) -> None:
    source_hash = _register_source(service, _openai_source())
    spec = SourceInstanceSpec(source_name="configured", source_hash=source_hash)

    with pytest.raises(ValueError, match="only to registered targets"):
        await service.create_target_async(
            request=CreateTargetRequest(
                type="OpenAIChatTarget", register=False, requires_caller_credential=True, source=spec
            ),
            caller=_ALICE,
        )
    with pytest.raises(PermissionError, match="verified caller identity"):
        await _copy_async(service, source_hash=source_hash, caller=None)


def test_registered_source_clone_requires_personal_copy() -> None:
    spec = SourceInstanceSpec(source_name="configured", source_hash="hash")

    with pytest.raises(ValidationError, match="personal copy"):
        CreateTargetRequest(name="mine", type="OpenAIChatTarget", source=spec)

    assert CreateTargetRequest(type="OpenAIChatTarget", register=False, source=spec).source == spec


def test_rest_creates_personal_copy(compatibility_headers: dict[str, str], patch_central_database: object) -> None:
    source_hash = _register_source(get_target_service(), _openai_source())

    def principal(_: Request) -> str:
        return _ALICE

    with patch("pyrit.backend.routes.targets.caller_principal", side_effect=principal):
        response = TestClient(app, headers=compatibility_headers).post(
            "/api/targets",
            json={
                "name": "mine",
                "type": "OpenAIChatTarget",
                "requires_caller_credential": True,
                "source": {
                    "source_name": "configured",
                    "source_hash": source_hash,
                    "params": {"api_key": _ALICE_KEY},
                },
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert _ALICE_KEY not in response.text
    assert response.json()["requires_caller_credential"] is True
    assert response.json()["caller_credential_configured"] is True
