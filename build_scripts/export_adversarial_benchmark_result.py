# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export readable partial or completed adversarial benchmark results from SQLite."""

import argparse
import asyncio
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pyrit.common.path import DATASETS_PATH
from pyrit.memory import CentralMemory
from pyrit.models import ScenarioResult
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.output.sink import FileSink
from pyrit.scenario.scenarios.benchmark.adversarial import resolve_objective_identity
from pyrit.setup import SQLITE, initialize_pyrit_async

_BENCHMARK_METRICS_KEY_FIELDS = (
    "technique",
    "adversarial_model",
    "objective_target",
    "objective_scorer",
    "dataset",
)

DEFAULT_BENCHMARK_STORE_PATH = DATASETS_PATH / "benchmark_results" / "adversarial_benchmark_metrics.jsonl"


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


def _attack_rows(*, result: ScenarioResult) -> list[dict[str, Any]]:
    """Build machine-readable per-attack rows from the embedded attack results."""
    rows: list[dict[str, Any]] = []
    for atomic_attack_name, attacks in result.attack_results.items():
        for attack in attacks:
            score = attack.last_score
            score_value = None
            if score is not None:
                score_value = score.score_value if score.score_value is not None else score.status.value
            rows.append(
                {
                    "attack_result_id": attack.attack_result_id,
                    "atomic_attack_name": atomic_attack_name,
                    "objective": attack.objective,
                    "outcome": attack.outcome.value,
                    "executed_turns": attack.executed_turns,
                    "score_value": score_value,
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


def _objective_identity(*, result: ScenarioResult) -> tuple[str, str]:
    """
    Derive the (objective_target, objective_scorer) display identity for this run.

    Both are constant across an entire scenario run (``AdversarialBenchmark`` fixes exactly
    one objective target and one objective scorer per run), so they're computed once and
    attached to every technique-metrics row rather than re-derived per group. Delegates to
    ``resolve_objective_identity`` so this exporter and ``AdversarialBenchmark`` itself
    (which uses the same helper to recognize already-exported rows via
    ``benchmark_store_path``) always agree on what "the same objective_target/objective_scorer"
    means.

    Args:
        result (ScenarioResult): The scenario result to derive identity from.

    Returns:
        tuple[str, str]: The (objective_target, objective_scorer) display labels.
    """
    return resolve_objective_identity(
        objective_target_identifier=result.objective_target_identifier,
        objective_scorer_identifier=result.objective_scorer_identifier,
    )


def _dataset_identity(*, result: ScenarioResult) -> str:
    """
    Derive a stable display string for the scenario's resolved dataset(s).

    Args:
        result (ScenarioResult): The scenario result to derive dataset identity from.

    Returns:
        str: A comma-separated, sorted list of resolved dataset names, or "<unknown>" if unset.
    """
    datasets = result.scenario_identifier.datasets
    return ",".join(sorted(datasets)) if datasets else "<unknown>"


def _build_technique_metrics(*, result: ScenarioResult) -> list[dict[str, Any]]:
    """Aggregate persisted outcomes by technique and adversarial model."""
    objective_target, objective_scorer = _objective_identity(result=result)
    dataset = _dataset_identity(result=result)

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
                "objective_target": objective_target,
                "objective_scorer": objective_scorer,
                "dataset": dataset,
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


def _write_technique_metrics(*, metrics: list[dict[str, Any]], output_dir: Path) -> None:
    """Write per-technique metrics in text, CSV, and JSON formats."""
    (output_dir / "technique-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    fieldnames = [
        "technique",
        "adversarial_model",
        "objective_target",
        "objective_scorer",
        "dataset",
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
        "{:<32} {:<30} {:<24} {:<24} {:<20} {:>4} {:>8} {:>8} {:>6} {:>8} {:>8}".format(
            "Technique",
            "Adversarial model",
            "Objective target",
            "Objective scorer",
            "Dataset",
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
            "{technique:<32} {adversarial_model:<30} {objective_target:<24} {objective_scorer:<24} "
            "{dataset:<20} {total:>4} {success:>8} "
            "{failure:>8} {error:>6} {retry_records:>8} {success_rate:>7.1%}"
        ).format(**metric)
        for metric in metrics
    )
    (output_dir / "technique-metrics.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def upsert_benchmark_metrics(*, metrics: list[dict[str, Any]], store_path: Path) -> None:
    """
    Upsert technique-metrics rows into the committed benchmark metrics JSONL store.

    Rows are keyed by ``_BENCHMARK_METRICS_KEY_FIELDS`` (technique, adversarial_model,
    objective_target, objective_scorer, dataset) — the same composite identity the scenario's
    own cache-reuse logic uses to decide whether a prior result may be reused. An existing row
    with a matching key is replaced; otherwise the new row is appended. This keeps the store a
    single upserted snapshot per unique combination rather than an unbounded per-run log.

    Not underscore-prefixed: also reused by ``build_scripts.import_adversarial_benchmark_snapshot``
    to merge in metrics rows exported elsewhere (e.g. an Azure DevOps pipeline artifact), so both
    entry points share one upsert implementation instead of duplicating the key/merge logic.

    Args:
        metrics (list[dict[str, Any]]): Freshly computed technique-metrics rows to upsert.
        store_path (Path): Path to the committed JSONL store.
    """
    existing: list[dict[str, Any]] = []
    if store_path.exists():
        with open(store_path, encoding="utf-8") as f:
            existing = [json.loads(line) for line in f if line.strip()]

    def _key(entry: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(entry[field] for field in _BENCHMARK_METRICS_KEY_FIELDS)

    new_keys = {_key(entry) for entry in metrics}
    combined = [entry for entry in existing if _key(entry) not in new_keys] + metrics
    combined.sort(key=_key)

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as f:
        for entry in combined:
            f.write(json.dumps(entry) + "\n")


async def _export_async(
    *,
    scenario_result_id: str,
    output_dir: Path,
    update_benchmark_store: bool = False,
    benchmark_store_path: Path | None = None,
) -> None:
    """Export all readable result views."""
    result = await _load_result_async(scenario_result_id=scenario_result_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    await _write_overview_async(result=result, output_dir=output_dir)
    await _write_attacks_async(result=result, output_dir=output_dir)
    metrics = _build_technique_metrics(result=result)
    await asyncio.to_thread(_write_technique_metrics, metrics=metrics, output_dir=output_dir)

    if update_benchmark_store:
        store_path = benchmark_store_path or DEFAULT_BENCHMARK_STORE_PATH
        await asyncio.to_thread(upsert_benchmark_metrics, metrics=metrics, store_path=store_path)


def main() -> None:
    """Run the result exporter."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-result-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--update-benchmark-store",
        action="store_true",
        help=(
            "Upsert this run's technique-metrics rows into the committed benchmark metrics "
            f"JSONL store (default: {DEFAULT_BENCHMARK_STORE_PATH})."
        ),
    )
    parser.add_argument(
        "--benchmark-store-path",
        type=Path,
        default=None,
        help="Override the committed benchmark metrics store path (implies --update-benchmark-store).",
    )
    args = parser.parse_args()
    asyncio.run(
        _export_async(
            scenario_result_id=args.scenario_result_id,
            output_dir=args.output_dir,
            update_benchmark_store=args.update_benchmark_store or args.benchmark_store_path is not None,
            benchmark_store_path=args.benchmark_store_path,
        )
    )


if __name__ == "__main__":
    main()
