# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Caller-supplied credentials for registered targets: service and REST contract."""

from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Request, status
from fastapi.testclient import TestClient

from pyrit.auth import CallerCredentialRequiredError, caller_principal_scope
from pyrit.backend.main import app
from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.services.service_lifecycle import close_services_async
from pyrit.backend.services.target_service import (
    TargetService,
    clear_caller_credentials,
    get_caller_credential_store,
    get_target_service,
)
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.registry import TargetRegistry

_ALICE = "entra:tenant:alice"
_BOB = "entra:tenant:bob"
_ALICE_KEY = "fake-alice-secret-marker"
_BOB_KEY = "fake-bob-secret-marker"
_TARGET_PARAMS = {"model_name": "test-model", "endpoint": "https://example.test/v1"}


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


async def _create_async(
    service: TargetService, *, caller: str | None, name: str = "shared", api_key: str = _ALICE_KEY
) -> object:
    return await service.create_target_async(
        request=CreateTargetRequest(name=name, type="OpenAIChatTarget", params={**_TARGET_PARAMS, "api_key": api_key}),
        caller=caller,
    )


async def _resolve_as_async(target: PromptTarget, *, caller: str | None) -> str:
    assert isinstance(target, OpenAIChatTarget)
    with caller_principal_scope(caller):
        return await target._client._refresh_api_key()


async def test_create_with_key_registers_target_without_the_secret_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)

    target = service.get_target_object(target_registry_name="shared")
    entry = service._registry.instances.get_entry("shared")
    assert callable(target._api_key)
    assert _ALICE_KEY not in created.model_dump_json()
    assert _ALICE_KEY not in str(target.get_identifier())
    assert _ALICE_KEY not in str(entry.metadata)
    assert entry.metadata[TargetService.CREATED_BY_METADATA_KEY] == _ALICE
    assert created.requires_caller_credential
    assert created.caller_credential_configured
    assert created.can_delete
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY


async def test_other_caller_sees_target_but_cannot_borrow_its_key_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="shared")

    as_bob = await service.get_target_async(target_registry_name="shared", caller=_BOB)
    listed = await service.list_targets_async(caller=_BOB)

    assert as_bob is not None
    assert as_bob.requires_caller_credential
    assert not as_bob.caller_credential_configured
    assert not as_bob.can_delete
    assert listed.items == [as_bob]
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_BOB)
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=None)


async def test_set_and_remove_caller_credential_affect_only_that_caller_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="shared")

    updated = await service.set_caller_credential_async(target_registry_name="shared", caller=_BOB, credential=_BOB_KEY)

    assert updated is not None
    assert updated.caller_credential_configured
    assert _BOB_KEY not in updated.model_dump_json()
    assert await _resolve_as_async(target, caller=_BOB) == _BOB_KEY
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY

    assert await service.remove_caller_credential_async(target_registry_name="shared", caller=_BOB)

    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_BOB)
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY


async def test_replaced_credential_keeps_target_identity_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="shared")

    await service.set_caller_credential_async(target_registry_name="shared", caller=_ALICE, credential="replacement")

    assert target.get_identifier().hash == created.identifier.hash
    assert await _resolve_as_async(target, caller=_ALICE) == "replacement"


async def test_saved_binding_copy_resolves_the_current_caller_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)
    copy = await service.build_from_source_async(
        SourceInstanceSpec(source_name="shared", source_hash=created.identifier.hash, params={"temperature": 0.3})
    )
    try:
        assert await _resolve_as_async(copy, caller=_ALICE) == _ALICE_KEY
        with pytest.raises(CallerCredentialRequiredError):
            await _resolve_as_async(copy, caller=_BOB)
    finally:
        await copy.cleanup_target_async()


async def test_caller_credential_operations_reject_targets_without_caller_credentials_async(
    service: TargetService,
) -> None:
    await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"), caller=_ALICE)

    with pytest.raises(ValueError, match="does not use caller credentials"):
        await service.set_caller_credential_async(target_registry_name="text", caller=_ALICE, credential="key")
    with pytest.raises(ValueError, match="does not use caller credentials"):
        await service.remove_caller_credential_async(target_registry_name="text", caller=_ALICE)


