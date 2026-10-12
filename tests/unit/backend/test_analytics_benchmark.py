# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local SQLite performance workload and ordinary unit coverage of its failure gates."""

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncGenerator, Iterator
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.main import app as backend_app
from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.backend.services.analytics_service import get_analytics_service
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.memory import SQLiteMemory
from pyrit.memory.memory_models import Base
from pyrit.models import AttackAnalyticsResultsQuery
from unit.backend.analytics_benchmark import AnalyticsBenchmark, RequestSample, percentile95
from unit.conftest import _use_sqlite_memory
from unit.memory.test_attack_analytics import make_result


@pytest.fixture
def benchmark_directory(tmp_path: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="analytics-benchmark-", dir=tmp_path) as temporary:
        directory = Path(temporary)
        (directory / "config.yaml").write_text(
            json.dumps(
                {
                    "memory_db_type": "sqlite",
                    "env_files": [],
                    "env_akv_ref": [],
                    "initializers": ["technique"],
                    "initialization_scripts": [],
                    "custom_initializers_source": str(directory / "initializers"),
                    "silent": True,
                }
            ),
            encoding="utf-8",
        )
        yield directory


@pytest.fixture
async def sqlite_instance(benchmark_directory: Path) -> AsyncGenerator[SQLiteMemory, None]:
    """Use file-backed SQLite for concurrent WAL readers; all files remain fixture-owned."""
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=benchmark_directory / "analytics.sqlite", skip_schema_migration=True)
    memory.results_path = str(benchmark_directory)
    memory.disable_embedding()
    try:
        async with await memory.get_session_async() as session:
            connection = await session.connection()
            assert (await connection.exec_driver_sql("PRAGMA journal_mode=WAL")).scalar_one() == "wal"
            await connection.run_sync(Base.metadata.create_all)
            await session.commit()
        with _use_sqlite_memory(memory):
            yield memory
    finally:
        await memory.dispose_engine_async()


@pytest.fixture
async def benchmark_app(
    *, benchmark_directory: Path, sqlite_instance: SQLiteMemory, compatibility_id: str
) -> AsyncGenerator[CompatibilityAPI, None]:
    """Exercise production routes, middleware, startup and shutdown with isolated test configuration."""
    assert get_analytics_service.cache_info().currsize == 0
    with (
        patch.dict(
            os.environ,
            {"ENTRA_CLIENT_ID": "", "ENTRA_TENANT_ID": "", "ENTRA_ALLOWED_GROUP_IDS": "", "ENTRA_ADMIN_GROUP_ID": ""},
        ),
        patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", benchmark_directory / "missing-default.yaml"),
    ):
        app = CompatibilityAPI(routes=list(backend_app.router.routes), middleware=backend_app.user_middleware)
        app.exception_handlers.update(backend_app.exception_handlers)
        app.state.compatibility_id = compatibility_id
        source = ConfigurationFileService(config_file_value=str(benchmark_directory / "config.yaml"))
        app.state.configuration_file_service = source
        runtime = RuntimeLifecycle(app=app, source=source)
        app.state.runtime_lifecycle = runtime
        try:
            await runtime.startup_async()
            assert runtime.state == "ready", runtime.message
            yield app
        finally:
            await runtime.shutdown_async()
        assert get_analytics_service.cache_info().currsize == 0


@pytest.fixture
def measurements() -> AnalyticsBenchmark:
    benchmark = AnalyticsBenchmark(
        memory=MagicMock(spec=SQLiteMemory), app=CompatibilityAPI(), headers={}, count=100_000
    )
    for kind in benchmark.LIMITS:
        benchmark.cold[kind] = RequestSample(0.1, 200, None)
        benchmark.samples[kind] = [RequestSample(0.1, 200, None) for _ in range(30)]
    benchmark.write_samples = [0.01, 0.02]
    benchmark.inserted = 1
    benchmark.updates = 2
    benchmark.peak_requests = 5
    return benchmark


async def test_local_sqlite_workload_counts_requests_writes_and_continuations_async(
    *,
    benchmark_app: CompatibilityAPI,
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
) -> None:
    budgets = partial(AnalyticsExecution, queue_timeout=30.0, report_timeout=30.0, quick_timeout=30.0)
    workload = AnalyticsBenchmark(memory=sqlite_instance, app=benchmark_app, headers=compatibility_headers, count=1_000)
    # This ordinary unit test checks semantics; only the opt-in test asserts production latency.
    with patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", side_effect=budgets):
        report = await workload.run_async(analysts=5, iterations=1)
    assert not workload.failures
    assert report["final_results"] == 1_000 + report["writes"]["inserts"]
    assert report["writes"]["updates"] > 0 and report["writes"]["inserts"] > 0
    assert not report["writes"]["errors"]
    assert report["verification_requests"] == {"successful": 2, "errors": 0}
    assert report["peak_concurrent_requests"] == 5
    for measurement in report["workloads"].values():
        assert measurement["successful_requests"] == 6
        assert measurement["error_requests"] == measurement["not_sent"] == 0


