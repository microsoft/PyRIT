# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private targets: who can see and use a target, and who can update its credentials."""

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from pyrit.auth import caller_principal_scope
from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.models import (
    CreateConverterRequest,
    CreateScorerRequest,
    CreateTargetRequest,
    UpdateTargetCredentialsRequest,
)
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.backend.routes.targets import router
from pyrit.backend.services import ConverterService, TargetService
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scorer_service import ScorerService
from pyrit.backend.services.target_service import CredentialUpdateConflictError
from pyrit.backend.services.technique_service import TechniqueService
from pyrit.converter import Base64Converter
from pyrit.models.component_spec import SourceInstanceSpec
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import (
    ConverterRegistry,
    Registry,
    TargetRegistry,
    credential_update_metadata,
    get_entry_owner,
    is_private_entry,
    owner_metadata,
)
from pyrit.registry.instance_registry import saved_entry_registration
from unit.mocks import MockPromptTarget

_ENDPOINT = "https://api.openai.com/v1"
_SECRET = "fake-secret-marker"


@pytest.fixture(autouse=True)
def isolated_registries(patch_central_database: Any) -> Iterator[None]:
    with patch.dict(Registry._singletons, {}, clear=True):
        yield


def _request(name: str = "mine", *, private: bool | None = None, **params: Any) -> CreateTargetRequest:
    return CreateTargetRequest(
        name=name,
        type="OpenAIChatTarget",
        params={"endpoint": _ENDPOINT, "model_name": "gpt-4o", **params},
        private=private,
    )


async def _create_async(caller: str | None, request: CreateTargetRequest) -> Any:
    with caller_principal_scope(caller):
        return await TargetService().create_target_async(request=request)


def _entry(caller: str | None, name: str) -> Any:
    with caller_principal_scope(caller):
        return TargetRegistry.get_registry_singleton().instances.get_entry(name)


# ============================================================================
# Creation: a target is shared unless the request makes it private
# ============================================================================


@pytest.mark.parametrize("params", [{"api_key": _SECRET}, {}])
async def test_a_target_is_shared_by_default_and_records_its_creator_async(
    monkeypatch: pytest.MonkeyPatch, params: dict[str, Any]
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")

    created = await _create_async("alice", _request(**params))

    assert (created.private, created.allow_credential_updates, created.credentials_rejected) == (
        False,
        False,
        None,
    )
    entry = _entry("bob", "mine")
    assert (get_entry_owner(entry), is_private_entry(entry)) == ("alice", False)


@pytest.mark.parametrize(("private", "expected"), [(True, True), (False, False)])
async def test_an_explicit_choice_decides_whether_a_target_is_private_async(private: bool, expected: bool) -> None:
    created = await _create_async("alice", _request(private=private, api_key=_SECRET))

    assert created.private is expected
    assert (_entry("bob", "mine") is None) is expected
    assert get_entry_owner(_entry("alice", "mine")) == "alice"


async def test_a_private_target_must_be_registered_async() -> None:
    request = _request(private=True, api_key=_SECRET).model_copy(update={"register": False})

    with pytest.raises(ValueError, match="must be registered"):
        await _create_async("alice", request)


async def test_a_private_target_needs_a_signed_in_caller_async() -> None:
    with pytest.raises(PermissionError, match="signed-in caller"):
        await _create_async(None, _request(private=True, api_key=_SECRET))


async def test_a_target_created_with_no_caller_is_shared_and_unowned_async() -> None:
    await _create_async(None, _request(api_key=_SECRET))

    entry = _entry("bob", "mine")
    assert (get_entry_owner(entry), is_private_entry(entry)) == (None, False)


async def test_a_name_held_by_another_callers_private_target_is_taken_without_naming_them_async() -> None:
    await _create_async("entra:tenant:alice-oid", _request(private=True, api_key=_SECRET))

    with pytest.raises(ValueError, match="already exists") as error:
        await _create_async("bob", _request(api_key="bob-key"))

    assert "alice" not in str(error.value)
    assert _entry("entra:tenant:alice-oid", "mine").instance._api_key == _SECRET


# ============================================================================
# Every way a request reaches a target goes through the access rule
# ============================================================================


@pytest.mark.parametrize(("caller", "visible"), [("alice", True), ("bob", False), (None, False)])
async def test_list_get_and_chat_lookups_show_a_private_target_only_to_its_owner_async(
    caller: str | None, visible: bool
) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    target = _entry("alice", "mine").instance
    service = TargetService()

    with caller_principal_scope(caller):
        listed = [item.target_registry_name for item in (await service.list_targets_async()).items]
        assert ("mine" in listed) is visible
        assert (await service.get_target_async(target_registry_name="mine") is not None) is visible
        assert (service.get_target_object(target_registry_name="mine") is target) is visible
        assert (service.get_target_object_by_identifier_hash(target.get_identifier().hash) is target) is visible


async def test_chat_copies_build_only_from_the_callers_own_private_target_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    source = _entry("alice", "mine").instance
    spec = SourceInstanceSpec(source_name="mine", source_hash=source.get_identifier().hash, params={"temperature": 0.2})

    with caller_principal_scope("bob"), pytest.raises(ValueError, match="missing"):
        await TargetService().build_from_source_async(spec)

    with caller_principal_scope("alice"):
        copy = await TargetService().build_from_source_async(spec)
    copy._credential_status_owner()._credentials_rejected = True
    assert source.credentials_rejected


async def test_scenario_roles_resolve_only_the_callers_private_target_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="team")

    with caller_principal_scope("bob"), pytest.raises(ValueError, match="not found") as error:
        ScenarioConfigurationResolver.resolve_target(target_name="mine")
    assert "mine" not in str(error.value).partition("Available")[2]

    with caller_principal_scope("alice"):
        assert ScenarioConfigurationResolver.resolve_target(target_name="mine") is _entry("alice", "mine").instance


