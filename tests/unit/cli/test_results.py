# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the ``scenario-results`` payload builders, view policies, and
shared argument parser (``pyrit.cli._results`` and ``pyrit.cli._cli_args``).
"""

import pytest

from pyrit.cli._cli_args import (
    ScenarioResultView,
    add_results_arguments,
    build_scenario_results_parser,
)
from pyrit.cli._results import (
    apply_view_limit_policy,
    resolve_view,
)

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
