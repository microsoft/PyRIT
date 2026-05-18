# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.identifiers.component_identifier import ComponentIdentifier
from pyrit.models import AttackOutcome, AttackResult
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter


def _scenario_identifier(*, name: str = "TestScenario", description: str = "") -> ScenarioIdentifier:
    return ScenarioIdentifier(name=name, description=description, scenario_version=1, pyrit_version="1.0.0")


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
    return ScenarioResult(
        scenario_identifier=_scenario_identifier(description=description),
        objective_target_identifier=_target_identifier(**(target_params or {})),
        attack_results=attack_results or {"strategy_a": [_attack_result()]},
        objective_scorer_identifier=objective_scorer_identifier,
        display_group_map=display_group_map,
    )


@pytest.fixture
def printer(patch_central_database):
    return PrettyScenarioResultMemoryPrinter(width=100, indent_size=2, enable_colors=False)


# --- write_async ---


async def test_write_async_renders_full_summary(printer, capsys):
    result = _scenario_result(
        description="A scenario with a long description that should be wrapped neatly across multiple lines",
        target_params={"model_name": "gpt-test", "endpoint": "https://example.com"},
        attack_results={
            "strategy_a": [
                _attack_result(outcome=AttackOutcome.SUCCESS),
                _attack_result(outcome=AttackOutcome.FAILURE),
            ],
            "strategy_b": [_attack_result(outcome=AttackOutcome.SUCCESS)],
        },
    )
    await printer.write_async(result)
    out = capsys.readouterr().out

    assert "SCENARIO RESULTS" in out
    assert "TestScenario" in out
    assert "Scenario Information" in out
    assert "Description" in out
    assert "Target Type: MockTarget" in out
    assert "gpt-test" in out
    assert "https://example.com" in out
    assert "Overall Statistics" in out
    assert "Total Strategies: 2" in out
    assert "Total Attack Results: 3" in out
    assert "Per-Group Breakdown" in out
    assert "strategy_a" in out
    assert "strategy_b" in out


async def test_write_async_with_unknown_target_when_no_params(printer, capsys):
    result = ScenarioResult(
        scenario_identifier=_scenario_identifier(),
        objective_target_identifier=ComponentIdentifier.from_dict({}),
        attack_results={"s": []},
        objective_scorer_identifier=None,
    )
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "Target Model: Unknown" in out
    assert "Target Endpoint: Unknown" in out


async def test_write_async_renders_scorer_section_when_scorer_identifier_present(printer, monkeypatch, capsys):
    # Stub the scorer printer's render_async so we don't depend on real evaluation data.
    async def fake_render_async(*, scorer_identifier, harm_category=None):
        return "[scorer-render-output]"

    monkeypatch.setattr(printer._scorer_printer, "render_async", fake_render_async)

    result = _scenario_result(objective_scorer_identifier=_target_identifier())
    await printer.write_async(result)
    assert "[scorer-render-output]" in capsys.readouterr().out


async def test_write_async_raises_when_scorer_identifier_present_without_scorer_printer(patch_central_database):
    printer = PrettyScenarioResultMemoryPrinter(enable_colors=False)
    printer._scorer_printer = None
    result = _scenario_result(objective_scorer_identifier=_target_identifier())
    with pytest.raises(ValueError, match="scorer_printer is required"):
        await printer.write_async(result)


@pytest.mark.parametrize(
    "expected_rate,attack_outcomes",
    [
        (100, [AttackOutcome.SUCCESS, AttackOutcome.SUCCESS]),  # >=75 RED band
        (50, [AttackOutcome.SUCCESS, AttackOutcome.FAILURE]),  # >=50 YELLOW band
        (33, [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.FAILURE]),  # >=25 CYAN band
        (0, [AttackOutcome.FAILURE]),  # <25 GREEN band
    ],
)
async def test_write_async_color_bands_for_success_rate(patch_central_database, capsys, expected_rate, attack_outcomes):
    p = PrettyScenarioResultMemoryPrinter(enable_colors=True)
    result = _scenario_result(attack_results={"s": [_attack_result(outcome=o) for o in attack_outcomes]})
    await p.write_async(result)
    out = capsys.readouterr().out
    assert f"Overall Success Rate: {expected_rate}%" in out
    assert "\x1b[" in out


async def test_write_async_per_group_breakdown_with_display_group_map(printer, capsys):
    result = _scenario_result(
        attack_results={
            "atomic_a": [_attack_result(outcome=AttackOutcome.SUCCESS)],
            "atomic_b": [_attack_result(outcome=AttackOutcome.FAILURE)],
        },
        display_group_map={"atomic_a": "Group X", "atomic_b": "Group X"},
    )
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "Group: Group X" in out
    assert "Number of Results: 2" in out


async def test_write_async_per_group_breakdown_with_empty_group(printer, capsys):
    result = _scenario_result(attack_results={"empty_strategy": []})
    await printer.write_async(result)
    out = capsys.readouterr().out
    assert "Group: empty_strategy" in out
    assert "Number of Results: 0" in out
    assert "Success Rate: 0%" in out


# --- deprecated alias ---


async def test_print_summary_async_emits_deprecation_warning(printer, capsys):
    with pytest.warns(DeprecationWarning, match="print_summary_async"):
        await printer.print_summary_async(_scenario_result())
    assert "SCENARIO RESULTS" in capsys.readouterr().out
