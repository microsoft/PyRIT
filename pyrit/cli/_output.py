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


def _truncate_text(text: str, max_length: int) -> str:
    """
    Truncate *text* to *max_length* characters, appending an ellipsis when cut.

    Returns:
        str: The original text, or a truncated copy ending in ``...``.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


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
        if sc.aggregate_techniques:
            print("    Aggregate Techniques:")
            print(_wrap(text=", ".join(sc.aggregate_techniques), indent="      - "))
        if sc.all_techniques:
            print(f"    Available Techniques ({len(sc.all_techniques)}):")
            print(_wrap(text=", ".join(sc.all_techniques), indent="      "))
        if sc.default_technique:
            print(f"    Default Technique: {sc.default_technique}")
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
        identifier = tgt.identifier
        _header(tgt.target_registry_name)
        print(f"    Class: {identifier.class_name}")
        model = identifier.underlying_model_name or identifier.model_name or ""
        if model:
            print(f"    Model: {model}")
        if identifier.endpoint:
            print(f"    Endpoint: {identifier.endpoint}")
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
            "  --techniques role_play_movie_script:converter.translation_spanish\n"
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
    print("  --techniques role_play_movie_script:converter.<name>\n")


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


def _format_retry_location(retry: Any) -> str:
    """
    Build a human-readable "on <component>, endpoint <url>" clause for a retry.

    Returns:
        str: The location clause, or an empty string when no context is available.
    """
    bits: list[str] = []
    if retry.component_role:
        role = retry.component_role.replace("_", " ")
        bits.append(f"{role} {retry.component_name}" if retry.component_name else role)
    if retry.endpoint:
        bits.append(f"endpoint {retry.endpoint}")
    return " on " + ", ".join(bits) if bits else ""


def print_scenario_retry_warnings(*, run: ScenarioRunSummary, seen_attack_ids: set[str]) -> None:
    """
    Print retry warnings for attack results seen for the first time.

    Called during polling so retries stream to the console as each attack result
    lands. ``seen_attack_ids`` is mutated to de-duplicate across polls.

    Args:
        run: ``ScenarioRunSummary`` from ``GET /api/scenarios/runs/{id}``.
        seen_attack_ids: Attack-result IDs already printed; updated in place.
    """
    new_attacks = [a for a in run.attack_retries if a.attack_result_id not in seen_attack_ids]
    if not new_attacks:
        return

    # Finalize the in-place progress line (written with `\r`, no newline) so these
    # warnings become persistent scrollback above the next progress redraw.
    print()
    for attack in new_attacks:
        seen_attack_ids.add(attack.attack_result_id)
        for retry in attack.retries:
            exc = retry.exception_type or "error"
            message = (retry.exception_message or "").strip().splitlines()
            if message:
                exc = f"{exc}: {_truncate_text(message[0], 160)}"
            location = _format_retry_location(retry)
            _cprint(
                f"  ! retry #{retry.attempt_number} [{attack.atomic_attack_name}]{location}: {exc}",
                color="yellow",
            )


def print_scenario_run_progress(*, run: ScenarioRunSummary, total_techniques: int = 0) -> None:
    """
    Print a single-line progress update (overwrites the current line).

    Args:
        run: ``ScenarioRunSummary`` from ``GET /api/scenarios/runs/{id}``.
        total_techniques: Total number of techniques expected (0 if unknown).
    """
    techniques_done = len(run.techniques_used)
    # Techniques the user passed may be aggregates that expand on the server
    # (e.g. `single_turn` -> N concrete techniques). Trust whichever count is larger.
    effective_total = max(total_techniques, techniques_done)

    parts: list[str] = []

    # The bar tracks techniques completed / total, which is the only ratio we can
    # honestly compute mid-run: the server only knows about attacks already persisted,
    # so an attacks-based bar would always read 100%.
    if effective_total > 0:
        pct = int((techniques_done / effective_total) * 100)
        bar_width = 30
        filled = int(bar_width * techniques_done / effective_total)
        bar = "█" * filled + "░" * (bar_width - filled)
        try:
            bar.encode(sys.stdout.encoding or "utf-8")
        except (LookupError, UnicodeEncodeError):
            bar = "#" * filled + "-" * (bar_width - filled)
        parts.append(f"[{bar}] techniques: {techniques_done}/{effective_total} ({pct}%)")
    else:
        parts.append(f"techniques: {techniques_done}")

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
    # Count of individual attack-result records persisted (one per technique x objective
    # that ran), not a planned total. It stops growing wherever a failed run halted.
    print(f"  Attack Results: {run.total_attacks}")
    print(f"  Success Rate:   {run.objective_achieved_rate}%")

    if run.total_retries:
        print(f"  Retries:        {run.total_retries} (endpoint-stress signal)")

    if run.error:
        print(f"  Error:          {run.error}")

    if run.techniques_used:
        print(f"  Techniques:     {', '.join(run.techniques_used)}")

    if run.failed_attacks:
        print(f"\n  Failed Attacks ({len(run.failed_attacks)}):")
        for failed in run.failed_attacks:
            error_type = failed.error_type or "Error"
            message = (failed.error_message or "").strip().splitlines()
            detail = _truncate_text(message[0], 200) if message else "no detail"
            retry_note = f" [{failed.total_retries} retries]" if failed.total_retries else ""
            print(f"    - {failed.atomic_attack_name}{retry_note}: {error_type}: {detail}")


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
