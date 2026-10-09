# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for saving API-created instances and restoring them after a restart."""

import asyncio
import dataclasses
import json
import os
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.converters import CreateConverterRequest
from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.models.targets import CreateTargetRequest, UpdateTargetRequest
from pyrit.backend.routes.common import translate_saved_instance_errors
from pyrit.backend.services.converter_service import ConverterService, get_converter_service
from pyrit.backend.services.instance_persistence_service import (
    AdministratorRequiredError,
    InstanceConflictError,
    InstanceNotFoundError,
    InstancePersistenceService,
    InstanceStoreUnavailableError,
    PreconditionRequiredError,
    get_instance_persistence_service,
    restore_saved_instances_async,
)
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.backend.services.target_service import get_target_service
from pyrit.converter import Base64Converter
from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import CredentialReference, InstanceRecipe
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget, TextTarget
from pyrit.registry import ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.file_document_storage import DocumentConflictError
from pyrit.registry.instance_recipe_storage import InstanceRecipeStorage
from pyrit.registry.instance_restore import InstanceRestorePlanner
from pyrit.score import SubStringScorer

_SECRET = "sk-test-secret-value"
_KEY_VARIABLE = "TEST_PERSISTED_OPENAI_KEY"
_ENDPOINT = "https://test.openai.azure.com/"


def _openai_request(name: str, **overrides: object) -> CreateTargetRequest:
    fields: dict[str, object] = {
        "name": name,
        "type": "OpenAIChatTarget",
        "params": {"endpoint": _ENDPOINT, "model_name": "gpt-4o"},
        "credentials": {"api_key": {"env_var": _KEY_VARIABLE}},
    }
    return CreateTargetRequest.model_validate({**fields, **overrides})


def _saved_documents(directory: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.json"))}


def _reset_registries() -> None:
    for registry in (TargetRegistry, ConverterRegistry, ScorerRegistry):
        registry.reset_registry_singleton()
    for factory in (get_target_service, get_converter_service, get_scorer_service):
        factory.cache_clear()


async def _restart_async() -> InstancePersistenceService:
    """Discard every live instance and service, as a backend restart does, then restore."""
    await get_converter_service().close_async()
    _reset_registries()
    get_instance_persistence_service.cache_clear()
    await restore_saved_instances_async()
    return get_instance_persistence_service()


async def _restore_with_initializer_target_async(name: str, target: PromptTarget | None = None) -> PromptTarget:
    """Restart with an initializer that registers a target, a TextTarget by default, under a saved target's name."""
    await get_converter_service().close_async()
    _reset_registries()
    get_instance_persistence_service.cache_clear()
    registry = TargetRegistry.get_registry_singleton()
    initializer_target = target or registry.create_instance("TextTarget")
    registry.instances.register(initializer_target, name=name)
    await restore_saved_instances_async()
    return initializer_target


@pytest.fixture(autouse=True)
def isolated_registries(patch_central_database: object) -> Iterator[None]:
    _reset_registries()
    with patch.dict(os.environ, {_KEY_VARIABLE: _SECRET}):
        yield
    _reset_registries()


@pytest.fixture
async def converter_service() -> AsyncGenerator[ConverterService, None]:
    service = get_converter_service()
    try:
        yield service
    finally:
        await service.close_async()


async def test_create_saves_variable_name_and_never_the_credential(isolated_instance_recipes: Path) -> None:
    result = await get_target_service().create_target_async(request=_openai_request("chat"), is_admin=True)

    documents = _saved_documents(isolated_instance_recipes)
    assert len(documents) == 1
    content = next(iter(documents.values()))
    assert _KEY_VARIABLE in content
    assert _SECRET not in content
    assert json.loads(content)["credentials"] == {"api_key": {"env_var": _KEY_VARIABLE}}
    assert result.version
    entry = TargetRegistry.get_registry_singleton().instances.get_entry("chat")
    assert entry is not None
    assert get_instance_persistence_service().get_version(entry) == result.version


async def test_create_with_credential_reference_requires_admin(isolated_instance_recipes: Path) -> None:
    with pytest.raises(AdministratorRequiredError):
        await get_target_service().create_target_async(request=_openai_request("chat"))

    assert _saved_documents(isolated_instance_recipes) == {}
    assert "chat" not in TargetRegistry.get_registry_singleton().instances


async def test_create_without_credentials_does_not_require_admin(isolated_instance_recipes: Path) -> None:
    result = await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert result.version
    assert len(_saved_documents(isolated_instance_recipes)) == 1


async def test_create_rejects_credential_sent_as_value(isolated_instance_recipes: Path) -> None:
    request = _openai_request("chat", credentials={}, params={"endpoint": _ENDPOINT, "api_key": _SECRET})

    with pytest.raises(ValueError, match="'api_key' cannot be sent as a value"):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_create_drops_empty_credential_value(isolated_instance_recipes: Path) -> None:
    request = _openai_request("chat", params={"endpoint": _ENDPOINT, "model_name": "gpt-4o", "api_key": None})

    await get_target_service().create_target_async(request=request, is_admin=True)

    saved = json.loads(next(iter(_saved_documents(isolated_instance_recipes).values())))
    assert "api_key" not in saved["params"]


async def test_constructor_changes_to_its_arguments_never_reach_the_saved_recipe(
    isolated_instance_recipes: Path,
) -> None:
    client_settings = {"default_headers": {"X-Routing-Label": "review"}}
    request = _openai_request(
        "chat",
        params={"endpoint": _ENDPOINT, "model_name": "gpt-4o", "httpx_client_kwargs": client_settings},
        credentials={"api_key": {"env_var": _KEY_VARIABLE}, "headers": {"env_var": "TEST_PERSISTED_HEADERS"}},
    )
    with patch.dict(os.environ, {"TEST_PERSISTED_HEADERS": json.dumps({"Authorization": "Bearer header-secret"})}):
        await get_target_service().create_target_async(request=request, is_admin=True)

        content = next(iter(_saved_documents(isolated_instance_recipes).values()))
        assert "header-secret" not in content
        assert json.loads(content)["params"]["httpx_client_kwargs"] == client_settings
        persistence = await _restart_async()

    assert persistence.get_unrestorable(ComponentType.TARGET) == []
    assert "chat" in TargetRegistry.get_registry_singleton().instances


@pytest.mark.parametrize(
    ("credentials", "message"),
    [
        ({"endpoint": {"env_var": _KEY_VARIABLE}}, "'endpoint' is not a credential parameter"),
        ({"not_a_parameter": {"env_var": _KEY_VARIABLE}}, "Unknown parameter 'not_a_parameter'"),
        ({"api_key": {"env_var": "UNSET_PERSISTED_VARIABLE"}}, "'UNSET_PERSISTED_VARIABLE' .* is not set"),
    ],
)
async def test_create_rejects_invalid_credential_reference(
    isolated_instance_recipes: Path, credentials: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        await get_target_service().create_target_async(
            request=_openai_request("chat", credentials=credentials), is_admin=True
        )

    assert _saved_documents(isolated_instance_recipes) == {}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:hunter2@test.openai.azure.com/",
        "https://account.blob.core.windows.net/container?sv=2024&sig=abc123",
        "https://test.openai.azure.com/?api-key=abc123",
        "wss://user:hunter2@test.openai.azure.com/realtime",
        "https://test.openai.azure.com/#access_token=abc123",
    ],
)
async def test_create_rejects_credential_embedded_in_url(isolated_instance_recipes: Path, endpoint: str) -> None:
    request = _openai_request("chat", params={"endpoint": endpoint, "model_name": "gpt-4o"})

    with pytest.raises(ValueError, match="'endpoint' contains a credential in its URL"):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert _saved_documents(isolated_instance_recipes) == {}


@pytest.mark.parametrize(
    ("target_type", "params", "path"),
    [
        (
            "OpenAIChatTarget",
            {"endpoint": _ENDPOINT, "httpx_client_kwargs": {"headers": {"Authorization": "Bearer abc123"}}},
            "'httpx_client_kwargs.headers.Authorization' holds a credential",
        ),
        (
            "OpenAIChatTarget",
            {"endpoint": _ENDPOINT, "extra_body_parameters": {"metadata": {"api-key": "abc123"}}},
            "'extra_body_parameters.metadata.api-key' holds a credential",
        ),
        (
            "OpenAIChatTarget",
            {"endpoint": _ENDPOINT, "httpx_client_kwargs": {"proxy": "http://user:hunter2@proxy.test:8080"}},
            "'httpx_client_kwargs.proxy' contains a credential in its URL",
        ),
        (
            "HTTPXAPITarget",
            {"http_url": "https://api.test/chat", "form_data": {"password": "x"}},
            "'form_data.password' holds a credential",
        ),
        (
            "HTTPXAPITarget",
            {"http_url": "https://api.test/chat", "json_data": {"items": [{"client_secret": "abc123"}]}},
            r"'json_data.items\[0\].client_secret' holds a credential",
        ),
        (
            "HTTPXAPITarget",
            {"http_url": "https://api.test/chat", "params": {"code": "abc123"}},
            "'params.code' holds a credential",
        ),
        (
            "OpenAIChatTarget",
            {"endpoint": _ENDPOINT, "httpx_client_kwargs": {"default_query": {"sig": "abc123"}}},
            "'httpx_client_kwargs.default_query.sig' holds a credential",
        ),
        (
            "HTTPXAPITarget",
            {"http_url": "https://api.test/chat", "params": "api-version=1&api-key=abc123"},
            "'params' holds a credential",
        ),
        (
            "HTTPXAPITarget",
            {"http_url": "https://api.test/chat", "params": [["code", "abc123"]]},
            r"'params\[0\]' holds a credential",
        ),
    ],
)
async def test_create_rejects_credential_nested_in_free_form_settings(
    isolated_instance_recipes: Path, target_type: str, params: dict[str, object], path: str
) -> None:
    request = CreateTargetRequest.model_validate({"name": "nested", "type": target_type, "params": params})

    with pytest.raises(ValueError, match=f"{path}. Remove it; saved instances never store credentials"):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_create_keeps_ordinary_free_form_settings(isolated_instance_recipes: Path) -> None:
    extra_body = {"prompt_cache_key": "team", "reasoning": {"effort": "high"}, "authorization": None}
    request = _openai_request(
        "chat",
        params={"endpoint": _ENDPOINT, "model_name": "gpt-4o", "extra_body_parameters": extra_body},
    )

    await get_target_service().create_target_async(request=request, is_admin=True)

    saved = json.loads(next(iter(_saved_documents(isolated_instance_recipes).values())))
    assert saved["params"]["extra_body_parameters"] == extra_body


