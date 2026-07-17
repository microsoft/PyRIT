# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli.api_client.PyRITApiClient.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pyrit.cli.api_client import PyRITApiClient, ServerNotAvailableError
from pyrit.models import ScenarioRunState, TargetCapabilities
from pyrit.models.catalog import (
    RegisteredInitializer,
    RegisteredScenario,
    RunScenarioRequest,
    ScenarioRunSummary,
    TargetInstance,
)
from unit.mocks import make_scenario_result


@pytest.fixture()
def mock_httpx_client():
    """A MagicMock standing in for an opened ``httpx.AsyncClient``."""
    client = MagicMock()
    client.get = AsyncMock()
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
    now = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
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
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c as opened:
            assert opened is c
            assert c._client is mock_httpx_client
        # After exit, close was called
        mock_httpx_client.aclose.assert_awaited_once()
        assert c._client is None
    # Default request_timeout (60s) propagates to the httpx client constructor.
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=60.0)


async def test_async_context_manager_passes_custom_request_timeout(mock_httpx_client):
    c = PyRITApiClient(base_url="http://localhost:8000", request_timeout=120.0)
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            pass
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=120.0)


async def test_async_context_manager_uses_default_when_request_timeout_is_none(
    mock_httpx_client,
):
    c = PyRITApiClient(base_url="http://localhost:8000", request_timeout=None)
    fake_async_client_cls = MagicMock(return_value=mock_httpx_client)
    with patch("httpx.AsyncClient", fake_async_client_cls):
        async with c:
            pass
    fake_async_client_cls.assert_called_once_with(base_url="http://localhost:8000", timeout=60.0)


async def test_close_async_is_noop_when_already_closed():
    c = PyRITApiClient(base_url="http://localhost:8000")
    await c.close_async()  # Should not raise.


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
    assert isinstance(result[0], ScenarioRunSummary)
    mock_httpx_client.get.assert_awaited_once_with("/api/scenarios/runs", params={"limit": 20})


# ---------------------------------------------------------------------------
# _get_json_async error path
# ---------------------------------------------------------------------------


async def test_get_json_wraps_connect_error_as_server_not_available(client, mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.ConnectError("nope")
    with pytest.raises(ServerNotAvailableError, match="Cannot connect"):
        await client.list_scenarios_async()
