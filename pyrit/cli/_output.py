# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Console output formatting for the PyRIT CLI thin client.

All functions accept plain ``dict`` payloads (deserialized JSON from the REST
API) and print human-readable output to stdout.  No heavy pyrit imports.
"""

from __future__ import annotations

import sys
from typing import Any

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


def print_scenario_list(*, items: list[dict[str, Any]]) -> None:
    """
    Print a formatted list of scenarios.

    Args:
        items: List of scenario dicts from ``GET /api/scenarios/catalog``.
    """
    if not items:
        print("No scenarios found.")
        return

    print("\nAvailable Scenarios:")
    print("=" * 80)
    for sc in items:
        _header(sc.get("scenario_name", "unknown"))
        print(f"    Class: {sc.get('scenario_type', '')}")
        desc = sc.get("description", "")
        if desc:
            print("    Description:")
            print(_wrap(text=desc, indent="      "))
        agg = sc.get("aggregate_strategies") or []
        if agg:
            print("    Aggregate Strategies:")
            print(_wrap(text=", ".join(agg), indent="      - "))
        strategies = sc.get("all_strategies") or []
        if strategies:
            print(f"    Available Strategies ({len(strategies)}):")
            print(_wrap(text=", ".join(strategies), indent="      "))
        default_strat = sc.get("default_strategy")
        if default_strat:
            print(f"    Default Strategy: {default_strat}")
        datasets = sc.get("default_datasets") or []
        max_ds = sc.get("max_dataset_size")
        if datasets:
            suffix = f", max {max_ds} per dataset" if max_ds else ""
            print(f"    Default Datasets ({len(datasets)}{suffix}):")
            print(_wrap(text=", ".join(datasets), indent="      "))
        params = sc.get("supported_parameters") or []
        if params:
            print("    Supported Parameters:")
            for p in params:
                default_str = f" [default: {p.get('default')!r}]" if p.get("default") is not None else ""
                type_str = f" ({p.get('param_type', '')})" if p.get("param_type") else ""
                choices = p.get("choices")
                choices_display = ", ".join(choices) if isinstance(choices, list) else choices
                choices_str = f" [choices: {choices_display}]" if choices_display else ""
                print(f"      - {p.get('name', '?')}{type_str}{default_str}{choices_str}: {p.get('description', '')}")
    print("\n" + "=" * 80)
    print(f"\nTotal scenarios: {len(items)}")


# ---------------------------------------------------------------------------
# Initializer listing
# ---------------------------------------------------------------------------


def print_initializer_list(*, items: list[dict[str, Any]]) -> None:
    """
    Print a formatted list of initializers.

    Args:
        items: List of initializer dicts from ``GET /api/initializers``.
    """
    if not items:
        print("No initializers found.")
        return

    print("\nAvailable Initializers:")
    print("=" * 80)
    for init in items:
        _header(init.get("initializer_name", "unknown"))
        print(f"    Class: {init.get('initializer_type', '')}")
        env_vars = init.get("required_env_vars") or []
        if env_vars:
            print("    Required Environment Variables:")
            for var in env_vars:
                print(f"      - {var}")
        else:
            print("    Required Environment Variables: None")
        params = init.get("supported_parameters") or []
        if params:
            print("    Supported Parameters:")
            for p in params:
                default_str = f" [default: {p.get('default')}]" if p.get("default") else ""
                print(f"      - {p.get('name', '?')}{default_str}: {p.get('description', '')}")
        desc = init.get("description", "")
        if desc:
            print("    Description:")
            print(_wrap(text=desc, indent="      "))
    print("\n" + "=" * 80)
    print(f"\nTotal initializers: {len(items)}")


# ---------------------------------------------------------------------------
# Target listing
# ---------------------------------------------------------------------------


def print_target_list(*, items: list[dict[str, Any]]) -> None:
    """
    Print a formatted list of targets.

    Args:
        items: List of target dicts from ``GET /api/targets``.
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
        _header(tgt.get("target_registry_name", "unknown"))
        print(f"    Class: {tgt.get('target_type', '')}")
        model = tgt.get("underlying_model_name") or tgt.get("model_name") or ""
        if model:
            print(f"    Model: {model}")
        endpoint = tgt.get("endpoint") or ""
        if endpoint:
            print(f"    Endpoint: {endpoint}")
    print("\n" + "=" * 80)
    print(f"\nTotal targets: {len(items)}")


