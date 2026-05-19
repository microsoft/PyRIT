# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

from pyrit.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.attack_result import AttackOutcome, AttackResult
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult


def _make_scenario_identifier(**kwargs):
    defaults = {"name": "TestScenario", "description": "A test", "scenario_version": 1}
    defaults.update(kwargs)
    return ScenarioIdentifier(**defaults)


def _make_component_identifier_dict(class_name="TestTarget"):
    return ComponentIdentifier.from_dict({"__type__": class_name, "__module__": "test.module", "params": {}})


def _make_attack_result(*, objective="test objective", outcome=AttackOutcome.SUCCESS):
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
    )


class TestScenarioIdentifier:
    def test_init_basic(self):
        si = ScenarioIdentifier(name="MySc")
        assert si.name == "MySc"
        assert si.description == ""
        assert si.version == 1
        assert si.init_data is None

    def test_init_with_all_params(self):
        si = ScenarioIdentifier(
            name="MySc",
            description="desc",
            scenario_version=2,
            init_data={"key": "val"},
            pyrit_version="1.0.0",
        )
        assert si.version == 2
        assert si.init_data == {"key": "val"}
        assert si.pyrit_version == "1.0.0"

    def test_init_default_pyrit_version(self):
        import pyrit

        si = ScenarioIdentifier(name="X")
        assert si.pyrit_version == pyrit.__version__


class TestScenarioResult:
    def test_init_basic(self):
        si = _make_scenario_identifier()
        target_id = _make_component_identifier_dict()
        scorer_id = _make_component_identifier_dict("TestScorer")
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=target_id,
            attack_results={"strat1": []},
            objective_scorer_identifier=scorer_id,
        )
        assert result.scenario_identifier is si
        assert result.scenario_run_state == "CREATED"
        assert result.labels == {}
        assert result.number_tries == 0
        assert isinstance(result.id, uuid.UUID)

    def test_init_with_explicit_id(self):
        si = _make_scenario_identifier()
        explicit_id = uuid.uuid4()
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
            id=explicit_id,
        )
        assert result.id == explicit_id

    def test_get_strategies_used(self):
        si = _make_scenario_identifier()
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"crescendo": [], "flip": []},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        strategies = result.get_strategies_used()
        assert sorted(strategies) == ["crescendo", "flip"]

    def test_get_objectives_all(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        ar3 = _make_attack_result(objective="obj1")
        result = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": [ar1, ar3], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        objectives = result.get_objectives()
        assert sorted(objectives) == ["obj1", "obj2"]

    def test_get_objectives_by_attack_name(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        result = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": [ar1], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert result.get_objectives(atomic_attack_name="s1") == ["obj1"]
        assert result.get_objectives(atomic_attack_name="nonexistent") == []

    def test_objective_achieved_rate_all(self):
        results = [
            _make_attack_result(outcome=AttackOutcome.SUCCESS),
            _make_attack_result(outcome=AttackOutcome.FAILURE),
            _make_attack_result(outcome=AttackOutcome.SUCCESS),
            _make_attack_result(outcome=AttackOutcome.UNDETERMINED),
        ]
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": results},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.objective_achieved_rate() == 50

    def test_objective_achieved_rate_empty(self):
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": []},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.objective_achieved_rate() == 0

    def test_objective_achieved_rate_by_name(self):
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={
                "s1": [_make_attack_result(outcome=AttackOutcome.SUCCESS)],
                "s2": [_make_attack_result(outcome=AttackOutcome.FAILURE)],
            },
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.objective_achieved_rate(atomic_attack_name="s1") == 100
        assert sr.objective_achieved_rate(atomic_attack_name="s2") == 0
        assert sr.objective_achieved_rate(atomic_attack_name="missing") == 0

    def test_normalize_scenario_name_snake_case(self):
        assert ScenarioResult.normalize_scenario_name("content_harms") == "ContentHarms"
        assert ScenarioResult.normalize_scenario_name("foundry") == "foundry"

    def test_normalize_scenario_name_already_pascal(self):
        assert ScenarioResult.normalize_scenario_name("ContentHarms") == "ContentHarms"

    def test_normalize_scenario_name_mixed_case_with_underscore(self):
        assert ScenarioResult.normalize_scenario_name("Content_harms") == "Content_harms"
