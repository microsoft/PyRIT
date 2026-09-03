# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the ``scenario-results`` payload builders, view policies, and
shared argument parser (``pyrit.cli._results`` and ``pyrit.cli._cli_args``).
"""

import uuid

import pytest

from pyrit.cli._cli_args import (
    ScenarioResultView,
    add_results_arguments,
    build_scenario_results_parser,
)
from pyrit.cli._results import (
    _to_transcript_score,
    apply_view_limit_policy,
    build_attacks_table_payload,
    build_conversations_payload_async,
    resolve_view,
)
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, Score
from unit.mocks import make_scenario_result


def _scorer_id(class_name):
    return ComponentIdentifier(class_name=class_name, class_module="tests.unit.mocks")


def _attack(*, outcome=AttackOutcome.SUCCESS, objective="obj", turns=1, with_score=False):
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
        executed_turns=turns,
    )
    if with_score:
        attack.last_score = Score(
            score_value="0.9",
            score_type="float_scale",
            message_piece_id=str(uuid.uuid4()),
        )
    return attack


def _result(attack_results, *, objective_scorer=None):
    return make_scenario_result(attack_results=attack_results, objective_scorer_identifier=objective_scorer)


def test_to_transcript_score_uses_status_for_null_value():
    score = _to_transcript_score(
        score={
            "score_value": None,
            "status": "undetermined",
            "score_type": "TrueFalseCompositeScorer",
        }
    )

    assert score.value == "undetermined"


class _FakeMessagesClient:
    """A minimal stand-in for ``PyRITApiClient.get_conversation_messages_async``."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    async def get_conversation_messages_async(self, *, attack_result_id, conversation_id):
        self.calls.append((attack_result_id, conversation_id))
        return self._responses.get(conversation_id, {"messages": []})


# ---------------------------------------------------------------------------
# ScenarioResultView
# ---------------------------------------------------------------------------


def test_scenario_result_view_values():
    assert ScenarioResultView.OVERVIEW.value == "overview"
    assert ScenarioResultView.ATTACKS.value == "attacks"


# ---------------------------------------------------------------------------
# resolve_view
# ---------------------------------------------------------------------------


def test_resolve_view_defaults_to_overview_when_omitted():
    assert resolve_view(view=None) is ScenarioResultView.OVERVIEW


def test_resolve_view_passes_through_explicit_value():
    assert resolve_view(view=ScenarioResultView.ATTACKS) is ScenarioResultView.ATTACKS


# ---------------------------------------------------------------------------
# apply_view_limit_policy
# ---------------------------------------------------------------------------


def test_limit_policy_drops_and_warns_for_overview(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.OVERVIEW, limit=5)
    assert effective is None
    assert "no effect" in capsys.readouterr().out


def test_limit_policy_keeps_limit_for_attacks(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.ATTACKS, limit=5)
    assert effective == 5
    assert capsys.readouterr().out == ""


def test_limit_policy_noop_when_no_limit(capsys):
    assert apply_view_limit_policy(view=ScenarioResultView.OVERVIEW, limit=None) is None
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# build_attacks_table_payload
# ---------------------------------------------------------------------------


def test_builder_includes_all_attacks_grouped_by_atomic_name():
    result = _result(
        {
            "tech_a": [_attack(objective="a1"), _attack(objective="a2")],
            "tech_b": [_attack(objective="b1")],
        }
    )
    payload = build_attacks_table_payload(result=result, scenario_result_id="SID")
    assert payload.scenario_result_id == "SID"
    assert payload.total == 3
    assert len(payload.rows) == 3
    assert {row.atomic_attack_name for row in payload.rows} == {"tech_a", "tech_b"}


def test_builder_maps_outcome_and_score():
    result = _result(
        {
            "tech_a": [
                _attack(outcome=AttackOutcome.SUCCESS, turns=4, with_score=True),
                _attack(outcome=AttackOutcome.FAILURE, with_score=False),
            ]
        }
    )
    payload = build_attacks_table_payload(result=result, scenario_result_id="SID")
    scored, unscored = payload.rows[0], payload.rows[1]
    assert scored.outcome == "success"
    assert scored.executed_turns == 4
    assert scored.score_value == "0.9"
    assert unscored.outcome == "failure"
    assert unscored.score_value is None