async def test_caller_credential_operations_require_a_caller_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)

    with pytest.raises(PermissionError, match="verified caller identity"):
        await service.set_caller_credential_async(target_registry_name="shared", caller=None, credential="key")
    with pytest.raises(PermissionError, match="verified caller identity"):
        await service.remove_caller_credential_async(target_registry_name="shared", caller=None)


async def test_caller_credential_operations_report_missing_targets_async(service: TargetService) -> None:
    assert (
        await service.set_caller_credential_async(target_registry_name="missing", caller=_ALICE, credential="k") is None
    )
    assert not await service.remove_caller_credential_async(target_registry_name="missing", caller=_ALICE)
    assert not await service.delete_target_async(target_registry_name="missing", caller=_ALICE)


async def test_set_caller_credential_rejects_blank_value_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)

    with pytest.raises(ValueError, match="nonempty string"):
        await service.set_caller_credential_async(target_registry_name="shared", caller=_BOB, credential="  ")


async def test_delete_target_requires_its_creator_and_forgets_all_credentials_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="shared")
    await service.set_caller_credential_async(target_registry_name="shared", caller=_BOB, credential=_BOB_KEY)

    with pytest.raises(PermissionError, match="Only the caller who created"):
        await service.delete_target_async(target_registry_name="shared", caller=_BOB)
    assert await service.delete_target_async(target_registry_name="shared", caller=_ALICE)

    assert service.get_target_object(target_registry_name="shared") is None
    assert len(get_caller_credential_store()) == 0
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_ALICE)


async def test_recreated_name_never_inherits_old_credentials_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    old_target = service.get_target_object(target_registry_name="shared")
    await service.delete_target_async(target_registry_name="shared", caller=_ALICE)

    await _create_async(service, caller=_BOB, api_key=_BOB_KEY)
    new_target = service.get_target_object(target_registry_name="shared")

    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(new_target, caller=_ALICE)
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(old_target, caller=_BOB)


async def test_initializer_targets_cannot_be_deleted_through_the_api_async(service: TargetService) -> None:
    service._registry.instances.register(OpenAIChatTarget(**_TARGET_PARAMS, api_key="server-key"), name="configured")

    with pytest.raises(PermissionError):
        await service.delete_target_async(target_registry_name="configured", caller=_ALICE)

    configured = await service.get_target_async(target_registry_name="configured", caller=_ALICE)
    assert configured is not None
    assert not configured.requires_caller_credential
    assert not configured.can_delete


@pytest.mark.parametrize(
    ("caller", "request_kwargs"),
    [
        (None, {}),
        (_ALICE, {"register": False, "name": None}),
        (_ALICE, {"params": {**_TARGET_PARAMS, "api_key": "  "}}),
    ],
)
async def test_create_keeps_existing_key_handling_outside_registered_caller_credentials_async(
    service: TargetService, caller: str | None, request_kwargs: dict[str, object]
) -> None:
    payload: dict[str, object] = {
        "name": "shared",
        "type": "OpenAIChatTarget",
        "params": {**_TARGET_PARAMS, "api_key": _ALICE_KEY},
        **request_kwargs,
    }
    with patch.dict("os.environ", {"OPENAI_CHAT_KEY": "environment-key"}):
        created = await service.create_target_async(request=CreateTargetRequest(**payload), caller=caller)

    assert not getattr(created, "requires_caller_credential", False)
    assert len(get_caller_credential_store()) == 0
    if request_kwargs.get("register", True):
        target = service.get_target_object(target_registry_name="shared")
        assert isinstance(target._api_key, str)


async def test_realtime_target_keeps_existing_key_handling_async(service: TargetService) -> None:
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name="realtime",
            type="RealtimeTarget",
            params={"model_name": "test-model", "endpoint": "wss://example.test/v1", "api_key": _ALICE_KEY},
        ),
        caller=_ALICE,
    )

    assert isinstance(created, TargetInstance)
    assert not created.requires_caller_credential
    assert len(get_caller_credential_store()) == 0


async def test_failed_registration_stores_no_credential_async(service: TargetService) -> None:
    with (
        patch.object(service._registry.instances, "register", side_effect=ValueError("taken")),
        pytest.raises(ValueError, match="taken"),
    ):
        await _create_async(service, caller=_ALICE)

    assert len(get_caller_credential_store()) == 0