async def test_create_keeps_credential_named_keys_of_a_typed_mapping(isolated_instance_recipes: Path) -> None:
    patterns = {"password": r"password\s*=\s*\S+", "GitHub Token": r"ghp_[A-Za-z0-9]{36}"}

    await get_scorer_service().create_scorer_async(
        request=CreateScorerRequest(name="leaks", type="CredentialLeakScorer", params={"patterns": patterns})
    )

    saved = json.loads(next(iter(_saved_documents(isolated_instance_recipes).values())))
    assert saved["params"]["patterns"] == patterns


async def test_create_redacts_credential_from_construction_error(isolated_instance_recipes: Path) -> None:
    registry = TargetRegistry.get_registry_singleton()

    with (
        patch.object(registry, "create_instance", side_effect=ValueError(f"key {_SECRET} was rejected")),
        pytest.raises(ValueError, match=r"key \*\*\* was rejected") as error,
    ):
        await get_target_service().create_target_async(request=_openai_request("chat"), is_admin=True)

    assert _SECRET not in str(error.value)
    assert _saved_documents(isolated_instance_recipes) == {}


@pytest.mark.parametrize(
    ("target_type", "params", "credential"),
    [
        ("OpenAIChatTarget", {"endpoint": _ENDPOINT, "model_name": "gpt-4o"}, "api_key"),
        ("OpenAIChatTarget", {"endpoint": _ENDPOINT, "model_name": "gpt-4o"}, "headers"),
        ("AzureBlobStorageTarget", {"container_url": "https://account.blob.core.windows.net/container"}, "sas_token"),
    ],
)
async def test_create_identity_rejects_reference_to_a_credential_that_replaces_it(
    isolated_instance_recipes: Path, target_type: str, params: dict[str, object], credential: str
) -> None:
    request = CreateTargetRequest.model_validate(
        {
            "name": "identity",
            "type": target_type,
            "params": params,
            "credentials": {credential: {"env_var": _KEY_VARIABLE}},
            "auth_mode": "identity",
        }
    )

    with pytest.raises(ValueError, match=f"Identity authentication does not use {credential}"):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_create_identity_refuses_a_value_the_type_metadata_marks_as_replacing_it(
    isolated_instance_recipes: Path,
) -> None:
    registry = TargetRegistry.get_registry_singleton()
    metadata = registry.get_registered_class_metadata("AzureBlobStorageTarget")
    assert metadata is not None
    marked = dataclasses.replace(
        metadata,
        parameters=tuple(
            parameter.model_copy(update={"identity_conflicting": True})
            if parameter.name == "container_url"
            else parameter
            for parameter in metadata.parameters
        ),
    )
    request = CreateTargetRequest.model_validate(
        {
            "name": "blob",
            "type": "AzureBlobStorageTarget",
            "params": {"container_url": "https://account.blob.core.windows.net/container"},
            "auth_mode": "identity",
        }
    )

    with (
        patch.object(registry, "get_registered_class_metadata", return_value=marked),
        pytest.raises(ValueError, match="Identity authentication does not use container_url"),
    ):
        await get_target_service().create_target_async(request=request)

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_restore_rebuilds_the_parameters_an_authentication_mode_implies(isolated_instance_recipes: Path) -> None:
    request = CreateTargetRequest.model_validate(
        {
            "name": "blob",
            "type": "AzureBlobStorageTarget",
            "params": {"container_url": "https://account.blob.core.windows.net/container"},
            "auth_mode": "identity",
        }
    )
    await get_target_service().create_target_async(request=request)

    await _restart_async()

    restored = TargetRegistry.get_registry_singleton().instances.get("blob")
    assert restored._auth_mode == "identity"  # type: ignore[attr-defined]
    [document] = _saved_documents(isolated_instance_recipes).values()
    assert json.loads(document)["params"] == {"container_url": "https://account.blob.core.windows.net/container"}


async def test_create_rejects_name_already_registered(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    with pytest.raises(InstanceConflictError, match="already exists"):
        await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert len(_saved_documents(isolated_instance_recipes)) == 1


async def test_create_rejects_name_saved_but_not_restored() -> None:
    storage = InstanceRecipeStorage()
    storage.save_recipe(
        recipe=InstanceRecipe(kind=ComponentType.TARGET, name="text", type="TextTarget"), expected_version=None
    )

    with pytest.raises(InstanceConflictError, match="A saved target is already named 'text'"):
        await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))


async def test_create_removes_saved_recipe_when_registration_fails(isolated_instance_recipes: Path) -> None:
    instances = TargetRegistry.get_registry_singleton().instances

    with (
        patch.object(instances, "register", side_effect=ValueError("registration failed")),
        pytest.raises(ValueError, match="registration failed"),
    ):
        await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_create_completes_when_the_request_is_cancelled(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    started, release = asyncio.Event(), asyncio.Event()
    build_async = service.build_async

    async def slow_build_async(**kwargs: object) -> object:
        started.set()
        await release.wait()
        return await build_async(**kwargs)

    with patch.object(service, "build_async", side_effect=slow_build_async):
        request = asyncio.create_task(
            service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
        )
        await started.wait()
        request.cancel()
        release.set()
        await get_instance_persistence_service().close_async()

    assert request.cancelled()
    assert "text" in TargetRegistry.get_registry_singleton().instances
    assert len(_saved_documents(isolated_instance_recipes)) == 1


async def test_create_decodes_mapping_credential_from_json() -> None:
    headers = {"Authorization": f"Bearer {_SECRET}"}
    request = CreateTargetRequest(
        name="http",
        type="HTTPXAPITarget",
        params={"http_url": "https://example.com/api"},
        credentials={"headers": {"env_var": "TEST_PERSISTED_HEADERS"}},
    )

    with patch.dict(os.environ, {"TEST_PERSISTED_HEADERS": json.dumps(headers)}):
        await get_target_service().create_target_async(request=request, is_admin=True)

    target = TargetRegistry.get_registry_singleton().instances.get("http")
    assert target is not None
    assert target.headers == headers


@pytest.mark.parametrize("value", ["Bearer token", "[1, 2]", '{"Authorization": 1}'])
async def test_create_rejects_mapping_credential_that_is_not_a_json_object_of_strings(value: str) -> None:
    request = CreateTargetRequest(
        name="http",
        type="HTTPXAPITarget",
        params={"http_url": "https://example.com/api"},
        credentials={"headers": {"env_var": "TEST_PERSISTED_HEADERS"}},
    )

    with (
        patch.dict(os.environ, {"TEST_PERSISTED_HEADERS": value}),
        pytest.raises(ValueError, match="must hold a JSON object of strings"),
    ):
        await get_target_service().create_target_async(request=request, is_admin=True)


async def test_create_rejects_self_reference() -> None:
    service = get_target_service()
    await service.create_target_async(request=_openai_request("first"), is_admin=True)
    request = CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "pool"]})

    with pytest.raises(ValueError, match="cannot reference itself"):
        await service.create_target_async(request=request)


async def test_update_replaces_recipe_and_live_instance(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)

    updated = await service.update_target_async(
        target_registry_name="chat",
        request=UpdateTargetRequest(
            type="OpenAIChatTarget",
            params={"endpoint": _ENDPOINT, "model_name": "gpt-4.1"},
            credentials={"api_key": CredentialReference(env_var=_KEY_VARIABLE)},
            version=created.version,
        ),
        is_admin=True,
    )

    assert updated.version != created.version
    assert updated.identifier.model_name == "gpt-4.1"
    target = TargetRegistry.get_registry_singleton().instances.get("chat")
    assert target is not None
    assert target.get_identifier().params["model_name"] == "gpt-4.1"
    saved = json.loads(next(iter(_saved_documents(isolated_instance_recipes).values())))
    assert saved["params"]["model_name"] == "gpt-4.1"


async def test_update_removes_credentials_the_request_omits(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)

    await service.update_target_async(
        target_registry_name="chat",
        request=UpdateTargetRequest(type="TextTarget", version=created.version),
        is_admin=True,
    )

    saved = json.loads(next(iter(_saved_documents(isolated_instance_recipes).values())))
    assert "credentials" not in saved or saved["credentials"] == {}


