# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._output formatting helpers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from pyrit.cli import _output

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_cprint_no_color_falls_back_to_print(capsys):
    with patch.object(_output, "_HAS_COLOR", False):
        _output._cprint("plain text", color="red", bold=True)
    captured = capsys.readouterr()
    assert "plain text" in captured.out


def test_cprint_uses_termcolor_when_available(capsys):
    fake_termcolor = MagicMock()
    with (
        patch.object(_output, "_HAS_COLOR", True),
        patch.object(_output, "termcolor", fake_termcolor, create=True),
    ):
        _output._cprint("hello", color="cyan", bold=True)
    fake_termcolor.cprint.assert_called_once_with("hello", "cyan", attrs=["bold"])


def test_cprint_without_color_arg(capsys):
    with patch.object(_output, "_HAS_COLOR", True):
        _output._cprint("plain text")
    captured = capsys.readouterr()
    assert "plain text" in captured.out


def test_header_prints_with_cyan(capsys):
    _output._header("Section Title")
    captured = capsys.readouterr()
    assert "Section Title" in captured.out


def test_wrap_short_text_single_line():
    result = _output._wrap(text="short text", indent="  ")
    assert result == "  short text"


def test_wrap_long_text_breaks_into_multiple_lines():
    text = "word " * 40
    result = _output._wrap(text=text.strip(), indent="    ", width=40)
    assert "\n" in result
    for line in result.split("\n"):
        assert line.startswith("    ")


def test_wrap_empty_text_returns_empty_string():
    assert _output._wrap(text="", indent="  ") == ""


# ---------------------------------------------------------------------------
# print_scenario_list
# ---------------------------------------------------------------------------


def test_print_scenario_list_empty(capsys):
    _output.print_scenario_list(items=[])
    captured = capsys.readouterr()
    assert "No scenarios found." in captured.out


def test_print_scenario_list_full(capsys):
    items = [
        {
            "scenario_name": "airt.scam",
            "scenario_type": "ScamScenario",
            "description": "A test scenario.",
            "aggregate_strategies": ["single_turn"],
            "all_strategies": ["s1", "s2", "s3"],
            "default_strategy": "s1",
            "default_datasets": ["d1", "d2"],
            "max_dataset_size": 50,
            "supported_parameters": [
                {
                    "name": "max_turns",
                    "default": 5,
                    "param_type": "int",
                    "choices": None,
                    "description": "Maximum turns.",
                },
                {
                    "name": "mode",
                    "default": None,
                    "param_type": "str",
                    "choices": ["a", "b"],
                    "description": "Mode.",
                },
            ],
        }
    ]
    _output.print_scenario_list(items=items)
    captured = capsys.readouterr()
    assert "airt.scam" in captured.out
    assert "ScamScenario" in captured.out
    assert "A test scenario." in captured.out
    assert "Aggregate Strategies" in captured.out
    assert "single_turn" in captured.out
    assert "Available Strategies (3)" in captured.out
    assert "Default Strategy: s1" in captured.out
    assert "Default Datasets (2, max 50 per dataset)" in captured.out
    assert "Supported Parameters" in captured.out
    assert "max_turns" in captured.out
    assert "mode" in captured.out
    assert "Total scenarios: 1" in captured.out


def test_print_scenario_list_minimal_fields(capsys):
    items = [{"scenario_name": "min", "scenario_type": "MinScenario"}]
    _output.print_scenario_list(items=items)
    captured = capsys.readouterr()
    assert "min" in captured.out
    assert "MinScenario" in captured.out


def test_print_scenario_list_no_max_dataset_size(capsys):
    items = [
        {
            "scenario_name": "no_max",
            "scenario_type": "T",
            "default_datasets": ["d1"],
        }
    ]
    _output.print_scenario_list(items=items)
    captured = capsys.readouterr()
    assert "Default Datasets (1)" in captured.out
    assert "max" not in captured.out.split("Default Datasets")[1].split("\n")[0]


# ---------------------------------------------------------------------------
# print_initializer_list
# ---------------------------------------------------------------------------


def test_print_initializer_list_empty(capsys):
    _output.print_initializer_list(items=[])
    captured = capsys.readouterr()
    assert "No initializers found." in captured.out


