# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from datetime import timedelta

import pytest
from unit.mocks import make_scenario_result

from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, ScenarioResult
from pyrit.output.scenario_result.json import (
    JsonScenarioResultMemoryPrinter,
    JsonScenarioResultPrinter,
    build_scenario_conversations_document,
    build_scenario_full_document,
)


def _target_identifier(**params) -> ComponentIdentifier:
    return ComponentIdentifier(class_name="MockTarget", class_module="tests", params=params)


def _attack_result(*, outcome: AttackOutcome = AttackOutcome.SUCCESS, objective: str = "obj") -> AttackResult:
    return AttackResult(conversation_id=str(uuid.uuid4()), objective=objective, outcome=outcome)


def _scenario_result(
    *,
    description: str = "",
    target_params: dict | None = None,
    attack_results: dict[str, list[AttackResult]] | None = None,
    objective_scorer_identifier: ComponentIdentifier | None = None,
    display_group_map: dict[str, str] | None = None,
) -> ScenarioResult:
    return make_scenario_result(
        scenario_name="TestScenario",
        scenario_version=1,
        pyrit_version="1.0.0",
        scenario_description=description,
        objective_target_identifier=_target_identifier(**(target_params or {})),
        attack_results=attack_results or {"technique_a": [_attack_result()]},
        objective_scorer_identifier=objective_scorer_identifier,
        display_group_map=display_group_map or {},
    )


class _StubScorerPrinter:
    """Minimal scorer printer stand-in: returns a fixed scorer block from build()."""

    def build(self, *, scorer_identifier, harm_category=None):
        return {"class_name": scorer_identifier.class_name, "metrics": {"accuracy": 0.9}}


@pytest.fixture
def printer(patch_central_database):
    return JsonScenarioResultPrinter()


# --- overview ---


async def test_overview_reports_scenario_and_stats(printer):
    result = _scenario_result(
        description="a description",
        target_params={"model_name": "gpt-test", "endpoint": "https://example.com"},
        attack_results={
            "technique_a": [
                _attack_result(outcome=AttackOutcome.SUCCESS, objective="obj1"),
                _attack_result(outcome=AttackOutcome.FAILURE, objective="obj2"),
            ],
            "technique_b": [_attack_result(outcome=AttackOutcome.SUCCESS)],
        },
    )
    payload = json.loads(await printer.render_async(result))

    assert payload["view"] == "overview"
    assert payload["scenario"]["name"] == "TestScenario"
    assert payload["scenario"]["id"] == str(result.id)
    assert payload["scenario"]["description"] == "a description"
    assert payload["target"]["type"] == "MockTarget"
    assert payload["target"]["model"] == "gpt-test"
    assert payload["target"]["endpoint"] == "https://example.com"
    assert payload["stats"]["total_techniques"] == 2
    assert payload["stats"]["total_results"] == 3
    assert payload["stats"]["total_attempts"] == 3
    assert payload["stats"]["unique_objectives"] == 3
    assert {g["name"] for g in payload["groups"]} == {"technique_a", "technique_b"}


async def test_overview_prefers_underlying_model_name(printer):
    result = _scenario_result(target_params={"model_name": "deploy", "underlying_model_name": "gpt-4o"})
    payload = json.loads(await printer.render_async(result))
    assert payload["target"]["model"] == "gpt-4o"


async def test_overview_scorer_null_without_scorer(printer):
    result = _scenario_result(objective_scorer_identifier=None)
    payload = json.loads(await printer.render_async(result))
    assert payload["scorer"] is None


async def test_overview_scorer_block_from_injected_printer():
    printer = JsonScenarioResultPrinter(scorer_printer=_StubScorerPrinter())
    result = _scenario_result(objective_scorer_identifier=_target_identifier())
    payload = json.loads(await printer.render_async(result))
    assert payload["scorer"]["class_name"] == "MockTarget"
    assert payload["scorer"]["metrics"] == {"accuracy": 0.9}


async def test_overview_raises_without_scorer_printer_when_scorer_present(printer):
    result = _scenario_result(objective_scorer_identifier=_target_identifier())
    with pytest.raises(ValueError, match="scorer_printer is required"):
        await printer.render_async(result)


# --- attacks ---


