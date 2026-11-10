# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.main import app
from pyrit.backend.middleware.runtime import RuntimeAdmissionMiddleware
from pyrit.backend.models.saved_entries import SavedEntryResponse, SavedEntryUpdateResponse
from pyrit.backend.routes import converters, saved_entries
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.backend.services.saved_entries import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.backend.services.saved_entry_service import (
    SavedEntryCaller,
    SavedEntryForbiddenError,
    SavedEntryInUseError,
    SavedEntryNotFoundError,
    SavedEntryRequestError,
    SavedEntryService,
    SavedEntryVersionRequiredError,
)
from pyrit.models.catalog import RegistryRecipeKind

_ENTRY = SavedEntryResponse(
    kind=RegistryRecipeKind.CONVERTERS,
    name="cat_replace",
    version="v1",
    saved_by="Ada",
    saved_by_me=True,
    can_change=True,
)


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> Iterator[TestClient]:
    with patch.object(app.state, "registry_recipes_source", "/recipes", create=True):
        yield TestClient(app, headers=compatibility_headers)


def test_get_describes_the_saved_file(client: TestClient) -> None:
    with patch.object(SavedEntryService, "get_saved_entry_async", AsyncMock(return_value=_ENTRY)) as get:
        response = client.get("/api/saved-entries/converters/cat_replace")

    assert response.status_code == 200
    assert response.json() == {
        "kind": "converters",
        "name": "cat_replace",
        "version": "v1",
        "problem": None,
        "recipe": None,
        "saved_by": "Ada",
        "saved_by_me": True,
        "can_change": True,
    }
    assert get.call_args.kwargs["recipes_source"] == "/recipes"
    assert get.call_args.kwargs["kind"] is RegistryRecipeKind.CONVERTERS


@pytest.mark.parametrize(
    "path", ["/api/saved-entries/converters/Cat-Replace", "/api/saved-entries/presets/cat_replace"]
)
def test_a_name_or_kind_that_cant_be_saved_is_refused(client: TestClient, path: str) -> None:
    with patch.object(SavedEntryService, "get_saved_entry_async", AsyncMock()) as get:
        assert client.get(path).status_code == 422
        assert client.delete(f"{path}?expected_version=v1").status_code == 422
    get.assert_not_awaited()


def test_delete_sends_the_version_and_the_runtime(client: TestClient) -> None:
    runtime = RuntimeLifecycle(app=app, source=ConfigurationFileService(config_file_value=None))
    runtime.state = "ready"
    with (
        patch.object(app.state, "runtime_lifecycle", runtime, create=True),
        patch.object(SavedEntryService, "delete_saved_entry_async", AsyncMock()) as delete,
    ):
        response = client.delete("/api/saved-entries/techniques/formal_send?expected_version=v1")

    assert response.status_code == 204
    kwargs = delete.call_args.kwargs
    assert (kwargs["kind"], kwargs["name"], kwargs["expected_version"]) == (
        RegistryRecipeKind.TECHNIQUES,
        "formal_send",
        "v1",
    )
    assert kwargs["runtime"] is runtime


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (SavedEntryNotFoundError("No saved converter is named 'cat_replace'."), 404),
        (SavedEntryForbiddenError("Only Ada or an administrator can delete saved converter 'cat_replace'."), 403),
        (SavedEntryVersionRequiredError("Send the version."), 422),
        (SavedEntryInUseError("Saved converter 'cat_replace' is used by saved technique 'x'."), 409),
        (SavedEntryStorageError("Saved converters can't be deleted right now."), 503),
    ],
)
def test_delete_explains_why_it_didnt_delete(client: TestClient, error: Exception, status_code: int) -> None:
    with patch.object(SavedEntryService, "delete_saved_entry_async", AsyncMock(side_effect=error)):
        response = client.delete("/api/saved-entries/converters/cat_replace?expected_version=v1")

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(SavedEntryNotFoundError("No saved converter is named 'x'."), 404), (SavedEntryStorageError("down"), 503)],
)
def test_get_explains_why_it_found_nothing(client: TestClient, error: Exception, status_code: int) -> None:
    with patch.object(SavedEntryService, "get_saved_entry_async", AsyncMock(side_effect=error)):
        response = client.get("/api/saved-entries/converters/cat_replace")

    assert response.status_code == status_code


@pytest.mark.parametrize(
    ("policy", "caller"),
    [
        (AuthenticationPolicy(mode="local"), SavedEntryCaller(oid=None, is_admin=False, sign_in=False)),
        (
            AuthenticationPolicy(mode="local", allow_unauthenticated_admin=True),
            SavedEntryCaller(oid=None, is_admin=True, sign_in=False),
        ),
    ],
)
def test_the_caller_follows_the_sign_in_setting(
    client: TestClient, policy: AuthenticationPolicy, caller: SavedEntryCaller
) -> None:
    with (
        patch.object(app.state, "authentication_policy", policy),
        patch.object(SavedEntryService, "get_saved_entry_async", AsyncMock(return_value=_ENTRY)) as get,
    ):
        client.get("/api/saved-entries/converters/cat_replace")

    assert get.call_args.kwargs["caller"] == caller