def test_print_initializer_list_full(capsys):
    items = [
        {
            "initializer_name": "openai_target",
            "initializer_type": "OpenAITargetInitializer",
            "required_env_vars": ["OPENAI_API_KEY", "OPENAI_ENDPOINT"],
            "supported_parameters": [
                {"name": "model", "default": "gpt-4", "description": "Model name."},
                {"name": "temp", "default": None, "description": "Temperature."},
            ],
            "description": "Registers OpenAI targets.",
        },
        {
            "initializer_name": "no_env",
            "initializer_type": "NoEnvInitializer",
            "required_env_vars": [],
        },
    ]
    _output.print_initializer_list(items=items)
    captured = capsys.readouterr()
    assert "openai_target" in captured.out
    assert "OPENAI_API_KEY" in captured.out
    assert "OPENAI_ENDPOINT" in captured.out
    assert "Required Environment Variables: None" in captured.out
    assert "model" in captured.out
    assert "Registers OpenAI targets." in captured.out
    assert "Total initializers: 2" in captured.out


# ---------------------------------------------------------------------------
# print_target_list
# ---------------------------------------------------------------------------


def test_print_target_list_empty(capsys):
    _output.print_target_list(items=[])
    captured = capsys.readouterr()
    assert "No targets found in registry" in captured.out
    assert "--initializers target" in captured.out


def test_print_target_list_full(capsys):
    items = [
        {
            "target_registry_name": "openai_chat",
            "target_type": "OpenAIChatTarget",
            "underlying_model_name": "gpt-4",
            "endpoint": "https://example.com",
        },
        {
            "target_registry_name": "claude",
            "target_type": "AnthropicTarget",
            "model_name": "claude-sonnet",
        },
        {
            "target_registry_name": "minimal",
            "target_type": "MinimalTarget",
        },
    ]
    _output.print_target_list(items=items)
    captured = capsys.readouterr()
    assert "openai_chat" in captured.out
    assert "Model: gpt-4" in captured.out
    assert "Endpoint: https://example.com" in captured.out
    assert "Model: claude-sonnet" in captured.out
    assert "minimal" in captured.out
    assert "Total targets: 3" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_run_progress
# ---------------------------------------------------------------------------


def test_print_scenario_run_progress_with_known_totals(capsys):
    run = {
        "status": "RUNNING",
        "total_attacks": 10,
        "completed_attacks": 5,
        "objective_achieved_rate": 30,
        "strategies_used": ["s1", "s2"],
    }
    _output.print_scenario_run_progress(run=run, total_strategies=4)
    captured = capsys.readouterr()
    assert "strategies: 2/4" in captured.out
    assert "5/10" in captured.out
    assert "RUNNING" in captured.out
    assert "30%" in captured.out


def test_print_scenario_run_progress_no_total_attacks(capsys):
    run = {
        "status": "PENDING",
        "total_attacks": 0,
        "completed_attacks": 0,
        "objective_achieved_rate": 0,
        "strategies_used": [],
    }
    _output.print_scenario_run_progress(run=run, total_strategies=0)
    captured = capsys.readouterr()
    assert "attacks: 0" in captured.out
    assert "PENDING" in captured.out


def test_print_scenario_run_progress_strategies_done_only(capsys):
    run = {
        "status": "RUNNING",
        "total_attacks": 0,
        "completed_attacks": 0,
        "objective_achieved_rate": 0,
        "strategies_used": ["s1"],
    }
    _output.print_scenario_run_progress(run=run, total_strategies=0)
    captured = capsys.readouterr()
    assert "strategies: 1" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_run_summary
# ---------------------------------------------------------------------------


def test_print_scenario_run_summary_completed(capsys):
    run = {
        "scenario_name": "test_sc",
        "scenario_result_id": "abc-123",
        "status": "COMPLETED",
        "total_attacks": 5,
        "completed_attacks": 5,
        "objective_achieved_rate": 40,
        "strategies_used": ["s1", "s2"],
    }
    _output.print_scenario_run_summary(run=run)
    captured = capsys.readouterr()
    assert "test_sc" in captured.out
    assert "abc-123" in captured.out
    assert "COMPLETED" in captured.out
    assert "40%" in captured.out
    assert "s1, s2" in captured.out


def test_print_scenario_run_summary_with_error(capsys):
    run = {
        "scenario_name": "failing",
        "scenario_result_id": "id",
        "status": "FAILED",
        "total_attacks": 0,
        "completed_attacks": 0,
        "objective_achieved_rate": 0,
        "error": "boom",
    }
    _output.print_scenario_run_summary(run=run)
    captured = capsys.readouterr()
    assert "Error:" in captured.out
    assert "boom" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_result_async
