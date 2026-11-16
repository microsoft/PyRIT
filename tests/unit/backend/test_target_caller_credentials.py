# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Targets that use each caller's own key: service and REST contract."""

from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Request, status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pyrit.auth import CallerCredential, CallerCredentialRequiredError, caller_principal_scope
from pyrit.backend.main import app
from pyrit.backend.models import CreateTargetRequest
from pyrit.backend.services import TargetService, get_target_service
from pyrit.models.catalog import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget, RoundRobinTarget
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
    yield
    TargetRegistry.reset_registry_singleton()
    get_target_service.cache_clear()


@pytest.fixture
def service(patch_central_database: object) -> TargetService:
    return TargetService()


async def _create_async(
    service: TargetService,
    *,
    caller: str | None,
    name: str = "caller",
    credential: str | None = _ALICE_KEY,
) -> TargetInstance:
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name=name,
            type="OpenAIChatTarget",
            params=_TARGET_PARAMS,
            auth_mode="caller",
            credential=credential,
        ),
        caller=caller,
    )
    assert isinstance(created, TargetInstance)
    return created


async def _create_shared_async(service: TargetService, *, name: str = "shared") -> TargetInstance:
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name=name, type="OpenAIChatTarget", params={**_TARGET_PARAMS, "api_key": _ALICE_KEY}
        ),
        caller=_ALICE,
    )
    assert isinstance(created, TargetInstance)
    return created


async def _resolve_as_async(target: PromptTarget, *, caller: str | None) -> str:
    assert isinstance(target, OpenAIChatTarget)
    with caller_principal_scope(caller):
        return await target._client._refresh_api_key()


async def test_targets_are_shared_by_default_async(service: TargetService) -> None:
    created = await _create_shared_async(service)

    target = service.get_target_object(target_registry_name="shared")
    assert target.caller_credential is None
    assert not created.requires_caller_credential
    assert created.caller_credential_configured is None
    assert _ALICE_KEY not in created.model_dump_json()
    assert await _resolve_as_async(target, caller=_BOB) == _ALICE_KEY


async def test_caller_target_keeps_no_key_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)

    target = service.get_target_object(target_registry_name="caller")
    assert isinstance(target.caller_credential, CallerCredential)
    assert _ALICE_KEY not in created.model_dump_json()
    assert _ALICE_KEY not in str(target.get_identifier())
    assert _ALICE_KEY not in repr(target.caller_credential)
    assert created.requires_caller_credential
    assert created.caller_credential_configured is True
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY


async def test_other_caller_sees_caller_target_but_cannot_use_its_key_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="caller")

    as_bob = await service.get_target_async(target_registry_name="caller", caller=_BOB)
    listed = await service.list_targets_async(caller=_BOB)

    assert as_bob is not None
    assert as_bob.requires_caller_credential
    assert as_bob.caller_credential_configured is False
    assert listed.items == [as_bob]
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_BOB)
    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=None)


async def test_caller_status_is_unknown_without_a_caller_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)

    anonymous = await service.get_target_async(target_registry_name="caller", caller=None)

    assert anonymous is not None
    assert anonymous.requires_caller_credential
    assert anonymous.caller_credential_configured is None


async def test_caller_target_can_be_created_before_any_key_is_set_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE, credential=None)
    target = service.get_target_object(target_registry_name="caller")

    assert created.requires_caller_credential
    assert created.caller_credential_configured is False
    with patch.dict("os.environ", {"OPENAI_CHAT_KEY": "environment-key"}), pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_ALICE)

    await service.set_caller_credential_async(target_registry_name="caller", caller=_ALICE, credential=_ALICE_KEY)

    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY


async def test_caller_target_without_a_key_needs_no_caller_async(service: TargetService) -> None:
    # A saved entry is restored as the server, with no caller, so it can't carry anyone's key.
    created = await _create_async(service, caller=None, credential=None)

    assert created.requires_caller_credential
    assert created.caller_credential_configured is None


