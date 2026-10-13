# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real SDK/SQLite responses through the complete production middleware stack."""

import asyncio
import base64
import json
import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from pyrit import _compatibility
from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.main import app as backend_app
from pyrit.backend.middleware.auth import AuthenticatedUser, EntraAuthMiddleware
from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.backend.middleware.request_size import RequestSizeLimitMiddleware
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.exceptions.analytics_exception import (
    AnalyticsBusyException,
    AnalyticsDataException,
    AnalyticsException,
    AnalyticsTimeoutException,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackAnalyticsFacetQuery,
    AttackAnalyticsQuery,
    AttackAnalyticsReport,
    AttackAnalyticsResultsQuery,
    AttackOutcome,
)
from unit.memory.test_attack_analytics import make_result


@pytest.fixture
def analytics_app(*, tmp_path: Path, compatibility_id: str) -> Iterator[CompatibilityAPI]:
    """Copy actual routes, handlers, and middleware; only external auth I/O is mocked."""
    with patch.dict(
        os.environ,
        {
            "ENTRA_CLIENT_ID": "test-client",
            "ENTRA_TENANT_ID": "test-tenant",
            "ENTRA_ALLOWED_GROUP_IDS": "test-group",
            "ENTRA_ADMIN_GROUP_ID": "test-admin-group",
        },
    ):
        app = CompatibilityAPI(
            routes=list(backend_app.router.routes),
            middleware=backend_app.user_middleware,
            exception_handlers=backend_app.exception_handlers,
        )
        app.state.compatibility_id = compatibility_id
        runtime = RuntimeLifecycle(
            app=app, source=ConfigurationFileService(config_file_value=str(tmp_path / "config.yaml"))
        )
        runtime.state = "ready"
        app.state.runtime_lifecycle = runtime
        yield app
        assert not runtime.operations


@pytest.fixture
async def analytics_client(
    *, analytics_app: CompatibilityAPI, analytics_service: AttackResultAnalytics, compatibility_headers: dict[str, str]
) -> AsyncGenerator[httpx.AsyncClient, None]:
    user = AuthenticatedUser(oid="test-user", name="Test analyst", email="test@example.invalid", groups=["test-group"])
    with patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=user):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analytics_app, raise_app_exceptions=False),
            base_url="http://test",
            headers={**compatibility_headers, "Authorization": "Bearer test-only-token"},
        ) as client:
            yield client


@pytest.fixture
async def analytics_results(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            make_result(
                index=index,
                outcome=outcome,
                categories=["privacy", "safety"],
                converters=["Alpha", "ALPHA", "Beta"],
                labels={"case": f"case-{index}"},
            )
            for index, outcome in enumerate(AttackOutcome, 1)
        ]
    )


async def test_concurrent_first_requests_construct_one_sdk_on_the_runtime_loop_async(
    *,
    analytics_app: CompatibilityAPI,
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
) -> None:
    loop = asyncio.get_running_loop()
    owners = []

    def create() -> AttackResultAnalytics:
        assert asyncio.get_running_loop() is loop
        owner = AttackResultAnalytics(memory=sqlite_instance)
        owners.append(owner)
        return owner

    assert get_analytics_service.cache_info().currsize == 0
    user = AuthenticatedUser(oid="test", name="Test", email="test@example.invalid", groups=["test-group"])
    budgets = partial(AnalyticsExecution, report_timeout=30.0, quick_timeout=30.0, queue_timeout=30.0)
    with (
        patch("pyrit.backend.services.analytics_service.AttackResultAnalytics", side_effect=create),
        patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", side_effect=budgets),
        patch.object(EntraAuthMiddleware, "_authenticate_with_graph_async", return_value=user),
    ):
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=analytics_app),
                base_url="http://test",
                headers={**compatibility_headers, "Authorization": "Bearer test-only-token"},
            ) as client:
                responses = await asyncio.gather(
                    *(client.post("/api/analytics/attacks/results", json={}) for _ in range(5))
                )
            assert all(response.status_code == 200 for response in responses)
            assert len(owners) == 1
            assert get_analytics_service() is owners[0]
        finally:
            await close_analytics_service_async()


