# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Full REST/SDK/SQLite analytics workload, included in every integration-suite run."""

import asyncio
import json

import pytest

from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.memory import SQLiteMemory
from test_support.analytics_benchmark import (  # noqa: F401
    AnalyticsBenchmark,
    benchmark_app,
    benchmark_directory,
    sqlite_instance,
)
from test_support.fixtures import authentication_policy, compatibility_headers, compatibility_id  # noqa: F401


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
