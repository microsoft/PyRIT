# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import make_suite_execution, make_suite_execution_entry, make_suite_scenario_result

from pyrit.models import (
    SCENARIO_SUITE_EXECUTION_METADATA_KEY,
    ScenarioResult,
    ScenarioRunState,
    ScenarioSuiteExecutionMetadata,
    ScenarioSuiteExecutionSummary,
    ScenarioSuiteSubmissionState,
)
from pyrit.output.scenario_suite_result.base import ScenarioSuiteResultPrinterBase


class _Printer(ScenarioSuiteResultPrinterBase):
    async def render_async(
        self,
        execution: ScenarioSuiteExecutionSummary,
        *,
        results: Mapping[str, ScenarioResult] | None = None,
    ) -> str:
        return ""


def test_scenario_suite_result_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScenarioSuiteResultPrinterBase()  # type: ignore[abstract]


# --- _build_entries_async ---


async def test_build_entries_async_orders_by_position_and_keeps_entries_without_results():
    first = make_suite_execution_entry()
    second = make_suite_execution_entry(position=2, state=ScenarioSuiteSubmissionState.PENDING, with_run=False)
    execution = make_suite_execution(entries=[second, first])
    result = make_suite_scenario_result(execution=execution, entry=first)

    entries = await _Printer()._build_entries_async(execution=execution, results={str(result.id): result})

    assert [row.entry for row in entries] == [first, second]
    assert entries[0].statistics is not None
    assert entries[1].statistics is None


async def test_build_entries_async_reports_submission_or_run_error():
    submission_failed = make_suite_execution_entry(state=ScenarioSuiteSubmissionState.SUBMISSION_FAILED, with_run=False)
    submission_failed.error = "Submission failed"
    run_failed = make_suite_execution_entry(position=2, state=ScenarioRunState.FAILED)
    assert run_failed.run is not None
    run_failed.run.error = "Run failed"

    entries = await _Printer()._build_entries_async(
        execution=make_suite_execution(entries=[submission_failed, run_failed]), results={}
    )

    assert [row.error for row in entries] == ["Submission failed", "Run failed"]


async def test_build_entries_async_loads_omitted_results():
    entry = make_suite_execution_entry()
    execution = make_suite_execution(entries=[entry])
    result = make_suite_scenario_result(execution=execution, entry=entry)
    printer = _Printer()

    with patch.object(printer, "_get_scenario_results_async", new_callable=AsyncMock, return_value=[result]) as load:
        entries = await printer._build_entries_async(execution=execution, results=None)

    assert load.call_args.kwargs["scenario_result_ids"] == [str(result.id)]
    assert entries[0].statistics is not None


@pytest.mark.parametrize("results", [None, {}])
async def test_build_entries_async_skips_loading_when_not_needed(results):
    execution = make_suite_execution(
        entries=[make_suite_execution_entry(state=ScenarioSuiteSubmissionState.PENDING, with_run=False)]
    )
    printer = _Printer()

    with patch.object(printer, "_get_scenario_results_async", new_callable=AsyncMock) as load:
        await printer._build_entries_async(execution=execution, results=results)

    load.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("suite_execution_id", uuid.uuid4()),
        ("suite_entry_id", uuid.uuid4()),
        ("suite_name", "wrong_suite"),
        ("suite_version", "wrong_version"),
        ("entry_position", 2),
        ("preset_name", "wrong_preset"),
        ("preset_version", "wrong_version"),
    ],
)
async def test_build_entries_async_rejects_mismatched_metadata(field: str, value: Any):
    entry = make_suite_execution_entry()
    execution = make_suite_execution(entries=[entry])
    result = make_suite_scenario_result(execution=execution, entry=entry)
    metadata = ScenarioSuiteExecutionMetadata.from_metadata(result.metadata)
    assert metadata is not None
    result.metadata = metadata.model_copy(update={field: value}).to_metadata_fields()

    with pytest.raises(ValueError, match="does not match"):
        await _Printer()._build_entries_async(execution=execution, results={str(result.id): result})


@pytest.mark.parametrize("problem", ["extra_result", "wrong_result_id", "no_metadata", "malformed_metadata"])
async def test_build_entries_async_rejects_unrelated_or_unattributed_results(problem: str):
    entry = make_suite_execution_entry()
    execution = make_suite_execution(entries=[entry])
    result = make_suite_scenario_result(execution=execution, entry=entry)
    key = str(result.id)
    if problem == "extra_result":
        key = str(uuid.uuid4())
    elif problem == "wrong_result_id":
        result.id = uuid.uuid4()
    elif problem == "no_metadata":
        result.metadata = {}
    else:
        result.metadata = {SCENARIO_SUITE_EXECUTION_METADATA_KEY: {"version": 1}}

    with pytest.raises(ValueError, match="does not match|not present"):
        await _Printer()._build_entries_async(execution=execution, results={key: result})


@pytest.mark.parametrize("problem", ["entry_id", "entry_position", "run"])
async def test_build_entries_async_rejects_ambiguous_snapshot(problem: str):
    first, second = make_suite_execution_entry(), make_suite_execution_entry(position=2)
    setattr(second, problem, getattr(first, problem))

    with pytest.raises(ValueError, match="duplicate"):
        await _Printer()._build_entries_async(execution=make_suite_execution(entries=[first, second]), results=None)
