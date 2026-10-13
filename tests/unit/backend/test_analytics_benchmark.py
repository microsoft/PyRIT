# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Local SQLite smoke coverage and unit tests of the integration workload's failure gates."""

import os
import subprocess
import sys
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackAnalyticsResultsQuery
from test_support.analytics_benchmark import (  # noqa: F401
    AnalyticsBenchmark,
    RequestSample,
    benchmark_app,
    benchmark_directory,
    percentile95,
    sqlite_instance,
)
from unit.memory.test_attack_analytics import make_result


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
    # Unit tests check semantics; the integration workload asserts production latency.
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


def test_shared_support_imports_without_test_tiers_or_pythonpath() -> None:
    script = """
import sys
from pathlib import Path
from test_support import analytics_benchmark, fixtures

assert analytics_benchmark.AnalyticsBenchmark
assert fixtures.use_sqlite_memory
assert all(Path(path).resolve() != Path("tests").resolve() for path in sys.path)
assert not any(
    name == tier or name.startswith(tier + ".")
    for name in sys.modules
    for tier in ("unit", "integration")
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        env={name: value for name, value in os.environ.items() if name.upper() != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
