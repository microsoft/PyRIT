# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Full REST/SDK/SQLite analytics workload, included in every integration-suite run."""

import asyncio
import json

import pytest
from unit.backend.analytics_benchmark import AnalyticsBenchmark, benchmark_app, benchmark_directory, sqlite_instance  # noqa: F401
from unit.backend.conftest import compatibility_headers, compatibility_id  # noqa: F401

from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.memory import SQLiteMemory


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