async def test_update_of_saved_credentials_requires_admin() -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)

    with pytest.raises(AdministratorRequiredError):
        await service.update_target_async(
            target_registry_name="chat",
            request=UpdateTargetRequest(type="TextTarget", version=created.version),
        )


async def test_update_with_stale_version_conflicts() -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)
    replacement = UpdateTargetRequest(
        type="OpenAIChatTarget",
        params={"endpoint": _ENDPOINT, "model_name": "gpt-4.1"},
        credentials={"api_key": CredentialReference(env_var=_KEY_VARIABLE)},
        version=created.version,
    )
    await service.update_target_async(target_registry_name="chat", request=replacement, is_admin=True)

    with pytest.raises(InstanceConflictError, match="was changed after it was read"):
        await service.update_target_async(target_registry_name="chat", request=replacement, is_admin=True)


async def test_update_with_identical_content_keeps_version() -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    updated = await service.update_target_async(
        target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=created.version)
    )

    assert updated.version == created.version


async def test_update_unsaved_target_is_not_found() -> None:
    with pytest.raises(InstanceNotFoundError):
        await get_target_service().update_target_async(
            target_registry_name="missing", request=UpdateTargetRequest(type="TextTarget", version="0" * 64)
        )


async def test_update_refuses_saved_target_shadowed_by_initializer() -> None:
    storage = InstanceRecipeStorage()
    stored = storage.save_recipe(
        recipe=InstanceRecipe(kind=ComponentType.TARGET, name="text", type="TextTarget"), expected_version=None
    )
    TargetRegistry.get_registry_singleton().instances.register(
        TargetRegistry.get_registry_singleton().create_instance("TextTarget"), name="text"
    )

    with pytest.raises(InstanceConflictError, match="registered by an initializer"):
        await get_target_service().update_target_async(
            target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=stored.version)
        )


async def test_update_and_delete_refuse_target_referenced_by_saved_instance() -> None:
    service = get_target_service()
    first = await service.create_target_async(request=_openai_request("first"), is_admin=True)
    await service.create_target_async(request=_openai_request("second"), is_admin=True)
    await service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
        await service.delete_target_async(target_registry_name="first", expected_version=first.version, is_admin=True)
    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
        await service.update_target_async(
            target_registry_name="first",
            request=UpdateTargetRequest(type="TextTarget", version=first.version),
            is_admin=True,
        )


@pytest.mark.parametrize("change", ["delete", "update"])
async def test_a_saved_document_the_store_cannot_read_blocks_changes_it_may_depend_on(
    isolated_instance_recipes: Path, change: str
) -> None:
    service = get_target_service()
    first = await service.create_target_async(request=_openai_request("first"), is_admin=True)
    await service.create_target_async(request=_openai_request("second"), is_admin=True)
    await service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    saved = _saved_documents(isolated_instance_recipes)
    pool = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="pool")
    read_bytes = Path.read_bytes

    def _read_bytes(path: Path) -> bytes:
        if path == isolated_instance_recipes / f"{pool}.json":
            raise PermissionError("access denied")
        return read_bytes(path)

    with (
        patch.object(Path, "read_bytes", _read_bytes),
        pytest.raises(InstanceStoreUnavailableError, match=f"because they may reference it: {pool}"),
    ):
        if change == "delete":
            await service.delete_target_async(
                target_registry_name="first", expected_version=first.version, is_admin=True
            )
        else:
            await service.update_target_async(
                target_registry_name="first",
                request=UpdateTargetRequest(type="TextTarget", version=first.version),
                is_admin=True,
            )

    assert _saved_documents(isolated_instance_recipes) == saved
    assert "first" in TargetRegistry.get_registry_singleton().instances


@pytest.mark.parametrize("content", ["{not json", '{"schema_version": 2, "kind": "target", "name": "pool"}'])
async def test_a_restored_dependent_still_protects_what_it_holds_after_its_document_changes(
    isolated_instance_recipes: Path, content: str
) -> None:
    for name in ("first", "second"):
        await get_target_service().create_target_async(request=_openai_request(name), is_admin=True)
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await _restart_async()
    service = get_target_service()
    [first] = [item for item in (await service.list_targets_async()).items if item.target_registry_name == "first"]
    pool = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="pool")
    (isolated_instance_recipes / f"{pool}.json").write_text(content, encoding="utf-8")
    message = r"saved instances reference it: Target 'pool'\..* until a restart or reinitialization: Target 'pool'\."

    with pytest.raises(InstanceConflictError, match=message):
        await service.update_target_async(
            target_registry_name="first",
            request=UpdateTargetRequest(type="TextTarget", version=first.version),
            is_admin=True,
        )
    with pytest.raises(InstanceConflictError, match=message):
        await service.delete_target_async(target_registry_name="first", expected_version=first.version, is_admin=True)

    targets = TargetRegistry.get_registry_singleton().instances
    assert targets.get("first") in targets.get("pool")._targets


async def test_a_store_directory_that_cannot_be_listed_is_never_read_as_empty(isolated_instance_recipes: Path) -> None:
    first = await get_target_service().create_target_async(request=_openai_request("first"), is_admin=True)
    iterdir = Path.iterdir

    def _iterdir(path: Path) -> Iterator[Path]:
        if path == isolated_instance_recipes:
            raise PermissionError("cannot list")
        return iterdir(path)

    with patch.object(Path, "iterdir", _iterdir):
        persistence = await _restart_async()
        with pytest.raises(InstanceStoreUnavailableError, match="is unavailable: cannot list"):
            await get_target_service().delete_target_async(
                target_registry_name="first", expected_version=first.version, is_admin=True
            )

    assert persistence.restore_error is not None
    assert "is unavailable: cannot list" in persistence.restore_error
    assert len(_saved_documents(isolated_instance_recipes)) == 1


def _touch_document(directory: Path, name: str) -> str:
    """Change a saved target's document outside the API without changing what it says, and return its new version."""
    document = directory / f"{InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name=name)}.json"
    document.write_bytes(document.read_bytes() + b"\n")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.TARGET, name=name)
    assert stored is not None and stored.version
    return stored.version


async def test_a_restored_parent_whose_document_changed_is_still_protected(isolated_instance_recipes: Path) -> None:
    for name in ("first", "second"):
        await get_target_service().create_target_async(request=_openai_request(name), is_admin=True)
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await _restart_async()
    version = _touch_document(isolated_instance_recipes, "first")

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
        await get_target_service().delete_target_async(
            target_registry_name="first", expected_version=version, is_admin=True
        )

    assert len(_saved_documents(isolated_instance_recipes)) == 3
    assert "first" in TargetRegistry.get_registry_singleton().instances


@pytest.mark.parametrize("change", ["delete", "update"])
async def test_a_restored_instance_whose_document_changed_is_changed_or_removed_with_its_document(
    isolated_instance_recipes: Path, change: str
) -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    await _restart_async()
    version = _touch_document(isolated_instance_recipes, "text")
    service = get_target_service()

    if change == "delete":
        assert await service.delete_target_async(target_registry_name="text", expected_version=version)
        assert "text" not in TargetRegistry.get_registry_singleton().instances
        assert _saved_documents(isolated_instance_recipes) == {}
    else:
        updated = await service.update_target_async(
            target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=version)
        )
        entry = TargetRegistry.get_registry_singleton().instances.get_entry("text")
        assert get_instance_persistence_service().get_version(entry) == updated.version


@pytest.mark.parametrize(
    "content", ["{not json", '{"schema_version": 2, "kind": "target", "name": "first", "type": "OpenAIChatTarget"}']
)
async def test_deleting_a_live_instance_by_its_document_name_checks_and_removes_the_live_one(
    isolated_instance_recipes: Path, content: str
) -> None:
    for name in ("first", "second"):
        await get_target_service().create_target_async(request=_openai_request(name), is_admin=True)
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await _restart_async()
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="first")
    (isolated_instance_recipes / f"{document_name}.json").write_text(content, encoding="utf-8")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.TARGET, name=document_name)
    assert stored is not None and stored.version
    service = get_target_service()
    [pool] = [item for item in (await service.list_targets_async()).items if item.target_registry_name == "pool"]

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
        await service.delete_target_async(
            target_registry_name=document_name, expected_version=stored.version, is_admin=True
        )
    assert await service.delete_target_async(target_registry_name="pool", expected_version=pool.version)
    assert await service.delete_target_async(
        target_registry_name=document_name, expected_version=stored.version, is_admin=True
    )

    assert "first" not in TargetRegistry.get_registry_singleton().instances
    assert await service.create_target_async(request=_openai_request("first"), is_admin=True)


async def test_a_document_name_maps_to_the_live_instance_built_from_it_even_when_registered_by_another(
    isolated_instance_recipes: Path,
) -> None:
    for name in ("first", "second"):
        await get_target_service().create_target_async(request=_openai_request(name), is_admin=True)
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await _restart_async()
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="first")
    TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name=document_name)
    (isolated_instance_recipes / f"{document_name}.json").write_text("{not json", encoding="utf-8")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.TARGET, name=document_name)
    assert stored is not None and stored.version

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
        await get_target_service().delete_target_async(
            target_registry_name=document_name, expected_version=stored.version, is_admin=True
        )

    assert "first" in TargetRegistry.get_registry_singleton().instances


