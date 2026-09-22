# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strict lockstep behavior through the production middleware ordering."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import pyrit
from pyrit import _compatibility
from pyrit.backend.main import app, lifespan
from pyrit.backend.middleware.auth import AuthenticatedUser, EntraAuthMiddleware
from pyrit.backend.middleware.compatibility import CompatibilityMiddleware
from pyrit.backend.routes.version import VersionResponse
from pyrit.setup.configuration_loader import ConfigurationLoader


@pytest.fixture
def guarded_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Exercise the real middleware stack with auth enabled and fresh state."""
    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client")
    monkeypatch.setenv("ENTRA_ALLOWED_GROUP_IDS", "allowed-group")
    monkeypatch.setenv("ENTRA_ADMIN_GROUP_ID", "admin-group")
    test_app = FastAPI(routes=list(app.router.routes), middleware=app.user_middleware)
    test_app.state.effects = []

    def stateful_dependency() -> None:
        test_app.state.effects.append("dependency")

    @test_app.api_route(
        "/api/compatibility-probe",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        dependencies=[Depends(stateful_dependency)],
    )
    async def business_handler() -> dict[str, bool]:
        test_app.state.effects.append("handler")
        return {"ok": True}

    return test_app


@pytest.fixture
def guarded_client(guarded_app: FastAPI) -> TestClient:
    """Do not supply a marker implicitly in protocol tests."""
    return TestClient(guarded_app)


@pytest.fixture
def graph_user() -> AuthenticatedUser:
    """Provide an authorized Graph identity without network access."""
    return AuthenticatedUser(oid="user-1", name="Test User", email="test@example.com", groups=["allowed-group"])


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("marker", [None, "malformed", "0.14.0+g" + "b" * 40])
def test_rejection_precedes_auth_dependencies_and_handlers(
    guarded_client: TestClient, guarded_app: FastAPI, compatibility_id: str, method: str, marker: str | None
) -> None:
    headers = {_compatibility.COMPATIBILITY_HEADER: marker} if marker is not None else {}
    with patch.object(EntraAuthMiddleware, "_authenticate_request_async", new_callable=AsyncMock) as authenticate:
        response = guarded_client.request(method, "/api/compatibility-probe", headers=headers, content=b"not JSON")

    expected_status = 409 if marker and _compatibility.is_valid_compatibility_id(marker) else 400
    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert set(problem) == {"type", "title", "status", "detail", "expected", "actual"}
    assert problem["type"] == (
        _compatibility.MISMATCH_COMPATIBILITY_TYPE
        if expected_status == 409
        else _compatibility.INVALID_COMPATIBILITY_TYPE
    )
    assert problem["status"] == expected_status
    assert problem["expected"] == compatibility_id
    assert problem["actual"] == marker
    assert problem["title"] and problem["detail"]
    assert guarded_app.state.effects == []
    authenticate.assert_not_awaited()


@pytest.mark.parametrize(
    "marker",
    ["", "0.14.0", "0.14.0+gabc123", "0.14.0+g" + "A" * 40, " 0.14.0+g" + "a" * 40, "0.14.0+g" + "a" * 41],
)
def test_malformed_markers(guarded_client: TestClient, marker: str) -> None:
    response = guarded_client.get("/api/compatibility-probe", headers={_compatibility.COMPATIBILITY_HEADER: marker})
    assert response.status_code == 400
    assert response.json()["actual"] == marker


@pytest.mark.parametrize("combined", [False, True])
def test_duplicate_markers_are_invalid_even_when_identical(
    guarded_client: TestClient, compatibility_id: str, combined: bool
) -> None:
    headers = (
        [(_compatibility.COMPATIBILITY_HEADER, f"{compatibility_id}, {compatibility_id}")]
        if combined
        else [
            (_compatibility.COMPATIBILITY_HEADER, compatibility_id),
            (_compatibility.COMPATIBILITY_HEADER.lower(), compatibility_id),
        ]
    )
    response = guarded_client.get("/api/compatibility-probe", headers=headers)
    assert response.status_code == 400
    assert response.json()["actual"] == f"{compatibility_id}, {compatibility_id}"


def test_matching_identity_runs_business_request(
    guarded_client: TestClient, guarded_app: FastAPI, compatibility_id: str, graph_user: AuthenticatedUser
) -> None:
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user):
        response = guarded_client.post(
            "/api/compatibility-probe",
            headers={_compatibility.COMPATIBILITY_HEADER.lower(): compatibility_id, "Authorization": "Bearer token"},
        )
    assert response.status_code == 200
    assert guarded_app.state.effects == ["dependency", "handler"]


def test_same_version_different_commit_is_a_mismatch(guarded_client: TestClient, compatibility_id: str) -> None:
    client_id = compatibility_id.split("+g")[0] + "+g" + "b" * 40
    response = guarded_client.get("/api/compatibility-probe", headers={_compatibility.COMPATIBILITY_HEADER: client_id})
    assert response.status_code == 409
    assert response.json()["expected"] == compatibility_id
    assert response.json()["actual"] == client_id


def test_matching_identity_does_not_bypass_group_authorization(
    guarded_client: TestClient,
    guarded_app: FastAPI,
    compatibility_headers: dict[str, str],
    graph_user: AuthenticatedUser,
) -> None:
    graph_user.groups = []
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user):
        response = guarded_client.post(
            "/api/compatibility-probe", headers={**compatibility_headers, "Authorization": "Bearer token"}
        )
    assert response.status_code == 403
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]
    assert guarded_app.state.effects == []


@pytest.mark.parametrize("path", ["/api/health", "/api/auth/config", "/api/media"])
@pytest.mark.parametrize("marker", [None, "invalid", "0.14.0+g" + "b" * 40])
def test_public_neutral_routes_bypass_guard_and_auth(guarded_client: TestClient, path: str, marker: str | None) -> None:
    headers = {_compatibility.COMPATIBILITY_HEADER: marker} if marker is not None else {}
    with patch.object(EntraAuthMiddleware, "_authenticate_request_async", new_callable=AsyncMock) as authenticate:
        response = guarded_client.get(path, headers=headers)
    assert response.status_code == (422 if path == "/api/media" else 200)
    authenticate.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "/api",
        "/api/health/",
        "/api/health/extra",
        "/api/auth/config/",
        "/api/version/",
        "/api/media/",
        "/api/auth/access",
    ],
)
def test_neutral_exemptions_are_exact(guarded_client: TestClient, path: str) -> None:
    response = guarded_client.get(path, follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["type"] == _compatibility.INVALID_COMPATIBILITY_TYPE


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/apikeys", "/docs"])
def test_non_api_routes_are_not_guarded(guarded_client: TestClient, path: str) -> None:
    response = guarded_client.get(path)
    assert response.status_code not in (400, 409)


@pytest.mark.parametrize("path", ["/api/version", "/api/auth/access", "/api/compatibility-probe"])
def test_compatible_requests_still_require_authentication(
    guarded_client: TestClient, compatibility_headers: dict[str, str], path: str
) -> None:
    response = guarded_client.get(path, headers=compatibility_headers)
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("marker", [None, "invalid", "0.14.0+g" + "b" * 40])
def test_version_is_neutral_but_authenticated(
    guarded_client: TestClient,
    guarded_app: FastAPI,
    compatibility_id: str,
    graph_user: AuthenticatedUser,
    marker: str | None,
) -> None:
    headers = {_compatibility.COMPATIBILITY_HEADER: marker} if marker is not None else {}
    assert guarded_client.get("/api/version", headers=headers).status_code == 401
    headers["Authorization"] = "Bearer token"
    guarded_app.state.default_labels = {"operator": "alice"}
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user):
        response = guarded_client.get("/api/version", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == pyrit.__version__
    assert payload["compatibility_id"] == compatibility_id
    assert payload["default_labels"] == {"operator": "alice"}
    assert set(payload) == {
        "version",
        "compatibility_id",
        "source",
        "commit",
        "modified",
        "display",
        "database_info",
        "default_labels",
    }
    assert "compatibility_id" in VersionResponse.model_json_schema()["required"]


def test_auth_access_is_guarded_then_authorized(
    guarded_client: TestClient, compatibility_headers: dict[str, str], graph_user: AuthenticatedUser
) -> None:
    assert guarded_client.get("/api/auth/access").status_code == 400
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user):
        response = guarded_client.get(
            "/api/auth/access", headers={**compatibility_headers, "Authorization": "Bearer token"}
        )
    assert response.status_code == 200
    assert response.json() == {"isAdmin": False}


@pytest.mark.parametrize("marker", ["invalid", "0.14.0+g" + "b" * 40])
def test_problem_responses_preserve_security_request_ids_and_cors(guarded_client: TestClient, marker: str) -> None:
    response = guarded_client.get(
        "/api/compatibility-probe",
        headers={
            _compatibility.COMPATIBILITY_HEADER: marker,
            "X-Request-ID": "client-request",
            "Origin": "http://localhost:3000",
        },
    )
    assert response.status_code in (400, 409)
    assert response.headers["x-request-id"] == "client-request"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_preflight_accepts_marker_without_auth_or_side_effects(
    guarded_client: TestClient, guarded_app: FastAPI
) -> None:
    response = guarded_client.options(
        "/api/compatibility-probe",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": f"Authorization, Content-Type, {_compatibility.COMPATIBILITY_HEADER}",
        },
    )
    assert response.status_code == 200
    assert _compatibility.COMPATIBILITY_HEADER.lower() in response.headers["access-control-allow-headers"].lower()
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["x-frame-options"] == "DENY"
    assert UUID(response.headers["x-request-id"])
    assert guarded_app.state.effects == []


async def test_guard_does_not_consume_rejected_request_body() -> None:
    downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    middleware = CompatibilityMiddleware(downstream)
    await middleware({"type": "http", "method": "POST", "path": "/api/attacks", "headers": []}, receive, send)
    receive.assert_not_awaited()
    downstream.assert_not_awaited()
    assert send.call_args_list[0].args[0]["status"] == 400


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "lifespan"},
        {"type": "websocket", "path": "/socket"},
        {"type": "http", "method": "OPTIONS", "path": "/api/attacks"},
    ],
)
async def test_non_http_and_options_pass_through(scope: dict[str, object]) -> None:
    downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    middleware = CompatibilityMiddleware(downstream)
    await middleware(scope, receive, send)
    downstream.assert_awaited_once_with(scope, receive, send)


@pytest.mark.parametrize("stamp_error", [None, "missing stamp", "malformed stamp"])
def test_identity_stays_fixed_without_lifespan_after_stamp_changes(
    guarded_client: TestClient,
    guarded_app: FastAPI,
    compatibility_id: str,
    graph_user: AuthenticatedUser,
    stamp_error: str | None,
) -> None:
    changed_id = "0.14.0+g" + "b" * 40
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user):
        headers = {"Authorization": "Bearer token"}
        first_version = guarded_client.get("/api/version", headers=headers)
        assert first_version.json()["compatibility_id"] == compatibility_id
        assert guarded_app.state.compatibility_id == compatibility_id
        with patch.object(
            _compatibility,
            "get_compatibility_id",
            return_value=changed_id,
            side_effect=ValueError(stamp_error) if stamp_error else None,
        ) as read_stamp:
            version = guarded_client.get("/api/version", headers=headers)
            matching = guarded_client.post(
                "/api/compatibility-probe", headers={**headers, _compatibility.COMPATIBILITY_HEADER: compatibility_id}
            )
            mismatched = guarded_client.post(
                "/api/compatibility-probe", headers={**headers, _compatibility.COMPATIBILITY_HEADER: changed_id}
            )
        read_stamp.assert_not_called()
    assert version.status_code == matching.status_code == 200
    assert version.json()["compatibility_id"] == compatibility_id
    assert mismatched.status_code == 409
    assert mismatched.json()["expected"] == compatibility_id


async def test_lifespan_identity_is_shared_by_guard_and_version(
    guarded_app: FastAPI,
    compatibility_id: str,
    graph_user: AuthenticatedUser,
) -> None:
    guarded_app.middleware_stack = guarded_app.build_middleware_stack()
    startup_id = "0.14.0+g" + "b" * 40
    with (
        patch.object(_compatibility, "get_compatibility_id", return_value=startup_id),
        patch.object(ConfigurationLoader, "load_with_overrides", return_value=ConfigurationLoader()),
        patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
        patch(
            "pyrit.backend.main.get_scenario_run_service",
            return_value=MagicMock(
                reconcile_interrupted_runs_async=AsyncMock(return_value=0),
                shutdown_async=AsyncMock(),
            ),
        ),
        patch("pyrit.backend.main.setup_frontend"),
        patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=graph_user),
    ):
        async with lifespan(guarded_app):
            assert guarded_app.state.compatibility_id == startup_id
            with patch.object(
                _compatibility, "get_compatibility_id", side_effect=ValueError("stamp changed")
            ) as read_stamp:
                client = TestClient(guarded_app, headers={"Authorization": "Bearer token"})
                version = client.get("/api/version")
                matching = client.post(
                    "/api/compatibility-probe", headers={_compatibility.COMPATIBILITY_HEADER: startup_id}
                )
                mismatched = client.post(
                    "/api/compatibility-probe", headers={_compatibility.COMPATIBILITY_HEADER: compatibility_id}
                )
            read_stamp.assert_not_called()
    assert version.status_code == matching.status_code == 200
    assert version.json()["compatibility_id"] == startup_id
    assert mismatched.status_code == 409
    assert mismatched.json()["expected"] == startup_id


@pytest.mark.parametrize("reason", ["missing stamp", "malformed stamp"])
async def test_startup_fails_before_initialization_without_provenance(reason: str) -> None:
    with (
        patch.object(_compatibility, "get_compatibility_id", side_effect=ValueError(reason)),
        patch("pyrit.backend.main.ConfigurationFileService") as configuration,
        patch("pyrit.backend.main.setup_frontend") as frontend,
    ):
        with pytest.raises(ValueError, match=reason):
            async with lifespan(FastAPI()):
                pytest.fail("Startup accepted invalid provenance")
        with pytest.raises(ValueError, match=reason):
            CompatibilityMiddleware(MagicMock())
    configuration.assert_not_called()
    frontend.assert_not_called()
