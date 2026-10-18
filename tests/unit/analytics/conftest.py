# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Finite execution budgets for native SQLite analytics correctness tests."""

from collections.abc import AsyncGenerator
from functools import partial
from unittest.mock import patch

import pytest

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.memory import SQLiteMemory


@pytest.fixture
async def analytics(sqlite_instance: SQLiteMemory) -> AsyncGenerator[AttackResultAnalytics, None]:
    """
    Run real SQLite correctness checks with bounded test time, not a production latency requirement.

    Full-suite contention must not turn filter/count assertions into benchmarks.
    Default budgets and expiration/cleanup are tested independently in
    test_execution.py. Admission capacities and native database work are unchanged.
    """
    execution = partial(AnalyticsExecution, queue_timeout=300.0, report_timeout=300.0, quick_timeout=300.0)
    with patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", side_effect=execution):
        async with AttackResultAnalytics(memory=sqlite_instance) as analytics:
            yield analytics
