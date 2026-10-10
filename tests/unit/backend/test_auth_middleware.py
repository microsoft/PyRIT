# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Entra ID auth middleware."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.middleware.auth import AuthenticatedUser, AuthenticationError, EntraAuthMiddleware, require_admin


def _make_middleware() -> EntraAuthMiddleware:
    return EntraAuthMiddleware(MagicMock())


def _entra_policy(
    *, allowed_group_ids: tuple[str, ...] = ("allowed-group",), admin_group_id: str = ""
) -> AuthenticationPolicy:
    return AuthenticationPolicy(
        mode="entra",
        tenant_id="test-tenant",
        client_id="test-client",
        allowed_group_ids=allowed_group_ids,
        admin_group_id=admin_group_id,
    )


def _request_with_policy(policy: AuthenticationPolicy) -> Request:
    app = FastAPI()
    app.state.authentication_policy = policy
    return Request(
        {
            "type": "http",
            "app": app,
            "path": "/api/targets",
            "root_path": "",
            "method": "GET",
            "headers": [],
        }
    )


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


def test_require_admin_rejects_anonymous_request_by_default() -> None:
    request = _request_with_policy(AuthenticationPolicy(mode="local"))
    request.state.user = None

    with pytest.raises(HTTPException) as error:
        require_admin(request)

    assert error.value.status_code == 403


def test_require_admin_allows_explicit_local_development_override() -> None:
    request = _request_with_policy(AuthenticationPolicy(mode="local", allow_unauthenticated_admin=True))
    request.state.user = None

    require_admin(request)


async def test_authenticate_with_graph_resolves_groups_when_restricted() -> None:
    middleware = _make_middleware()
    policy = _entra_policy(allowed_group_ids=("group-1",))
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
        result = await middleware._authenticate_with_graph_async(token="graph-token", policy=policy)

    assert isinstance(result, AuthenticatedUser)
    assert result.email == "test@example.com"
    assert result.groups == ["group-1"]
    assert client.post.call_args.args[0] == EntraAuthMiddleware._GRAPH_CHECK_MEMBER_GROUPS_URL
    assert client.post.call_args.kwargs["json"] == {"groupIds": ["group-1"]}


async def test_authenticate_with_graph_marks_admin_membership() -> None:
    middleware = _make_middleware()
    policy = _entra_policy(admin_group_id="admin-group")
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(
        status_code=200,
        data={"id": "admin-1", "displayName": "Admin User", "mail": "admin@example.com"},
    )
    client.post.return_value = _response(status_code=200, data={"value": ["admin-group"]})

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        result = await middleware._authenticate_with_graph_async(token="graph-token", policy=policy)

    assert result.is_admin is True
    assert middleware._is_authorized(result, policy=policy) is True
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
            await middleware._authenticate_with_graph_async(token="graph-token", policy=_entra_policy())

    assert error.value.status_code == expected_status


async def test_authenticate_with_graph_returns_service_unavailable_on_network_error() -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("Graph unavailable")

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token", policy=_entra_policy())

    assert error.value.status_code == 503


async def test_authenticate_with_graph_rejects_non_object_profile() -> None:
    middleware = _make_middleware()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(status_code=200, data={})
    client.get.return_value.json.return_value = []

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=_client_context(client)):
        with pytest.raises(AuthenticationError) as error:
            await middleware._authenticate_with_graph_async(token="graph-token", policy=_entra_policy())

    assert error.value.status_code == 503


async def test_check_group_memberships_batches_allowed_group_ids() -> None:
    allowed_group_ids = [f"group-{index:02}" for index in range(21)]
    middleware = _make_middleware()
    policy = _entra_policy(allowed_group_ids=tuple(reversed(allowed_group_ids)))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        _response(status_code=200, data={"value": ["group-00"]}),
        _response(status_code=200, data={"value": ["group-20"]}),
    ]

    result = await middleware._check_group_memberships_async(client=client, token="graph-token", policy=policy)

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
        await middleware._check_group_memberships_async(
            client=client,
            token="graph-token",
            policy=_entra_policy(),
        )

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
            await middleware._authenticate_with_graph_async(token="graph-token", policy=_entra_policy())

    assert error.value.status_code == 503


async def test_authenticate_request_denies_and_does_not_cache_unauthorized_user() -> None:
    middleware = _make_middleware()
    policy = _entra_policy()
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
            await middleware._authenticate_request_async(request, policy=policy)
        with pytest.raises(AuthenticationError):
            await middleware._authenticate_request_async(request, policy=policy)

    assert first_error.value.status_code == 403
    assert first_error.value.detail == "You are not authorized to access this application"
    assert authenticate.await_count == 2


async def test_authenticate_request_caches_successful_authorization() -> None:
    middleware = _make_middleware()
    policy = _entra_policy(admin_group_id="admin-group")
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
        first_result = await middleware._authenticate_request_async(request, policy=policy)
        second_result = await middleware._authenticate_request_async(request, policy=policy)

    assert first_result == user
    assert second_result == user
    assert second_result.is_admin is True
    authenticate.assert_awaited_once_with(token="graph-token", policy=policy)


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
        await middleware._authenticate_request_async(request, policy=_entra_policy())

    assert error.value.status_code == 401


async def test_dispatch_maps_authentication_error_to_json_response() -> None:
    middleware = _make_middleware()
    request = _request_with_policy(_entra_policy())
    error = AuthenticationError(status_code=401, detail="Invalid or expired token")

    with patch.object(middleware, "_authenticate_request_async", new_callable=AsyncMock, side_effect=error):
        response = await middleware.dispatch(request, AsyncMock())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert json.loads(response.body) == {"detail": "Invalid or expired token"}