async def test_a_document_name_registered_by_another_still_removes_the_live_instance_built_from_it(
    isolated_instance_recipes: Path,
) -> None:
    await get_converter_service().create_converter_async(
        request=CreateConverterRequest(name="codec", type="Base64Converter")
    )
    await _restart_async()
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name="codec")
    converters = ConverterRegistry.get_registry_singleton().instances
    converters.register(Base64Converter(), name=document_name)
    (isolated_instance_recipes / f"{document_name}.json").write_text("{not json", encoding="utf-8")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.CONVERTER, name=document_name)
    assert stored is not None and stored.version

    assert await get_converter_service().delete_converter_async(
        converter_id=document_name, expected_version=stored.version, is_admin=True
    )

    assert "codec" not in converters
    assert document_name in converters
    assert _saved_documents(isolated_instance_recipes) == {}


async def test_deleting_a_document_clears_what_the_last_restore_listed_for_it(isolated_instance_recipes: Path) -> None:
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="future")
    document = isolated_instance_recipes / f"{document_name}.json"
    document.write_text(
        json.dumps({"schema_version": 2, "kind": "target", "name": "future", "type": "TextTarget"}), encoding="utf-8"
    )
    assert [item.name for item in (await _restart_async()).get_unrestorable(ComponentType.TARGET)] == ["future"]
    document.write_text("{not json", encoding="utf-8")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.TARGET, name=document_name)
    assert stored is not None and stored.version

    assert await get_target_service().delete_target_async(
        target_registry_name=document_name, expected_version=stored.version, is_admin=True
    )

    assert get_instance_persistence_service().get_unrestorable(ComponentType.TARGET) == []


async def test_deleting_a_document_by_its_name_removes_that_document_even_when_one_is_saved_under_it_as_a_name(
    isolated_instance_recipes: Path,
) -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="owner", type="TextTarget"))
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="owner")
    other_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name=document_name)
    for name in (document_name, other_name):
        (isolated_instance_recipes / f"{name}.json").write_text("{not json", encoding="utf-8")
    listed = {item.name: item for item in (await _restart_async()).get_unrestorable(ComponentType.TARGET)}

    assert await get_target_service().delete_target_async(
        target_registry_name=document_name, expected_version=listed[document_name].version, is_admin=True
    )

    remaining = get_instance_persistence_service().get_unrestorable(ComponentType.TARGET)
    assert [item.name for item in remaining] == [other_name]
    assert [document.stem for document in isolated_instance_recipes.glob("*.json")] == [other_name]


async def test_deleting_a_live_instance_by_its_document_name_removes_its_document_not_one_saved_under_that_name(
    isolated_instance_recipes: Path,
) -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="owner", type="TextTarget"))
    await _restart_async()
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="owner")
    other_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name=document_name)
    for name in (document_name, other_name):
        (isolated_instance_recipes / f"{name}.json").write_text("{not json", encoding="utf-8")
    stored = InstanceRecipeStorage().load_recipe(kind=ComponentType.TARGET, name=document_name)
    assert stored is not None and stored.version

    assert await get_target_service().delete_target_async(
        target_registry_name=document_name, expected_version=stored.version, is_admin=True
    )

    assert "owner" not in TargetRegistry.get_registry_singleton().instances
    assert [document.stem for document in isolated_instance_recipes.glob("*.json")] == [other_name]


async def test_deleting_a_restored_instance_whose_document_is_gone_says_to_restart(
    isolated_instance_recipes: Path,
) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    for document in isolated_instance_recipes.glob("*.json"):
        document.unlink()

    with pytest.raises(InstanceConflictError, match="whose document no longer exists. Restart or reinitialize"):
        await service.delete_target_async(target_registry_name="text", expected_version=created.version)

    assert "text" in TargetRegistry.get_registry_singleton().instances


async def test_saving_is_refused_while_a_failed_restore_still_cannot_list_the_store(
    isolated_instance_recipes: Path,
) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    iterdir = Path.iterdir
    refused = "cannot be saved while the saved instances cannot be listed"

    def _iterdir(path: Path) -> Iterator[Path]:
        if path == isolated_instance_recipes:
            raise PermissionError("cannot list")
        return iterdir(path)

    with patch.object(Path, "iterdir", _iterdir):
        await _restart_async()
        with pytest.raises(InstanceStoreUnavailableError, match=refused):
            await get_target_service().create_target_async(request=CreateTargetRequest(name="plain", type="TextTarget"))
        with pytest.raises(InstanceStoreUnavailableError, match=refused):
            await get_target_service().update_target_async(
                target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=created.version)
            )

    assert len(_saved_documents(isolated_instance_recipes)) == 1
    assert await get_target_service().create_target_async(request=CreateTargetRequest(name="plain", type="TextTarget"))


async def test_delete_saved_target_requires_version() -> None:
    service = get_target_service()
    await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    with pytest.raises(PreconditionRequiredError):
        await service.delete_target_async(target_registry_name="text", expected_version=None)


async def test_delete_removes_recipe_and_live_instance(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert await service.delete_target_async(target_registry_name="text", expected_version=created.version)

    assert _saved_documents(isolated_instance_recipes) == {}
    assert "text" not in TargetRegistry.get_registry_singleton().instances


async def test_delete_of_saved_credentials_requires_admin(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)

    with pytest.raises(AdministratorRequiredError):
        await service.delete_target_async(target_registry_name="chat", expected_version=created.version)

    assert len(_saved_documents(isolated_instance_recipes)) == 1


async def test_delete_shadowed_saved_target_keeps_initializer_target(isolated_instance_recipes: Path) -> None:
    stored = InstanceRecipeStorage().save_recipe(
        recipe=InstanceRecipe(kind=ComponentType.TARGET, name="text", type="TextTarget"), expected_version=None
    )
    registry = TargetRegistry.get_registry_singleton()
    initializer_target = registry.create_instance("TextTarget")
    registry.instances.register(initializer_target, name="text")

    assert await get_target_service().delete_target_async(target_registry_name="text", expected_version=stored.version)

    assert _saved_documents(isolated_instance_recipes) == {}
    assert registry.instances.get("text") is initializer_target


async def test_delete_unsaved_target_from_initializer_conflicts() -> None:
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(registry.create_instance("TextTarget"), name="text")

    with pytest.raises(InstanceConflictError, match="registered by an initializer and is not saved"):
        await get_target_service().delete_target_async(target_registry_name="text", expected_version=None)


async def test_delete_missing_target_returns_false() -> None:
    assert not await get_target_service().delete_target_async(target_registry_name="missing", expected_version=None)


async def test_delete_unreadable_document_by_reported_name_requires_admin(isolated_instance_recipes: Path) -> None:
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="broken")
    (isolated_instance_recipes / f"{document}.json").write_text(
        '{"kind": "target", "name": "broken"}', encoding="utf-8"
    )
    persistence = await _restart_async()
    [unreadable] = persistence.get_unrestorable(ComponentType.TARGET)
    assert unreadable.name == "broken"
    assert "Repair or delete the document" in unreadable.reason

    with pytest.raises(AdministratorRequiredError):
        await get_target_service().delete_target_async(
            target_registry_name=unreadable.name, expected_version=unreadable.version
        )
    assert await get_target_service().delete_target_async(
        target_registry_name=unreadable.name, expected_version=unreadable.version, is_admin=True
    )
    assert _saved_documents(isolated_instance_recipes) == {}
    assert get_instance_persistence_service().get_unrestorable(ComponentType.TARGET) == []


async def test_restore_rebuilds_instances_in_reference_order() -> None:
    target_service = get_target_service()
    await target_service.create_target_async(request=_openai_request("first"), is_admin=True)
    await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await get_scorer_service().create_scorer_async(
        request=CreateScorerRequest(name="refusal", type="SelfAskRefusalScorer", params={"chat_target": "pool"})
    )

    persistence = await _restart_async()

    targets = TargetRegistry.get_registry_singleton().instances
    assert {"first", "second", "pool"} <= set(targets.get_names())
    pool = targets.get("pool")
    assert pool is not None
    assert {target.get_identifier().hash for target in pool._targets} == {
        targets.get("first").get_identifier().hash,
        targets.get("second").get_identifier().hash,
    }
    assert "refusal" in ScorerRegistry.get_registry_singleton().instances
    assert persistence.get_unrestorable(ComponentType.TARGET) == []
    assert persistence.restore_error is None
    listed = await get_target_service().list_targets_async()
    assert all(item.version for item in listed.items if item.target_registry_name in {"first", "second", "pool"})


async def test_restore_reports_unset_environment_variable_and_dependents() -> None:
    target_service = get_target_service()
    await target_service.create_target_async(request=_openai_request("first"), is_admin=True)
    await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )

    with patch.dict(os.environ, {_KEY_VARIABLE: ""}):
        persistence = await _restart_async()

    reasons = {item.name: item.reason for item in persistence.get_unrestorable(ComponentType.TARGET)}
    assert reasons["first"] == f"Environment variable '{_KEY_VARIABLE}' (for 'api_key') is not set."
    assert reasons["pool"] == "It depends on target 'first', which could not be restored."
    assert TargetRegistry.get_registry_singleton().instances.get_names() == []
    listed = await get_target_service().list_targets_async()
    assert [item.name for item in listed.unrestorable] == ["first", "pool", "second"]
    assert (
        get_target_service()
        .describe_missing_target(target_registry_name="first")
        .startswith("Target 'first' is saved but was not restored: Environment variable")
    )


