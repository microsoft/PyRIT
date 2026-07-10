# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for pyrit.cli._output formatting helpers.

All public ``print_*`` functions accept typed ``pyrit.models`` objects
(``RegisteredScenario``, ``RegisteredInitializer``, ``TargetInstance``,
``ScenarioRunSummary``, ``ScenarioResult``).
"""

from datetime import datetime, timezone
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

from pyrit.cli import _output
from pyrit.models import Parameter, ScenarioRunState, TargetCapabilities, TargetIdentifier
from pyrit.models.catalog import (
    RegisteredInitializer,
    RegisteredScenario,
    ScenarioRunSummary,
    TargetInstance,
)
from unit.mocks import make_scenario_result

# ---------------------------------------------------------------------------
# Typed-object factory helpers
# ---------------------------------------------------------------------------


def _make_scenario(**overrides) -> RegisteredScenario:
    defaults = {
        "scenario_name": "s1",
        "scenario_type": "X",
        "description": "",
        "default_technique": "",
        "aggregate_techniques": [],
        "all_techniques": [],
        "default_datasets": [],
        "supported_parameters": [],
    }
    defaults.update(overrides)
    return RegisteredScenario(**defaults)


def _make_initializer(**overrides) -> RegisteredInitializer:
    defaults = {
        "initializer_name": "i1",
        "initializer_type": "T",
        "description": "",
        "required_env_vars": [],
        "supported_parameters": [],
    }
    defaults.update(overrides)
    return RegisteredInitializer(**defaults)


def _make_target(**overrides) -> TargetInstance:
    """Build a ``TargetInstance``; identity kwargs (``target_type``/``endpoint``/
    ``model_name``/...) are folded into the embedded ``TargetIdentifier``."""
    if "target_type" in overrides:
        overrides["class_name"] = overrides.pop("target_type")
    identifier_kwargs = {
        "class_name": overrides.pop("class_name", "X"),
        "class_module": overrides.pop("class_module", "pyrit.prompt_target"),
    }
    for key in ("endpoint", "model_name", "underlying_model_name", "temperature", "top_p", "max_requests_per_minute"):
        if key in overrides:
            identifier_kwargs[key] = overrides.pop(key)
    defaults = {
        "target_registry_name": "t1",
        "identifier": TargetIdentifier(**identifier_kwargs),
        "capabilities": TargetCapabilities(),
        "target_specific_params": None,
        "inner_targets": None,
    }
    defaults.update(overrides)
    return TargetInstance(**defaults)


def _make_run(**overrides) -> ScenarioRunSummary:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    defaults = {
        "scenario_result_id": "abc-123",
        "scenario_name": "test_sc",
        "scenario_version": 0,
        "status": ScenarioRunState.CREATED,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "error_type": None,
        "techniques_used": [],
        "total_attacks": 0,
        "completed_attacks": 0,
        "objective_achieved_rate": 0,
        "labels": {},
        "completed_at": None,
    }
    defaults.update(overrides)
    return ScenarioRunSummary(**defaults)


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
        _make_scenario(
            scenario_name="airt.scam",
            scenario_type="ScamScenario",
            description="A test scenario.",
            aggregate_techniques=["single_turn"],
            all_techniques=["s1", "s2", "s3"],
            default_technique="s1",
            default_datasets=["d1", "d2"],
            supported_parameters=[
                Parameter(
                    name="max_turns",
                    default=5,
                    param_type=int,
                    description="Maximum turns.",
                ),
                Parameter(
                    name="mode",
                    param_type=Literal["a", "b"],
                    description="Mode.",
                ),
            ],
        )
    ]
    _output.print_scenario_list(items=items)
    captured = capsys.readouterr()
    assert "airt.scam" in captured.out
    assert "ScamScenario" in captured.out
    assert "A test scenario." in captured.out
    assert "Aggregate Techniques" in captured.out
    assert "single_turn" in captured.out
    assert "Available Techniques (3)" in captured.out
    assert "Default Technique: s1" in captured.out
    assert "Default Datasets (2)" in captured.out
    assert "Supported Parameters" in captured.out
    assert "max_turns" in captured.out
    assert "mode" in captured.out
    assert "Total scenarios: 1" in captured.out


def test_print_scenario_list_minimal_fields(capsys):
    items = [_make_scenario(scenario_name="min", scenario_type="MinScenario")]
    _output.print_scenario_list(items=items)
    captured = capsys.readouterr()
    assert "min" in captured.out
    assert "MinScenario" in captured.out


def test_print_scenario_list_no_max_dataset_size(capsys):
    items = [
        _make_scenario(
            scenario_name="no_max",
            scenario_type="T",
            default_datasets=["d1"],
        )
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
        _make_initializer(
            initializer_name="openai_target",
            initializer_type="OpenAITargetInitializer",
            required_env_vars=["OPENAI_API_KEY", "OPENAI_ENDPOINT"],
            supported_parameters=[
                Parameter(name="model", default=["gpt-4"], param_type=list[str], description="Model name."),
                Parameter(name="temp", default=None, param_type=str, description="Temperature."),
            ],
            description="Registers OpenAI targets.",
        ),
        _make_initializer(
            initializer_name="no_env",
            initializer_type="NoEnvInitializer",
            required_env_vars=[],
        ),
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
        _make_target(
            target_registry_name="openai_chat",
            target_type="OpenAIChatTarget",
            underlying_model_name="gpt-4",
            endpoint="https://example.com",
        ),
        _make_target(
            target_registry_name="claude",
            target_type="AnthropicTarget",
            model_name="claude-sonnet",
        ),
        _make_target(
            target_registry_name="minimal",
            target_type="MinimalTarget",
        ),
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
# ---------------------------------------------------------------------------
# print_converter_list
# ---------------------------------------------------------------------------


def test_print_converter_list_empty(capsys):
    _output.print_converter_list(items=[])
    captured = capsys.readouterr()
    assert "No converters found in registry" in captured.out
    assert "converter.translation_spanish" in captured.out


def test_print_converter_list_full(capsys):
    items = [
        {
            "converter_id": "translation_spanish",
            "converter_type": "TranslationConverter",
            "display_name": "Spanish translation",
        },
        {
            "converter_id": "pipeline_1",
            "converter_type": "PromptConverterPipeline",
            "sub_converter_ids": ["base64", "rot13"],
        },
    ]
    _output.print_converter_list(items=items)
    captured = capsys.readouterr()
    assert "translation_spanish" in captured.out
    assert "Class: TranslationConverter" in captured.out
    assert "Name: Spanish translation" in captured.out
    assert "Sub-converters: base64, rot13" in captured.out
    assert "Total converters: 2" in captured.out


# ---------------------------------------------------------------------------
# print_dataset_list
# ---------------------------------------------------------------------------


def test_print_dataset_list_empty(capsys):
    _output.print_dataset_list(items=[])
    captured = capsys.readouterr()
    assert "No datasets found" in captured.out


def test_print_dataset_list_full(capsys):
    items = [
        {"name": "airt_hate"},
        {"name": "harmbench"},
    ]
    _output.print_dataset_list(items=items)
    captured = capsys.readouterr()
    assert "airt_hate" in captured.out
    assert "harmbench" in captured.out
    assert "Total datasets: 2" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_run_progress
# ---------------------------------------------------------------------------


def test_print_scenario_run_progress_with_known_totals(capsys):
    run = _make_run(
        status=ScenarioRunState.IN_PROGRESS,
        total_attacks=10,
        completed_attacks=5,
        objective_achieved_rate=30,
        techniques_used=["s1", "s2"],
    )
    _output.print_scenario_run_progress(run=run, total_techniques=4)
    captured = capsys.readouterr()
    assert "techniques: 2/4" in captured.out
    assert "5/10" in captured.out
    assert "IN_PROGRESS" in captured.out
    assert "30%" in captured.out


def test_print_scenario_run_progress_no_total_attacks(capsys):
    run = _make_run(
        status=ScenarioRunState.CREATED,
        total_attacks=0,
        completed_attacks=0,
        objective_achieved_rate=0,
        techniques_used=[],
    )
    _output.print_scenario_run_progress(run=run, total_techniques=0)
    captured = capsys.readouterr()
    assert "attacks: 0" in captured.out
    assert "CREATED" in captured.out


def test_print_scenario_run_progress_techniques_done_only(capsys):
    run = _make_run(
        status=ScenarioRunState.IN_PROGRESS,
        total_attacks=0,
        completed_attacks=0,
        objective_achieved_rate=0,
        techniques_used=["s1"],
    )
    _output.print_scenario_run_progress(run=run, total_techniques=0)
    captured = capsys.readouterr()
    assert "techniques: 1" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_run_summary
# ---------------------------------------------------------------------------


def test_print_scenario_run_summary_completed(capsys):
    run = _make_run(
        scenario_name="test_sc",
        scenario_result_id="abc-123",
        status=ScenarioRunState.COMPLETED,
        total_attacks=5,
        completed_attacks=5,
        objective_achieved_rate=40,
        techniques_used=["s1", "s2"],
    )
    _output.print_scenario_run_summary(run=run)
    captured = capsys.readouterr()
    assert "test_sc" in captured.out
    assert "abc-123" in captured.out
    assert "COMPLETED" in captured.out
    assert "40%" in captured.out
    assert "s1, s2" in captured.out


def test_print_scenario_run_summary_with_error(capsys):
    run = _make_run(
        scenario_name="failing",
        scenario_result_id="id",
        status=ScenarioRunState.FAILED,
        total_attacks=0,
        completed_attacks=0,
        objective_achieved_rate=0,
        error="boom",
    )
    _output.print_scenario_run_summary(run=run)
    captured = capsys.readouterr()
    assert "Error:" in captured.out
    assert "boom" in captured.out


# ---------------------------------------------------------------------------
# print_scenario_result_async
# ---------------------------------------------------------------------------


async def test_print_scenario_result_async_uses_pretty_printer():
    """``print_scenario_result_async`` hands the typed ``ScenarioResult`` to the pretty printer."""
    fake_scenario = MagicMock()
    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()

    with patch(
        "pyrit.output.scenario_result.pretty.PrettyScenarioResultMemoryPrinter",
        return_value=fake_printer,
    ) as printer_cls:
        await _output.print_scenario_result_async(result=fake_scenario)

    printer_cls.assert_called_once_with()
    fake_printer.write_async.assert_awaited_once_with(fake_scenario)


async def test_print_scenario_result_async_accepts_real_scenario_result():
    """A real ``ScenarioResult`` instance flows through ``print_scenario_result_async``."""
    from pyrit.models import (
        AttackOutcome,
        AttackResult,
        ComponentIdentifier,
    )

    target_identifier = ComponentIdentifier.model_validate(
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
    scenario_result = make_scenario_result(
        scenario_name="test.scenario",
        scenario_description="A test",
        objective_target_identifier=target_identifier,
        objective_scorer_identifier=None,
        attack_results={"strat_a": [attack]},
        scenario_run_state=ScenarioRunState.COMPLETED,
    )

    fake_printer = MagicMock()
    fake_printer.write_async = AsyncMock()
    with patch(
        "pyrit.output.scenario_result.pretty.PrettyScenarioResultMemoryPrinter",
        return_value=fake_printer,
    ):
        await _output.print_scenario_result_async(result=scenario_result)

    fake_printer.write_async.assert_awaited_once_with(scenario_result)


# ---------------------------------------------------------------------------
# print_scenario_runs_list
# ---------------------------------------------------------------------------


def test_print_scenario_runs_list_empty(capsys):
    _output.print_scenario_runs_list(runs=[])
    captured = capsys.readouterr()
    assert "No scenario runs found." in captured.out


def test_print_scenario_runs_list_populated(capsys):
    runs = [
        _make_run(
            status=ScenarioRunState.COMPLETED,
            scenario_name="scen-a",
            scenario_result_id="abcdefgh1234",
            total_attacks=4,
            objective_achieved_rate=75,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        _make_run(
            status=ScenarioRunState.IN_PROGRESS,
            scenario_name="scen-b",
            scenario_result_id="ijklmnop5678",
            total_attacks=0,
            objective_achieved_rate=0,
            created_at=datetime(2024, 2, 2, tzinfo=timezone.utc),
        ),
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
