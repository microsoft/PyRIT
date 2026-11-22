# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from unit.mocks import (
    make_scenario_identifier,
    make_suite_execution,
    make_suite_execution_entry,
    make_suite_scenario_result,
)

from pyrit.models import (
    AttackOutcome,
    ComponentIdentifier,
    ScenarioRunState,
    ScenarioSuiteExecutionStatus,
    ScenarioSuiteSubmissionState,
)
from pyrit.output.scenario_suite_result.pretty import PrettyScenarioSuiteResultMemoryPrinter


@pytest.fixture
def printer(patch_central_database):
    return PrettyScenarioSuiteResultMemoryPrinter(width=100, indent_size=2, enable_colors=False)


# --- write_async ---


async def test_write_async_renders_suite_details_and_entry_breakdown(printer, capsys):
    first = make_suite_execution_entry()
    second = make_suite_execution_entry(position=2, state=ScenarioRunState.IN_PROGRESS)
    execution = make_suite_execution(entries=[second, first])
    results = [
        make_suite_scenario_result(
            execution=execution,
            entry=first,
            outcomes=[
                ("A", AttackOutcome.ERROR),
                ("A", AttackOutcome.SUCCESS),
                ("B", AttackOutcome.FAILURE),
                ("C", AttackOutcome.ERROR),
                ("D", AttackOutcome.UNDETERMINED),
            ],
            planned_objectives=["A", "B", "C", "D", "E"],
        ),
        make_suite_scenario_result(execution=execution, entry=second, outcomes=[("X", AttackOutcome.FAILURE)]),
    ]

    await printer.write_async(execution, results={str(result.id): result for result in results})
    out = capsys.readouterr().out

    assert "SCENARIO SUITE RESULTS: test_suite" in out
    assert "Suite Information" in out
    assert f"Execution ID: {execution.suite_execution_id}" in out
    assert "Status: IN_PROGRESS" in out
    assert "Results Available: 2 of 2" in out
    first_block, second_block = out.split("🔸 1. test_preset")[1].split("🔸 2. test_preset")
    assert "State: COMPLETED" in first_block
    assert "Objective Executions: 4 of 5" in first_block
    assert "Outcomes: 1 succeeded, 1 failed, 1 errors, 1 undetermined" in first_block
    assert "Success Rate: 25%" in first_block
    assert "State: IN_PROGRESS" in second_block
    assert "Objective Executions: 1\n" in second_block
    assert "Success Rate: 0%" in second_block


async def test_write_async_shows_entries_without_results(printer, capsys):
    entries = [
        make_suite_execution_entry(position=1, state=ScenarioSuiteSubmissionState.PENDING, with_run=False),
        make_suite_execution_entry(position=2, state=ScenarioSuiteSubmissionState.SUBMISSION_FAILED, with_run=False),
        make_suite_execution_entry(position=3, state=ScenarioRunState.CANCELLED, with_run=False),
        make_suite_execution_entry(position=4, state=ScenarioRunState.QUEUED),
        make_suite_execution_entry(position=5, state=ScenarioRunState.FAILED),
        make_suite_execution_entry(position=6, state=ScenarioRunState.IN_PROGRESS),
    ]
    entries[1].error = "Submission failed"
    execution = make_suite_execution(entries=entries)
    empty = make_suite_scenario_result(execution=execution, entry=entries[-1], planned_objectives=["A", "B"])

    await printer.write_async(execution, results={str(empty.id): empty})
    out = capsys.readouterr().out

    assert "Results Available: 1 of 6" in out
    assert out.count("Results: unavailable") == 5
    for entry in entries:
        assert f"State: {entry.state.value}" in out
    assert "Error: Submission failed" in out
    assert "Objective Executions: 0 of 2" in out
    assert "Success Rate" not in out


async def test_write_async_shows_target_from_available_result(printer, capsys):
    first, second = make_suite_execution_entry(), make_suite_execution_entry(position=2)
    execution = make_suite_execution(entries=[first, second])
    result = make_suite_scenario_result(execution=execution, entry=second)
    result.scenario_identifier = make_scenario_identifier(
        objective_target=ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "test-model", "endpoint": "https://example.com/\x1b[2J"},
        )
    )

    await printer.write_async(execution, results={str(result.id): result})
    out = capsys.readouterr().out

    assert "Target Information" in out
    assert "Target Type: OpenAIChatTarget" in out
    assert "Target Model: test-model" in out
    assert "Target Endpoint: https://example.com/" in out
    assert "\x1b[2J" not in out


async def test_write_async_without_entries(printer, capsys):
    await printer.write_async(make_suite_execution(entries=[], status=ScenarioSuiteExecutionStatus.FAILED))
    out = capsys.readouterr().out

    assert "Status: FAILED" in out
    assert "No suite entries." in out
    assert "Target Type: Unknown" in out
    assert "Target Model: Unknown" in out
    assert "Target Endpoint: Unknown" in out


async def test_write_async_escapes_control_characters(printer, capsys):
    entry = make_suite_execution_entry(state=ScenarioSuiteSubmissionState.SUBMISSION_FAILED, with_run=False)
    entry.error = "failure\x1b]52;clipboard\x07"
    execution = make_suite_execution(entries=[entry])
    execution.suite_name = "unsafe\x1b[2Jname"

    await printer.write_async(execution)
    out = capsys.readouterr().out

    assert "\x1b[2J" not in out
    assert "\x1b]52;" not in out
