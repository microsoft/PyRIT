# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

from collections.abc import AsyncGenerator, Iterator
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.attack_service import get_attack_service
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.memory import SQLiteMemory
from pyrit.models import Finding, FindingField, FindingFields, Operation
from pyrit.setup.finding_fields import set_active_finding_fields
from test_support.fixtures import authentication_policy, compatibility_headers, compatibility_id  # noqa: F401
from unit.async_utils import TEST_WATCHDOG_SECONDS


@pytest.fixture
async def analytics_service(sqlite_instance: SQLiteMemory) -> AsyncGenerator[AttackResultAnalytics, None]:
    """Use real isolated memory with finite correctness budgets, not production latency assertions."""
    execution = partial(
        AnalyticsExecution,
        queue_timeout=TEST_WATCHDOG_SECONDS,
        report_timeout=TEST_WATCHDOG_SECONDS,
        quick_timeout=TEST_WATCHDOG_SECONDS,
    )
    assert get_analytics_service.cache_info().currsize == 0
    with patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", side_effect=execution):
        try:
            yield get_analytics_service()
        finally:
            await close_analytics_service_async()


@pytest.fixture
def managed_media_path(*, sqlite_instance: SQLiteMemory, patch_central_database: MagicMock) -> Path:
    """Create a file in the isolated memory's media directory."""
    assert sqlite_instance.results_path is not None
    path = Path(sqlite_instance.results_path) / "prompt-memory-entries" / "image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return path.resolve()


@pytest.fixture(autouse=True)
def isolated_manual_message_services() -> Iterator[None]:
    """Keep cached service owners bound to this test's memory and event loop."""
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler, get_dataset_service):
        factory.cache_clear()
    yield
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler, get_dataset_service):
        factory.cache_clear()


@pytest.fixture
def contoso_finding_fields(reset_finding_fields: None) -> FindingFields:
    """Make Contoso's finding fields active: severities ``sev1`` and ``sev2``, and a required ``steps`` field."""
    fields = FindingFields(
        severity=FindingField(
            key="severity", label="Severity", type="choice", options=[("sev1", "Sev 1"), ("sev2", "Sev 2")]
        ),
        fields=[FindingField(key="steps", label="Steps to reproduce", type="multiline", required=True)],
    )
    set_active_finding_fields(finding_fields=fields, source="test")
    return fields


@pytest.fixture
async def retired_value_finding_async(sqlite_instance: SQLiteMemory, contoso_finding_fields: FindingFields) -> Finding:
    """Seed a finding with ``steps`` and a ``description`` value that Contoso's fields don't use."""
    return await _seed_contoso_finding_async(
        sqlite_instance=sqlite_instance, severity="sev1", field_values={"steps": "1. Open", "description": "Old text"}
    )


@pytest.fixture
async def unmatched_severity_finding_async(
    sqlite_instance: SQLiteMemory, contoso_finding_fields: FindingFields
) -> Finding:
    """Seed a finding with severity ``low``, which Contoso's fields don't offer."""
    return await _seed_contoso_finding_async(
        sqlite_instance=sqlite_instance, severity="low", field_values={"steps": "1. Open"}
    )


async def _seed_contoso_finding_async(
    *, sqlite_instance: SQLiteMemory, severity: str, field_values: dict[str, str]
) -> Finding:
    operation = await sqlite_instance.add_operation_async(Operation(name="Contoso"))
    return await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Leak", severity=severity, field_values=field_values)
    )