async def test_set_and_remove_caller_credential_affect_only_that_caller_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="caller")

    updated = await service.set_caller_credential_async(target_registry_name="caller", caller=_BOB, credential=_BOB_KEY)

    assert updated is not None
    assert updated.caller_credential_configured is True
    assert _BOB_KEY not in updated.model_dump_json()
    assert await _resolve_as_async(target, caller=_BOB) == _BOB_KEY
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY

    assert await service.remove_caller_credential_async(target_registry_name="caller", caller=_BOB)

    with pytest.raises(CallerCredentialRequiredError):
        await _resolve_as_async(target, caller=_BOB)
    assert await _resolve_as_async(target, caller=_ALICE) == _ALICE_KEY


async def test_replaced_credential_keeps_target_identity_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)
    target = service.get_target_object(target_registry_name="caller")

    await service.set_caller_credential_async(target_registry_name="caller", caller=_ALICE, credential="replacement")

    assert target.get_identifier().hash == created.identifier.hash
    assert await _resolve_as_async(target, caller=_ALICE) == "replacement"


async def test_saved_binding_copy_resolves_the_current_caller_async(service: TargetService) -> None:
    created = await _create_async(service, caller=_ALICE)
    copy = await service.build_from_source_async(
        SourceInstanceSpec(source_name="caller", source_hash=created.identifier.hash, params={"temperature": 0.3})
    )
    try:
        assert await _resolve_as_async(copy, caller=_ALICE) == _ALICE_KEY
        with pytest.raises(CallerCredentialRequiredError):
            await _resolve_as_async(copy, caller=_BOB)
    finally:
        await copy.cleanup_target_async()


async def test_initializer_caller_target_takes_each_callers_key_async(service: TargetService) -> None:
    service._registry.instances.register(OpenAIChatTarget(**_TARGET_PARAMS, auth_mode="caller"), name="configured")

    before = await service.get_target_async(target_registry_name="configured", caller=_ALICE)
    after = await service.set_caller_credential_async(
        target_registry_name="configured", caller=_ALICE, credential=_ALICE_KEY
    )

    assert before is not None and before.requires_caller_credential
    assert before.caller_credential_configured is False
    assert after is not None and after.caller_credential_configured is True


@pytest.mark.parametrize(
    ("request_kwargs", "match"),
    [
        ({"type": "TextTarget", "params": {}}, "does not support each caller's own key"),
        (
            {"type": "RealtimeTarget", "params": {**_TARGET_PARAMS, "endpoint": "wss://example.test/v1"}},
            "does not support each caller's own key",
        ),
        (
            {"type": "LiteLLMDecisionTarget", "params": {"model_name": "openrouter/microsoft/microsoft-decision-1"}},
            "does not support each caller's own key",
        ),
        ({"params": {**_TARGET_PARAMS, "api_key": _BOB_KEY}}, "keeps no key"),
        ({"credential": "  "}, "nonempty string"),
    ],
)
async def test_caller_auth_rejects_unsupported_requests_async(
    service: TargetService, request_kwargs: dict[str, object], match: str
) -> None:
    payload: dict[str, object] = {
        "name": "caller",
        "type": "OpenAIChatTarget",
        "params": _TARGET_PARAMS,
        "auth_mode": "caller",
        "credential": _ALICE_KEY,
        **request_kwargs,
    }

    with pytest.raises(ValueError, match=match) as exc:
        await service.create_target_async(request=CreateTargetRequest(**payload), caller=_ALICE)

    assert _BOB_KEY not in str(exc.value)
    assert service.get_target_object(target_registry_name="caller") is None


@pytest.mark.parametrize(
    ("request_kwargs", "match"),
    [
        ({"auth_mode": "caller", "register": False}, "only to registered targets"),
        ({"auth_mode": "api_key", "credential": _ALICE_KEY}, "applies only to auth_mode 'caller'"),
    ],
)
def test_create_request_validates_caller_auth(request_kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match) as exc:
        CreateTargetRequest(name="caller", type="OpenAIChatTarget", params=_TARGET_PARAMS, **request_kwargs)

    assert _ALICE_KEY not in str(exc.value)


async def test_credential_requires_a_caller_async(service: TargetService) -> None:
    with pytest.raises(PermissionError, match="verified caller identity"):
        await _create_async(service, caller=None)

    assert service.get_target_object(target_registry_name="caller") is None


