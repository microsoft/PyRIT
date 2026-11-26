# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A baseline must never dilute the attack success denominator in reports."""

import json

import pytest
from unit.mocks import make_suite_execution, make_suite_execution_entry, make_suite_scenario_result

from pyrit.models import SCENARIO_RUN_PLAN_METADATA_KEY, AttackOutcome
from pyrit.output._derivation import scenario_overview, suite_entry_statistics
from pyrit.output.scenario_result.html import HtmlScenarioReportPrinter
from pyrit.output.scenario_result.json import JsonScenarioResultPrinter
from pyrit.output.scenario_result.pretty import PrettyScenarioResultPrinter
from pyrit.output.scenario_suite_result.json import JsonScenarioSuiteResultPrinter
from pyrit.output.scenario_suite_result.pretty import PrettyScenarioSuiteResultPrinter


def _result(*, baseline_only=False, shared_name=False):
    entry = make_suite_execution_entry()
    execution = make_suite_execution(entries=[entry])
    result = make_suite_scenario_result(
        execution=execution,
        entry=entry,
        outcomes=[("A", AttackOutcome.SUCCESS), ("B", AttackOutcome.FAILURE)],
        planned_objectives=["A", "B"],
    )
    plan = result.metadata[SCENARIO_RUN_PLAN_METADATA_KEY]
    original = plan["atomic_groups"][0]
    original["kind"] = "attack"
    baseline = {
        **original,
        "id": "control-group",
        "atomic_attack_name": "attack" if shared_name else "control",
        "technique_eval_hash": "control-eval",
        "kind": "baseline",
        "display_group": "Unmodified",
    }
    control = result.attack_results["attack"][1].model_copy(deep=True)
    control.attribution_data = {
        **control.attribution_data,
        "parent_collection": baseline["atomic_attack_name"],
        "parent_eval_hash": "control-eval",
    }
    if baseline_only:
        result.attack_results = {baseline["atomic_attack_name"]: [control]}
        plan["atomic_groups"] = [baseline]
    else:
        result.attack_results["attack"] = [result.attack_results["attack"][0]]
        if shared_name:
            result.attack_results["attack"].append(control)
        else:
            result.attack_results["control"] = [control]
        plan["atomic_groups"] = [original, baseline]
    return execution, result


@pytest.mark.parametrize("shared_name", [False, True])
async def test_mixed_populations_keep_baseline_out_of_attack_rate(shared_name):
    execution, result = _result(shared_name=shared_name)
    before = result.model_dump(mode="json")
    overview = scenario_overview(result)
    assert (overview.objective_executions, overview.attempts, overview.success_rate) == (1, 1, 100)
    assert overview.baseline.completed == 1
    assert overview.baseline.failures == 1
    assert overview.baseline.planned == 2
    assert suite_entry_statistics(result).planned == 2
    assert sum(group.objective_executions for group in overview.groups) == 2
    payload = json.loads(await JsonScenarioResultPrinter().render_async(result))
    assert payload["stats"]["overall_success_rate"] == 100
    assert payload["baseline_statistics"]["success_percentage"] == 0
    text = await PrettyScenarioResultPrinter(enable_colors=False).render_async(result)
    assert "Overall Success Rate: 100%" in text
    assert "Baseline Statistics" in text
    results = {str(result.id): result}
    suite = json.loads(await JsonScenarioSuiteResultPrinter().render_async(execution, results=results))
    assert suite["entries"][0]["statistics"]["success_percentage"] == 100
    assert suite["entries"][0]["baseline_statistics"]["failures"] == 1
    suite_text = await PrettyScenarioSuiteResultPrinter(enable_colors=False).render_async(execution, results=results)
    assert "Success Rate: 100%" in suite_text
    assert "Baseline Success Rate: 0%" in suite_text
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("baseline_outcome", [AttackOutcome.SUCCESS, AttackOutcome.FAILURE])
async def test_baseline_only_is_not_an_attack_failure(baseline_outcome):
    execution, result = _result(baseline_only=True)
    result.attack_results["control"][0].outcome = baseline_outcome
    counts = suite_entry_statistics(result)
    assert counts.completed == 0
    assert counts.planned == 0
    assert counts.success_percentage is None
    assert scenario_overview(result).baseline.completed == 1
    scenario = json.loads(await JsonScenarioResultPrinter().render_async(result))
    assert scenario["stats"]["overall_success_rate"] is None
    assert scenario["stats"]["total_objective_executions"] == 0
    baseline_rate = 100 if baseline_outcome == AttackOutcome.SUCCESS else 0
    assert scenario["baseline_statistics"]["success_percentage"] == baseline_rate
    text = await PrettyScenarioResultPrinter(enable_colors=False).render_async(result)
    assert "Overall Success Rate: N/A" in text
    assert f"Success Rate: {baseline_rate}%" in text.split("Baseline Statistics", 1)[1]
    html = await HtmlScenarioReportPrinter().render_async(
        {"scenario_result_id": str(result.id), "overview": scenario, "conversations": []}
    )
    assert ">N/A</div>" in html
    assert "None%" not in html
    payload = json.loads(
        await JsonScenarioSuiteResultPrinter().render_async(execution, results={str(result.id): result})
    )
    assert payload["entries"][0]["statistics"]["success_percentage"] is None
    assert payload["entries"][0]["baseline_statistics"]["completed"] == 1


def test_legacy_name_does_not_invent_baseline_identity():
    _, result = _result()
    result.metadata.pop(SCENARIO_RUN_PLAN_METADATA_KEY)
    result.attack_results["baseline"] = result.attack_results.pop("control")
    overview = scenario_overview(result)
    assert overview.baseline is None
    assert overview.objective_executions == 2
    assert overview.success_rate == 50


def test_plan_without_kinds_keeps_historical_counts():
    _, result = _result()
    for group in result.metadata[SCENARIO_RUN_PLAN_METADATA_KEY]["atomic_groups"]:
        group.pop("kind")
    overview = scenario_overview(result)
    assert overview.baseline is None
    assert overview.objective_executions == 2
    assert overview.success_rate == 50


def test_shared_name_missing_hash_remains_unattributed():
    _, result = _result(shared_name=True)
    rows = result.attack_results["attack"]
    ambiguous = rows[0].model_copy(deep=True)
    ambiguous.attribution_data.pop("parent_eval_hash")
    ambiguous.outcome = AttackOutcome.FAILURE
    ambiguous.timestamp = ambiguous.timestamp.replace(year=ambiguous.timestamp.year + 1)
    result.attack_results["attack"].append(ambiguous)
    before = result.model_dump(mode="json")
    overview = scenario_overview(result)
    assert overview.objective_executions == 1
    assert overview.success_rate == 100
    assert overview.baseline.completed == 1
    assert overview.baseline.failures == 1
    assert suite_entry_statistics(result).success_percentage == 100
    assert result.model_dump(mode="json") == before
