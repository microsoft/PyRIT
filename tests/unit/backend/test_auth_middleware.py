# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Entra ID auth middleware."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from pyrit.backend.middleware.auth import AuthenticatedUser, AuthenticationError, EntraAuthMiddleware, require_admin


def _make_middleware(*, allowed_group_ids: str = "allowed-group", admin_group_id: str = "") -> EntraAuthMiddleware:
    environment = {
        "ENTRA_TENANT_ID": "test-tenant",
        "ENTRA_CLIENT_ID": "test-client",
        "ENTRA_ALLOWED_GROUP_IDS": allowed_group_ids,
        "ENTRA_ADMIN_GROUP_ID": admin_group_id,
    }
    with patch.dict("os.environ", environment, clear=False):
        return EntraAuthMiddleware(MagicMock())


def _response(*, status_code: int, data: dict[str, object]) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = data
    return response


def _client_context(client: AsyncMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.parametrize(
    "environment",
    [
        {"ENTRA_TENANT_ID": "test-tenant", "ENTRA_CLIENT_ID": "", "ENTRA_ALLOWED_GROUP_IDS": ""},
        {"ENTRA_TENANT_ID": "", "ENTRA_CLIENT_ID": "test-client", "ENTRA_ALLOWED_GROUP_IDS": ""},
        {"ENTRA_TENANT_ID": "", "ENTRA_CLIENT_ID": "", "ENTRA_ALLOWED_GROUP_IDS": "allowed-group"},
        {"ENTRA_TENANT_ID": "test-tenant", "ENTRA_CLIENT_ID": "test-client", "ENTRA_ALLOWED_GROUP_IDS": ""},
        {"ENTRA_TENANT_ID": " ", "ENTRA_CLIENT_ID": " ", "ENTRA_ALLOWED_GROUP_IDS": " , "},
    ],
)
def test_init_rejects_incomplete_auth_configuration(environment: dict[str, str]) -> None:
    with patch.dict("os.environ", environment, clear=False):
        with pytest.raises(ValueError, match="Incomplete Entra ID configuration"):
            EntraAuthMiddleware(MagicMock())


def test_init_allows_auth_disabled_when_configuration_is_absent() -> None:
    environment = {"ENTRA_TENANT_ID": "", "ENTRA_CLIENT_ID": "", "ENTRA_ALLOWED_GROUP_IDS": ""}

    with patch.dict("os.environ", environment, clear=False):
        middleware = EntraAuthMiddleware(MagicMock())

    assert middleware._enabled is False


def test_init_warns_when_admin_group_is_missing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"), patch.dict("os.environ", {"ENTRA_ADMIN_GROUP_ID": ""}, clear=False):
        _make_middleware()

    assert "ENTRA_ADMIN_GROUP_ID is not set" in caplog.text


def test_require_admin_rejects_anonymous_request_by_default() -> None:
    request = MagicMock(spec=Request)
    request.state.user = None

    with patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": ""}, clear=False):
        with pytest.raises(HTTPException) as error:
            require_admin(request)

    assert error.value.status_code == 403


def test_require_admin_allows_explicit_local_development_override() -> None:
    request = MagicMock(spec=Request)
    request.state.user = None

    with patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}, clear=False):
        require_admin(request)


async def test_authenticate_with_graph_resolves_groups_when_restricted() -> None:
    middleware = _make_middleware(allowed_group_ids="group-1")
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(
        status_code=200,
        data={
            "id": "user-1",
            "displayName": "Test User",
            "mail": None,
            "userPrincipalName": "test@example.com",
        },
    )
    client.post.return_value = _response(status_code=200, data={"value": ["group-1"]})

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        result = await middleware._authenticate_with_graph_async(token="graph-token")

    assert isinstance(result, AuthenticatedUser)
    assert result.email == "test@example.com"
    assert result.groups == ["group-1"]
    assert client.post.call_args.args[0] == EntraAuthMiddleware._GRAPH_CHECK_MEMBER_GROUPS_URL
    assert client.post.call_args.kwargs["json"] == {"groupIds": ["group-1"]}


async def test_authenticate_with_graph_marks_admin_membership() -> None:
    middleware = _make_middleware(allowed_group_ids="allowed-group", admin_group_id="admin-group")
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(
        status_code=200,
        data={"id": "admin-1", "displayName": "Admin User", "mail": "admin@example.com"},
    )
    client.post.return_value = _response(status_code=200, data={"value": ["admin-group"]})

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        result = await middleware._authenticate_with_graph_async(token="graph-token")

    assert result.is_admin is True
    assert middleware._is_authorized(result) is True
    assert client.post.call_args.kwargs["json"] == {"groupIds": ["admin-group", "allowed-group"]}


@pytest.mark.parametrize(
    "graph_status, expected_status",
    [(401, 401), (403, 403), (429, 503), (500, 503)],
)
async def test_authenticate_with_graph_maps_profile_errors(graph_status: int, expected_status: int) -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(status_code=graph_status, data={})

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token")

    assert error.value.status_code == expected_status


async def test_authenticate_with_graph_returns_service_unavailable_on_network_error() -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("Graph unavailable")

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token")

    assert error.value.status_code == 503