def _composite(private: bool | None = None) -> CreateTargetRequest:
    return CreateTargetRequest(
        name="both", type="RoundRobinTarget", params={"targets": ["mine", "team"]}, private=private
    )


@pytest.mark.parametrize("private", [None, True])
async def test_a_composite_using_a_private_target_is_private_async(private: bool | None) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _request("team", private=False, api_key="team-key"))

    created = await _create_async("alice", _composite(private))

    assert created.private
    assert _entry("bob", "both") is None
    assert _entry("alice", "both") is not None


async def test_a_composite_using_a_private_target_cannot_be_made_shared_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _request("team", private=False, api_key="team-key"))

    with pytest.raises(ValueError, match="'both' is built on your private target 'mine'; it must be private too"):
        await _create_async("alice", _composite(private=False))

    assert _entry("alice", "both") is None


async def test_a_composite_of_shared_targets_can_be_made_private_async() -> None:
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(MockPromptTarget(), name="mine")
    registry.instances.register(MockPromptTarget(), name="team")

    assert not (await _create_async("alice", _composite())).private
    registry.instances.unregister("both")
    assert (await _create_async("alice", _composite(private=True))).private


async def test_another_caller_cannot_reference_a_private_target_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="team")

    with pytest.raises(ValueError, match="not found"):
        await _create_async(
            "bob", CreateTargetRequest(name="both", type="RoundRobinTarget", params={"targets": ["mine", "team"]})
        )


def _scorer(private: bool | None = None) -> CreateScorerRequest:
    return CreateScorerRequest(
        name="judge", type="SelfAskRefusalScorer", params={"chat_target": "mine"}, private=private
    )


async def _get_scorer_async(caller: str) -> Any:
    with caller_principal_scope(caller):
        return await ScorerService().get_scorer_async(scorer_registry_name="judge")


@pytest.mark.parametrize("private", [None, True])
async def test_a_scorer_using_a_private_target_is_private_async(private: bool | None) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))

    with caller_principal_scope("bob"), pytest.raises(ValueError, match="not found"):
        await ScorerService().create_scorer_async(request=_scorer(private))
    with caller_principal_scope("alice"):
        await ScorerService().create_scorer_async(request=_scorer(private))

    assert await _get_scorer_async("bob") is None
    assert await _get_scorer_async("alice") is not None