async def test_restore_never_rebuilds_a_raw_credential_saved_by_hand(isolated_instance_recipes: Path) -> None:
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="chat")
    recipe = {"kind": "target", "name": "chat", "type": "OpenAIChatTarget", "params": {"api_key": _SECRET}}
    (isolated_instance_recipes / f"{document}.json").write_text(json.dumps(recipe), encoding="utf-8")

    persistence = await _restart_async()

    [item] = persistence.get_unrestorable(ComponentType.TARGET)
    assert "'api_key' cannot be sent as a value" in item.reason
    assert "chat" not in TargetRegistry.get_registry_singleton().instances


async def test_restore_leaves_initializer_instance_and_blocks_its_saved_dependents() -> None:
    target_service = get_target_service()
    await target_service.create_target_async(request=_openai_request("first"), is_admin=True)
    await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )

    initializer_target = await _restore_with_initializer_target_async("first")

    reasons = {
        item.name: item.reason for item in get_instance_persistence_service().get_unrestorable(ComponentType.TARGET)
    }
    assert "already registered" in reasons["first"]
    assert reasons["pool"] == "It depends on target 'first', which could not be restored."
    registry = TargetRegistry.get_registry_singleton()
    assert registry.instances.get("first") is initializer_target
    assert "second" in registry.instances


async def test_restore_records_store_outage_without_raising() -> None:
    with patch.object(InstanceRecipeStorage, "list_recipes", side_effect=OSError("disk unavailable")):
        persistence = await _restart_async()

    assert persistence.restore_error is not None
    assert "disk unavailable" in persistence.restore_error
    listed = await get_target_service().list_targets_async()
    assert listed.restore_error == persistence.restore_error


async def test_references_are_refused_until_a_failed_restore_succeeds() -> None:
    target_service = get_target_service()
    for name in ("first", "second"):
        await target_service.create_target_async(request=_openai_request(name), is_admin=True)
    with patch.object(InstanceRecipeStorage, "list_recipes", side_effect=OSError("disk unavailable")):
        await _restart_async()
    registry = TargetRegistry.get_registry_singleton()
    for name in ("first", "second"):
        registry.instances.register(
            OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key=_SECRET), name=name
        )
    pool = CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})

    with pytest.raises(InstanceConflictError, match="cannot reference other instances until the saved instances are"):
        await get_target_service().create_target_async(request=pool)
    assert await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    await _restart_async()
    assert (await get_target_service().create_target_async(request=pool)).version


async def test_restore_rebuilds_converter_upload_in_new_service(converter_service: ConverterService) -> None:
    content = b"%PDF-1.4\n"
    data_uri = "data:application/pdf;base64,JVBERi0xLjQK"
    await converter_service.create_converter_async(
        request=CreateConverterRequest(name="pdf", type="PDFConverter", params={"existing_pdf": data_uri})
    )

    await _restart_async()

    restored_service = get_converter_service()
    try:
        entry = ConverterRegistry.get_registry_singleton().instances.get_entry("pdf")
        assert entry is not None
        [owned] = entry.metadata["owned_artifact_paths"]
        assert Path(owned).parent == restored_service._upload_path
        assert Path(owned).read_bytes() == content
    finally:
        await restored_service.close_async()


async def test_scorer_and_converter_share_the_same_persistence(isolated_instance_recipes: Path) -> None:
    ScorerRegistry.get_registry_singleton().register_class(SubStringScorer)
    await get_scorer_service().create_scorer_async(
        request=CreateScorerRequest(name="contains", type="SubStringScorer", params={"substring": "x"})
    )
    await get_converter_service().create_converter_async(
        request=CreateConverterRequest(name="b64", type="Base64Converter")
    )

    await _restart_async()

    assert "contains" in ScorerRegistry.get_registry_singleton().instances
    assert "b64" in ConverterRegistry.get_registry_singleton().instances
    assert len(_saved_documents(isolated_instance_recipes)) == 2
    await get_converter_service().close_async()


def test_routes_map_saved_instance_errors(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)
    body = {"name": "chat", "type": "OpenAIChatTarget", "params": {"endpoint": _ENDPOINT, "model_name": "gpt-4o"}}

    assert client.post("/api/targets", json={**body, "params": {**body["params"], "api_key": "x"}}).status_code == 400
    with_reference = {**body, "credentials": {"api_key": {"env_var": _KEY_VARIABLE}}}
    assert client.post("/api/targets", json=with_reference).status_code == 403

    with patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}):
        created = client.post("/api/targets", json=with_reference)
        assert created.status_code == 201
        version = created.json()["version"]
        assert client.post("/api/targets", json=with_reference).status_code == 409
        assert client.delete("/api/targets/chat").status_code == 428
        stale = {
            "type": "OpenAIChatTarget",
            "params": {"endpoint": _ENDPOINT, "model_name": "gpt-4o"},
            "version": "0" * 64,
        }
        assert client.put("/api/targets/chat", json=stale).status_code == 409
        assert client.put("/api/targets/missing", json=stale).status_code == 404
        assert client.get("/api/targets/chat").json()["version"] == version
        assert client.delete(f"/api/targets/chat?version={version}").status_code == 204

    assert client.get("/api/targets/chat").status_code == 404
    assert client.delete("/api/targets/missing").status_code == 404