async def test_caller_credential_operations_reject_shared_targets_async(service: TargetService) -> None:
    await _create_shared_async(service)

    with pytest.raises(ValueError, match="does not take your own key"):
        await service.set_caller_credential_async(target_registry_name="shared", caller=_ALICE, credential="key")
    with pytest.raises(ValueError, match="does not take your own key"):
        await service.remove_caller_credential_async(target_registry_name="shared", caller=_ALICE)


async def test_caller_credential_operations_require_a_caller_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)

    with pytest.raises(PermissionError, match="verified caller identity"):
        await service.set_caller_credential_async(target_registry_name="caller", caller=None, credential="key")
    with pytest.raises(PermissionError, match="verified caller identity"):
        await service.remove_caller_credential_async(target_registry_name="caller", caller=None)


async def test_caller_credential_operations_report_missing_targets_async(service: TargetService) -> None:
    assert (
        await service.set_caller_credential_async(target_registry_name="missing", caller=_ALICE, credential="k") is None
    )
    assert not await service.remove_caller_credential_async(target_registry_name="missing", caller=_ALICE)


async def test_set_caller_credential_rejects_blank_value_async(service: TargetService) -> None:
    await _create_async(service, caller=_ALICE)

    with pytest.raises(ValueError, match="nonempty string"):
        await service.set_caller_credential_async(target_registry_name="caller", caller=_BOB, credential="  ")


async def test_decision_target_keeps_shared_api_key_behavior_async(service: TargetService) -> None:
    created = await service.create_target_async(
        request=CreateTargetRequest(
            name="decision",
            type="LiteLLMDecisionTarget",
            params={"model_name": "openrouter/microsoft/microsoft-decision-1", "api_key": _ALICE_KEY},
        ),
        caller=_ALICE,
    )
    assert isinstance(created, TargetInstance)
    assert not created.requires_caller_credential
    assert service.get_target_object(target_registry_name="decision")._api_key == _ALICE_KEY


async def test_composite_reports_and_checks_its_inner_targets_keys_async(service: TargetService) -> None:
    inner = OpenAIChatTarget(**_TARGET_PARAMS, auth_mode="caller")
    shared = OpenAIChatTarget(**_TARGET_PARAMS, api_key="server-key")
    service._registry.instances.register(RoundRobinTarget(targets=[inner, shared]), name="mixed")

    before = await service.get_target_async(target_registry_name="mixed", caller=_ALICE)
    assert inner.caller_credential is not None
    inner.caller_credential.set_credential(principal=_ALICE, credential=_ALICE_KEY)
    after = await service.get_target_async(target_registry_name="mixed", caller=_ALICE)

    assert before is not None and before.requires_caller_credential
    assert before.caller_credential_configured is False
    assert after is not None and after.caller_credential_configured is True
    with pytest.raises(ValueError, match="inner targets"):
        await service.set_caller_credential_async(target_registry_name="mixed", caller=_ALICE, credential="key")


async def test_failed_registration_leaves_no_target_async(service: TargetService) -> None:
    with (
        patch.object(service._registry.instances, "register", side_effect=ValueError("taken")),
        pytest.raises(ValueError, match="taken"),
    ):
        await _create_async(service, caller=_ALICE)

    assert service.get_target_object(target_registry_name="caller") is None


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


def _create_through_api(client: TestClient, *, caller_auth: bool = True) -> TargetInstance:
    body: dict[str, object] = (
        {
            "name": "caller",
            "type": "OpenAIChatTarget",
            "params": _TARGET_PARAMS,
            "auth_mode": "caller",
            "credential": _ALICE_KEY,
        }
        if caller_auth
        else {"name": "caller", "type": "OpenAIChatTarget", "params": {**_TARGET_PARAMS, "api_key": _ALICE_KEY}}
    )
    response = client.post("/api/targets", json=body)
    assert response.status_code == status.HTTP_201_CREATED
    assert _ALICE_KEY not in response.text
    return TargetInstance.model_validate(response.json())


@pytest.mark.parametrize(("caller_auth", "configured"), [(True, True), (False, None)])
def test_rest_create_reports_caller_credential_state(
    client: TestClient, caller: _Caller, caller_auth: bool, configured: bool | None
) -> None:
    created = _create_through_api(client, caller_auth=caller_auth)

    assert created.requires_caller_credential is caller_auth
    assert created.caller_credential_configured is configured