async def test_a_scorer_using_a_private_target_cannot_be_made_shared_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))

    with (
        caller_principal_scope("alice"),
        pytest.raises(ValueError, match="'judge' is built on your private target 'mine'; it must be private too"),
    ):
        await ScorerService().create_scorer_async(request=_scorer(private=False))

    assert await _get_scorer_async("alice") is None


async def test_a_converter_using_a_private_target_is_private_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    request = CreateConverterRequest(
        name="translate", type="TranslationConverter", params={"converter_target": "mine", "language": "French"}
    )

    with caller_principal_scope("alice"):
        await ConverterService().create_converter_async(request=request)

    with caller_principal_scope("bob"):
        assert ConverterRegistry.get_registry_singleton().instances.get("translate") is None
    with caller_principal_scope("alice"):
        assert ConverterRegistry.get_registry_singleton().instances.get("translate") is not None


@pytest.mark.parametrize("private", [None, False])
async def test_a_converter_on_a_shared_target_stays_shared_when_the_caller_owns_private_targets_async(
    private: bool | None,
) -> None:
    await _create_async("alice", _request("aaa-private", private=True, api_key=_SECRET))
    await _create_async("alice", _request("zzz-shared", private=False, api_key="team-key"))
    request = CreateConverterRequest(
        name="translate",
        type="TranslationConverter",
        params={"converter_target": "zzz-shared", "language": "French"},
        private=private,
    )

    with caller_principal_scope("alice"):
        await ConverterService().create_converter_async(request=request)

    with caller_principal_scope("bob"):
        assert ConverterRegistry.get_registry_singleton().instances.get("translate") is not None


async def test_closing_the_converter_service_removes_every_owners_converters_async() -> None:
    converters = ConverterRegistry.get_registry_singleton().instances
    service = ConverterService()
    with caller_principal_scope("alice"):
        await service.create_converter_async(
            request=CreateConverterRequest(name="mine", type="Base64Converter", private=True)
        )
    entry = converters.get_all_instances_of_every_owner()[0]
    entry.metadata["owned_artifact_paths"] = [str(service._upload_path / "file.bin")]

    await service.close_async()

    assert converters.get_all_instances_of_every_owner() == []


async def test_a_technique_using_a_private_converter_is_private_async() -> None:
    converters = ConverterRegistry.get_registry_singleton().instances
    converters.register(Base64Converter(), name="mine", metadata=owner_metadata("alice", private=True))
    request = CreateTechniqueRequest(name="encoded", type="PromptSendingAttack", request_converters=["mine"])

    with caller_principal_scope("bob"), pytest.raises(ValueError):
        await TechniqueService().create_async(request)
    with caller_principal_scope("alice"):
        await TechniqueService().create_async(request)

    with caller_principal_scope("bob"):
        assert await TechniqueService().get_async("encoded") is None
    with caller_principal_scope("alice"):
        assert await TechniqueService().get_async("encoded") is not None


# ============================================================================
# Updating credentials
# ============================================================================


async def _update_async(caller: str | None, name: str = "mine", **fields: Any) -> Any:
    with caller_principal_scope(caller):
        return await TargetService().update_credentials_async(
            target_registry_name=name, request=UpdateTargetCredentialsRequest(**fields)
        )


def _shared_request(name: str, **params: Any) -> CreateTargetRequest:
    return _request(name, **params).model_copy(update={"allow_credential_updates": True})


