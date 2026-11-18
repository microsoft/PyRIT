# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from unit.mocks import (
    make_scenario_identifier,
    make_suite_execution,
    make_suite_execution_entry,
    make_suite_scenario_result,
)

from pyrit.models import AttackOutcome, ComponentIdentifier, ScenarioRunState, ScenarioSuiteSubmissionState
from pyrit.output.scenario_suite_result.json import JsonScenarioSuiteResultMemoryPrinter, JsonScenarioSuiteResultPrinter


@pytest.fixture
def printer():
    return JsonScenarioSuiteResultPrinter()


# --- render_async ---


async def test_render_async_reports_suite_and_entry_statistics(printer):
    entry = make_suite_execution_entry()
    execution = make_suite_execution(entries=[entry])
    result = make_suite_scenario_result(
        execution=execution,
        entry=entry,
        outcomes=[("A", AttackOutcome.SUCCESS), ("B", AttackOutcome.FAILURE)],
        planned_objectives=["A", "B", "C"],
    )

    payload = json.loads(await printer.render_async(execution, results={str(result.id): result}))

    assert payload["view"] == "suite_overview"
    assert payload["suite"]["suite_name"] == "test_suite"
    assert payload["suite"]["status"] == "IN_PROGRESS"
    assert "entries" not in payload["suite"]
    assert payload["target"] == {"type": "MockTarget", "model": None, "endpoint": None}
    [row] = payload["entries"]
    assert row["entry_id"] == str(entry.entry_id)
    assert row["state"] == "COMPLETED"
    assert row["scenario_result_id"] == str(result.id)
    assert row["results_available"] is True
    assert row["statistics"] == {
        "completed": 2,
        "planned": 3,
        "successes": 1,
        "failures": 1,
        "errors": 0,
        "undetermined": 0,
        "success_rate_all": 0.5,
        "success_percentage": 50,
    }


@pytest.mark.parametrize("with_target", [True, False])
async def test_render_async_reports_target_from_available_result(printer, with_target):
    first, second = make_suite_execution_entry(), make_suite_execution_entry(position=2)
    execution = make_suite_execution(entries=[first, second])
    result = make_suite_scenario_result(execution=execution, entry=second)
    result.scenario_identifier = make_scenario_identifier(
        objective_target=(
            ComponentIdentifier(
                class_name="OpenAIChatTarget",
                class_module="pyrit.prompt_target",
                params={
                    "model_name": "deployment",
                    "underlying_model_name": "test-model",
                    "endpoint": "https://example.com/",
                },
            )
            if with_target
            else None
        )
    )

    payload = json.loads(await printer.render_async(execution, results={str(result.id): result}))

    assert payload["target"] == {
        "type": "OpenAIChatTarget" if with_target else None,
        "model": "test-model" if with_target else None,
        "endpoint": "https://example.com/" if with_target else None,
    }


async def test_render_async_reports_unavailable_entries_without_internal_error_types(printer):
    entry = make_suite_execution_entry(state=ScenarioRunState.FAILED)
    assert entry.run is not None
    entry.run.error = "Run failed"
    entry.run.error_type = "InternalException"

    payload = json.loads(await printer.render_async(make_suite_execution(entries=[entry])))

    [row] = payload["entries"]
    assert row["error"] == "Run failed"
    assert row["results_available"] is False
    assert row["statistics"] is None
    assert payload["target"] == {"type": None, "model": None, "endpoint": None}
    assert "InternalException" not in json.dumps(payload)


async def test_render_async_escapes_control_characters(printer):
    execution = make_suite_execution(
        entries=[make_suite_execution_entry(state=ScenarioSuiteSubmissionState.PENDING, with_run=False)]
    )
    execution.suite_name = "unsafe\x1b[2Jname"

    output = await printer.render_async(execution)

    assert "\x1b" not in output
    assert json.loads(output)["suite"]["suite_name"] == "unsafe\x1b[2Jname"


# --- memory printer ---


@pytest.mark.usefixtures("patch_central_database")
async def test_memory_printer_loads_stored_results(sqlite_instance):
    stored_entry = make_suite_execution_entry()
    missing_entry = make_suite_execution_entry(position=2, state=ScenarioRunState.FAILED)
    execution = make_suite_execution(entries=[stored_entry, missing_entry])
    stored = make_suite_scenario_result(execution=execution, entry=stored_entry, planned_objectives=["A", "B", "C"])
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[stored])

    payload = json.loads(await JsonScenarioSuiteResultMemoryPrinter().render_async(execution))

    assert payload["entries"][0]["statistics"]["planned"] == 3
    assert payload["entries"][1]["statistics"] is None
