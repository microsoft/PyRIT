# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.backend.models.operations import FindingUpdateRequest
from pyrit.backend.services.operation_service import OperationService
from pyrit.memory import SQLiteMemory
from pyrit.models import Finding, FindingValuesError
from unit.backend.mocks import read_finding_async

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.mark.parametrize(("title_query", "title_pattern"), [("leak", None), (None, "Le_k%")])
async def test_list_filters_titles_and_pages_in_configured_severity_order(
    sqlite_instance: SQLiteMemory,
    retired_value_finding_async: Finding,
    title_query: str | None,
    title_pattern: str | None,
) -> None:
    finding = retired_value_finding_async
    await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=finding.operation_id, title="Leak followup", severity="sev2")
    )
    await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=finding.operation_id, title="Unrelated", severity="sev1")
    )
    pages = [
        await OperationService().list_findings_async(
            operation_id=finding.operation_id,
            limit=1,
            offset=offset,
            title_query=title_query,
            title_pattern=title_pattern,
        )
        for offset in (0, 1)
    ]
    assert [page.items[0].title for page in pages] == ["Leak", "Leak followup"]
    assert [page.has_more for page in pages] == [True, False]
    assert [page.next_offset for page in pages] == [1, None]


async def _update_async(
    *,
    finding: Finding,
    field_values: dict[str, str],
    remove_retired_keys: list[str] | None = None,
    severity: str = "sev1",
) -> Finding:
    request = FindingUpdateRequest(
        title="Leak", severity=severity, field_values=field_values, remove_retired_keys=remove_retired_keys or []
    )
    return await OperationService().update_finding_async(
        operation_id=finding.operation_id, finding_id=finding.id, request=request
    )


async def test_update_keeps_an_omitted_retired_value(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding
) -> None:
    finding = retired_value_finding_async
    updated = await _update_async(finding=finding, field_values={"steps": "1. Open"})
    assert updated.field_values == finding.field_values
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == finding.field_values


async def test_update_keeps_a_blank_retired_value(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding
) -> None:
    finding = retired_value_finding_async
    await _update_async(finding=finding, field_values={"steps": "1. Open", "description": " "})
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == finding.field_values


async def test_update_removes_a_listed_retired_value(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding
) -> None:
    finding = retired_value_finding_async
    updated = await _update_async(
        finding=finding, field_values={"steps": "1. Open"}, remove_retired_keys=["description"]
    )
    assert updated.field_values == {"steps": "1. Open"}
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == {"steps": "1. Open"}


@pytest.mark.parametrize("key", ["steps", "nonexistent"])
async def test_update_rejects_removing_a_value_that_is_not_retired(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding, key: str
) -> None:
    finding = retired_value_finding_async
    with pytest.raises(FindingValuesError, match=re.escape(f"{key!r} is not a retired value of this finding.")):
        await _update_async(finding=finding, field_values={"steps": "1. Open"}, remove_retired_keys=[key])
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == finding.field_values


async def test_update_rejects_sending_and_removing_a_value(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding
) -> None:
    finding = retired_value_finding_async
    with pytest.raises(FindingValuesError, match=re.escape("'description' can't be both sent and removed.")):
        await _update_async(finding=finding, field_values=finding.field_values, remove_retired_keys=["description"])
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == finding.field_values


async def test_update_rejects_changing_a_retired_value(
    sqlite_instance: SQLiteMemory, retired_value_finding_async: Finding
) -> None:
    finding = retired_value_finding_async
    message = "Finding field 'description' is no longer configured, so its saved value can't change."
    with pytest.raises(FindingValuesError, match=re.escape(message)):
        await _update_async(finding=finding, field_values={"steps": "1. Open", "description": "New text"})
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).field_values == finding.field_values


async def test_update_keeps_an_unchanged_severity_that_is_no_longer_an_option(
    sqlite_instance: SQLiteMemory, unmatched_severity_finding_async: Finding
) -> None:
    finding = unmatched_severity_finding_async
    updated = await _update_async(finding=finding, severity="low", field_values={"steps": "1. Open"})
    assert updated.severity == "low"
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).severity == "low"


async def test_update_rejects_changing_to_another_severity_that_is_not_an_option(
    sqlite_instance: SQLiteMemory, unmatched_severity_finding_async: Finding
) -> None:
    finding = unmatched_severity_finding_async
    with pytest.raises(FindingValuesError, match=re.escape("Severity has no option 'medium'.")):
        await _update_async(finding=finding, severity="medium", field_values={"steps": "1. Open"})
    assert (await read_finding_async(memory=sqlite_instance, finding=finding)).severity == "low"
