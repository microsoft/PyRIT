# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private targets: who can see and use a target, and who can update its credentials."""

from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import patch

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
from pyrit.backend.services.technique_service import TechniqueService
from pyrit.converter import Base64Converter
from pyrit.models.component_spec import SourceInstanceSpec
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import (
    ConverterRegistry,
    Registry,
    TargetRegistry,
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

    assert (created.private, created.credentials_rejected) == (False, False)
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


async def _update_async(caller: str | None, name: str = "mine", **params: Any) -> Any:
    with caller_principal_scope(caller):
        return await TargetService().update_credentials_async(
            target_registry_name=name, request=UpdateTargetCredentialsRequest(params=params)
        )


@pytest.mark.parametrize(
    "params", [{"api_key": "new-key"}, {"headers": '{"Authorization": "Bearer new-token", "X-Team": "red"}'}]
)
async def test_the_creator_can_update_credentials_without_changing_identity_async(params: dict[str, Any]) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    old = _entry("alice", "mine")
    old.instance._credentials_rejected = True

    updated = await _update_async("alice", **params)

    new = _entry("alice", "mine")
    assert new.instance is not old.instance
    assert isinstance(new.instance, OpenAIChatTarget)
    assert new.instance.get_identifier().hash == old.instance.get_identifier().hash
    assert new.metadata == old.metadata
    assert (updated.private, updated.credentials_rejected) == (True, False)
    assert old.instance._api_key == _SECRET
    if "api_key" in params:
        assert new.instance._api_key == "new-key"
    assert "new-key" not in updated.model_dump_json() and "new-token" not in updated.model_dump_json()


@pytest.mark.parametrize("params", [{"endpoint": "https://other.example/v1"}, {"model_name": "gpt-4o-mini"}])
async def test_an_update_that_changes_identity_is_refused_and_keeps_the_target_async(params: dict[str, Any]) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    old = _entry("alice", "mine").instance

    with pytest.raises(ValueError, match="changes the target's identity") as error:
        await _update_async("alice", **params)

    assert _entry("alice", "mine").instance is old
    assert next(iter(params.values())) not in str(error.value)


@pytest.mark.parametrize(
    "params",
    [
        {"headers": f"not json {_SECRET}"},
        {"api_key": [f"Bearer {_SECRET}"]},
        {"max_requests_per_minute": {"x": _SECRET}},
        {"temperature": [_SECRET]},
        {"extra_body_parameters": {"api_key": _SECRET}, "max_tokens": f"{_SECRET}x"},
    ],
)
async def test_an_update_never_echoes_a_rejected_value_async(params: dict[str, Any]) -> None:
    await _create_async("alice", _request(private=True, api_key="old-key"))
    old = _entry("alice", "mine").instance

    with pytest.raises(ValueError) as error:
        await _update_async("alice", **params)

    assert _SECRET not in str(error.value)
    assert _entry("alice", "mine").instance is old


@pytest.mark.parametrize("value", ["", " ", None, [], {}, 0, False, ["key"]])
async def test_a_credential_update_needs_a_nonblank_string_async(value: Any) -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))

    with pytest.raises(ValueError, match="api_key"):
        await _update_async("alice", api_key=value)

    assert _entry("alice", "mine").instance._api_key == _SECRET


async def test_an_update_cannot_change_the_authentication_mode_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))

    with pytest.raises(ValueError, match="authentication mode"):
        await _update_async("alice", auth_mode="identity")


async def test_a_refused_update_releases_the_rebuilt_target_async() -> None:
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    released: list[Any] = []

    async def release_async(component: Any) -> None:
        released.append(component)

    with (
        patch("pyrit.backend.services.target_service.release_component_async", side_effect=release_async),
        pytest.raises(ValueError, match="identity"),
    ):
        await _update_async("alice", model_name="gpt-4o-mini")

    assert len(released) == 1
    assert released[0] is not _entry("alice", "mine").instance


async def test_the_creator_can_add_a_key_to_a_shared_target_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request())

    updated = await _update_async("alice", api_key="alice-key")

    assert updated.private is False
    assert _entry("bob", "mine").instance._api_key == "alice-key"


async def test_a_saved_target_cannot_be_updated_through_the_api_async() -> None:
    with saved_entry_registration(kind="targets", name="saved"):
        await _create_async(None, _request("saved", api_key=_SECRET))

    with pytest.raises(PermissionError, match="saved configuration"):
        await _update_async("alice", "saved", api_key="alice-key")


async def test_anyone_signed_in_can_update_a_shared_target_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request("shared-one"))
    await _create_async(None, _request("from-initializer"))

    assert (await _update_async("bob", "shared-one", api_key="bob-key")).private is False
    assert (await _update_async("bob", "from-initializer", api_key="bob-key")).private is False

    entry = _entry("carol", "shared-one")
    assert (entry.instance._api_key, get_entry_owner(entry)) == ("bob-key", "alice")


async def test_another_user_cannot_see_or_update_a_private_target_async() -> None:
    await _create_async("alice", _request("private-one", private=True, api_key=_SECRET))

    assert await _update_async("bob", "private-one", api_key="bob-key") is None
    assert _entry("alice", "private-one").instance._api_key == _SECRET


async def test_an_update_needs_a_signed_in_caller_async(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request("shared-one"))

    with pytest.raises(PermissionError, match="signed-in caller"):
        await _update_async(None, "shared-one", api_key="key")


async def test_a_target_that_cannot_be_rebuilt_is_refused_cleanly_async() -> None:
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="from-initializer")

    with pytest.raises(ValueError, match="can't be rebuilt"):
        await _update_async("alice", "from-initializer", api_key="alice-key")


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
    ("caller", "name", "params", "status_code"),
    [
        ("alice", "mine", {"api_key": "new-key"}, 200),
        ("alice", "mine", {"model_name": "gpt-4o-mini"}, 400),
        ("bob", "mine", {"api_key": "new-key"}, 404),
        ("bob", "team", {"api_key": "new-key"}, 200),
    ],
)
async def test_update_credentials_route_maps_each_outcome(
    monkeypatch: pytest.MonkeyPatch, caller: str, name: str, params: dict[str, Any], status_code: int
) -> None:
    monkeypatch.setenv("OPENAI_CHAT_KEY", "server-key")
    await _create_async("alice", _request(private=True, api_key=_SECRET))
    await _create_async("alice", _request("team"))

    with patch("pyrit.backend.routes.targets.get_target_service", return_value=TargetService()):
        response = _client_as(caller).put(f"/api/targets/{name}/credentials", json={"params": params})

    assert response.status_code == status_code
    assert "new-key" not in response.text