def test_rest_other_caller_sets_and_removes_own_credential(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)
    caller.principal = _BOB

    before = client.get("/api/targets/caller").json()
    updated = client.put("/api/targets/caller/credentials", json={"credential": _BOB_KEY})
    removed = client.delete("/api/targets/caller/credentials")
    after = client.get("/api/targets/caller").json()
    listed = client.get("/api/targets").json()["items"]

    assert before["caller_credential_configured"] is False
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["caller_credential_configured"] is True
    assert _BOB_KEY not in updated.text
    assert removed.status_code == status.HTTP_204_NO_CONTENT
    assert after["caller_credential_configured"] is False
    assert [item["caller_credential_configured"] for item in listed] == [False]


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

    response = client.put("/api/targets/caller/credentials", json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _BOB_KEY not in response.text


def test_rest_credential_routes_report_missing_and_shared_targets(client: TestClient, caller: _Caller) -> None:
    missing = client.put("/api/targets/missing/credentials", json={"credential": _BOB_KEY})
    _create_through_api(client, caller_auth=False)
    shared = client.put("/api/targets/caller/credentials", json={"credential": _BOB_KEY})
    missing_delete = client.delete("/api/targets/missing/credentials")

    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert shared.status_code == status.HTTP_400_BAD_REQUEST
    assert _BOB_KEY not in shared.text
    assert missing_delete.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize(
    "body",
    [
        {"name": "text", "type": "TextTarget", "auth_mode": "caller"},
        {
            "name": "caller",
            "type": "OpenAIChatTarget",
            "auth_mode": "caller",
            "params": {**_TARGET_PARAMS, "api_key": _BOB_KEY},
        },
    ],
)
def test_rest_create_rejects_unsupported_caller_targets(
    client: TestClient, caller: _Caller, body: dict[str, object]
) -> None:
    response = client.post("/api/targets", json=body)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _BOB_KEY not in response.text


def test_rest_create_rejects_a_credential_for_a_shared_target_without_echoing_it(
    client: TestClient, caller: _Caller
) -> None:
    response = client.post(
        "/api/targets",
        json={"name": "shared", "type": "OpenAIChatTarget", "params": _TARGET_PARAMS, "credential": _BOB_KEY},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _BOB_KEY not in response.text


def test_rest_credential_routes_require_a_verified_caller(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)
    caller.principal = None

    response = client.put("/api/targets/caller/credentials", json={"credential": _BOB_KEY})

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_rest_target_deletion_is_not_offered(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)

    response = client.delete("/api/targets/caller")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert client.get("/api/targets/caller").status_code == status.HTTP_200_OK


def _single_process_unsupported_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        is_stopping=False,
        state="ready",
        topology_supported=False,
        operations=set(),
        management_operations=set(),
        apply_task=None,
    )


def test_rest_set_credential_requires_one_backend_process(client: TestClient, caller: _Caller) -> None:
    _create_through_api(client)

    with patch.object(app.state, "runtime_lifecycle", _single_process_unsupported_runtime(), create=True):
        response = client.put("/api/targets/caller/credentials", json={"credential": _BOB_KEY})

    assert response.status_code == status.HTTP_409_CONFLICT


def test_rest_create_caller_target_requires_one_backend_process(client: TestClient, caller: _Caller) -> None:
    with patch.object(app.state, "runtime_lifecycle", _single_process_unsupported_runtime(), create=True):
        caller_target = client.post(
            "/api/targets",
            json={
                "name": "caller",
                "type": "OpenAIChatTarget",
                "params": _TARGET_PARAMS,
                "auth_mode": "caller",
                "credential": _ALICE_KEY,
            },
        )
        shared = client.post(
            "/api/targets",
            json={"name": "shared", "type": "OpenAIChatTarget", "params": {**_TARGET_PARAMS, "api_key": _ALICE_KEY}},
        )

    assert caller_target.status_code == status.HTTP_409_CONFLICT
    assert _ALICE_KEY not in caller_target.text
    assert shared.status_code == status.HTTP_201_CREATED
    assert get_target_service().get_target_object(target_registry_name="caller") is None