@pytest.mark.parametrize(
    ("fields", "expected_key", "expected_headers"),
    [
        ({"api_key": "new-key"}, "new-key", {}),
        (
            {"headers": {"Authorization": "Bearer new-token", "Cookie": "session=abc; theme=dark"}},
            _SECRET,
            {"Authorization": "Bearer new-token", "Cookie": "session=abc; theme=dark"},
        ),
    ],
)
async def test_the_owner_updates_a_private_target_in_place_async(
    fields: dict[str, Any], expected_key: str, expected_headers: dict[str, str]
) -> None:
    created = await _create_async("alice", _request(private=True, api_key=_SECRET))
    entry = _entry("alice", "mine")
    target = entry.instance
    identifier = target.get_identifier().hash
    target._credentials_rejected = True

    updated = await _update_async("alice", **fields)

    assert created.allow_credential_updates is True
    assert _entry("alice", "mine").instance is target
    assert target.get_identifier().hash == identifier
    assert _entry("alice", "mine").metadata == entry.metadata
    assert (updated.private, updated.allow_credential_updates, updated.credentials_rejected) == (True, True, None)
    assert target._api_key == expected_key
    assert target._headers == expected_headers
    for name, value in expected_headers.items():
        assert target._client.default_headers[name] == value
    assert "new-key" not in updated.model_dump_json() and "new-token" not in updated.model_dump_json()


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"endpoint": "https://other.example/v1"},
        {"model_name": "gpt-4o-mini"},
        {"auth_mode": "identity"},
        {"params": {"api_key": "k"}},
        {"headers": {}},
    ],
)
def test_an_update_request_takes_only_credentials(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        UpdateTargetCredentialsRequest(**fields)


async def test_a_header_replaces_one_configured_with_other_casing_wherever_it_was_set_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    monkeypatch.setenv("OPENAI_ADDITIONAL_REQUEST_HEADERS", json.dumps({"X-Team-Token": "old", "X-Route": "blue"}))
    await _create_async(None, _shared_request("ops"))
    target = _entry("bob", "ops").instance

    await _update_async("bob", "ops", headers={"x-team-token": "fresh"})

    assert target._headers == {"X-Team-Token": "fresh", "X-Route": "blue"}
    sent = {name.lower(): value for name, value in target._client.default_headers.items() if isinstance(value, str)}
    assert (sent["x-team-token"], sent["x-route"]) == ("fresh", "blue")
    assert [name for name in target._client.default_headers if name.lower() == "x-team-token"] == ["X-Team-Token"]


async def test_a_copy_built_after_an_update_uses_the_new_credentials_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    source = _entry("alice", "mine").instance
    await _update_async("alice", api_key="new-key", headers={"X-Team-Token": "fresh"})

    copy = TargetRegistry.get_registry_singleton().recreate_instance(source=source, params={}, external_input=True)

    assert (copy._api_key, copy._headers) == ("new-key", {"X-Team-Token": "fresh"})


@pytest.mark.parametrize(
    "header_params",
    [
        {"headers": '{"Authorization": "Bearer old-token"}'},
        {"httpx_client_kwargs": {"default_headers": {"aUtHoRiZaTiOn": "Bearer old-token"}}},
        {
            "headers": '{"Authorization": "Bearer old-token"}',
            "httpx_client_kwargs": {"default_headers": {"aUtHoRiZaTiOn": "Bearer default-token"}},
        },
    ],
)
async def test_an_explicit_authorization_header_cannot_be_rotated_with_only_an_api_key_async(
    header_params: dict[str, Any],
) -> None:
    target = OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key="old-key", **header_params)
    TargetRegistry.get_registry_singleton().instances.register(
        target, name="mine", metadata=owner_metadata("alice", private=True) | credential_update_metadata(allowed=True)
    )

    with pytest.raises(ValueError, match="update the Authorization header"):
        await _update_async("alice", api_key="new-key")
    assert target._client.api_key == "old-key"

    await _update_async("alice", headers={"authorization": "Bearer new-token"})
    assert [value for name, value in target._client.default_headers.items() if name.lower() == "authorization"] == [
        "Bearer new-token"
    ]


async def test_a_no_key_target_takes_headers_but_not_a_key_async() -> None:
    request = _request(private=True).model_copy(update={"auth_mode": "none"})
    await _create_async("alice", request)
    target = _entry("alice", "mine").instance

    await _update_async("alice", headers={"X-Gateway-Key": "team-secret"})
    with pytest.raises(ValueError, match="can't take an API key"):
        await _update_async("alice", api_key="new-key")

    assert target._headers == {"X-Gateway-Key": "team-secret"}
    assert target._auth_mode == "none"


