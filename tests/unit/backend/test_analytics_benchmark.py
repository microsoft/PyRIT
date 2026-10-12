# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Workload gates must not turn errors, missing continuations, or bad counts into a pass."""

import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from build_scripts.benchmark_attack_analytics import (
    AnalyticsBenchmark,
    RequestSample,
    isolated_environment,
    percentile95,
)
from pyrit.analytics import AttackResultAnalytics
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.models import AttackAnalyticsResultsQuery
from unit.memory.test_attack_analytics import make_result


@pytest.fixture
def measurements(tmp_path: Path) -> AnalyticsBenchmark:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100_000)
    for kind in benchmark.LIMITS:
        benchmark.cold[kind] = RequestSample(0.1, 200, None)
        benchmark.samples[kind] = [RequestSample(0.1, 200, None) for _ in range(30)]
    benchmark.write_samples = [0.01, 0.02]
    benchmark.inserted = 1
    benchmark.updates = 2
    benchmark.peak_requests = 5
    return benchmark


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


async def test_missing_cursor_is_an_error_not_a_repeated_first_page(tmp_path: Path) -> None:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100)

    def reject_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No request may substitute for the missing continuation")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request), base_url="http://test") as client:
        sample, data = await benchmark._request_async(client=client, kind="continuation")
    assert sample.status is None
    assert sample.error and "actual next_cursor" in sample.error
    assert data is None


@pytest.mark.parametrize("status", [422, 503, 504, 500])
async def test_http_error_is_recorded(*, tmp_path: Path, status: int) -> None:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100)
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "test failure"}))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sample, data = await benchmark._request_async(client=client, kind="page")
    assert sample.status == status
    assert sample.error and str(status) in sample.error
    assert data is None


async def test_workload_refuses_existing_memory_without_disposal(
    *, tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100)
    with pytest.raises(RuntimeError, match="fresh process"):
        await benchmark.run_async(analysts=5, iterations=6)
    assert CentralMemory.get_memory_instance() is sqlite_instance
    assert not (tmp_path / "analytics.sqlite").exists()
    assert await sqlite_instance.get_attack_results_async() == []


async def test_wrong_report_count_is_an_explicit_workload_failure(
    *, tmp_path: Path, analytics_service: AttackResultAnalytics
) -> None:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100)
    report = await analytics_service.query_async()
    with pytest.raises(ValueError, match="every saved result ID"):
        benchmark._validate(kind="report_attack", data=report, previous=None, lower_count=100)


async def test_wrong_page_bounds_and_repeated_continuations_fail(
    *, tmp_path: Path, analytics_service: AttackResultAnalytics, sqlite_instance: SQLiteMemory
) -> None:
    benchmark = AnalyticsBenchmark(directory=tmp_path, count=100)
    empty = await analytics_service.results_async()
    with pytest.raises(ValueError, match="full page"):
        benchmark._validate(kind="page", data=empty, previous=None, lower_count=100)
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[make_result(index=index) for index in range(1, 27)]
    )
    page = await analytics_service.results_async(query=AttackAnalyticsResultsQuery(limit=25))
    with pytest.raises(ValueError, match="disjoint"):
        benchmark._validate(kind="continuation", data=page, previous=page, lower_count=100)


def test_workload_legacy_documents_use_current_wrapped_identifiers() -> None:
    identifiers = AnalyticsBenchmark._identifiers()
    legacy = AnalyticsBenchmark._legacy_document(identifiers[1].model_dump(mode="json"))
    attack = legacy["children"]["attack"]
    assert attack["__type__"] == "BenchmarkAttack01"
    assert attack["children"]["objective_target"]["model_name"] == "model-1"
    assert len(attack["children"]["request_converters"]) == 2
    assert "class_name" not in attack
    assert "hash" not in legacy


def test_configuration_home_and_external_auth_are_isolated_and_restored(tmp_path: Path) -> None:
    original_home = Path.home()
    with patch.dict(os.environ, {"ENTRA_TENANT_ID": "caller", "ENTRA_CLIENT_ID": "client"}):
        with isolated_environment(tmp_path):
            assert os.environ["ENTRA_TENANT_ID"] == ""
            assert os.environ["ENTRA_CLIENT_ID"] == ""
            assert Path.home() == tmp_path
        assert os.environ["ENTRA_TENANT_ID"] == "caller"
        assert os.environ["ENTRA_CLIENT_ID"] == "client"
        assert Path.home() == original_home