# ---------------------------------------------------------------------------


async def test_print_scenario_result_async_uses_pretty_printer():
    result_dict = {"some": "data"}
    fake_scenario = MagicMock()
    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()

    with (
        patch(
            "pyrit.models.scenario_result.ScenarioResult.model_validate", return_value=fake_scenario
        ) as model_validate_mock,
        patch(
            "pyrit.output.scenario_result.pretty.PrettyScenarioResultMemoryPrinter", return_value=fake_printer
        ) as printer_cls,
    ):
        await _output.print_scenario_result_async(result_dict=result_dict)

    model_validate_mock.assert_called_once_with(result_dict)
    printer_cls.assert_called_once_with()
    fake_printer.write_async.assert_awaited_once_with(fake_scenario)


async def test_print_scenario_result_async_roundtrip_with_real_payload():
    """
    Integration smoke test: a real ``ScenarioResult.model_dump(mode="json", by_alias=True)``
    payload must flow through ``ScenarioResult.model_validate(...)`` inside
    ``print_scenario_result_async`` without raising. Locks the REST contract used by the CLI
    thin client.
    """
    from datetime import datetime, timezone

    from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier
    from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult

    identifier = ScenarioIdentifier(name="test.scenario", description="A test")
    target_identifier = ComponentIdentifier.from_dict(
        {"__type__": "FakeTarget", "__module__": "test.mod", "params": {}}
    )
    attack = AttackResult(
        conversation_id="conv-1",
        objective="extract data",
        outcome=AttackOutcome.SUCCESS,
        executed_turns=2,
        execution_time_ms=150,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    original = ScenarioResult(
        scenario_identifier=identifier,
        objective_target_identifier=target_identifier,
        objective_scorer_identifier=None,
        attack_results={"strat_a": [attack]},
        scenario_run_state="COMPLETED",
    )
    payload = original.model_dump(mode="json", by_alias=True)

    # Drive print_scenario_result_async through the real model_validate path;
    # only stub the printer to keep the test fast.
    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()
    with patch(
        "pyrit.output.scenario_result.pretty.PrettyScenarioResultMemoryPrinter",
        return_value=fake_printer,
    ):
        await _output.print_scenario_result_async(result_dict=payload)

    fake_printer.write_async.assert_awaited_once()
    reconstructed = fake_printer.write_async.await_args.args[0]
    assert isinstance(reconstructed, ScenarioResult)
    assert reconstructed.scenario_identifier.name == "test.scenario"
    assert list(reconstructed.attack_results.keys()) == ["strat_a"]
    assert reconstructed.attack_results["strat_a"][0].outcome == AttackOutcome.SUCCESS


# ---------------------------------------------------------------------------
# print_scenario_runs_list
# ---------------------------------------------------------------------------


def test_print_scenario_runs_list_empty(capsys):
    _output.print_scenario_runs_list(runs=[])
    captured = capsys.readouterr()
    assert "No scenario runs found." in captured.out


def test_print_scenario_runs_list_populated(capsys):
    runs = [
        {
            "status": "COMPLETED",
            "scenario_name": "scen-a",
            "scenario_result_id": "abcdefgh1234",
            "total_attacks": 4,
            "objective_achieved_rate": 75,
            "created_at": "2024-01-01",
        },
        {
            "status": "RUNNING",
            "scenario_name": "scen-b",
            "scenario_result_id": "ijklmnop5678",
            "total_attacks": 0,
            "objective_achieved_rate": 0,
            "created_at": "2024-02-02",
        },
    ]
    _output.print_scenario_runs_list(runs=runs)
    captured = capsys.readouterr()
    assert "scen-a" in captured.out
    assert "scen-b" in captured.out
    assert "abcdefgh" in captured.out
    assert "Total runs: 2" in captured.out


# ---------------------------------------------------------------------------
# print_error_with_hint
# ---------------------------------------------------------------------------


def test_print_error_with_hint_message_only(capsys):
    _output.print_error_with_hint(message="oops")
    captured = capsys.readouterr()
    assert "Error: oops" in captured.out
    assert "Hint:" not in captured.out


def test_print_error_with_hint_with_hint(capsys):
    _output.print_error_with_hint(message="oops", hint="try this")
    captured = capsys.readouterr()
    assert "Error: oops" in captured.out
    assert "Hint: try this" in captured.out