async def test_an_identity_target_takes_other_headers_but_not_authorization_async() -> None:
    provider = AsyncMock(return_value="entra-token")
    request = CreateTargetRequest(
        name="mine",
        type="OpenAIChatTarget",
        params={"endpoint": "https://example.openai.azure.com/openai/v1", "model_name": "gpt-4o"},
        auth_mode="identity",
        private=True,
    )
    with patch("pyrit.auth.openai_auth.get_azure_openai_auth", return_value=provider), caller_principal_scope("alice"):
        await TargetService().create_target_async(request=request, is_admin=True)

    await _update_async("alice", headers={"Ocp-Apim-Subscription-Key": "team-key"})
    with pytest.raises(ValueError, match="Authorization header"):
        await _update_async("alice", headers={"authorization": "Bearer mine"})

    assert _entry("alice", "mine").instance._headers == {"Ocp-Apim-Subscription-Key": "team-key"}


@pytest.mark.parametrize(
    ("headers", "match"),
    [
        ({"Host": "elsewhere.example"}, "control how a request is sent"),
        ({"Transfer-Encoding": "chunked"}, "control how a request is sent"),
        ({"X-Team-Token": " "}, "nonblank"),
        ({f"Bad Name {_SECRET}": "v"}, "header name"),
        ({"X-Team-Token": f"{_SECRET}\nX-Injected: 1"}, "control character"),
    ],
)
async def test_a_header_update_that_cannot_be_sent_is_refused_without_echoing_it_async(
    headers: dict[str, str], match: str
) -> None:
    await _create_async("alice", _request(private=True, api_key="old-key"))
    target = _entry("alice", "mine").instance

    with pytest.raises(ValueError, match=match) as error:
        await _update_async("alice", headers=headers)

    assert _SECRET not in str(error.value)
    assert (target._api_key, target._headers) == ("old-key", {})


@pytest.mark.parametrize("value", ["", " ", "\n"])
async def test_a_key_update_needs_a_nonblank_value_async(value: str) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))

    with pytest.raises(ValueError, match="api_key"):
        await _update_async("alice", api_key=value)

    assert _entry("alice", "mine").instance._api_key == _SECRET


async def test_anyone_signed_in_can_update_a_shared_target_that_allows_it_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    created = await _create_async("alice", _shared_request("team"))

    updated = await _update_async("bob", "team", api_key="bob-key")

    assert (created.private, created.allow_credential_updates) == (False, True)
    assert (updated.private, updated.allow_credential_updates) == (False, True)
    entry = _entry("carol", "team")
    assert (entry.instance._api_key, get_entry_owner(entry)) == ("bob-key", "alice")


async def test_an_initializer_can_register_a_shared_target_that_allows_updates_async() -> None:
    target = OpenAIChatTarget(
        endpoint=_ENDPOINT, model_name="gpt-4o", api_key="unused", headers=json.dumps({"X-Team-Token": "expired"})
    )
    TargetRegistry.get_registry_singleton().instances.register(
        target, name="ops", metadata=credential_update_metadata(allowed=True)
    )

    updated = await _update_async("bob", "ops", headers={"X-Team-Token": "fresh"})

    assert (updated.private, updated.allow_credential_updates) == (False, True)
    assert target._headers == {"X-Team-Token": "fresh"}


async def test_a_target_with_a_token_provider_keeps_it_when_its_initializer_allows_updates_async() -> None:
    provider = AsyncMock(return_value="entra-token")
    target = OpenAIChatTarget(
        endpoint="https://example.openai.azure.com/openai/v1", model_name="gpt-4o", api_key=provider
    )
    TargetRegistry.get_registry_singleton().instances.register(
        target, name="ops", metadata=credential_update_metadata(allowed=True)
    )

    with pytest.raises(ValueError, match="token provider, so it can't take an API key"):
        await _update_async("bob", "ops", api_key="static-key")
    with pytest.raises(ValueError, match="Authorization header"):
        await _update_async("bob", "ops", headers={"Authorization": "Bearer static"})
    await _update_async("bob", "ops", headers={"Ocp-Apim-Subscription-Key": "gateway-key"})

    assert target._api_key is provider
    assert target._client._api_key_provider is not None
    assert target._headers == {"Ocp-Apim-Subscription-Key": "gateway-key"}