async def test_authenticate_with_graph_rejects_non_object_profile() -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(status_code=200, data={})
    client.get.return_value.json.return_value = []

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token")

    assert error.value.status_code == 503


async def test_check_group_memberships_batches_allowed_group_ids() -> None:
    allowed_group_ids = [f"group-{index:02}" for index in range(21)]
    middleware = _make_middleware(allowed_group_ids=",".join(reversed(allowed_group_ids)))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        _response(status_code=200, data={"value": ["group-00"]}),
        _response(status_code=200, data={"value": ["group-20"]}),
    ]

    result = await middleware._check_group_memberships_async(client=client, token="graph-token")

    assert result == ["group-00", "group-20"]
    assert client.post.await_count == 2
    assert client.post.await_args_list[0].kwargs["json"] == {"groupIds": allowed_group_ids[:20]}
    assert client.post.await_args_list[1].kwargs["json"] == {"groupIds": allowed_group_ids[20:]}


@pytest.mark.parametrize("graph_status, expected_status", [(401, 401), (403, 403), (429, 503)])
async def test_check_group_memberships_maps_graph_errors(graph_status: int, expected_status: int) -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _response(status_code=graph_status, data={})

    with pytest.raises(AuthenticationError) as error:
        await middleware._check_group_memberships_async(client=client, token="graph-token")

    assert error.value.status_code == expected_status


async def test_authenticate_with_graph_rejects_non_object_membership_data() -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(
        status_code=200,
        data={"id": "user-1", "displayName": "Test User", "mail": "test@example.com"},
    )
    client.post.return_value = _response(status_code=200, data={})
    client.post.return_value.json.return_value = []

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token")

    assert error.value.status_code == 503


async def test_authenticate_request_denies_and_does_not_cache_unauthorized_user() -> None:
    middleware = _make_middleware(allowed_group_ids="allowed-group")
    request = MagicMock()
    request.headers = {"Authorization": "Bearer graph-token"}
    user = AuthenticatedUser(oid="user-1", name="Test User", email="test@example.com", groups=["other-group"])

    with patch.object(
        middleware,
        "_authenticate_with_graph_async",
        new_callable=AsyncMock,
        return_value=user,
    ) as authenticate:
        with pytest.raises(AuthenticationError) as first_error:
            await middleware._authenticate_request_async(request)
        with pytest.raises(AuthenticationError):
            await middleware._authenticate_request_async(request)

    assert first_error.value.status_code == 403
    assert first_error.value.detail == "You are not authorized to access this application"
    assert authenticate.await_count == 2


async def test_authenticate_request_caches_successful_authorization() -> None:
    middleware = _make_middleware(admin_group_id="admin-group")
    request = MagicMock()
    request.headers = {"Authorization": "bearer graph-token"}
    user = AuthenticatedUser(
        oid="user-1",
        name="Test User",
        email="test@example.com",
        groups=["allowed-group", "admin-group"],
        is_admin=True,
    )

    with patch.object(
        middleware,
        "_authenticate_with_graph_async",
        new_callable=AsyncMock,
        return_value=user,
    ) as authenticate:
        first_result = await middleware._authenticate_request_async(request)
        second_result = await middleware._authenticate_request_async(request)

    assert first_result == user
    assert second_result == user
    assert second_result.is_admin is True
    authenticate.assert_awaited_once_with(token="graph-token")


def test_auth_cache_expires_and_evicts_oldest_entry() -> None:
    middleware = _make_middleware()
    middleware._AUTH_CACHE_MAX_ENTRIES = 2
    user = AuthenticatedUser(oid="user-1", name="Test User", email="test@example.com", groups=["allowed-group"])

    with patch("pyrit.backend.middleware.auth.monotonic", return_value=100.0):
        middleware._cache_user(cache_key="first", user=user)
        middleware._cache_user(cache_key="second", user=user)
        middleware._cache_user(cache_key="third", user=user)
    assert list(middleware._auth_cache) == ["second", "third"]

    with patch("pyrit.backend.middleware.auth.monotonic", return_value=161.0):
        result = middleware._get_cached_user(cache_key="second")

    assert result is None
    assert list(middleware._auth_cache) == ["third"]


@pytest.mark.parametrize(
    "authorization",
    ["", "Bearer", "Bearer ", "Basic token", "Bearer token extra"],
)
async def test_authenticate_request_rejects_malformed_authorization_header(authorization: str) -> None:
    middleware = _make_middleware()
    request = MagicMock()
    request.headers = {"Authorization": authorization}

    with pytest.raises(AuthenticationError) as error:
        await middleware._authenticate_request_async(request)

    assert error.value.status_code == 401


async def test_dispatch_maps_authentication_error_to_json_response() -> None:
    middleware = _make_middleware()
    request = MagicMock()
    request.url.path = "/api/targets"
    error = AuthenticationError(status_code=401, detail="Invalid or expired token")

    with patch.object(middleware, "_authenticate_request_async", new_callable=AsyncMock, side_effect=error):
        response = await middleware.dispatch(request, AsyncMock())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert json.loads(response.body) == {"detail": "Invalid or expired token"}