@pytest.mark.skipif(
    os.getenv("RUN_ANALYTICS_PERFORMANCE_TESTS", "").lower() != "true",
    reason="Set RUN_ANALYTICS_PERFORMANCE_TESTS=true to run the isolated 100k-result latency gate",
)
async def test_100k_attack_analytics_performance_async(
    *,
    benchmark_app: CompatibilityAPI,
    sqlite_instance: SQLiteMemory,
    compatibility_headers: dict[str, str],
    pytestconfig: pytest.Config,
) -> None:
    workload = AnalyticsBenchmark(
        memory=sqlite_instance, app=benchmark_app, headers=compatibility_headers, count=100_000
    )
    report = await workload.run_async(analysts=5, iterations=6)
    await asyncio.to_thread(pytestconfig.cache.set, "analytics/attack-results", report)
    print(json.dumps(report, indent=2))
    assert report["representative_workload"]
    assert report["meets_latency_targets"], "\n".join(report["failures"])


def test_nearest_rank_percentile_preserves_threshold_precision() -> None:
    assert percentile95(list(range(1, 21))) == 19
    assert percentile95([0.50000000001]) > 0.5
    with pytest.raises(ValueError, match="no samples"):
        percentile95([])


@pytest.mark.parametrize("kind", list(AnalyticsBenchmark.LIMITS))
def test_each_workload_independently_enforces_its_threshold(*, measurements: AnalyticsBenchmark, kind: str) -> None:
    limit = measurements.LIMITS[kind]
    measurements.samples[kind] = [RequestSample(limit + 0.000001, 200, None) for _ in range(30)]
    report = measurements._measurements(analysts=5, iterations=6, elapsed=1.0)
    assert not report["meets_latency_targets"]
    assert any(kind in failure and "exceeds" in failure for failure in report["failures"])


@pytest.mark.parametrize("cold", [False, True])
def test_fast_errors_cannot_pass_by_only_timing_successes(*, measurements: AnalyticsBenchmark, cold: bool) -> None:
    sample = RequestSample(0.00001, 503, "HTTP 503")
    if cold:
        measurements.cold["page"] = sample
    else:
        measurements.samples["page"][0] = sample
    report = measurements._measurements(analysts=5, iterations=6, elapsed=1.0)
    assert not report["meets_latency_targets"]
    assert report["workloads"]["page"]["error_requests"] == 1
    assert report["workloads"]["page"]["successful_requests"] == 30


@pytest.mark.parametrize("missing", ["writes", "insert", "sample", "parallelism"])
def test_incomplete_workload_cannot_pass(*, measurements: AnalyticsBenchmark, missing: str) -> None:
    if missing == "writes":
        measurements.updates = 0
    elif missing == "insert":
        measurements.inserted = 0
    elif missing == "sample":
        measurements.samples["continuation"].pop()
    else:
        measurements.peak_requests = 1
    assert not measurements._measurements(analysts=5, iterations=6, elapsed=1.0)["meets_latency_targets"]


async def test_missing_cursor_is_an_error_not_a_repeated_first_page(measurements: AnalyticsBenchmark) -> None:
    def reject_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No request may substitute for the missing continuation")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request), base_url="http://test") as client:
        sample, data = await measurements._request_async(client=client, kind="continuation")
    assert sample.status is None
    assert sample.error and "actual next_cursor" in sample.error
    assert data is None


@pytest.mark.parametrize("status", [422, 503, 504, 500])
async def test_http_error_is_recorded(*, measurements: AnalyticsBenchmark, status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "test failure"}))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sample, data = await measurements._request_async(client=client, kind="page")
    assert sample.status == status
    assert sample.error and str(status) in sample.error
    assert data is None


async def test_wrong_report_count_is_an_explicit_workload_failure(
    *, measurements: AnalyticsBenchmark, analytics_service: AttackResultAnalytics
) -> None:
    report = await analytics_service.query_async()
    with pytest.raises(ValueError, match="every saved result ID"):
        measurements._validate(kind="report_attack", data=report, previous=None, lower_count=100_000)


async def test_wrong_page_bounds_and_repeated_continuations_fail(
    *, measurements: AnalyticsBenchmark, analytics_service: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    empty = await analytics_service.results_async()
    with pytest.raises(ValueError, match="full page"):
        measurements._validate(kind="page", data=empty, previous=None, lower_count=100_000)
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(index=index) for index in range(1, 27)]
    )
    page = await analytics_service.results_async(query=AttackAnalyticsResultsQuery(limit=25))
    with pytest.raises(ValueError, match="disjoint"):
        measurements._validate(kind="continuation", data=page, previous=page, lower_count=100_000)


def test_workload_legacy_documents_use_current_wrapped_identifiers() -> None:
    identifiers = AnalyticsBenchmark._identifiers()
    legacy = AnalyticsBenchmark._legacy_document(identifiers[1].model_dump(mode="json"))
    attack = legacy["children"]["attack"]
    assert attack["__type__"] == "BenchmarkAttack01"
    assert attack["children"]["objective_target"]["model_name"] == "model-1"
    assert len(attack["children"]["request_converters"]) == 2
    assert "class_name" not in attack
    assert "hash" not in legacy


def test_workload_does_not_create_or_dispose_fixture_resources(measurements: AnalyticsBenchmark) -> None:
    assert isinstance(measurements.memory, MagicMock)
    assert measurements.memory.mock_calls == []