async def test_a_target_that_cannot_update_its_credentials_is_refused_async() -> None:
    TargetRegistry.get_registry_singleton().instances.register(
        MockPromptTarget(), name="mock", metadata=credential_update_metadata(allowed=True)
    )

    with pytest.raises(ValueError, match="can't update its credentials"):
        await _update_async("alice", "mock", api_key="alice-key")


async def test_updates_cannot_be_allowed_for_a_type_that_cannot_make_them_async() -> None:
    request = CreateTargetRequest(
        name="mine",
        type="RealtimeTarget",
        params={"endpoint": "wss://example.openai.azure.com/openai/v1", "model_name": "gpt-4o", "api_key": "k"},
        allow_credential_updates=True,
    )

    with pytest.raises(ValueError, match="allow_credential_updates"):
        await _create_async("alice", request)

    assert _entry("alice", "mine") is None


async def test_a_private_composite_does_not_allow_updates_by_default_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _request("team", private=False, api_key="team-key"))
    created = await _create_async("alice", _composite())

    assert (created.private, created.allow_credential_updates) == (True, False)


def test_a_saved_target_cannot_allow_credential_updates() -> None:
    with pytest.raises(ValueError, match="allow_credential_updates"):
        CreateTargetRequest(
            name="mine",
            type="OpenAIChatTarget",
            params={"endpoint": _ENDPOINT, "model_name": "gpt-4o"},
            auth_mode="none",
            persist=True,
            allow_credential_updates=True,
        )


async def test_a_saved_target_cannot_be_updated_through_the_api_async() -> None:
    with saved_entry_registration(kind="targets", name="saved"):
        await _create_async(None, _shared_request("saved", api_key=_SECRET))

    with pytest.raises(PermissionError, match="is saved"):
        await _update_async("alice", "saved", api_key="alice-key")


async def test_another_user_cannot_see_or_update_a_private_target_async() -> None:
    await _create_async("alice", _request("private-one", private=True, api_key=_SECRET))

    assert await _update_async("bob", "private-one", api_key="bob-key") is None
    assert _entry("alice", "private-one").instance._api_key == _SECRET


async def test_an_update_needs_a_signed_in_caller_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _shared_request("team"))

    with pytest.raises(PermissionError, match="signed-in caller"):
        await _update_async(None, "team", api_key="key")


@pytest.mark.parametrize(("name", "expected_private"), [("mine", False), ("team", True)])
async def test_an_update_for_a_target_that_is_not_as_expected_changes_nothing_async(
    monkeypatch: pytest.MonkeyPatch, name: str, expected_private: bool
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _shared_request("team"))
    before = _entry("alice", name).instance._api_key

    with pytest.raises(CredentialUpdateConflictError, match="not the target you expected"):
        await _update_async("alice", name, api_key="new-key", private=expected_private)

    assert _entry("alice", name).instance._api_key == before