def test_builder_filters_by_attack_result_ids():
    keep = _attack(objective="keep")
    drop = _attack(objective="drop")
    result = _result({"tech_a": [keep, drop]})
    payload = build_attacks_table_payload(
        result=result,
        scenario_result_id="SID",
        attack_result_ids=[keep.attack_result_id],
    )
    assert payload.total == 1
    assert payload.rows[0].attack_result_id == keep.attack_result_id


def test_builder_limit_caps_rows_but_total_is_pre_limit():
    result = _result({"tech_a": [_attack() for _ in range(5)]})
    payload = build_attacks_table_payload(result=result, scenario_result_id="SID", limit=2)
    assert payload.total == 5
    assert len(payload.rows) == 2


def test_builder_handles_no_attacks():
    payload = build_attacks_table_payload(result=_result({}), scenario_result_id="SID")
    assert payload.total == 0
    assert payload.rows == []


# ---------------------------------------------------------------------------
# Shared argument parser
# ---------------------------------------------------------------------------


def test_shell_parser_parses_id_and_flags():
    parser = build_scenario_results_parser()
    parsed = parser.parse_args(["SID", "--view", "attacks", "--attack-result-ids", "x", "y", "--limit", "3"])
    assert parsed.scenario_result_id == "SID"
    assert parsed.view is ScenarioResultView.ATTACKS
    assert parsed.attack_result_ids == ["x", "y"]
    assert parsed.limit == 3


def test_shell_parser_view_defaults_to_none_when_omitted():
    parser = build_scenario_results_parser()
    parsed = parser.parse_args(["SID"])
    assert parsed.view is None
    assert parsed.attack_result_ids is None
    assert parsed.limit is None


def test_shell_parser_rejects_unknown_view(capsys):
    parser = build_scenario_results_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["SID", "--view", "bogus"])
    err = capsys.readouterr().err
    assert "choose from overview, attacks" in err


def test_parse_scenario_result_view_valid_and_invalid():
    import argparse

    from pyrit.cli._cli_args import parse_scenario_result_view

    assert parse_scenario_result_view("attacks") is ScenarioResultView.ATTACKS
    with pytest.raises(argparse.ArgumentTypeError, match="choose from overview, attacks"):
        parse_scenario_result_view("nope")


def test_shell_parser_rejects_non_positive_limit():
    parser = build_scenario_results_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["SID", "--limit", "0"])


def test_add_results_arguments_registers_view_flags():
    import argparse

    parser = argparse.ArgumentParser()
    add_results_arguments(parser=parser)
    parsed = parser.parse_args(["--view", "attacks", "--attack-result-ids", "a", "b", "--limit", "3"])
    assert parsed.view is ScenarioResultView.ATTACKS
    assert parsed.attack_result_ids == ["a", "b"]
    assert parsed.limit == 3


# ---------------------------------------------------------------------------
# conversations / full views
# ---------------------------------------------------------------------------


def test_scenario_result_view_values_conversations_and_full():
    assert ScenarioResultView.CONVERSATIONS.value == "conversations"
    assert ScenarioResultView.FULL.value == "full"


def test_resolve_view_passes_through_conversations():
    assert resolve_view(view=ScenarioResultView.CONVERSATIONS) is ScenarioResultView.CONVERSATIONS


def test_limit_policy_defaults_heavy_view_when_unscoped(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.CONVERSATIONS, limit=None)
    assert effective == 5
    assert "at most 5" in capsys.readouterr().out


def test_limit_policy_heavy_view_respects_explicit_limit(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.FULL, limit=3)
    assert effective == 3
    assert capsys.readouterr().out == ""


def test_limit_policy_heavy_view_respects_attack_ids(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.CONVERSATIONS, limit=None, attack_result_ids=["a"])
    assert effective is None
    assert capsys.readouterr().out == ""


