# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli.api_client.PyRITApiClient.
"""

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import httpx
import pytest

from pyrit._compatibility import COMPATIBILITY_HEADER, INVALID_COMPATIBILITY_TYPE, MISMATCH_COMPATIBILITY_TYPE
from pyrit.cli._auth import CliAuthenticationError
from pyrit.cli.api_client import CompatibilityError, PyRITApiClient, ServerNotAvailableError
from pyrit.models import ScenarioRunState, TargetCapabilities
from pyrit.models.catalog import (
    RegisteredInitializer,
    RegisteredScenario,
    RunScenarioRequest,
    ScenarioRunListItem,
    ScenarioRunSummary,
    TargetInstance,
)
from unit.mocks import make_scenario_result

COMPATIBILITY_ID = "0.14.0+g" + "a" * 40


@pytest.fixture(autouse=True)
def packaged_stamp():
    """Use an installed build identity independent of the test checkout."""
    with patch("pyrit._compatibility.get_compatibility_id", return_value=COMPATIBILITY_ID):
        yield


OTHER_ID = "0.14.0+g" + "b" * 40


async def test_request_waiting_for_auth_stops_after_concurrent_mismatch(monkeypatch):
    client = PyRITApiClient(base_url="http://localhost:8000")
    client._compatibility_id = COMPATIBILITY_ID
    error = CompatibilityError("Backend replaced", expected=OTHER_ID, actual=COMPATIBILITY_ID)

    async def invalidate_while_authenticating(request):
        client._compatibility_error = error

    monkeypatch.setattr(client, "_add_authorization_header_async", invalidate_while_authenticating)
    request = httpx.Request("POST", "http://localhost:8000/api/scenarios/runs")
    with pytest.raises(CompatibilityError) as raised:
        await client._prepare_request_async(request)
    assert raised.value is error


@pytest.fixture()
def transport_client(monkeypatch):
    """Install real HTTPX clients with an in-memory transport and real hooks."""
    client_type = httpx.AsyncClient
    opened = []

    def install(handler):
        def factory(**kwargs):
            client = client_type(transport=httpx.MockTransport(handler), **kwargs)
            opened.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return opened

    return install


async def test_handshake_authenticates_before_version_and_stamps_business_requests(transport_client):
    requests = []
    provider = MagicMock(get_token_async=AsyncMock(return_value="token"), close_async=AsyncMock())

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/auth/config":
            assert "Authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "tenantId": "tenant",
                    "clientId": "client",
                    "scopes": ["https://graph.microsoft.com/User.Read"],
                },
            )
        assert request.headers["Authorization"] == "Bearer token"
        assert request.headers[COMPATIBILITY_HEADER] == COMPATIBILITY_ID
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"compatibility_id": COMPATIBILITY_ID})
        return httpx.Response(200, json=_run_summary_payload())

    opened = transport_client(handler)
    with patch("pyrit.cli._auth.create_token_provider_async", AsyncMock(return_value=provider)):
        async with PyRITApiClient(base_url="https://backend.example", auth_mode="auto") as client:
            await client.start_scenario_run_async(
                request=RunScenarioRequest(scenario_name="test", target_name="target")
            )
            await client.get_scenario_run_async(scenario_result_id="abc")
            await client.cancel_scenario_run_async(scenario_result_id="abc")
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/auth/config"),
        ("GET", "/api/version"),
        ("POST", "/api/scenarios/runs"),
        ("GET", "/api/scenarios/runs/abc"),
        ("POST", "/api/scenarios/runs/abc/cancel"),
    ]
    assert opened[0].is_closed
    provider.close_async.assert_awaited_once()


def test_backend_readme_example_authenticates_before_handshake(transport_client):
    readme = Path(__file__).resolve().parents[3] / "pyrit/backend/README.md"
    example = readme.read_text(encoding="utf-8").split("```python\n", 1)[1].split("```", 1)[0]
    requests = []
    provider = MagicMock(get_token_async=AsyncMock(return_value="token"), close_async=AsyncMock())

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/auth/config":
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "tenantId": "tenant",
                    "clientId": "client",
                    "scopes": ["https://graph.microsoft.com/User.Read"],
                },
            )
        assert request.headers.get("Authorization") == "Bearer token"
        assert request.headers[COMPATIBILITY_HEADER] == COMPATIBILITY_ID
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"compatibility_id": COMPATIBILITY_ID})
        assert request.url.path == "/api/scenarios/catalog"
        return httpx.Response(200, json={"items": []})

    opened = transport_client(handler)
    with patch("pyrit.cli._auth.create_token_provider_async", AsyncMock(return_value=provider)):
        exec(compile(example, str(readme), "exec"), {})
    assert requests == ["/api/auth/config", "/api/version", "/api/scenarios/catalog"]
    assert opened[0].is_closed
    provider.close_async.assert_awaited_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"compatibility_id": OTHER_ID},
        {},
        [],
        {"compatibility_id": None},
        {"compatibility_id": 42},
        {"compatibility_id": "0.14.0+gaaaaaaa"},
        {"compatibility_id": COMPATIBILITY_ID.upper()},
    ],
)
async def test_failed_handshake_closes_and_blocks_business_requests(transport_client, payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    opened = transport_client(handler)
    client = PyRITApiClient(base_url="http://localhost:8000")
    with pytest.raises(CompatibilityError) as failure:
        await client.__aenter__()
    assert failure.value.expected == COMPATIBILITY_ID
    assert opened[0].is_closed
    assert client._client is None
    with pytest.raises(CompatibilityError):
        await client.cancel_scenario_run_async(scenario_result_id="abc")
    assert [request.url.path for request in requests] == ["/api/version"]


async def test_invalid_packaged_stamp_fails_before_network():
    with (
        patch("pyrit._compatibility.get_compatibility_id", side_effect=ValueError("missing stamp")),
        patch("httpx.AsyncClient") as factory,
        pytest.raises(CompatibilityError, match="Invalid installed"),
    ):
        await PyRITApiClient(base_url="http://localhost:8000").__aenter__()
    factory.assert_not_called()


async def test_cancelling_entry_awaits_resource_cleanup(transport_client):
    waiting = asyncio.Event()
    provider = MagicMock(get_token_async=AsyncMock(return_value="token"), close_async=AsyncMock())

    async def handler(request):
        waiting.set()
        await asyncio.Event().wait()

    opened = transport_client(handler)
    client = PyRITApiClient(base_url="http://localhost:8000", auth_mode="auto")

    async def configure():
        client._token_provider = provider

    with patch.object(client, "_configure_authentication_async", side_effect=configure):
        task = asyncio.create_task(client.__aenter__())
        await asyncio.wait_for(waiting.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert opened[0].is_closed
    assert client._client is None
    assert client._token_provider is None
    provider.close_async.assert_awaited_once()


@pytest.mark.parametrize("failure", ["json", "missing", "http", "cancel", "unexpected"])
async def test_failed_entry_closes_http_and_credentials(transport_client, failure):
    provider = MagicMock(get_token_async=AsyncMock(return_value="token"), close_async=AsyncMock())

    def handler(request):
        if request.url.path == "/api/auth/config":
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "tenantId": "tenant",
                    "clientId": "client",
                    "scopes": ["https://graph.microsoft.com/User.Read"],
                },
            )
        if failure == "json":
            return httpx.Response(200, content=b"not JSON")
        if failure == "missing":
            return httpx.Response(200, json={})
        if failure == "http":
            raise httpx.ConnectError("offline")
        if failure == "cancel":
            raise asyncio.CancelledError
        raise RuntimeError("unexpected failure")

    opened = transport_client(handler)
    expected = {
        "json": CompatibilityError,
        "missing": CompatibilityError,
        "http": httpx.ConnectError,
        "cancel": asyncio.CancelledError,
        "unexpected": RuntimeError,
    }[failure]
    client = PyRITApiClient(base_url="https://backend.example", auth_mode="auto")
    with patch("pyrit.cli._auth.create_token_provider_async", AsyncMock(return_value=provider)):
        with pytest.raises(expected):
            await client.__aenter__()
    assert opened[0].is_closed
    assert client._client is None
    assert client._token_provider is None
    provider.close_async.assert_awaited_once()


@pytest.mark.parametrize(
    ("status", "problem_type"), [(400, INVALID_COMPATIBILITY_TYPE), (409, MISMATCH_COMPATIBILITY_TYPE)]
)
@pytest.mark.parametrize("operation", ["poll", "mutation"])
async def test_later_rejection_latches_without_replaying(transport_client, status, problem_type, operation):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"compatibility_id": COMPATIBILITY_ID})
        return httpx.Response(status, json={"type": problem_type, "expected": OTHER_ID, "actual": COMPATIBILITY_ID})

    transport_client(handler)
    async with PyRITApiClient(base_url="http://localhost:8000") as client:
        with pytest.raises(CompatibilityError) as failure:
            if operation == "poll":
                await client.get_scenario_run_async(scenario_result_id="abc")
            else:
                await client.start_scenario_run_async(
                    request=RunScenarioRequest(scenario_name="test", target_name="target")
                )
        assert failure.value.expected == OTHER_ID
        assert failure.value.actual == COMPATIBILITY_ID
        with pytest.raises(CompatibilityError):
            await client.cancel_scenario_run_async(scenario_result_id="abc")
        with pytest.raises(CompatibilityError):
            await client._client.get("/api/targets")
    assert len(requests) == 2
    assert all(request.headers[COMPATIBILITY_HEADER] == COMPATIBILITY_ID for request in requests)


@pytest.mark.parametrize("payload", [{"detail": "busy"}, [], {"type": INVALID_COMPATIBILITY_TYPE}])
async def test_unrelated_conflict_does_not_latch(transport_client, payload):
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"compatibility_id": COMPATIBILITY_ID})
        return httpx.Response(409, json=payload)

    transport_client(handler)
    async with PyRITApiClient(base_url="http://localhost:8000") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.cancel_scenario_run_async(scenario_result_id="abc")
        assert client._compatibility_error is None


@pytest.fixture()
def mock_httpx_client():
    """A MagicMock standing in for an opened ``httpx.AsyncClient``."""
    client = MagicMock()
    client.get = AsyncMock(return_value=_make_response(json_data={"compatibility_id": COMPATIBILITY_ID}))
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture()
def client(mock_httpx_client):
    """A PyRITApiClient with the underlying HTTP client pre-wired."""
    c = PyRITApiClient(base_url="http://localhost:8000/")
    c._client = mock_httpx_client
    return c


def _make_response(*, status_code=200, json_data=None, text_data=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value={} if json_data is None else json_data)
    resp.text = text_data
    resp.raise_for_status = MagicMock()
    return resp


def _scenario_payload(*, scenario_name: str = "s1") -> dict:
    """Build a wire-format ``RegisteredScenario`` payload."""
    return {
        "scenario_name": scenario_name,
        "scenario_type": "RedTeamAgentScenario",
        "description": "test scenario",
        "default_technique": "single_turn",
        "aggregate_techniques": [],
        "all_techniques": ["single_turn"],
        "default_datasets": [],
        "supported_parameters": [],
    }


def _initializer_payload(*, initializer_name: str = "x") -> dict:
    return {
        "initializer_name": initializer_name,
        "initializer_type": "TargetInitializer",
        "description": "",
        "required_env_vars": [],
        "supported_parameters": [],
    }


def _target_payload(*, target_registry_name: str = "t1") -> dict:
    return {
        "target_registry_name": target_registry_name,
        "identifier": {
            "class_name": "OpenAIChatTarget",
            "class_module": "pyrit.prompt_target",
        },
        "capabilities": TargetCapabilities().model_dump(mode="json"),
        "target_specific_params": None,
        "inner_targets": None,
    }


def _run_summary_payload(*, scenario_result_id: str = "abc", status: str = "CREATED") -> dict:
    now = datetime(2025, 1, 1, tzinfo=UTC).isoformat()
    return {
        "scenario_result_id": scenario_result_id,
        "scenario_name": "x",
        "scenario_version": 0,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "error_type": None,
        "techniques_used": [],
        "total_attacks": 0,
        "completed_attacks": 0,
        "objective_achieved_rate": 0,
        "labels": {},
        "completed_at": None,
    }


# ---------------------------------------------------------------------------
# Init / context manager / lifecycle
# ---------------------------------------------------------------------------


def test_init_strips_trailing_slash():
    c = PyRITApiClient(base_url="http://localhost:8000/")
    assert c._base_url == "http://localhost:8000"


async def test_async_context_manager_opens_and_closes(mock_httpx_client):
    c = PyRITApiClient(base_url="http://localhost:8000")
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    event_loop_thread_id = threading.get_ident()

    def read_stamp() -> str:
        assert threading.get_ident() != event_loop_thread_id
        fake_async_client_cls.assert_not_called()
        return COMPATIBILITY_ID

    with (
        patch("httpx.AsyncClient", fake_async_client_cls),
        patch("pyrit._compatibility.get_compatibility_id", side_effect=read_stamp) as stamp_reader,
    ):
        async with c as opened:
            assert opened is c
            assert c._client is mock_httpx_client
        # After exit, close was called
        mock_httpx_client.aclose.assert_awaited_once()
        assert c._client is None
    stamp_reader.assert_called_once()
    # Default request_timeout (60s) propagates to the httpx client constructor.
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=60.0, event_hooks=ANY)


async def test_async_context_manager_passes_custom_request_timeout(mock_httpx_client):
    c = PyRITApiClient(base_url="http://localhost:8000", request_timeout=120.0)
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            pass
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=120.0, event_hooks=ANY)


async def test_async_context_manager_uses_default_when_request_timeout_is_none(
    mock_httpx_client,
):
    c = PyRITApiClient(base_url="http://localhost:8000", request_timeout=None)
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            pass
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=60.0, event_hooks=ANY)


async def test_close_async_is_noop_when_already_closed():
    c = PyRITApiClient(base_url="http://localhost:8000")
    await c.close_async()  # Should not raise.


async def test_context_manager_discovers_auth_and_attaches_bearer_token(mock_httpx_client):
    c = PyRITApiClient(base_url="https://copyrit.example.com", auth_mode="auto", interactive=False)
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    mock_httpx_client.get.return_value = _make_response(
        json_data={
            "enabled": True,
            "tenantId": "tenant-id",
            "clientId": "client-id",
            "scopes": ["https://graph.microsoft.com/User.Read"],
        }
    )
    provider = MagicMock()
    mock_httpx_client.get.side_effect = [
        mock_httpx_client.get.return_value,
        _make_response(json_data={"compatibility_id": COMPATIBILITY_ID}),
    ]
    provider.get_token_async = AsyncMock(return_value="access-token")
    provider.close_async = AsyncMock()

    with (
        patch("httpx.AsyncClient", fake_async_client_cls),
        patch(
            "pyrit.cli._auth.create_token_provider_async",
            new_callable=AsyncMock,
            return_value=provider,
        ) as create_provider,
    ):
        async with c:
            request_hook = fake_async_client_cls.call_args.kwargs["event_hooks"]["request"][0]
            request = httpx.Request("GET", "https://copyrit.example.com/api/targets")
            await request_hook(request)
            assert request.headers["Authorization"] == "Bearer access-token"

    assert mock_httpx_client.get.await_args_list == [call("/api/auth/config"), call("/api/version")]
    create_provider.assert_awaited_once()
    provider.close_async.assert_awaited_once()


async def test_context_manager_leaves_public_requests_unauthenticated(mock_httpx_client):
    c = PyRITApiClient(base_url="https://copyrit.example.com", auth_mode="auto")
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    mock_httpx_client.get.return_value = _make_response(
        json_data={
            "enabled": False,
            "tenantId": "",
            "clientId": "",
            "scopes": [],
        }
    )

    mock_httpx_client.get.side_effect = [
        mock_httpx_client.get.return_value,
        _make_response(json_data={"compatibility_id": COMPATIBILITY_ID}),
    ]
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            request_hook = fake_async_client_cls.call_args.kwargs["event_hooks"]["request"][0]
            request = httpx.Request("GET", "https://copyrit.example.com/api/health")
            await request_hook(request)
            assert "Authorization" not in request.headers


async def test_context_manager_accepts_matching_backend_without_auth_endpoint(mock_httpx_client):
    c = PyRITApiClient(base_url="http://legacy.example.com", auth_mode="auto")
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    mock_httpx_client.get.side_effect = [
        _make_response(status_code=404),
        _make_response(json_data={"compatibility_id": COMPATIBILITY_ID}),
    ]

    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            assert c._token_provider is None


async def test_context_manager_rejects_authentication_over_remote_http(mock_httpx_client):
    c = PyRITApiClient(base_url="http://copyrit.example.com", auth_mode="auto")
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    mock_httpx_client.get.return_value = _make_response(
        json_data={
            "enabled": True,
            "tenantId": "tenant-id",
            "clientId": "client-id",
            "scopes": ["https://graph.microsoft.com/User.Read"],
        }
    )

    with patch("httpx.AsyncClient", fake_async_client_cls):
        with pytest.raises(CliAuthenticationError, match="non-HTTPS"):
            await c.__aenter__()

    mock_httpx_client.aclose.assert_awaited_once()


async def test_context_manager_rejects_invalid_auth_config_json(mock_httpx_client):
    c = PyRITApiClient(base_url="https://copyrit.example.com", auth_mode="auto")
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    response = _make_response()
    response.json.side_effect = ValueError("invalid JSON")
    mock_httpx_client.get.return_value = response

    with patch("httpx.AsyncClient", fake_async_client_cls):
        with pytest.raises(CliAuthenticationError, match="invalid JSON"):
            await c.__aenter__()

    mock_httpx_client.aclose.assert_awaited_once()


def test_get_client_raises_when_not_opened():
    c = PyRITApiClient(base_url="http://localhost:8000")
    with pytest.raises(ServerNotAvailableError, match="not connected"):
        c._get_client()


# ---------------------------------------------------------------------------
# health_check_async
# ---------------------------------------------------------------------------


async def test_health_check_returns_true_on_200(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(
        status_code=200,
        json_data={"status": "healthy", "service": "pyrit-backend"},
    )
    assert await client.health_check_async() is True
    mock_httpx_client.get.assert_awaited_once_with("/api/health")


async def test_health_check_returns_false_for_unrelated_service(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(
        status_code=200,
        json_data={"status": "ok", "service": "another-service"},
    )
    assert await client.health_check_async() is False


async def test_health_check_returns_false_on_non_200(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(status_code=503)
    assert await client.health_check_async() is False


async def test_health_check_returns_false_on_connect_error(client, mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.ConnectError("nope")
    assert await client.health_check_async() is False


async def test_health_check_returns_false_on_generic_exception(client, mock_httpx_client):
    mock_httpx_client.get.side_effect = RuntimeError("broken")
    assert await client.health_check_async() is False


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_list_scenarios_async(client, mock_httpx_client):
    payload = {"items": [_scenario_payload(scenario_name="s1")], "pagination": {}}
    mock_httpx_client.get.return_value = _make_response(json_data=payload)
    result = await client.list_scenarios_async(limit=10)
    assert len(result) == 1
    assert isinstance(result[0], RegisteredScenario)
    assert result[0].scenario_name == "s1"
    mock_httpx_client.get.assert_awaited_once_with("/api/scenarios/catalog", params={"limit": 10})


async def test_get_scenario_async_returns_payload(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data=_scenario_payload(scenario_name="foo"))
    result = await client.get_scenario_async(scenario_name="foo")
    assert isinstance(result, RegisteredScenario)
    assert result.scenario_name == "foo"
    mock_httpx_client.get.assert_awaited_once_with("/api/scenarios/catalog/foo", params=None)


async def test_get_scenario_async_returns_none_on_404(client, mock_httpx_client):
    resp = _make_response(status_code=404)
    error = httpx.HTTPStatusError("404", request=MagicMock(), response=resp)
    mock_httpx_client.get.return_value = resp
    resp.raise_for_status.side_effect = error
    result = await client.get_scenario_async(scenario_name="missing")
    assert result is None


async def test_get_scenario_async_raises_on_other_http_errors(client, mock_httpx_client):
    resp = _make_response(status_code=500)
    error = httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
    mock_httpx_client.get.return_value = resp
    resp.raise_for_status.side_effect = error
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_scenario_async(scenario_name="boom")


# ---------------------------------------------------------------------------
# Initializers
# ---------------------------------------------------------------------------


async def test_list_initializers_async(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data={"items": [_initializer_payload()]})
    result = await client.list_initializers_async(limit=5)
    assert len(result) == 1
    assert isinstance(result[0], RegisteredInitializer)
    mock_httpx_client.get.assert_awaited_once_with("/api/initializers", params={"limit": 5})


async def test_register_initializer_async_success(client, mock_httpx_client):
    payload = _initializer_payload(initializer_name="x")
    mock_httpx_client.post.return_value = _make_response(json_data=payload)
    result = await client.register_initializer_async(name="x", script_content="print(1)")
    assert isinstance(result, RegisteredInitializer)
    assert result.initializer_name == "x"
    mock_httpx_client.post.assert_awaited_once_with(
        "/api/initializers", json={"name": "x", "script_content": "print(1)"}
    )


async def test_register_initializer_async_raises_on_403(client, mock_httpx_client):
    resp = _make_response(status_code=403, json_data={"detail": "Custom initializers disabled"})
    mock_httpx_client.post.return_value = resp
    with pytest.raises(ServerNotAvailableError, match="disabled"):
        await client.register_initializer_async(name="x", script_content="...")


async def test_register_initializer_async_raises_on_403_with_plain_text_body(client, mock_httpx_client):
    resp = _make_response(status_code=403, text_data="Forbidden by proxy")
    resp.json.side_effect = ValueError("not json")
    mock_httpx_client.post.return_value = resp

    with pytest.raises(ServerNotAvailableError, match="Forbidden by proxy"):
        await client.register_initializer_async(name="x", script_content="...")


async def test_register_initializer_async_raises_on_500(client, mock_httpx_client):
    resp = _make_response(status_code=500)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
    mock_httpx_client.post.return_value = resp
    with pytest.raises(httpx.HTTPStatusError):
        await client.register_initializer_async(name="x", script_content="...")


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


async def test_list_targets_async(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data={"items": [_target_payload()]})
    result = await client.list_targets_async(limit=7)
    assert len(result) == 1
    assert isinstance(result[0], TargetInstance)
    mock_httpx_client.get.assert_awaited_once_with("/api/targets", params={"limit": 7})


async def test_list_converters_async(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data={"items": []})
    await client.list_converters_async()
    mock_httpx_client.get.assert_awaited_once_with("/api/converters", params=None)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


async def test_list_datasets_async(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data={"items": []})
    await client.list_datasets_async()
    mock_httpx_client.get.assert_awaited_once_with("/api/datasets", params=None)


# ---------------------------------------------------------------------------
# Scenario runs
# ---------------------------------------------------------------------------


async def test_start_scenario_run_async(client, mock_httpx_client):
    mock_httpx_client.post.return_value = _make_response(json_data=_run_summary_payload(scenario_result_id="abc"))
    request = RunScenarioRequest(scenario_name="x", target_name="t")
    result = await client.start_scenario_run_async(request=request)
    assert isinstance(result, ScenarioRunSummary)
    assert result.scenario_result_id == "abc"
    mock_httpx_client.post.assert_awaited_once()
    args, kwargs = mock_httpx_client.post.call_args
    assert args == ("/api/scenarios/runs",)
    # The CLI serializes the typed request via model_dump(mode="json", exclude_none=True);
    # required fields must appear in the body, None-valued fields must not.
    assert kwargs["json"]["scenario_name"] == "x"
    assert kwargs["json"]["target_name"] == "t"
    assert "scenario_params" not in kwargs["json"]


async def test_get_scenario_run_async(client, mock_httpx_client):
    import httpx as _httpx

    mock_httpx_client.get.return_value = _make_response(json_data=_run_summary_payload(status="IN_PROGRESS"))
    result = await client.get_scenario_run_async(scenario_result_id="abc")
    assert isinstance(result, ScenarioRunSummary)
    assert result.status == ScenarioRunState.IN_PROGRESS
    # Polling uses read=None so a busy server doesn't trip the client default
    # timeout while a scenario is executing.
    mock_httpx_client.get.assert_awaited_once()
    args, kwargs = mock_httpx_client.get.call_args
    assert args == ("/api/scenarios/runs/abc",)
    assert kwargs["params"] is None
    timeout = kwargs["timeout"]
    assert isinstance(timeout, _httpx.Timeout)
    assert timeout.read is None
    assert timeout.connect == 10.0


async def test_get_scenario_run_async_wraps_connect_error(client, mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.ConnectError("nope")
    with pytest.raises(ServerNotAvailableError, match="Cannot connect"):
        await client.get_scenario_run_async(scenario_result_id="abc")


async def test_get_scenario_run_results_async(client, mock_httpx_client):
    # Build a minimal ScenarioResult.to_dict() payload that from_dict can deserialize.
    from pyrit.models import ScenarioResult, ScenarioRunState

    scenario_result = make_scenario_result(
        scenario_name="x",
        objective_target_identifier=None,
        objective_scorer_identifier=None,
        attack_results={},
        scenario_run_state=ScenarioRunState.COMPLETED,
    )
    mock_httpx_client.get.return_value = _make_response(
        json_data=scenario_result.model_dump(mode="json", by_alias=True)
    )
    result = await client.get_scenario_run_results_async(scenario_result_id="abc")
    assert isinstance(result, ScenarioResult)
    mock_httpx_client.get.assert_awaited_once_with("/api/scenarios/runs/abc/results", params=None)


async def test_cancel_scenario_run_async(client, mock_httpx_client):
    mock_httpx_client.post.return_value = _make_response(json_data=_run_summary_payload(status="CANCELLED"))
    result = await client.cancel_scenario_run_async(scenario_result_id="abc")
    assert isinstance(result, ScenarioRunSummary)
    assert result.status == ScenarioRunState.CANCELLED
    mock_httpx_client.post.assert_awaited_once_with("/api/scenarios/runs/abc/cancel")


async def test_list_scenario_runs_async(client, mock_httpx_client):
    mock_httpx_client.get.return_value = _make_response(json_data={"items": [_run_summary_payload()]})
    result = await client.list_scenario_runs_async(limit=20)
    assert len(result) == 1
    assert isinstance(result[0], ScenarioRunListItem)
    mock_httpx_client.get.assert_awaited_once_with("/api/scenarios/runs", params={"limit": 20})


async def test_get_conversation_messages_async(client, mock_httpx_client):
    payload = {"conversation_id": "c1", "messages": []}
    mock_httpx_client.get.return_value = _make_response(json_data=payload)
    result = await client.get_conversation_messages_async(attack_result_id="a1", conversation_id="c1")
    assert result == payload
    mock_httpx_client.get.assert_awaited_once_with("/api/attacks/a1/messages", params={"conversation_id": "c1"})


async def test_list_scenario_runs_async_follows_bounded_pages(client, mock_httpx_client):
    first_page = [_run_summary_payload() for _ in range(100)]
    second_page = [_run_summary_payload()]
    mock_httpx_client.get.side_effect = [
        _make_response(
            json_data={
                "items": first_page,
                "pagination": {"limit": 100, "has_more": True, "next_cursor": "next-page"},
            }
        ),
        _make_response(
            json_data={
                "items": second_page,
                "pagination": {"limit": 1, "has_more": False},
            }
        ),
    ]

    result = await client.list_scenario_runs_async(limit=101)

    assert len(result) == 101
    assert mock_httpx_client.get.await_args_list == [
        call("/api/scenarios/runs", params={"limit": 100}),
        call("/api/scenarios/runs", params={"limit": 1, "cursor": "next-page"}),
    ]


# ---------------------------------------------------------------------------
# _get_json_async error path
# ---------------------------------------------------------------------------


async def test_get_json_wraps_connect_error_as_server_not_available(client, mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.ConnectError("nope")
    with pytest.raises(ServerNotAvailableError, match="Cannot connect"):
        await client.list_scenarios_async()