@pytest.mark.usefixtures("patch_central_database")
async def test_service_closure_forgets_caller_credentials_async() -> None:
    store = get_caller_credential_store()
    store.set_credential(principal=_ALICE, credential_id="shared", credential=_ALICE_KEY)

    await close_services_async()

    assert get_caller_credential_store() is store
    assert len(store) == 0


# ============================================================================
# REST contract
# ============================================================================


@dataclass
class _Caller:
    principal: str | None


@pytest.fixture
def caller() -> Iterator[_Caller]:
    current = _Caller(principal=_ALICE)

    def principal(_: Request) -> str | None:
        return current.principal

    with patch("pyrit.backend.routes.targets.caller_principal", side_effect=principal):
        yield current


@pytest.fixture
def client(compatibility_headers: dict[str, str], patch_central_database: object) -> TestClient:
    return TestClient(app, headers=compatibility_headers)


def _create_through_api(client: TestClient) -> TargetInstance:
    response = client.post(
        "/api/targets",
        json={"name": "shared", "type": "OpenAIChatTarget", "params": {**_TARGET_PARAMS, "api_key": _ALICE_KEY}},
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert _ALICE_KEY not in response.text
    return TargetInstance.model_validate(response.json())


def test_rest_create_reports_caller_credential_state(client: TestClient, caller: _Caller) -> None:
    created = _create_through_api(client)

    assert created.requires_caller_credential
    assert created.caller_credential_configured
    assert created.can_delete


def test_rest_other_caller_sets_and_removes_own_credential(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)
    caller.principal = _BOB

    before = client.get("/api/targets/shared").json()
    updated = client.put("/api/targets/shared/credentials", json={"credential": _BOB_KEY})
    removed = client.delete("/api/targets/shared/credentials")
    after = client.get("/api/targets/shared").json()

    assert before["caller_credential_configured"] is False
    assert before["can_delete"] is False
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["caller_credential_configured"] is True
    assert _BOB_KEY not in updated.text
    assert removed.status_code == status.HTTP_204_NO_CONTENT
    assert after["caller_credential_configured"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"credential": ""},
        {"credential": _BOB_KEY, "endpoint": "https://elsewhere.test/v1"},
        {},
    ],
)
def test_rest_set_credential_rejects_invalid_bodies_without_echoing_them(
    client: TestClient, caller: _Caller, body: dict[str, str]
) -> None:
    _create_through_api(client)

    response = client.put("/api/targets/shared/credentials", json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _BOB_KEY not in response.text


def test_rest_credential_routes_report_missing_and_unsupported_targets(client: TestClient, caller: _Caller) -> None:
    missing = client.put("/api/targets/missing/credentials", json={"credential": _BOB_KEY})
    client.post("/api/targets", json={"name": "text", "type": "TextTarget"})
    unsupported = client.put("/api/targets/text/credentials", json={"credential": _BOB_KEY})
    missing_delete = client.delete("/api/targets/missing/credentials")

    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert unsupported.status_code == status.HTTP_400_BAD_REQUEST
    assert _BOB_KEY not in unsupported.text
    assert missing_delete.status_code == status.HTTP_404_NOT_FOUND


def test_rest_credential_routes_require_a_verified_caller(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)
    caller.principal = None

    response = client.put("/api/targets/shared/credentials", json={"credential": _BOB_KEY})

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_rest_set_credential_requires_one_backend_process(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)
    runtime = SimpleNamespace(
        is_stopping=False,
        state="ready",
        topology_supported=False,
        operations=set(),
        management_operations=set(),
        apply_task=None,
    )

    with patch.object(app.state, "runtime_lifecycle", runtime, create=True):
        response = client.put("/api/targets/shared/credentials", json={"credential": _BOB_KEY})

    assert response.status_code == status.HTTP_409_CONFLICT


def test_rest_delete_target_is_limited_to_its_creator(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)

    caller.principal = _BOB
    forbidden = client.delete("/api/targets/shared")
    caller.principal = _ALICE
    deleted = client.delete("/api/targets/shared")
    missing = client.get("/api/targets/shared")

    assert forbidden.status_code == status.HTTP_403_FORBIDDEN
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert client.delete("/api/targets/shared").status_code == status.HTTP_404_NOT_FOUND