@pytest.mark.usefixtures("analytics_results")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"filters": {"outcomes": ["error"]}},
        {"group_by": {"name": "targeted_harm_category"}, "compare_by": {"name": "converter_type"}},
        {"filters": {"dimensions": [{"dimension": {"name": "operation"}, "values": [{"value": "operation-a"}]}] * 16}},
    ],
)
async def test_report_matches_shared_sdk_serialization_async(
    *, analytics_client: httpx.AsyncClient, analytics_service: AttackResultAnalytics, body: dict[str, Any]
) -> None:
    response = await analytics_client.post("/api/analytics/attacks/query", json=body)
    assert response.status_code == 200
    data = response.json()
    actual = AttackAnalyticsReport.model_validate(data)
    expected = await analytics_service.query_async(query=AttackAnalyticsQuery.model_validate(body))
    exclusions = {"computed_at": True, "results": {"computed_at"}}
    assert actual.model_dump(exclude=exclusions) == expected.model_dump(exclude=exclusions)
    assert data["computed_at"] == data["results"]["computed_at"]
    assert data["summary"] == expected.model_dump(mode="json")["summary"]
    assert data["summary"]["success_rate_decided"] == data["summary"]["success_rate"]
    assert all(
        group["statistics"]["success_rate_decided"] == group["statistics"]["success_rate"] for group in data["groups"]
    )
    assert all(
        cell["statistics"]["success_rate_decided"] == cell["statistics"]["success_rate"] for cell in data["cells"]
    )
    assert response.headers["x-request-id"]
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.usefixtures("analytics_results")
async def test_native_pages_and_facets_are_sdk_equivalent_without_reports_or_hydration_async(
    *, analytics_client: httpx.AsyncClient, analytics_service: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    get_session = sqlite_instance.get_session_async
    sessions: list[AsyncSession] = []

    async def get_session_async() -> AsyncSession:
        session = await get_session()
        assert isinstance(session, AsyncSession)
        sessions.append(session)
        return session

    with (
        patch.object(sqlite_instance, "get_session_async", side_effect=get_session_async),
        patch.object(sqlite_instance, "get_session", side_effect=AssertionError("No sync sessions")),
        patch.object(sqlite_instance, "get_attack_results_async", side_effect=AssertionError("No hydrated results")),
        patch.object(analytics_service, "query_async", side_effect=AssertionError("No reports")),
        patch.object(analytics_service._reader, "report_async", side_effect=AssertionError("No aggregation")),
    ):
        first = await analytics_client.post("/api/analytics/attacks/results", json={"limit": 2})
        assert first.status_code == 200
        page_query = AttackAnalyticsResultsQuery(limit=2, cursor=first.json()["next_cursor"])
        second = await analytics_client.post("/api/analytics/attacks/results", json=page_query.model_dump(mode="json"))
        assert second.status_code == 200
        expected = await analytics_service.results_async(query=page_query)
        assert {k: v for k, v in second.json().items() if k != "computed_at"} == expected.model_dump(
            mode="json", exclude={"computed_at"}
        )
        ids = [row["attack_result_id"] for page in (first, second) for row in page.json()["items"]]
        assert len(ids) == len(set(ids)) == 4
        assert second.json()["next_cursor"] is None
        facet_query = AttackAnalyticsFacetQuery.model_validate(
            {"dimension": {"name": "label", "label_key": "case"}, "limit": 2, "offset": 1, "search": "case"}
        )
        response = await analytics_client.post(
            "/api/analytics/attacks/facets", json=facet_query.model_dump(mode="json")
        )
        assert response.status_code == 200
        facets = await analytics_service.facets_async(query=facet_query)
        assert {k: v for k, v in response.json().items() if k != "computed_at"} == facets.model_dump(
            mode="json", exclude={"computed_at"}
        )
    assert len(sessions) == 5


@pytest.mark.parametrize(
    ("endpoint", "body"),
    [
        ("query", {"axis_limit": 21}),
        ("query", {"group_by": {"name": "arbitrary"}}),
        ("query", {"access_scope": "not-an-sdk-input"}),
        ("query", {"filters": {"updated_after": "2026-01-01"}}),
        ("query", {"filters": {"updated_after": "2026-02-01Z", "updated_before": "2026-01-01Z"}}),
        ("results", {"limit": 101}),
        ("facets", {"dimension": {"name": "label"}}),
        ("facets", {"offset": -1}),
    ],
)
async def test_invalid_queries_are_problem_responses_async(
    *, analytics_client: httpx.AsyncClient, endpoint: str, body: dict[str, Any]
) -> None:
    response = await analytics_client.post(f"/api/analytics/attacks/{endpoint}", json=body)
    assert response.status_code == response.json()["status"] == 422
    assert response.json()["errors"]
    assert "summary" not in response.json()


@pytest.mark.usefixtures("analytics_results")
@pytest.mark.parametrize("corruption", ["malformed", "identity", "filters"])
async def test_invalid_or_mismatched_cursor_fails_explicitly_async(
    *, analytics_client: httpx.AsyncClient, corruption: str
) -> None:
    first = await analytics_client.post("/api/analytics/attacks/results", json={"limit": 1})
    cursor = first.json()["next_cursor"]
    body: dict[str, Any] = {"cursor": cursor}
    if corruption == "malformed":
        body["cursor"] = "not-a-cursor"
    elif corruption == "identity":
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        payload["i"] = []
        body["cursor"] = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    else:
        body["filters"] = {"outcomes": ["success"]}
    response = await analytics_client.post("/api/analytics/attacks/results", json=body)
    assert response.status_code == response.json()["status"] == 400
    assert "cursor" in response.json()["detail"].lower()
    assert "items" not in response.json()


@pytest.mark.parametrize(
    ("endpoint", "method", "body"),
    [
        ("query", "query_async", {}),
        ("results", "results_async", {}),
        ("facets", "facets_async", {"dimension": {"name": "operation"}}),
    ],
)
@pytest.mark.parametrize(
    "error", [AnalyticsBusyException(), AnalyticsTimeoutException(), AnalyticsDataException("Invalid saved metadata")]
)
async def test_analytics_failures_remain_explicit_async(
    *,
    analytics_client: httpx.AsyncClient,
    analytics_service: AttackResultAnalytics,
    endpoint: str,
    method: str,
    body: dict[str, Any],
    error: AnalyticsException,
) -> None:
    with patch.object(analytics_service, method, new=AsyncMock(side_effect=error)):
        response = await analytics_client.post(
            f"/api/analytics/attacks/{endpoint}", json=body, headers={"X-Request-ID": "analytics-failure"}
        )
    assert response.status_code == response.json()["status"] == error.status_code
    assert response.json()["detail"] == error.message
    assert response.headers["x-request-id"] == "analytics-failure"
    assert ("Retry-After" in response.headers) == (error.status_code == 503)
    if error.status_code == 503:
        assert response.headers["Retry-After"] == "1"
    assert not {"items", "summary"} & response.json().keys()


async def test_unexpected_failure_is_not_a_success_or_leaked_detail_async(
    *, analytics_client: httpx.AsyncClient, analytics_service: AttackResultAnalytics
) -> None:
    with patch.object(analytics_service, "query_async", side_effect=RuntimeError("private connection details")):
        response = await analytics_client.post("/api/analytics/attacks/query", json={})
    assert response.status_code == response.json()["status"] == 500
    assert response.json()["detail"] == "An unexpected error occurred"


@pytest.mark.parametrize(
    ("gate", "status"),
    [
        ("missing-identity", 400),
        ("mismatched-identity", 409),
        ("authentication", 401),
        ("authorization", 403),
        ("readiness", 503),
        ("url", 414),
        ("declared-body", 413),
        ("streamed-body", 413),
    ],
)
async def test_full_app_gates_precede_sdk_work_async(
    *,
    analytics_client: httpx.AsyncClient,
    analytics_service: AttackResultAnalytics,
    analytics_app: CompatibilityAPI,
    gate: str,
    status: int,
) -> None:
    headers = dict(analytics_client.headers)
    url = "/api/analytics/attacks/query"
    content: bytes | AsyncGenerator[bytes, None] = b"{}"
    if gate == "missing-identity":
        del headers[_compatibility.COMPATIBILITY_HEADER.lower()]
    elif gate == "mismatched-identity":
        headers[_compatibility.COMPATIBILITY_HEADER.lower()] = "0.14.0+g" + "b" * 40
    elif gate == "authentication":
        headers.pop("authorization")
    elif gate == "authorization":
        EntraAuthMiddleware._authenticate_with_graph_async.return_value.groups = []
    elif gate == "readiness":
        analytics_app.state.runtime_lifecycle.state = "initializing"
    elif gate == "url":
        url += "?filter=" + "x" * RequestSizeLimitMiddleware.MAX_URL_LENGTH
    elif gate == "declared-body":
        headers["Content-Length"] = str(RequestSizeLimitMiddleware.MAX_BODY_BYTES + 1)
    elif gate == "streamed-body":

        async def chunks_async() -> AsyncGenerator[bytes, None]:
            yield b" " * 32
            yield b"{}"

        content = chunks_async()

    with (
        patch.object(analytics_service, "query_async", side_effect=AssertionError("Gate must reject first")) as query,
        patch.object(RequestSizeLimitMiddleware, "MAX_BODY_BYTES", 32) if gate == "streamed-body" else nullcontext(),
    ):
        request = analytics_client.build_request("POST", url, content=content)
        request.headers = httpx.Headers(headers)
        response = await analytics_client.send(request)
    assert response.status_code == status
    assert response.headers["x-request-id"]
    query.assert_not_called()


def test_openapi_uses_shared_models_and_packaged_compatibility(analytics_app: CompatibilityAPI) -> None:
    paths = analytics_app.openapi()["paths"]
    for endpoint, model in (
        ("query", "AttackAnalyticsQuery"),
        ("results", "AttackAnalyticsResultsQuery"),
        ("facets", "AttackAnalyticsFacetQuery"),
    ):
        operation = paths[f"/api/analytics/attacks/{endpoint}"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(model)
        assert any(
            parameter["name"] == _compatibility.COMPATIBILITY_HEADER and parameter["required"]
            for parameter in operation["parameters"]
        )
        assert {"400", "422", "500", "503", "504"} <= operation["responses"].keys()


async def test_full_quick_lane_returns_retry_after_without_coalescing_async(
    *, analytics_client: httpx.AsyncClient, analytics_service: AttackResultAnalytics
) -> None:
    page = await analytics_service.results_async()
    execution = analytics_service._execution()
    admitted, release = asyncio.Event(), asyncio.Event()
    attempts = 0
    admit = execution._admit_async

    async def admit_async(lane: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 12:
            admitted.set()
        await admit(lane)

    async def blocked_async(**kwargs: object) -> Any:
        await release.wait()
        return page

    with (
        patch.object(execution, "_admit_async", side_effect=admit_async),
        patch.object(analytics_service._reader, "results_async", side_effect=blocked_async) as read,
    ):
        requests = [
            asyncio.create_task(analytics_client.post("/api/analytics/attacks/results", json={})) for _ in range(12)
        ]
        try:
            await asyncio.wait_for(admitted.wait(), 10)
            rejected = await analytics_client.post("/api/analytics/attacks/results", json={})
            assert rejected.status_code == 503
            assert rejected.headers["Retry-After"] == "1"
            assert analytics_service.has_active_work
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*requests), 30)
            assert all(response.status_code == 200 for response in responses)
            assert read.await_count == 12
        finally:
            release.set()
            await asyncio.gather(*requests, return_exceptions=True)


@pytest.mark.parametrize(
    ("endpoint", "method", "body", "deadline"),
    [
        ("query", "report_async", {}, 5.0),
        ("results", "results_async", {}, 1.0),
        ("facets", "facets_async", {"dimension": {"name": "operation"}}, 1.0),
    ],
)
async def test_production_deadlines_return_504_and_keep_capacity_through_cleanup_async(
    *,
    analytics_client: httpx.AsyncClient,
    analytics_service: AttackResultAnalytics,
    endpoint: str,
    method: str,
    body: dict[str, Any],
    deadline: float,
) -> None:
    entered, expire, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed: list[float] = []

    async def blocked_async(**kwargs: object) -> None:
        entered.set()
        await release.wait()
        raise AnalyticsTimeoutException

    async def expire_async(tasks: set[Any], *, timeout: float) -> tuple[set[Any], set[Any]]:
        observed.append(timeout)
        await expire.wait()
        return set(), tasks

    with (
        patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", new=AnalyticsExecution),
        patch("pyrit.analytics._execution.monotonic", return_value=100.0),
        patch("pyrit.memory.query_control.time") as clock,
        patch("pyrit.analytics._execution.asyncio.wait", side_effect=expire_async),
        patch.object(analytics_service._reader, method, side_effect=blocked_async),
    ):
        clock.monotonic.return_value = 100.0
        request = asyncio.create_task(analytics_client.post(f"/api/analytics/attacks/{endpoint}", json=body))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            assert observed == [deadline]
            expire.set()
            response = await asyncio.wait_for(request, 10)
            assert response.status_code == response.json()["status"] == 504
            assert "Retry-After" not in response.headers
            assert analytics_service.has_active_work
        finally:
            expire.set()
            release.set()
            await request
            await analytics_service.close_async()
