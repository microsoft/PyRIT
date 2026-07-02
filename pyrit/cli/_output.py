# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Console output formatting for the PyRIT CLI thin client.

All public ``print_*`` functions accept typed ``pyrit.models`` objects
(``RegisteredScenario``, ``RegisteredInitializer``, ``TargetInstance``,
``ScenarioRunSummary``, ``ScenarioResult``). The heavy ``pyrit.models``
import is deferred to each function so importing this module stays cheap.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyrit.models import ScenarioResult
    from pyrit.models.catalog import (
        RegisteredInitializer,
        RegisteredScenario,
        ScenarioRunSummary,
        TargetInstance,
    )

try:
    import termcolor

    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cprint(text: str, *, color: str | None = None, bold: bool = False) -> None:
    """Print *text*, optionally colored if ``termcolor`` is available."""
    if _HAS_COLOR and color:
        attrs = ["bold"] if bold else None
        termcolor.cprint(text, color, attrs=attrs)
    else:
        print(text)


def _header(text: str) -> None:
    _cprint(f"\n  {text}", color="cyan", bold=True)


def _wrap(*, text: str, indent: str, width: int = 78) -> str:
    """
    Word-wrap *text* with the given *indent*.

    Returns:
        str: The wrapped text with newline separators.
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(indent) + len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(indent + current)
            current = word
    if current:
        lines.append(indent + current)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scenario listing
# ---------------------------------------------------------------------------


def print_scenario_list(*, items: list[RegisteredScenario]) -> None:
    """
    Print a formatted list of scenarios.

    Args:
        items: Scenarios from ``GET /api/scenarios/catalog``.
    """
    if not items:
        print("No scenarios found.")
        return

    print("\nAvailable Scenarios:")
    print("=" * 80)
    for sc in items:
        _header(sc.scenario_name)
        print(f"    Class: {sc.scenario_type}")
        if sc.description:
            print("    Description:")
            print(_wrap(text=sc.description, indent="      "))
        if sc.aggregate_strategies:
            print("    Aggregate Strategies:")
            print(_wrap(text=", ".join(sc.aggregate_strategies), indent="      - "))
        if sc.all_strategies:
            print(f"    Available Strategies ({len(sc.all_strategies)}):")
            print(_wrap(text=", ".join(sc.all_strategies), indent="      "))
        if sc.default_strategy:
            print(f"    Default Strategy: {sc.default_strategy}")
        if sc.default_datasets:
            print(f"    Default Datasets ({len(sc.default_datasets)}):")
            print(_wrap(text=", ".join(sc.default_datasets), indent="      "))
        if sc.supported_parameters:
            print("    Supported Parameters:")
            for p in sc.supported_parameters:
                default_str = f" [default: {p.default!r}]" if p.default is not None else ""
                type_str = f" ({p.type_name})" if p.type_name else ""
                choices_str = f" [choices: {', '.join(p.choices)}]" if p.choices else ""
                print(f"      - {p.name}{type_str}{default_str}{choices_str}: {p.description}")
    print("\n" + "=" * 80)
    print(f"\nTotal scenarios: {len(items)}")


# ---------------------------------------------------------------------------
# Initializer listing
# ---------------------------------------------------------------------------


def print_initializer_list(*, items: list[RegisteredInitializer]) -> None:
    """
    Print a formatted list of initializers.

    Args:
        items: Initializers from ``GET /api/initializers``.
    """
    if not items:
        print("No initializers found.")
        return

    print("\nAvailable Initializers:")
    print("=" * 80)
    for init in items:
        _header(init.initializer_name)
        print(f"    Class: {init.initializer_type}")
        if init.required_env_vars:
            print("    Required Environment Variables:")
            for var in init.required_env_vars:
                print(f"      - {var}")
        else:
            print("    Required Environment Variables: None")
        if init.supported_parameters:
            print("    Supported Parameters:")
            for p in init.supported_parameters:
                default_str = f" [default: {p.default}]" if p.default else ""
                print(f"      - {p.name}{default_str}: {p.description}")
        if init.description:
            print("    Description:")
            print(_wrap(text=init.description, indent="      "))
    print("\n" + "=" * 80)
    print(f"\nTotal initializers: {len(items)}")


# ---------------------------------------------------------------------------
# Target listing
# ---------------------------------------------------------------------------


def print_target_list(*, items: list[TargetInstance]) -> None:
    """
    Print a formatted list of targets.

    Args:
        items: Targets from ``GET /api/targets``.
    """
    if not items:
        print("\nNo targets found in registry.")
        print(
            "\nTargets are registered by initializers. Include an initializer that "
            "registers targets, for example:\n  --initializers target\n"
        )
        return

    print("\nRegistered Targets:")
    print("=" * 80)
    for tgt in items:
        _header(tgt.target_registry_name)
        print(f"    Class: {tgt.target_type}")
        model = tgt.underlying_model_name or tgt.model_name or ""
        if model:
            print(f"    Model: {model}")
        if tgt.endpoint:
            print(f"    Endpoint: {tgt.endpoint}")
    print("\n" + "=" * 80)
    print(f"\nTotal targets: {len(items)}")


# ---------------------------------------------------------------------------
# Converter listing
# ---------------------------------------------------------------------------


def print_converter_list(*, items: list[dict[str, Any]]) -> None:
    """
    Print a formatted list of registered converter instances.

    Args:
        items: List of converter dicts from ``GET /api/converters``.
    """
    if not items:
        print("\nNo converters found in registry.")
        print(
            "\nConverters are registered by initializers. Include an initializer that "
            "registers converters to attach them to scenario techniques, for example:\n"
            "  --strategies role_play:converter.translation_spanish\n"
        )
        return

    print("\nRegistered Converters:")
    print("=" * 80)
    for conv in items:
        name = conv.get("converter_id", "unknown")
        _header(name)
        print(f"    Class: {conv.get('converter_type', '')}")
        display_name = conv.get("display_name") or ""
        if display_name:
            print(f"    Name: {display_name}")
        sub_ids = conv.get("sub_converter_ids") or []
        if sub_ids:
            print(f"    Sub-converters: {', '.join(sub_ids)}")
    print("\n" + "=" * 80)
    print(f"\nTotal converters: {len(items)}")
    print("\nAttach a converter to a scenario technique with, for example:")
    print("  --strategies role_play:converter.<name>\n")


# ---------------------------------------------------------------------------
# Dataset listing
# ---------------------------------------------------------------------------


def print_dataset_list(*, items: list[dict[str, Any]]) -> None:
    """
    Print a formatted list of available datasets.

    Args:
        items: List of dataset dicts from ``GET /api/datasets``.
    """
    if not items:
        print("No datasets found.")
        return

    print("\nAvailable Datasets:")
    print("=" * 80)
    for ds in items:
        name = ds.get("name", "unknown")
        print(f"    {name}")
    print("=" * 80)
    print(f"\nTotal datasets: {len(items)}")


# ---------------------------------------------------------------------------
# Scenario run progress & summary
# ---------------------------------------------------------------------------


def print_scenario_run_progress(*, run: ScenarioRunSummary, total_strategies: int = 0) -> None:
    """
    Print a single-line progress update (overwrites the current line).

    Args:
        run: ``ScenarioRunSummary`` from ``GET /api/scenarios/runs/{id}``.
        total_strategies: Total number of strategies expected (0 if unknown).
    """
    strategies_done = len(run.strategies_used)
    # Strategies the user passed may be aggregates that expand on the server
    # (e.g. `single_turn` -> N concrete strategies). Trust whichever count is larger.
    effective_total = max(total_strategies, strategies_done)

    parts: list[str] = []

    if effective_total > 0:
        parts.append(f"strategies: {strategies_done}/{effective_total}")
    elif strategies_done > 0:
        parts.append(f"strategies: {strategies_done}")

    if run.total_attacks > 0:
        pct = int((run.completed_attacks / run.total_attacks) * 100)
        bar_width = 30
        filled = int(bar_width * run.completed_attacks / run.total_attacks)
        bar = "█" * filled + "░" * (bar_width - filled)
        parts.append(f"[{bar}] {run.completed_attacks}/{run.total_attacks} attacks ({pct}%)")
    else:
        parts.append(f"attacks: {run.completed_attacks}")

    parts.append(f"success rate: {run.objective_achieved_rate}%")
    parts.append(run.status.value)

    line = "\r  " + " | ".join(parts)
    sys.stdout.write(line)
    sys.stdout.flush()


def print_scenario_run_summary(*, run: ScenarioRunSummary) -> None:
    """
    Print a brief summary of a completed scenario run.

    Args:
        run: ``ScenarioRunSummary``.
    """
    print()  # newline after progress bar
    print(f"\nScenario: {run.scenario_name}")
    print(f"  Result ID:      {run.scenario_result_id}")
    print(f"  Status:         {run.status.value}")
    print(f"  Total Attacks:  {run.total_attacks}")
    print(f"  Completed:      {run.completed_attacks}")
    print(f"  Success Rate:   {run.objective_achieved_rate}%")

    if run.error:
        print(f"  Error:          {run.error}")

    if run.strategies_used:
        print(f"  Strategies:     {', '.join(run.strategies_used)}")


# ---------------------------------------------------------------------------
# Scenario run detail (full results via output module)
# ---------------------------------------------------------------------------


async def print_scenario_result_async(*, result: ScenarioResult) -> None:
    """
    Print detailed scenario results using the output module.

    Args:
        result: Deserialized ``ScenarioResult`` from the REST API.
    """
    from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

    printer = PrettyScenarioResultMemoryPrinter()
    await printer.write_async(result)


# ---------------------------------------------------------------------------
# Scenario run history
# ---------------------------------------------------------------------------


def print_scenario_runs_list(*, runs: list[ScenarioRunSummary]) -> None:
    """
    Print a list of scenario run summaries.

    Args:
        runs: Scenario runs from ``GET /api/scenarios/runs``.
    """
    if not runs:
        print("No scenario runs found.")
        return

    print("\nScenario Run History:")
    print("=" * 80)
    for idx, run in enumerate(runs, start=1):
        rid = run.scenario_result_id[:8]
        created = run.created_at.isoformat() if run.created_at else "?"
        print(
            f"  {idx}) [{run.status.value}] {run.scenario_name} (id: {rid}…) — "
            f"{run.total_attacks} attacks, {run.objective_achieved_rate}% success — {created}"
        )
    print("=" * 80)
    print(f"\nTotal runs: {len(runs)}")


# ---------------------------------------------------------------------------
# Error display
# ---------------------------------------------------------------------------


def print_error_with_hint(*, message: str, hint: str | None = None) -> None:
    """
    Print an error message with an optional actionable hint.

    Args:
        message: The error text.
        hint: Optional follow-up guidance.
    """
    print(f"\nError: {message}")
    if hint:
        print(f"Hint: {hint}")
