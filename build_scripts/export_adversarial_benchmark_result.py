# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export readable partial or completed adversarial benchmark results from SQLite."""

import argparse
import asyncio
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyrit.analytics import compute_scenario_statistics
from pyrit.analytics.scenario_statistics import combine_execution_counts
from pyrit.memory import CentralMemory
from pyrit.models import AttackResult, ScenarioResult
from pyrit.output import FileSink
from pyrit.output._derivation import attack_objective_scorer_identifier, objective_scorer_label
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.setup import SQLITE, initialize_pyrit_async


@dataclass(frozen=True)
class _BenchmarkDimensions:
    technique_count: int
    adversarial_model_count: int
    combination_count: int


async def _load_result_async(*, scenario_result_id: str) -> ScenarioResult:
    """Load one persisted scenario result, regardless of terminal state."""
    await initialize_pyrit_async(
        memory_db_type=SQLITE,
        load_defaults=False,
        env_files=[],
        silent=True,
    )
    results = await CentralMemory.get_memory_instance().get_scenario_results_async(
        scenario_result_ids=[scenario_result_id],
    )
    if not results:
        raise ValueError(f"Scenario result '{scenario_result_id}' was not found in SQLite memory.")
    return results[0]


async def _write_overview_async(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write the scenario overview with benchmark-specific matrix dimensions."""
    printer = PrettyScenarioResultMemoryPrinter(enable_colors=False)
    overview = await printer.render_async(result)
    dimensions = _build_benchmark_dimensions(result=result)
    overview = _replace_generic_technique_count(overview=overview, dimensions=dimensions)
    await FileSink(path=output_dir / "overview.txt").write_async(overview)


def _benchmark_groups(*, result: ScenarioResult) -> list[tuple[str, str, list[AttackResult]]]:
    """Return persisted attack groups keyed by technique and adversarial model."""
    atomic_attack_names = dict.fromkeys([*result.display_group_map, *result.attack_results])
    return [
        (
            atomic_attack_name.split("__", 1)[0],
            result.display_group_map.get(atomic_attack_name, "<ungrouped>"),
            result.attack_results.get(atomic_attack_name, []),
        )
        for atomic_attack_name in atomic_attack_names
    ]


def _build_benchmark_dimensions(*, result: ScenarioResult) -> _BenchmarkDimensions:
    """Count distinct techniques, adversarial models, and their combinations."""
    pairs = {(technique, model) for technique, model, _ in _benchmark_groups(result=result)}
    return _BenchmarkDimensions(
        technique_count=len({technique for technique, _ in pairs}),
        adversarial_model_count=len({model for _, model in pairs}),
        combination_count=len(pairs),
    )


def _replace_generic_technique_count(*, overview: str, dimensions: _BenchmarkDimensions) -> str:
    """Replace the display-group count with benchmark-specific matrix dimensions."""
    lines = overview.splitlines(keepends=True)
    summary_label = "\u2022 Total Techniques:"
    summary_indexes = [index for index, line in enumerate(lines) if line.lstrip().startswith(summary_label)]
    if len(summary_indexes) != 1:
        raise ValueError("Expected exactly one 'Total Techniques' line in the scenario overview.")

    index = summary_indexes[0]
    prefix = lines[index].partition("Total Techniques:")[0]
    newline = "\n" if lines[index].endswith("\n") else ""
    lines[index] = (
        f"{prefix}Distinct Techniques: {dimensions.technique_count}\n"
        f"{prefix}Distinct Adversarial Models: {dimensions.adversarial_model_count}\n"
        f"{prefix}Technique/Model Combinations: {dimensions.combination_count}{newline}"
    )
    return "".join(lines)


def _attack_rows(*, result: ScenarioResult) -> list[dict[str, Any]]:
    """Build machine-readable per-attack rows from the embedded attack results."""
    rows: list[dict[str, Any]] = []
    for atomic_attack_name, attacks in result.attack_results.items():
        for attack in attacks:
            score = attack.last_score
            score_value = None
            if score is not None:
                score_value = score.score_value if score.score_value is not None else score.status.value
            objective_scorer = attack_objective_scorer_identifier(
                attack=attack, run_scorer_identifier=result.objective_scorer_identifier
            )
            rows.append(
                {
                    "attack_result_id": attack.attack_result_id,
                    "atomic_attack_name": atomic_attack_name,
                    "objective": attack.objective,
                    "outcome": attack.outcome.value,
                    "executed_turns": attack.executed_turns,
                    "score_value": score_value,
                    "objective_scorer": f"{objective_scorer.class_name} ({objective_scorer.hash[:12]})"
                    if objective_scorer is not None
                    else None,
                }
            )
    return rows


async def _write_attacks_async(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write machine-readable and console-style partial attack tables."""
    rows = _attack_rows(result=result)
    document = {"scenario_result_id": str(result.id), "rows": rows, "total": len(rows)}
    (output_dir / "attacks.json").write_text(json.dumps(document, indent=2), encoding="utf-8")

    sink = FileSink(path=output_dir / "attacks.txt")
    printer = PrettyScenarioResultMemoryPrinter(sink=sink, enable_colors=False)
    await printer.write_async(result, view="attacks")


def _build_technique_metrics(*, result: ScenarioResult) -> list[dict[str, Any]]:
    """
    Aggregate effective execution-unit outcomes by technique and adversarial model.

    Counts use the scenario report's statistics: each execution unit (objective seed group under one
    technique configuration) counts once, by its latest completed result. Older results for the unit
    are reported as ``retry_records``.
    """
    statistics = compute_scenario_statistics(result)
    names_by_combination: dict[tuple[str, str], list[str]] = defaultdict(list)
    for atomic_attack_name in dict.fromkeys([*result.display_group_map, *result.attack_results]):
        technique_name = atomic_attack_name.split("__", 1)[0]
        display_group = result.display_group_map.get(atomic_attack_name, "<ungrouped>")
        names_by_combination[(technique_name, display_group)].append(atomic_attack_name)

    metrics: list[dict[str, Any]] = []
    for (technique_name, display_group), names in sorted(names_by_combination.items()):
        counts = combine_execution_counts(
            statistics.atomic_attacks[name] for name in names if name in statistics.atomic_attacks
        )
        outcomes = counts.outcomes
        attempts = sum(statistics.atomic_attack_attempts.get(name, 0) for name in names)
        if not attempts and not counts.completed:
            continue
        objective_scorers = [
            objective_scorer
            for objective_scorer in statistics.objective_scorers
            if any(
                objective_scorer.atomic_attacks[name].completed
                for name in names
                if name in objective_scorer.atomic_attacks
            )
        ]
        metrics.append(
            {
                "technique": technique_name,
                "adversarial_model": display_group,
                "total": counts.completed,
                "success": outcomes.successes if outcomes is not None else counts.succeeded,
                "failure": outcomes.failures if outcomes is not None else 0,
                "error": outcomes.errors if outcomes is not None else 0,
                "undetermined": outcomes.undetermined if outcomes is not None else 0,
                "retry_records": max(attempts - counts.completed, 0),
                "success_rate": round(counts.succeeded / counts.completed, 4) if counts.completed else 0.0,
                "success_rate_decided": (
                    round(outcomes.success_rate_decided, 4)
                    if outcomes is not None and outcomes.success_rate_decided is not None
                    else None
                ),
                "objective_scorer": objective_scorer_label(objective_scorers[0])
                if len(objective_scorers) == 1
                else ("mixed" if objective_scorers else ""),
                "objective_scorer_attribution": objective_scorers[0].attribution.value
                if len(objective_scorers) == 1
                else "",
            }
        )
    return metrics


def _write_technique_metrics(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write per-technique metrics in text, CSV, and JSON formats."""
    metrics = _build_technique_metrics(result=result)
    (output_dir / "technique-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    fieldnames = [
        "technique",
        "adversarial_model",
        "total",
        "success",
        "failure",
        "error",
        "undetermined",
        "retry_records",
        "success_rate",
        "success_rate_decided",
        "objective_scorer",
        "objective_scorer_attribution",
    ]
    with open(output_dir / "technique-metrics.csv", "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics)

    lines = [
        "{:<32} {:<30} {:>4} {:>8} {:>8} {:>6} {:>8} {:>8} {:>8}  {}".format(
            "Technique",
            "Adversarial model",
            "N",
            "Success",
            "Failure",
            "Error",
            "Retries",
            "ASR",
            "Decided",
            "Objective scorer",
        )
    ]
    lines.extend(
        (
            "{technique:<32} {adversarial_model:<30} {total:>4} {success:>8} "
            "{failure:>8} {error:>6} {retry_records:>8} {success_rate:>7.1%} {decided:>8}  {objective_scorer}"
        ).format(
            **metric,
            decided=(f"{metric['success_rate_decided']:.1%}" if metric["success_rate_decided"] is not None else "N/A"),
        )
        for metric in metrics
    )
    if len({metric["objective_scorer"] for metric in metrics if metric["objective_scorer"]}) > 1 or any(
        metric["objective_scorer"] == "mixed" for metric in metrics
    ):
        lines.append("")
        lines.append(
            "Different objective scorers scored these rows; compare ASR only between rows with the same scorer."
        )
    (output_dir / "technique-metrics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _export_async(*, scenario_result_id: str, output_dir: Path) -> None:
    """Export all readable result views."""
    result = await _load_result_async(scenario_result_id=scenario_result_id)
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    await _write_overview_async(result=result, output_dir=output_dir)
    await _write_attacks_async(result=result, output_dir=output_dir)
    await asyncio.to_thread(_write_technique_metrics, result=result, output_dir=output_dir)


def main() -> None:
    """Run the result exporter."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-result-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        _export_async(
            scenario_result_id=args.scenario_result_id,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
