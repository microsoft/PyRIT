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
    resolve_output_sink,
    resolve_view,
    warn_if_view_ignored_by_html,
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
    # Advisory notices go to stderr so stdout stays a clean document.
    assert "no effect" in capsys.readouterr().err


def test_limit_policy_keeps_limit_for_attacks(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.ATTACKS, limit=5)
    assert effective == 5
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_limit_policy_noop_when_no_limit(capsys):
    assert apply_view_limit_policy(view=ScenarioResultView.OVERVIEW, limit=None) is None
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


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
    assert "at most 5" in capsys.readouterr().err


def test_limit_policy_heavy_view_respects_explicit_limit(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.FULL, limit=3)
    assert effective == 3
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_limit_policy_heavy_view_respects_attack_ids(capsys):
    effective = apply_view_limit_policy(view=ScenarioResultView.CONVERSATIONS, limit=None, attack_result_ids=["a"])
    assert effective is None
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# ---------------------------------------------------------------------------
# warn_if_view_ignored_by_html
# ---------------------------------------------------------------------------


def test_html_warns_on_explicit_non_full_view(capsys):
    warn_if_view_ignored_by_html(view=ScenarioResultView.OVERVIEW)
    assert "--view overview is ignored with --format html" in capsys.readouterr().err


def test_html_silent_when_view_omitted(capsys):
    warn_if_view_ignored_by_html(view=None)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_html_silent_for_explicit_full_view(capsys):
    warn_if_view_ignored_by_html(view=ScenarioResultView.FULL)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# ---------------------------------------------------------------------------
# resolve_output_sink / --output flag
# ---------------------------------------------------------------------------


def test_resolve_output_sink_none_for_stdout():
    assert resolve_output_sink(output_path=None, output_format="json") is None
    assert resolve_output_sink(output_path=None, output_format="pretty") is None


def test_resolve_output_sink_json_returns_file_sink(tmp_path):
    from pyrit.output.sink import FileSink

    sink = resolve_output_sink(output_path=str(tmp_path / "out.json"), output_format="json")
    assert isinstance(sink, FileSink)


def test_resolve_output_sink_rejects_pretty(tmp_path):
    with pytest.raises(ValueError, match="requires --format json"):
        resolve_output_sink(output_path=str(tmp_path / "out.txt"), output_format="pretty")


def test_resolve_output_sink_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError, match="directory does not exist"):
        resolve_output_sink(output_path=str(tmp_path / "nope" / "out.json"), output_format="json")


def test_resolve_output_sink_html_requires_output():
    with pytest.raises(ValueError, match="requires --output"):
        resolve_output_sink(output_path=None, output_format="html")


def test_resolve_output_sink_html_returns_file_sink(tmp_path):
    from pyrit.output.sink import FileSink

    sink = resolve_output_sink(output_path=str(tmp_path / "report.html"), output_format="html")
    assert isinstance(sink, FileSink)


def test_output_flag_parses_via_short_and_long(tmp_path):
    parser = build_scenario_results_parser()
    assert parser.parse_args(["rid", "-o", "a.json"]).output == "a.json"
    assert parser.parse_args(["rid", "--output", "b.json"]).output == "b.json"
    assert parser.parse_args(["rid"]).output is None