def test_converter_and_scorer_routes_replace_and_delete_saved_instances(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers)

    created = client.post(
        "/api/converters", json={"name": "caesar", "type": "CaesarConverter", "params": {"caesar_offset": 1}}
    )
    assert created.status_code == 201
    replaced = client.put(
        "/api/converters/caesar",
        json={"type": "CaesarConverter", "params": {"caesar_offset": 2}, "version": created.json()["version"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["identifier"]["caesar_offset"] == 2
    assert client.delete("/api/converters/caesar").status_code == 428
    assert client.delete(f"/api/converters/caesar?version={replaced.json()['version']}").status_code == 204

    scorer = client.post(
        "/api/scorers", json={"name": "contains", "type": "SubStringScorer", "params": {"substring": "x"}}
    )
    assert scorer.status_code == 201
    replaced = client.put(
        "/api/scorers/contains",
        json={"type": "SubStringScorer", "params": {"substring": "y"}, "version": scorer.json()["version"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["version"] != scorer.json()["version"]
    assert client.delete("/api/scorers/contains").status_code == 428
    assert client.delete(f"/api/scorers/contains?version={replaced.json()['version']}").status_code == 204
    assert client.get("/api/scorers/contains").status_code == 404
    assert client.delete("/api/scorers/contains").status_code == 404

    ScorerRegistry.get_registry_singleton().instances.register(
        SubStringScorer(substring="z"), name="initializer-scorer"
    )
    assert client.delete("/api/scorers/initializer-scorer").status_code == 409


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AdministratorRequiredError("admin"), 403),
        (InstanceNotFoundError("missing"), 404),
        (PreconditionRequiredError("version"), 428),
        (InstanceConflictError("conflict"), 409),
        (InstanceStoreUnavailableError("store down"), 503),
        (ValueError("invalid"), 400),
        (RuntimeError("bug"), 500),
    ],
)
def test_saved_instance_errors_map_to_status_codes(error: Exception, status_code: int) -> None:
    with pytest.raises(HTTPException) as raised, translate_saved_instance_errors(action="create target"):
        raise error

    assert raised.value.status_code == status_code


async def test_update_puts_the_previous_recipe_back_when_registration_fails(isolated_instance_recipes: Path) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=_openai_request("chat"), is_admin=True)
    saved = _saved_documents(isolated_instance_recipes)
    registry = TargetRegistry.get_registry_singleton()
    live = registry.instances.get("chat")
    replacement = UpdateTargetRequest(
        type="OpenAIChatTarget",
        params={"endpoint": _ENDPOINT, "model_name": "gpt-4.1"},
        credentials={"api_key": CredentialReference(env_var=_KEY_VARIABLE)},
        version=created.version,
    )

    with (
        patch.object(registry.instances, "register", side_effect=TypeError("registration failed")),
        pytest.raises(TypeError, match="registration failed"),
    ):
        await service.update_target_async(target_registry_name="chat", request=replacement, is_admin=True)

    assert _saved_documents(isolated_instance_recipes) == saved
    assert registry.instances.get("chat") is live


async def test_update_conflicts_when_the_saved_recipe_changes_while_building() -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    live = TargetRegistry.get_registry_singleton().instances.get("text")
    changed = DocumentConflictError(name="text", expected_version=created.version, actual_version="other")

    with (
        patch.object(InstanceRecipeStorage, "save_recipe", side_effect=changed),
        pytest.raises(InstanceConflictError, match="was changed after it was read"),
    ):
        await service.update_target_async(
            target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=created.version)
        )

    assert TargetRegistry.get_registry_singleton().instances.get("text") is live


async def test_delete_conflicts_when_the_saved_recipe_changes_while_deleting() -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    changed = DocumentConflictError(name="text", expected_version=created.version, actual_version=None)

    with (
        patch.object(InstanceRecipeStorage, "delete_recipe", side_effect=changed),
        pytest.raises(InstanceConflictError, match="was changed after it was read"),
    ):
        await service.delete_target_async(target_registry_name="text", expected_version=created.version)

    assert "text" in TargetRegistry.get_registry_singleton().instances


async def test_create_leaves_the_recipe_for_the_next_restore_when_it_cannot_be_removed(
    isolated_instance_recipes: Path, caplog: pytest.LogCaptureFixture
) -> None:
    instances = TargetRegistry.get_registry_singleton().instances

    with (
        patch.object(instances, "register", side_effect=ValueError("registration failed")),
        patch.object(InstanceRecipeStorage, "delete_recipe", side_effect=OSError("disk full")),
        pytest.raises(ValueError, match="registration failed"),
    ):
        await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert len(_saved_documents(isolated_instance_recipes)) == 1
    assert "Could not remove the saved recipe of 'text'" in caplog.text


async def test_restore_reports_an_instance_the_registry_refuses() -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    await get_converter_service().close_async()
    _reset_registries()
    get_instance_persistence_service.cache_clear()

    with patch.object(TargetRegistry.get_registry_singleton().instances, "register", side_effect=ValueError("refused")):
        await restore_saved_instances_async()

    [item] = get_instance_persistence_service().get_unrestorable(ComponentType.TARGET)
    assert (item.name, item.reason) == ("text", "refused")


async def test_restore_reports_a_type_that_is_no_longer_registered() -> None:
    InstanceRecipeStorage().save_recipe(
        recipe=InstanceRecipe(kind=ComponentType.TARGET, name="gone", type="RemovedTarget"), expected_version=None
    )

    persistence = await _restart_async()

    [item] = persistence.get_unrestorable(ComponentType.TARGET)
    assert item.reason == "Type 'RemovedTarget' is not registered."


async def test_create_redacts_secrets_quoted_from_inside_a_credential(isolated_instance_recipes: Path) -> None:
    template = (
        "POST https://api.test/chat?code=fn-secret-123 HTTP/1.1\n"
        "Host: api.test\n"
        "Authorization: Bearer tok-secret-456\n\n{PROMPT}"
    )
    request = CreateTargetRequest.model_validate(
        {"name": "http", "type": "HTTPTarget", "credentials": {"http_request": {"env_var": "TEST_PERSISTED_TEMPLATE"}}}
    )
    error_text = "bad URL https://api.test/chat?code=fn-secret-123 and token tok-secret-456"

    with (
        patch.dict(os.environ, {"TEST_PERSISTED_TEMPLATE": template}),
        patch.object(TargetRegistry.get_registry_singleton(), "create_instance", side_effect=ValueError(error_text)),
        pytest.raises(ValueError, match="bad URL") as error,
    ):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert "fn-secret-123" not in str(error.value)
    assert "tok-secret-456" not in str(error.value)


async def test_create_redacts_a_json_escaped_credential(isolated_instance_recipes: Path) -> None:
    quoted = 'sk-"quoted"-secret'

    with (
        patch.dict(os.environ, {_KEY_VARIABLE: quoted}),
        patch.object(
            TargetRegistry.get_registry_singleton(),
            "create_instance",
            side_effect=ValueError(json.dumps({"error": quoted})),
        ),
        pytest.raises(ValueError, match=r"\*\*\*") as error,
    ):
        await get_target_service().create_target_async(request=_openai_request("chat"), is_admin=True)

    assert "quoted" not in str(error.value)


@pytest.mark.parametrize(
    ("template", "quoted"),
    [
        ("GET https://api.test:99999/f?code=q7z HTTP/1.1\nHost: api.test\n\n", "https://api.test:99999/f?code=***"),
        ("GET /f?code=fn-123456 HTTP/1.1\nHost: api.test:99999\n\n", "https://api.test:99999/f?code=***"),
        ("GET /f HTTP/1.1\nHost: user:pw-123456@api.test:99999\n\n", "https://***@api.test:99999/f"),
    ],
)
async def test_restore_reason_has_the_secrets_inside_a_credential_removed(
    isolated_instance_recipes: Path, template: str, quoted: str
) -> None:
    request = CreateTargetRequest.model_validate(
        {"name": "http", "type": "HTTPTarget", "credentials": {"http_request": {"env_var": "TEST_PERSISTED_TEMPLATE"}}}
    )
    with patch.dict(os.environ, {"TEST_PERSISTED_TEMPLATE": "GET /f HTTP/1.1\nHost: api.test\n\n"}):
        await get_target_service().create_target_async(request=request, is_admin=True)

    with patch.dict(os.environ, {"TEST_PERSISTED_TEMPLATE": template}):
        [item] = (await _restart_async()).get_unrestorable(ComponentType.TARGET)

    assert f"Invalid port in HTTP destination: {quoted}" in item.reason
    assert not any(secret in item.reason for secret in ("q7z", "fn-123456", "pw-123456"))


@pytest.mark.parametrize(
    "template",
    [
        "GET f?code=fn-123456 HTTP/1.1\nHost: api.test:99999\n\n",
        "GET ?code=fn-123456 HTTP/1.1\r\nHost: api.test:99999\r\n\r\n",
        "GET /f?code=fn#123456 HTTP/1.1\nHost: api.test:99999\n\n",
        "GET /f HTTP/1.1\nHost: user:pw#123456@api.test:99999\n\n",
        "GET /f HTTP/1.1\nHost: user:pw-123456@api.test\uff1a443\n\n",
        "CONNECT user:pw-123456@api.test:99999 HTTP/1.1\nHost: api.test:99999\n\n",
        "GET https://user:pw/123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
        "GET https://user:pw?123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
        "GET https://alice@example.com:pw/123456@api.test/f HTTP/1.1\nHost: api.test\n\n",
    ],
)
async def test_create_error_has_the_secrets_of_a_malformed_raw_request_removed(
    isolated_instance_recipes: Path, template: str
) -> None:
    request = CreateTargetRequest.model_validate(
        {"name": "http", "type": "HTTPTarget", "credentials": {"http_request": {"env_var": "TEST_PERSISTED_TEMPLATE"}}}
    )

    with (
        patch.dict(os.environ, {"TEST_PERSISTED_TEMPLATE": template}),
        pytest.raises(ValueError, match="api.test") as error,
    ):
        await get_target_service().create_target_async(request=request, is_admin=True)

    assert "123456" not in str(error.value)


async def test_update_refuses_a_saved_document_that_cannot_be_read(isolated_instance_recipes: Path) -> None:
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="broken")
    (isolated_instance_recipes / f"{document}.json").write_text(
        '{"kind": "target", "name": "broken"}', encoding="utf-8"
    )
    saved = _saved_documents(isolated_instance_recipes)
    [unreadable] = (await _restart_async()).get_unrestorable(ComponentType.TARGET)
    assert unreadable.version is not None

    with pytest.raises(InstanceConflictError, match="cannot be replaced: The saved document .* cannot be read"):
        await get_target_service().update_target_async(
            target_registry_name="broken",
            request=UpdateTargetRequest(type="TextTarget", version=unreadable.version),
            is_admin=True,
        )

    assert _saved_documents(isolated_instance_recipes) == saved


async def test_delete_finds_an_unreadable_document_by_the_name_it_is_reported_under(
    isolated_instance_recipes: Path,
) -> None:
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name="broken")
    (isolated_instance_recipes / f"{document}.json").write_text("{not json", encoding="utf-8")
    persistence = await _restart_async()
    [unreadable] = persistence.get_unrestorable(ComponentType.CONVERTER)
    assert unreadable.name == document
    service = get_converter_service()

    try:
        assert await service.delete_converter_async(
            converter_id=document, expected_version=unreadable.version, is_admin=True
        )
    finally:
        await service.close_async()

    assert _saved_documents(isolated_instance_recipes) == {}
    assert persistence.get_unrestorable(ComponentType.CONVERTER) == []


async def test_delete_refuses_an_unrestored_target_that_saved_instances_reference() -> None:
    target_service = get_target_service()
    first = await target_service.create_target_async(request=_openai_request("first"), is_admin=True)
    await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    with patch.dict(os.environ, {_KEY_VARIABLE: ""}):
        await _restart_async()
    assert "first" not in TargetRegistry.get_registry_singleton().instances

    with pytest.raises(InstanceConflictError, match="cannot be deleted because saved instances reference it"):
        await get_target_service().delete_target_async(
            target_registry_name="first", expected_version=first.version, is_admin=True
        )


@pytest.mark.parametrize(
    "document",
    [
        {
            "schema_version": 2,
            "kind": "converter",
            "name": "future",
            "type": "TranslationConverter",
            "params": {"converter_target": "text"},
        },
        {"schema_version": 2, "kind": "converter", "name": "future", "type": "Future", "uses": [{"target": "text"}]},
        {
            "kind": "converter",
            "name": "future",
            "type": "TranslationConverter",
            "params": {"converter_target": "text"},
            "x": 1,
        },
    ],
)
async def test_delete_refuses_a_target_that_a_saved_document_this_pyrit_cannot_use_names(
    isolated_instance_recipes: Path, document: dict[str, object]
) -> None:
    created = await get_target_service().create_target_async(
        request=CreateTargetRequest(name="text", type="TextTarget")
    )
    future = InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name="future")
    (isolated_instance_recipes / f"{future}.json").write_text(json.dumps(document), encoding="utf-8")
    [unusable] = (await _restart_async()).get_unrestorable(ComponentType.CONVERTER)

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Converter 'future'"):
        await get_target_service().delete_target_async(target_registry_name="text", expected_version=created.version)
    replaced = await get_target_service().update_target_async(
        target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=created.version)
    )
    converter_service = get_converter_service()
    try:
        assert await converter_service.delete_converter_async(
            converter_id="future", expected_version=unusable.version, is_admin=True
        )
    finally:
        await converter_service.close_async()

    assert await get_target_service().delete_target_async(
        target_registry_name="text", expected_version=replaced.version
    )


