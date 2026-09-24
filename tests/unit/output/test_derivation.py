# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from types import SimpleNamespace

from unit.mocks import make_scenario_result

from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, Score, ScoreStatus
from pyrit.output._derivation import (
    attack_score_display,
    resolve_scorer_name,
    resolve_target_info,
    scenario_success_rates,
    select_attacks,
)


def _target(**params) -> ComponentIdentifier:
    return ComponentIdentifier(class_name="MockTarget", class_module="tests", params=params)


def _attack(*, outcome: AttackOutcome = AttackOutcome.SUCCESS, conversation_id: str = "c") -> AttackResult:
    return AttackResult(conversation_id=conversation_id, objective="obj", outcome=outcome)


# --- resolve_target_info ---


def test_resolve_target_info_none():
    assert resolve_target_info(None) == (None, None, None)


def test_resolve_target_info_prefers_underlying_model_name():
    info = resolve_target_info(_target(underlying_model_name="gpt-x", model_name="fallback", endpoint="https://e"))
    assert info.type == "MockTarget"
    assert info.model == "gpt-x"
    assert info.endpoint == "https://e"


def test_resolve_target_info_falls_back_to_model_name():
    assert resolve_target_info(_target(model_name="gpt-y")).model == "gpt-y"


def test_resolve_target_info_missing_fields_are_none():
    info = resolve_target_info(_target())
    assert info.model is None
    assert info.endpoint is None


# --- scenario_success_rates ---


def test_scenario_success_rates_empty_is_zero():
    result = make_scenario_result(scenario_name="S", attack_results={"s1": []})
    assert scenario_success_rates(result) == (0, {})


def test_scenario_success_rates_per_display_group():
    result = make_scenario_result(
        scenario_name="S",
        attack_results={
            "base64": [
                AttackResult(conversation_id="c1", objective="o1", outcome=AttackOutcome.SUCCESS),
                AttackResult(conversation_id="c2", objective="o2", outcome=AttackOutcome.FAILURE),
            ],
            "rot13": [AttackResult(conversation_id="c3", objective="o1", outcome=AttackOutcome.SUCCESS)],
        },
        display_group_map={"base64": "encoding", "rot13": "encoding"},
    )
    assert scenario_success_rates(result) == (66, {"encoding": 66})


# --- attack_score_display ---


def test_attack_score_display_no_score_returns_none_value():
    attack = SimpleNamespace(last_score=None)
    assert attack_score_display(attack) is None
    assert attack_score_display(attack, none_value="-") == "-"


def test_attack_score_display_returns_value():
    attack = SimpleNamespace(last_score=Score(score_type="float_scale", score_value="0.42"))
    assert attack_score_display(attack) == "0.42"


def test_attack_score_display_falls_back_to_status():
    score = Score(score_type="true_false", score_value=None, status=ScoreStatus.UNDETERMINED)
    attack = SimpleNamespace(last_score=score)
    assert attack_score_display(attack) == ScoreStatus.UNDETERMINED.value


# --- select_attacks ---


def test_select_attacks_returns_all_pairs():
    a1, a2 = _attack(conversation_id="1"), _attack(conversation_id="2")
    result = make_scenario_result(attack_results={"tech": [a1, a2]})
    assert select_attacks(result) == [("tech", a1), ("tech", a2)]


def test_select_attacks_filters_by_id():
    a1, a2 = _attack(conversation_id="1"), _attack(conversation_id="2")
    result = make_scenario_result(attack_results={"tech": [a1, a2]})
    selected = select_attacks(result, attack_result_ids=[a2.attack_result_id])
    assert selected == [("tech", a2)]


# --- resolve_scorer_name ---


def test_resolve_scorer_name_present():
    score = Score(
        score_type="true_false",
        score_value="true",
        scorer_class_identifier=ComponentIdentifier(class_name="MockScorer", class_module="tests"),
    )
    assert resolve_scorer_name(score) == "MockScorer"


def test_resolve_scorer_name_absent_returns_none_value():
    score = Score(score_type="true_false", score_value="true")
    assert resolve_scorer_name(score) is None
    assert resolve_scorer_name(score, none_value="Unknown") == "Unknown"