# ---------------------------------------------------------------------------
# Scenario run progress & summary
# ---------------------------------------------------------------------------


def print_scenario_run_progress(*, run: dict[str, Any], total_strategies: int = 0) -> None:
    """
    Print a single-line progress update (overwrites the current line).

    Args:
        run: ScenarioRunSummary dict from ``GET /api/scenarios/runs/{id}``.
        total_strategies: Total number of strategies expected (0 if unknown).
    """
    run_status = run.get("status", "UNKNOWN")
    total = run.get("total_attacks", 0)
    completed = run.get("completed_attacks", 0)
    rate = run.get("objective_achieved_rate", 0)
    strategies_done = len(run.get("strategies_used") or [])
    # Strategies the user passed may be aggregates that expand on the server
    # (e.g. `single_turn` -> N concrete strategies). Trust whichever count is larger.
    effective_total = max(total_strategies, strategies_done)

    parts: list[str] = []

    if effective_total > 0:
        parts.append(f"strategies: {strategies_done}/{effective_total}")
    elif strategies_done > 0:
        parts.append(f"strategies: {strategies_done}")

    if total > 0:
        pct = int((completed / total) * 100)
        bar_width = 30
        filled = int(bar_width * completed / total)
        bar = "█" * filled + "░" * (bar_width - filled)
        parts.append(f"[{bar}] {completed}/{total} attacks ({pct}%)")
    else:
        parts.append(f"attacks: {completed}")

    parts.append(f"success rate: {rate}%")
    parts.append(run_status)

    line = "\r  " + " | ".join(parts)
    sys.stdout.write(line)
    sys.stdout.flush()


def print_scenario_run_summary(*, run: dict[str, Any]) -> None:
    """
    Print a brief summary of a completed scenario run.

    Args:
        run: ScenarioRunSummary dict.
    """
    print()  # newline after progress bar
    status = run.get("status", "UNKNOWN")
    name = run.get("scenario_name", "unknown")
    rid = run.get("scenario_result_id", "?")
    total = run.get("total_attacks", 0)
    completed = run.get("completed_attacks", 0)
    rate = run.get("objective_achieved_rate", 0)

    print(f"\nScenario: {name}")
    print(f"  Result ID:      {rid}")
    print(f"  Status:         {status}")
    print(f"  Total Attacks:  {total}")
    print(f"  Completed:      {completed}")
    print(f"  Success Rate:   {rate}%")

    error = run.get("error")
    if error:
        print(f"  Error:          {error}")

    strategies = run.get("strategies_used") or []
    if strategies:
        print(f"  Strategies:     {', '.join(strategies)}")


# ---------------------------------------------------------------------------
# Scenario run detail (full results via output module)
# ---------------------------------------------------------------------------


async def print_scenario_result_async(*, result_dict: dict[str, Any]) -> None:
    """
    Print detailed scenario results using the output module.

    Args:
        result_dict: ``ScenarioResult.model_dump(mode="json", by_alias=True)`` payload from the REST API.
    """
    from pyrit.models import ScenarioResult
    from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter

    scenario_result = ScenarioResult.model_validate(result_dict)
    printer = PrettyScenarioResultMemoryPrinter()
    await printer.write_async(scenario_result)


# ---------------------------------------------------------------------------
# Scenario run history
# ---------------------------------------------------------------------------


def print_scenario_runs_list(*, runs: list[dict[str, Any]]) -> None:
    """
    Print a list of scenario run summaries.

    Args:
        runs: List of ScenarioRunSummary dicts from ``GET /api/scenarios/runs``.
    """
    if not runs:
        print("No scenario runs found.")
        return

    print("\nScenario Run History:")
    print("=" * 80)
    for idx, run in enumerate(runs, start=1):
        status = run.get("status", "?")
        name = run.get("scenario_name", "unknown")
        rid = run.get("scenario_result_id", "?")[:8]
        total = run.get("total_attacks", 0)
        rate = run.get("objective_achieved_rate", 0)
        created = run.get("created_at", "?")
        print(f"  {idx}) [{status}] {name} (id: {rid}…) — {total} attacks, {rate}% success — {created}")
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