async def test_attacks_lists_each_attack(printer):
    a1 = _attack_result(objective="obj-1")
    a2 = _attack_result(outcome=AttackOutcome.FAILURE, objective="obj-2")
    result = _scenario_result(attack_results={"tech_a": [a1], "tech_b": [a2]})

    payload = json.loads(await printer.render_async(result, view="attacks"))

    assert payload["view"] == "attacks"
    assert payload["total"] == 2
    assert payload["shown"] == 2
    ids = {entry["id"] for entry in payload["attacks"]}
    assert ids == {a1.attack_result_id, a2.attack_result_id}
    techniques = {entry["technique"] for entry in payload["attacks"]}
    assert techniques == {"tech_a", "tech_b"}


async def test_attacks_limit_truncates(printer):
    attacks = [_attack_result(objective=f"o{i}") for i in range(3)]
    result = _scenario_result(attack_results={"tech_a": attacks})

    payload = json.loads(await printer.render_async(result, view="attacks", limit=1))

    assert payload["total"] == 3
    assert payload["shown"] == 1
    assert len(payload["attacks"]) == 1


async def test_attacks_filters_by_ids(printer):
    keep = _attack_result(objective="keep")
    drop = _attack_result(objective="drop")
    result = _scenario_result(attack_results={"tech_a": [keep, drop]})

    payload = json.loads(await printer.render_async(result, view="attacks", attack_result_ids=[keep.attack_result_id]))

    assert [entry["id"] for entry in payload["attacks"]] == [keep.attack_result_id]
    assert payload["total"] == 1


async def test_attacks_empty(printer):
    result = _scenario_result(attack_results={"tech_a": []})
    payload = json.loads(await printer.render_async(result, view="attacks"))
    assert payload["attacks"] == []
    assert payload["total"] == 0
    assert payload["shown"] == 0


# --- conversations assembly ---


def test_build_conversations_embeds_messages_per_attack():
    attack = _attack_result(objective="obj-1")
    result = _scenario_result(attack_results={"tech_a": [attack]})
    messages = [{"role": "user", "is_simulated": False, "pieces": [{"data_type": "text"}]}]

    payload = json.loads(build_scenario_conversations_document(result=result, entries=[("tech_a", attack, messages)]))

    assert payload["view"] == "conversations"
    assert payload["scenario_result_id"] == str(result.id)
    entry = payload["conversations"][0]
    assert entry["id"] == attack.attack_result_id
    assert entry["technique"] == "tech_a"
    assert entry["conversation_id"] == attack.conversation_id
    assert entry["messages"] == messages


def test_build_full_document_embeds_overview_and_conversations():
    attack = _attack_result(objective="obj-1")
    result = _scenario_result(attack_results={"tech_a": [attack]})
    overview = {"view": "overview", "stats": {"overall_success_rate": 100}}
    messages = [{"role": "user", "is_simulated": False, "pieces": [{"data_type": "text"}]}]

    payload = json.loads(
        build_scenario_full_document(result=result, overview=overview, entries=[("tech_a", attack, messages)])
    )

    assert payload["view"] == "full"
    assert payload["scenario_result_id"] == str(result.id)
    assert payload["overview"] == overview
    entry = payload["conversations"][0]
    assert entry["technique"] == "tech_a"
    assert entry["messages"] == messages


def test_build_conversations_empty_entries():
    result = _scenario_result()
    payload = json.loads(build_scenario_conversations_document(result=result, entries=[]))
    assert payload["conversations"] == []


# --- memory leaf ---


async def test_memory_printer_constructs_without_args(patch_central_database):
    assert isinstance(JsonScenarioResultMemoryPrinter(), JsonScenarioResultPrinter)


async def test_overview_separates_units_from_attempts(printer):
    retried = [
        AttackResult(conversation_id=str(uuid.uuid4()), objective="obj", outcome=AttackOutcome.ERROR),
        AttackResult(conversation_id=str(uuid.uuid4()), objective="obj", outcome=AttackOutcome.SUCCESS),
    ]
    retried[1].timestamp = retried[0].timestamp + timedelta(seconds=1)
    result = _scenario_result(attack_results={"technique_a": retried})

    payload = json.loads(await printer.render_async(result))

    assert payload["stats"]["total_results"] == 1
    assert payload["stats"]["total_attempts"] == 2
    assert payload["stats"]["overall_success_rate"] == 100
    assert payload["groups"] == [{"name": "technique_a", "num_results": 1, "num_attempts": 2, "success_rate": 100}]