@pytest.mark.parametrize(
    "content",
    ["{not json", '["text"]', '{"schema_version": 2, "kind": "converter", "name": "future", "type": "text"}'],
)
async def test_delete_is_not_refused_by_a_saved_document_without_readable_references(
    isolated_instance_recipes: Path, content: str
) -> None:
    created = await get_target_service().create_target_async(
        request=CreateTargetRequest(name="text", type="TextTarget")
    )
    future = InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name="future")
    (isolated_instance_recipes / f"{future}.json").write_text(content, encoding="utf-8")
    await _restart_async()

    assert await get_target_service().delete_target_async(target_registry_name="text", expected_version=created.version)


async def test_saved_documents_this_pyrit_cannot_use_that_name_each_other_can_be_deleted(
    isolated_instance_recipes: Path,
) -> None:
    for name, other in (("cycle-a", "cycle-b"), ("cycle-b", "cycle-a")):
        document = InstanceRecipeStorage.get_document_name(kind=ComponentType.CONVERTER, name=name)
        (isolated_instance_recipes / f"{document}.json").write_text(
            json.dumps(
                {"schema_version": 2, "kind": "converter", "name": name, "type": "X", "params": {"next": other}}
            ),
            encoding="utf-8",
        )
    persistence = await _restart_async()
    service = get_converter_service()

    try:
        for unusable in persistence.get_unrestorable(ComponentType.CONVERTER):
            assert await service.delete_converter_async(
                converter_id=unusable.name, expected_version=unusable.version, is_admin=True
            )
    finally:
        await service.close_async()

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_saved_document_this_pyrit_cannot_use_is_deleted_by_its_instance_name_when_it_names_itself(
    isolated_instance_recipes: Path,
) -> None:
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="alpha")
    (isolated_instance_recipes / f"{document}.json").write_text(
        json.dumps({"kind": "target", "name": "renamed", "type": "TextTarget", "params": {"note": "alpha"}}),
        encoding="utf-8",
    )
    [unusable] = (await _restart_async()).get_unrestorable(ComponentType.TARGET)
    assert unusable.name == document

    assert await get_target_service().delete_target_async(
        target_registry_name="alpha", expected_version=unusable.version, is_admin=True
    )

    assert _saved_documents(isolated_instance_recipes) == {}
    assert get_instance_persistence_service().get_unrestorable(ComponentType.TARGET) == []


async def test_a_recipe_and_a_document_this_pyrit_cannot_use_that_name_each_other_are_deleted_document_first(
    isolated_instance_recipes: Path,
) -> None:
    InstanceRecipeStorage().save_recipe(
        recipe=InstanceRecipe(
            kind=ComponentType.TARGET, name="pool", type="RoundRobinTarget", params={"targets": ["future"]}
        ),
        expected_version=None,
    )
    future = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="future")
    (isolated_instance_recipes / f"{future}.json").write_text(
        json.dumps({"schema_version": 2, "kind": "target", "name": "future", "type": "X", "params": {"next": "pool"}}),
        encoding="utf-8",
    )
    unrestorable = {item.name: item for item in (await _restart_async()).get_unrestorable(ComponentType.TARGET)}
    service = get_target_service()

    with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'future'"):
        await service.delete_target_async(target_registry_name="pool", expected_version=unrestorable["pool"].version)
    assert await service.delete_target_async(
        target_registry_name="future", expected_version=unrestorable["future"].version, is_admin=True
    )
    assert await service.delete_target_async(target_registry_name="pool", expected_version=unrestorable["pool"].version)

    assert _saved_documents(isolated_instance_recipes) == {}


@pytest.mark.parametrize(
    "content", ["{not json", '{"schema_version": 2, "kind": "target", "name": "first", "type": "OpenAIChatTarget"}']
)
@pytest.mark.parametrize("by_instance_name", [True, False])
async def test_a_document_this_pyrit_cannot_use_is_deleted_by_either_name_while_recipes_reference_it(
    isolated_instance_recipes: Path, content: str, by_instance_name: bool
) -> None:
    pool = InstanceRecipeStorage().save_recipe(
        recipe=InstanceRecipe(
            kind=ComponentType.TARGET, name="pool", type="RoundRobinTarget", params={"targets": ["first"]}
        ),
        expected_version=None,
    )
    document_name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="first")
    (isolated_instance_recipes / f"{document_name}.json").write_text(content, encoding="utf-8")
    unrestorable = {item.name: item for item in (await _restart_async()).get_unrestorable(ComponentType.TARGET)}
    assert unrestorable.pop("pool").reason == "It depends on target 'first', which could not be restored."
    [document] = unrestorable.values()

    assert await get_target_service().delete_target_async(
        target_registry_name="first" if by_instance_name else document_name,
        expected_version=document.version,
        is_admin=True,
    )

    assert _saved_documents(isolated_instance_recipes) == {
        f"{InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name='pool')}.json": pool.content.decode()
    }
    [blocked] = get_instance_persistence_service().get_unrestorable(ComponentType.TARGET)
    assert blocked.reason == "It references target 'first', which is not registered."


async def test_delete_of_a_shadowed_recipe_ignores_dependents_bound_to_the_registered_target(
    isolated_instance_recipes: Path,
) -> None:
    storage = InstanceRecipeStorage()
    stored = storage.save_recipe(
        recipe=InstanceRecipe(kind=ComponentType.TARGET, name="text", type="TextTarget"), expected_version=None
    )
    storage.save_recipe(
        recipe=InstanceRecipe(
            kind=ComponentType.TARGET, name="pool", type="RoundRobinTarget", params={"targets": ["text"]}
        ),
        expected_version=None,
    )
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(registry.create_instance("TextTarget"), name="text")

    assert await get_target_service().delete_target_async(target_registry_name="text", expected_version=stored.version)

    assert len(_saved_documents(isolated_instance_recipes)) == 1


async def test_update_puts_back_a_hand_formatted_recipe_byte_for_byte(isolated_instance_recipes: Path) -> None:
    document = isolated_instance_recipes / (
        InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="text") + ".json"
    )
    document.write_text('{"kind":"target","name":"text","type":"TextTarget"}', encoding="utf-8")
    original = document.read_bytes()
    persistence = await _restart_async()
    registry = TargetRegistry.get_registry_singleton()
    version = persistence.get_version(registry.instances.get_entry("text"))
    assert version is not None

    with (
        patch.object(registry.instances, "register", side_effect=TypeError("registration failed")),
        pytest.raises(TypeError, match="registration failed"),
    ):
        await get_target_service().update_target_async(
            target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=version)
        )

    assert document.read_bytes() == original
    assert await get_target_service().delete_target_async(target_registry_name="text", expected_version=version)
    assert "text" not in registry.instances


async def test_restore_records_an_unexpected_failure_without_raising() -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    with patch.object(InstanceRestorePlanner, "plan", side_effect=RuntimeError("planner failed")):
        persistence = await _restart_async()

    assert persistence.restore_error == "Saved instances were not restored: planner failed"
    listed = await get_target_service().list_targets_async()
    assert listed.restore_error == persistence.restore_error


async def test_restore_records_a_kind_service_that_cannot_be_built_without_raising() -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    with patch(
        "pyrit.backend.services.converter_service.get_converter_service",
        side_effect=OSError("uploads directory is gone"),
    ):
        persistence = await _restart_async()

    assert persistence.restore_error == "Saved instances were not restored: uploads directory is gone"
    listed = await get_target_service().list_targets_async()
    assert listed.items == []
    assert listed.restore_error == persistence.restore_error


async def test_restore_reports_a_saved_document_the_store_cannot_read(isolated_instance_recipes: Path) -> None:
    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))
    read_bytes = Path.read_bytes

    def _read_bytes(path: Path) -> bytes:
        if path.parent == isolated_instance_recipes and path.suffix == ".json":
            raise PermissionError("access denied")
        return read_bytes(path)

    with patch.object(Path, "read_bytes", _read_bytes):
        persistence = await _restart_async()

    [item] = persistence.get_unrestorable(ComponentType.TARGET)
    assert item.version is None
    assert "could not be read from storage: access denied" in item.reason
    assert "text" not in TargetRegistry.get_registry_singleton().instances


async def test_create_refuses_a_name_shaped_like_a_saved_document_name(isolated_instance_recipes: Path) -> None:
    name = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="text")

    with pytest.raises(ValueError, match="are reserved"):
        await get_target_service().create_target_async(request=CreateTargetRequest(name=name, type="TextTarget"))

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_create_discards_the_recipe_even_when_freeing_the_instance_fails(isolated_instance_recipes: Path) -> None:
    instances = TargetRegistry.get_registry_singleton().instances

    with (
        patch.object(instances, "register", side_effect=ValueError("registration failed")),
        patch.object(InstancePersistenceService, "_release_async", side_effect=OSError("cleanup failed")),
        pytest.raises(ValueError, match="registration failed"),
    ):
        await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    assert _saved_documents(isolated_instance_recipes) == {}


async def test_store_errors_never_report_the_source_sas_signature() -> None:
    get_instance_persistence_service.cache_clear()
    persistence = get_instance_persistence_service()
    persistence.configure_source("https://account.blob.core.windows.net/recipes?sv=2024&sig=sigsecret123")
    outage = OSError("GET https://account.blob.core.windows.net/recipes?sv=2024&sig=sigsecret123 failed")

    with patch.object(InstanceRecipeStorage, "list_recipes", side_effect=outage):
        await restore_saved_instances_async()

    assert persistence.restore_error is not None
    assert "sigsecret123" not in persistence.restore_error
    assert "sv=2024" in persistence.restore_error