def _client_as(caller: str) -> TestClient:
    app = FastAPI()
    app.state.authentication_policy = AuthenticationPolicy(mode="local")
    app.include_router(router, prefix="/api")

    @app.middleware("http")
    async def scope_caller_async(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        with caller_principal_scope(caller):
            return await call_next(request)

    return TestClient(app)


@pytest.mark.parametrize(
    ("caller", "name", "body", "status_code"),
    [
        ("alice", "mine", {"api_key": "new-key"}, 200),
        ("alice", "mine", {"api_key": "new-key", "private": True}, 200),
        ("alice", "mine", {"api_key": "new-key", "private": False}, 409),
        ("alice", "mine", {"headers": {"Host": "new-key.example"}}, 400),
        ("alice", "mine", {"model_name": "new-key"}, 422),
        ("bob", "mine", {"api_key": "new-key"}, 404),
        ("bob", "team", {"api_key": "new-key"}, 403),
        ("bob", "open-team", {"api_key": "new-key"}, 200),
    ],
)
async def test_update_credentials_route_maps_each_outcome(
    monkeypatch: pytest.MonkeyPatch, caller: str, name: str, body: dict[str, Any], status_code: int
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _request("team"))
    await _create_async("alice", _shared_request("open-team"))

    with patch("pyrit.backend.routes.targets.get_target_service", return_value=TargetService()):
        response = _client_as(caller).put(f"/api/targets/{name}/credentials", json=body)

    assert response.status_code == status_code
    if status_code != 422:
        assert "new-key" not in response.text


# ============================================================================
# Regressions: credentials change in place, only where the creator allowed it
# ============================================================================


async def test_a_component_holding_the_target_uses_updated_credentials_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    held = _entry("alice", "mine").instance

    await _update_async("alice", api_key="new-key")

    assert _entry("alice", "mine").instance is held
    assert held._api_key == "new-key"
    assert held._client.api_key == "new-key"


async def test_an_identity_target_cannot_switch_to_a_key_async() -> None:
    provider = AsyncMock(return_value="entra-token")
    request = CreateTargetRequest(
        name="mine",
        type="OpenAIChatTarget",
        params={"endpoint": "https://example.openai.azure.com/openai/v1", "model_name": "gpt-4o"},
        auth_mode="identity",
        private=True,
    )
    with patch("pyrit.auth.openai_auth.get_azure_openai_auth", return_value=provider), caller_principal_scope("alice"):
        await TargetService().create_target_async(request=request, is_admin=True)

    with pytest.raises(ValueError, match="authenticat"):
        await _update_async("alice", api_key="new-key")

    target = _entry("alice", "mine").instance
    assert (target._auth_mode, target._api_key) == ("identity", provider)


async def test_a_shared_target_can_be_updated_only_when_its_creator_allowed_it_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async(None, _request("from-initializer"))
    await _create_async("alice", _request("shared-one"))

    for name in ("from-initializer", "shared-one"):
        with pytest.raises(PermissionError, match="doesn't allow credential updates"):
            await _update_async("bob", name, api_key="bob-key")
        assert _entry("bob", name).instance._api_key == "server-key"


async def test_a_pasted_key_loses_surrounding_whitespace_async() -> None:
    await _create_async("alice", _request(private=True, api_key=" sk-created \n"))
    assert _entry("alice", "mine").instance._api_key == "sk-created"

    await _update_async("alice", api_key="sk-updated \n")
    assert _entry("alice", "mine").instance._api_key == "sk-updated"


@pytest.mark.parametrize("value", [f"{_SECRET}\x00", f"{_SECRET}\r\nX-Injected: 1", f"sk\t{_SECRET}"])
async def test_a_key_with_a_control_character_is_refused_without_echoing_it_async(value: str) -> None:
    with pytest.raises(ValueError) as created:
        await _create_async("alice", _request(private=True, api_key=value))
    assert _SECRET not in str(created.value)

    await _create_async("alice", _request(private=True, api_key="old-key"))
    with pytest.raises(ValueError) as updated:
        await _update_async("alice", api_key=value)
    assert _SECRET not in str(updated.value)
    assert _entry("alice", "mine").instance._api_key == "old-key"


async def test_a_header_value_with_a_line_break_is_refused_without_echoing_it_async() -> None:
    headers = json.dumps({"X-Team-Token": f"{_SECRET}\r\nX-Injected: 1"})

    with pytest.raises(ValueError) as error:
        await _create_async("alice", _request(private=True, api_key="key", headers=headers))

    assert _SECRET not in str(error.value)


def test_a_private_target_cannot_be_saved() -> None:
    with pytest.raises(ValueError, match="private"):
        CreateTargetRequest(
            name="mine",
            type="OpenAIChatTarget",
            params={"endpoint": _ENDPOINT, "model_name": "gpt-4o"},
            auth_mode="none",
            private=True,
            persist=True,
        )
