# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export readable partial or completed adversarial benchmark results from SQLite."""

import argparse
import asyncio
import contextlib
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pyrit.cli._output import print_attacks_table
from pyrit.cli._results import build_attacks_table_payload
from pyrit.memory import CentralMemory
from pyrit.models import ScenarioResult
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.output.sink import FileSink
from pyrit.setup import SQLITE, initialize_pyrit_async


async def _load_result_async(*, scenario_result_id: str) -> ScenarioResult:
    """Load one persisted scenario result, regardless of terminal state."""
    await initialize_pyrit_async(
        memory_db_type=SQLITE,
        load_defaults=False,
        env_files=[],
        silent=True,
    )
    results = CentralMemory.get_memory_instance().get_scenario_results(
        scenario_result_ids=[scenario_result_id],
    )
    if not results:
        raise ValueError(f"Scenario result '{scenario_result_id}' was not found in SQLite memory.")
    return results[0]


async def _write_overview_async(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write the existing scenario overview without terminal color codes."""
    printer = PrettyScenarioResultMemoryPrinter(
        sink=FileSink(path=output_dir / "overview.txt"),
        enable_colors=False,
    )
    await printer.write_async(result)


def _write_attacks(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write machine-readable and console-style partial attack tables."""
    payload = build_attacks_table_payload(
        result=result,
        scenario_result_id=str(result.id),
    )
    (output_dir / "attacks.json").write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    with open(output_dir / "attacks.txt", "w", encoding="utf-8") as output:
        with contextlib.redirect_stdout(output):
            print_attacks_table(payload=payload)


def _build_technique_metrics(*, result: ScenarioResult) -> list[dict[str, Any]]:
    """Aggregate persisted outcomes by technique and adversarial model."""
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    retry_records: Counter[tuple[str, str]] = Counter()
    for atomic_attack_name, attack_results in result.attack_results.items():
        technique_name = atomic_attack_name.split("__", 1)[0]
        display_group = result.display_group_map.get(atomic_attack_name, "<ungrouped>")
        group_key = (technique_name, display_group)
        latest_by_objective = {}
        for attack_result in attack_results:
            current = latest_by_objective.get(attack_result.objective)
            if current is None or attack_result.timestamp > current.timestamp:
                latest_by_objective[attack_result.objective] = attack_result
        retry_records[group_key] += len(attack_results) - len(latest_by_objective)
        for attack_result in latest_by_objective.values():
            grouped[(technique_name, display_group)][attack_result.outcome.value.lower()] += 1

    metrics: list[dict[str, Any]] = []
    for (technique_name, display_group), counts in sorted(grouped.items()):
        total = sum(counts.values())
        success_count = counts["success"]
        metrics.append(
            {
                "technique": technique_name,
                "adversarial_model": display_group,
                "total": total,
                "success": success_count,
                "failure": counts["failure"],
                "error": counts["error"],
                "undetermined": counts["undetermined"],
                "retry_records": retry_records[(technique_name, display_group)],
                "success_rate": round(success_count / total, 4) if total else 0.0,
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
    ]
    with open(output_dir / "technique-metrics.csv", "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics)

    lines = [
        "{:<32} {:<30} {:>4} {:>8} {:>8} {:>6} {:>8} {:>8}".format(
            "Technique",
            "Adversarial model",
            "N",
            "Success",
            "Failure",
            "Error",
            "Retries",
            "ASR",
        )
    ]
    lines.extend(
        (
            "{technique:<32} {adversarial_model:<30} {total:>4} {success:>8} "
            "{failure:>8} {error:>6} {retry_records:>8} {success_rate:>7.1%}"
        ).format(**metric)
        for metric in metrics
    )
    (output_dir / "technique-metrics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _export_async(*, scenario_result_id: str, output_dir: Path) -> None:
    """Export all readable result views."""
    result = await _load_result_async(scenario_result_id=scenario_result_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    await _write_overview_async(result=result, output_dir=output_dir)
    await asyncio.to_thread(_write_attacks, result=result, output_dir=output_dir)
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