async def test_a_saved_entry_can_be_deleted_while_the_runtime_isnt_ready() -> None:
    failed_app = FastAPI()
    failed_app.state.authentication_policy = AuthenticationPolicy(mode="local")
    runtime = RuntimeLifecycle(app=failed_app, source=ConfigurationFileService(config_file_value=None))
    runtime.state = "failed"
    failed_app.state.runtime_lifecycle = runtime
    failed_app.add_middleware(RuntimeAdmissionMiddleware)
    failed_app.include_router(saved_entries.router, prefix="/api")
    failed_app.include_router(converters.router, prefix="/api")

    with patch.object(SavedEntryService, "delete_saved_entry_async", AsyncMock()) as delete:
        transport = httpx.ASGITransport(app=failed_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            deleted = await http.delete("/api/saved-entries/converters/ghost?expected_version=v1")
            listed = await http.get("/api/converters")

    assert deleted.status_code == 204
    delete.assert_awaited_once()
    assert listed.status_code == 503


async def test_deleting_a_saved_entry_waits_while_the_runtime_is_starting() -> None:
    starting_app = FastAPI()
    starting_app.state.authentication_policy = AuthenticationPolicy(mode="local")
    runtime = RuntimeLifecycle(app=starting_app, source=ConfigurationFileService(config_file_value=None))
    runtime.state = "initializing"
    starting_app.state.runtime_lifecycle = runtime
    starting_app.add_middleware(RuntimeAdmissionMiddleware)
    starting_app.include_router(saved_entries.router, prefix="/api")

    with patch.object(SavedEntryService, "delete_saved_entry_async", AsyncMock()) as delete:
        transport = httpx.ASGITransport(app=starting_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            deleted = await http.delete("/api/saved-entries/converters/ghost?expected_version=v1")

    assert deleted.status_code == 503
    delete.assert_not_awaited()


def test_put_sends_the_changed_recipe_and_returns_the_saved_file(client: TestClient) -> None:
    updated = SavedEntryUpdateResponse(**_ENTRY.model_dump(), running=True, still_using_old=["temporary converter 'x'"])
    with patch.object(SavedEntryService, "update_saved_entry_async", AsyncMock(return_value=updated)) as update:
        response = client.put(
            "/api/saved-entries/converters/cat_replace",
            json={"expected_version": "v1", "recipe": {"params": {"pattern": "cat", "replace": "bird"}}},
        )

    assert response.status_code == 200
    assert (response.json()["running"], response.json()["still_using_old"]) == (True, ["temporary converter 'x'"])
    body = update.call_args.kwargs["request"]
    assert (body.expected_version, body.recipe) == ("v1", {"params": {"pattern": "cat", "replace": "bird"}})


@pytest.mark.parametrize(
    "body", [{"recipe": {}}, {"expected_version": "v1"}, {"expected_version": "v1", "recipe": {}, "x": 1}]
)
def test_put_needs_a_version_and_a_recipe(client: TestClient, body: dict[str, object]) -> None:
    with patch.object(SavedEntryService, "update_saved_entry_async", AsyncMock()) as update:
        response = client.put("/api/saved-entries/converters/cat_replace", json=body)

    assert response.status_code == 422
    update.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (SavedEntryNotFoundError("No saved converter is named 'cat_replace'."), 404),
        (SavedEntryForbiddenError("Only Ada or an administrator can change saved converter 'cat_replace'."), 403),
        (SavedEntryInUseError("Saved converter 'cat_replace' changed since it was read."), 409),
        (SavedEntryRequestError("A saved converter's class can't change."), 422),
        (SavedEntryConflictError("Converter 'x' was deleted while this was being saved."), 409),
        (SavedEntryStorageError("Saved converters can't be written right now."), 503),
        (PermissionError("Only an administrator can set trust_remote_code."), 403),
        (ValueError("tone must be one of ..."), 400),
    ],
)
def test_put_explains_why_it_didnt_change_it(client: TestClient, error: Exception, status_code: int) -> None:
    with patch.object(SavedEntryService, "update_saved_entry_async", AsyncMock(side_effect=error)):
        response = client.put(
            "/api/saved-entries/converters/cat_replace", json={"expected_version": "v1", "recipe": {}}
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)


def test_put_says_when_the_new_settings_cant_be_saved(client: TestClient) -> None:
    error = NotSaveableError("Uses temporary target 'launch_chat', which won't exist after a restart.")
    with patch.object(SavedEntryService, "update_saved_entry_async", AsyncMock(side_effect=error)):
        response = client.put(
            "/api/saved-entries/converters/cat_replace", json={"expected_version": "v1", "recipe": {}}
        )

    assert response.status_code == 400
    assert response.json()["type"] == "/errors/not-saveable"
    assert response.json()["detail"] == str(error)