async def test_build_conversations_payload_selects_objective_score_by_hash():
    # A response carries several scores; only the objective scorer's verdict should
    # surface — matched by identity hash, not by position in the list.
    objective = _scorer_id("TrueFalseCompositeScorer")
    auxiliary = _scorer_id("SelfAskRefusalScorer")
    attack = _attack(objective="a1")
    result = _result({"tech_a": [attack]}, objective_scorer=objective)
    messages_payload = {
        "conversation_id": attack.conversation_id,
        "messages": [
            {"role": "user", "turn_number": 0, "message_pieces": [{"converted_value": "hi"}]},
            {
                "role": "assistant",
                "turn_number": 1,
                "message_pieces": [
                    {
                        "converted_value": "there",
                        "scores": [
                            {
                                "score_value": "true",
                                "score_rationale": "refused",
                                "scorer_type": "SelfAskRefusalScorer",
                                "scorer_class_identifier": {"hash": auxiliary.hash},
                            },
                            {
                                "score_value": "false",
                                "score_rationale": "complied",
                                "scorer_type": "TrueFalseCompositeScorer",
                                "scorer_class_identifier": {"hash": objective.hash},
                            },
                        ],
                    }
                ],
            },
        ],
    }
    client = _FakeMessagesClient({attack.conversation_id: messages_payload})

    payload = await build_conversations_payload_async(result=result, client=client, scenario_result_id="SID")

    assert payload.scenario_result_id == "SID"
    assert payload.total == 1
    convo = payload.conversations[0]
    assert convo.attack_result_id == attack.attack_result_id
    assert convo.atomic_attack_name == "tech_a"
    assert [message.text for message in convo.messages] == ["hi", "there"]
    assert convo.messages[0].score is None
    # The objective (composite) score is surfaced, not the first (refusal) score.
    assert convo.messages[1].score.value == "false"
    assert convo.messages[1].score.rationale == "complied"
    assert convo.messages[1].score.scorer == "TrueFalseCompositeScorer"


async def test_build_conversations_payload_falls_back_to_class_name():
    # No identity hash on the stored score — fall back to matching the class name.
    objective = _scorer_id("TrueFalseCompositeScorer")
    attack = _attack(objective="a1")
    result = _result({"tech_a": [attack]}, objective_scorer=objective)
    messages_payload = {
        "conversation_id": attack.conversation_id,
        "messages": [
            {
                "role": "assistant",
                "turn_number": 1,
                "message_pieces": [
                    {
                        "converted_value": "there",
                        "scores": [
                            {"score_value": "true", "scorer_type": "SelfAskRefusalScorer"},
                            {"score_value": "false", "scorer_type": "TrueFalseCompositeScorer"},
                        ],
                    }
                ],
            },
        ],
    }
    client = _FakeMessagesClient({attack.conversation_id: messages_payload})

    payload = await build_conversations_payload_async(result=result, client=client, scenario_result_id="SID")

    score = payload.conversations[0].messages[0].score
    assert score.value == "false"
    assert score.scorer == "TrueFalseCompositeScorer"


async def test_build_conversations_payload_no_objective_scorer_yields_no_score():
    # Without a scenario objective scorer there's no canonical score to surface.
    attack = _attack(objective="a1")
    result = _result({"tech_a": [attack]})
    messages_payload = {
        "conversation_id": attack.conversation_id,
        "messages": [
            {
                "role": "assistant",
                "turn_number": 1,
                "message_pieces": [
                    {"converted_value": "there", "scores": [{"score_value": "true", "scorer_type": "X"}]}
                ],
            },
        ],
    }
    client = _FakeMessagesClient({attack.conversation_id: messages_payload})

    payload = await build_conversations_payload_async(result=result, client=client, scenario_result_id="SID")

    assert payload.conversations[0].messages[0].score is None


async def test_build_conversations_payload_filters_by_ids():
    keep = _attack(objective="keep")
    drop = _attack(objective="drop")
    result = _result({"tech_a": [keep, drop]})
    client = _FakeMessagesClient()

    payload = await build_conversations_payload_async(
        result=result,
        client=client,
        scenario_result_id="SID",
        attack_result_ids=[keep.attack_result_id],
    )

    assert payload.total == 1
    assert client.calls == [(keep.attack_result_id, keep.conversation_id)]


async def test_build_conversations_payload_limit_gates_fetch():
    attacks = [_attack(objective=f"o{i}") for i in range(4)]
    result = _result({"tech_a": attacks})
    client = _FakeMessagesClient()

    payload = await build_conversations_payload_async(result=result, client=client, scenario_result_id="SID", limit=2)

    assert payload.total == 4
    assert len(payload.conversations) == 2
    # --limit caps the number of message fetches, not just the rendered rows.
    assert len(client.calls) == 2