async def test_responses_never_show_a_secret_from_a_referenced_request_template() -> None:
    template = (
        "POST https://api.test/chat?code=fn-secret-123&auth_token=tok-secret-456 HTTP/1.1\nHost: api.test\n\n{PROMPT}"
    )
    request = CreateTargetRequest.model_validate(
        {"name": "http", "type": "HTTPTarget", "credentials": {"http_request": {"env_var": "TEST_PERSISTED_TEMPLATE"}}}
    )

    with patch.dict(os.environ, {"TEST_PERSISTED_TEMPLATE": template}):
        created = await get_target_service().create_target_async(request=request, is_admin=True)
    listed = await get_target_service().list_targets_async()

    for secret in ("fn-secret-123", "tok-secret-456"):
        assert secret not in created.model_dump_json()
        assert secret not in listed.model_dump_json()
    assert created.identifier.params["endpoint"] == "https://api.test/chat?code=***&auth_token=***"


async def test_committed_update_and_delete_succeed_when_freeing_the_old_instance_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = get_target_service()
    created = await service.create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    with patch.object(type(service), "release_entry_async", side_effect=OSError("cleanup failed")):
        updated = await service.update_target_async(
            target_registry_name="text", request=UpdateTargetRequest(type="TextTarget", version=created.version)
        )
        deleted = await service.delete_target_async(target_registry_name="text", expected_version=updated.version)

    assert deleted
    assert "text" not in TargetRegistry.get_registry_singleton().instances
    assert caplog.text.count("Could not free what 'text' owned after it was replaced or removed") == 2


async def test_repairing_a_dependency_updates_the_reason_its_dependents_were_not_restored() -> None:
    target_service = get_target_service()
    first = await target_service.create_target_async(request=_openai_request("first"), is_admin=True)
    second = await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    with patch.dict(os.environ, {_KEY_VARIABLE: ""}):
        persistence = await _restart_async()
    repair = {"type": "OpenAIChatTarget", "params": {"endpoint": _ENDPOINT, "model_name": "gpt-4o"}}
    repair["credentials"] = {"api_key": {"env_var": _KEY_VARIABLE}}

    await get_target_service().update_target_async(
        target_registry_name="first",
        request=UpdateTargetRequest.model_validate({**repair, "version": first.version}),
        is_admin=True,
    )
    reasons = {item.name: item.reason for item in persistence.get_unrestorable(ComponentType.TARGET)}
    assert reasons["pool"] == "It depends on target 'second', which could not be restored."

    await get_target_service().update_target_async(
        target_registry_name="second",
        request=UpdateTargetRequest.model_validate({**repair, "version": second.version}),
        is_admin=True,
    )
    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason.startswith("Everything it references is registered now.")


async def test_dependents_of_a_shadowed_instance_stay_blocked_until_its_saved_copy_is_deleted() -> None:
    target_service = get_target_service()
    for name in ("first", "second"):
        await target_service.create_target_async(request=_openai_request(name), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    initializer_target = OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key=_SECRET)
    await _restore_with_initializer_target_async("first", initializer_target)
    persistence = get_instance_persistence_service()

    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    unrestorable = {item.name: item for item in persistence.get_unrestorable(ComponentType.TARGET)}
    assert unrestorable["pool"].reason == "It depends on target 'first', which could not be restored."
    with pytest.raises(InstanceConflictError, match="cannot reference Target 'first' because the saved one was not"):
        await get_target_service().create_target_async(
            request=CreateTargetRequest(name="late", type="RoundRobinTarget", params={"targets": ["first", "second"]})
        )

    await get_target_service().delete_target_async(
        target_registry_name="first", expected_version=unrestorable["first"].version, is_admin=True
    )

    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason.startswith("Everything it references is registered now.")
    await _restore_with_initializer_target_async("first", initializer_target)
    assert get_instance_persistence_service().get_unrestorable(ComponentType.TARGET) == []
    assert initializer_target in TargetRegistry.get_registry_singleton().instances.get("pool").inner_targets


@pytest.mark.parametrize(
    "content", ['{"schema_version": 2, "kind": "target", "name": "first", "type": "OpenAIChatTarget"}', "{not json"]
)
async def test_dependents_of_a_saved_document_this_pyrit_cannot_use_are_not_bound_to_another_instance(
    isolated_instance_recipes: Path, content: str
) -> None:
    target_service = get_target_service()
    for name in ("first", "second"):
        await target_service.create_target_async(request=_openai_request(name), is_admin=True)
    await target_service.create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    first = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="first")
    (isolated_instance_recipes / f"{first}.json").write_text(content, encoding="utf-8")
    initializer_target = OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key=_SECRET)
    await _restore_with_initializer_target_async("first", initializer_target)
    persistence = get_instance_persistence_service()

    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    unrestorable = {item.name: item for item in persistence.get_unrestorable(ComponentType.TARGET)}
    pool = unrestorable.pop("pool")
    assert pool.reason == "It depends on target 'first', which could not be restored."
    assert "pool" not in TargetRegistry.get_registry_singleton().instances
    pool_settings = {"type": "RoundRobinTarget", "params": {"targets": ["first", "second"]}}
    with pytest.raises(InstanceConflictError, match="cannot reference Target 'first' because the saved one was not"):
        await get_target_service().update_target_async(
            target_registry_name="pool", request=UpdateTargetRequest(**pool_settings, version=pool.version)
        )
    with pytest.raises(InstanceConflictError, match="cannot reference Target 'first'"):
        await get_target_service().create_target_async(request=CreateTargetRequest(name="late", **pool_settings))
    [unusable] = unrestorable.values()
    await get_target_service().delete_target_async(
        target_registry_name=unusable.name, expected_version=unusable.version, is_admin=True
    )

    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason.startswith("Everything it references is registered now.")
    await get_target_service().create_target_async(request=CreateTargetRequest(name="late", **pool_settings))
    await _restore_with_initializer_target_async("first", initializer_target)
    for name in ("pool", "late"):
        assert initializer_target in TargetRegistry.get_registry_singleton().instances.get(name).inner_targets


async def test_a_document_listed_under_its_document_name_does_not_block_an_instance_of_that_name(
    isolated_instance_recipes: Path,
) -> None:
    target_service = get_target_service()
    await target_service.create_target_async(request=CreateTargetRequest(name="orphan", type="TextTarget"))
    await target_service.create_target_async(request=_openai_request("second"), is_admin=True)
    orphan = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="orphan")
    initializer_target = OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key=_SECRET)
    await _restore_with_initializer_target_async(orphan, initializer_target)
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": [orphan, "second"]})
    )
    (isolated_instance_recipes / f"{orphan}.json").write_text("{not json", encoding="utf-8")

    await _restore_with_initializer_target_async(orphan, initializer_target)

    [unreadable] = get_instance_persistence_service().get_unrestorable(ComponentType.TARGET)
    assert unreadable.name == orphan
    assert initializer_target in TargetRegistry.get_registry_singleton().instances.get("pool").inner_targets


async def test_a_reference_stays_missing_until_an_instance_takes_its_name() -> None:
    first, second = (OpenAIChatTarget(endpoint=_ENDPOINT, model_name="gpt-4o", api_key=_SECRET) for _ in range(2))
    registry = TargetRegistry.get_registry_singleton()
    registry.instances.register(first, name="first")
    registry.instances.register(second, name="second")
    await get_target_service().create_target_async(
        request=CreateTargetRequest(name="pool", type="RoundRobinTarget", params={"targets": ["first", "second"]})
    )
    await _restore_with_initializer_target_async("second", second)
    persistence = get_instance_persistence_service()

    await get_target_service().create_target_async(request=CreateTargetRequest(name="text", type="TextTarget"))

    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason == "It references target 'first', which is not registered."

    await get_target_service().create_target_async(request=_openai_request("first"), is_admin=True)

    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason.startswith("Everything it references is registered now.")


async def test_saved_instance_of_an_unknown_type_keeps_the_instances_it_names(isolated_instance_recipes: Path) -> None:
    leaf = await get_target_service().create_target_async(request=CreateTargetRequest(name="leaf", type="TextTarget"))
    keyed = await get_target_service().create_target_async(request=CreateTargetRequest(name="keyed", type="TextTarget"))
    named_like_a_parameter = await get_target_service().create_target_async(
        request=CreateTargetRequest(name="weights", type="TextTarget")
    )
    document = InstanceRecipeStorage.get_document_name(kind=ComponentType.TARGET, name="pool")
    params = {"targets": ["leaf"], "weights": {"keyed": 1}}
    recipe = {"kind": "target", "name": "pool", "type": "RemovedPoolTarget", "params": params}
    (isolated_instance_recipes / f"{document}.json").write_text(json.dumps(recipe), encoding="utf-8")
    persistence = await _restart_async()
    [pool] = persistence.get_unrestorable(ComponentType.TARGET)
    assert pool.reason == "Type 'RemovedPoolTarget' is not registered."

    for name, version in (("leaf", leaf.version), ("keyed", keyed.version)):
        with pytest.raises(InstanceConflictError, match="saved instances reference it: Target 'pool'"):
            await get_target_service().delete_target_async(target_registry_name=name, expected_version=version)

    assert await get_target_service().delete_target_async(
        target_registry_name="weights", expected_version=named_like_a_parameter.version
    )
    await get_target_service().delete_target_async(target_registry_name="pool", expected_version=pool.version)
    assert await get_target_service().delete_target_async(target_registry_name="leaf", expected_version=leaf.version)
