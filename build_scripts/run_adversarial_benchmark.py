# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Manual script for (re-)running the AdversarialBenchmark scenario.

This is a long-running process that should be run occasionally to refresh the
adversarial benchmark results backing the metrics dashboard. By default, any
(technique, adversarial model, objective target, objective scorer, dataset) combination
already present in the committed ``adversarial_benchmark_metrics.jsonl`` store — or
already completed in memory (matched via the live behavioral cache) — is skipped, so
re-running this script after adding a new adversarial target only executes the
newly-added combinations. Pass --force to ignore both caches and re-run everything.

This only runs the scenario and prints its scenario_result_id. Export the readable
result views and (optionally) update the committed benchmark metrics store with
build_scripts/export_adversarial_benchmark_result.py, either manually afterward or
automatically by passing --output-dir here.

Usage:
    python -m build_scripts.run_adversarial_benchmark \
        --objective-target openai_chat \
        --adversarial-targets gpt-4o gpt-4o-mini

    python -m build_scripts.run_adversarial_benchmark \
        --objective-target openai_chat \
        --adversarial-targets gpt-4o \
        --output-dir benchmark_results/latest

    python -m build_scripts.run_adversarial_benchmark \
        --objective-target openai_chat \
        --adversarial-targets gpt-4o \
        --force
"""

import argparse
import asyncio
import subprocess
import sys
import traceback
from pathlib import Path

from build_scripts.export_adversarial_benchmark_result import DEFAULT_BENCHMARK_STORE_PATH
from pyrit.models import ScenarioResult
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.setup import SQLITE, initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer, TechniqueInitializer


async def run_adversarial_benchmark_async(
    *,
    objective_target: str,
    adversarial_targets: list[str],
    max_concurrency: int = 4,
    force: bool = False,
    benchmark_store_path: Path | None = None,
) -> ScenarioResult:
    """
    Initialize PyRIT and run one AdversarialBenchmark scenario.

    This will:
    1. Initialize PyRIT with a persistent SQLite database (required so a later,
       separate export_adversarial_benchmark_result.py invocation can load the result
       by scenario_result_id).
    2. Register targets, scorers, and techniques from their respective initializers.
    3. Construct AdversarialBenchmark with the store-based and live-cache skip filters
       enabled, unless --force is set.
    4. Resolve objective_target/adversarial_targets and run the scenario.

    Args:
        objective_target: Registry name of the target under attack.
        adversarial_targets: Registry names of adversarial chat targets to benchmark.
        max_concurrency: Maximum number of concurrent units of work. Defaults to 4.
        force: When True, ignore both the committed benchmark metrics store and the
            live behavioral cache, so every (technique, adversarial model, dataset)
            combination runs regardless of prior results. Defaults to False.
        benchmark_store_path: Optional override for the committed benchmark metrics
            JSONL store path used to skip already-exported combinations. Ignored when
            force is True. Defaults to the exporter's own default store path.

    Returns:
        ScenarioResult: The scenario result (skipped combinations are not re-executed
        but do not appear in the result either — see AdversarialBenchmark's
        benchmark_store_path docstring). Its ``id`` is the value to pass to
        export_adversarial_benchmark_result.py --scenario-result-id.
    """
    print("Initializing PyRIT...")
    technique_init = TechniqueInitializer()
    technique_init.params = {"tags": ["all"]}
    target_init = TargetInitializer()
    target_init.params = {"tags": ["all"]}
    scorer_init = ScorerInitializer()
    await initialize_pyrit_async(
        memory_db_type=SQLITE,
        initializers=[technique_init, target_init, scorer_init],
    )

    store_path = None if force else (benchmark_store_path or DEFAULT_BENCHMARK_STORE_PATH)
    benchmark = AdversarialBenchmark(use_cached=not force, benchmark_store_path=store_path)
    benchmark.set_params_from_args(
        args={
            "objective_target": objective_target,
            "adversarial_targets": adversarial_targets,
            "max_concurrency": max_concurrency,
        }
    )

    print(f"Running AdversarialBenchmark against {len(adversarial_targets)} adversarial target(s)...")
    if store_path is not None:
        print(f"  Skipping combinations already present in: {store_path}")
    await benchmark.initialize_async()
    return await benchmark.run_async()


def _build_export_command(
    *,
    scenario_result_id: str,
    output_dir: Path,
    benchmark_store_path: Path | None,
) -> list[str]:
    """
    Build the export_adversarial_benchmark_result CLI invocation for this run.

    Args:
        scenario_result_id: The freshly-produced scenario result id to export.
        output_dir: Directory the exporter should write readable result views to.
        benchmark_store_path: Optional override for the committed benchmark metrics
            store path; omitted falls back to the exporter's own default.

    Returns:
        list[str]: The argv (including the interpreter) to invoke the exporter.
    """
    command = [
        sys.executable,
        "-m",
        "build_scripts.export_adversarial_benchmark_result",
        "--scenario-result-id",
        scenario_result_id,
        "--output-dir",
        str(output_dir),
        "--update-benchmark-store",
    ]
    if benchmark_store_path is not None:
        command.extend(["--benchmark-store-path", str(benchmark_store_path)])
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments. ``argv`` defaults to ``sys.argv[1:]`` when omitted."""
    parser = argparse.ArgumentParser(
        description="Run the AdversarialBenchmark scenario, skipping already-exported combinations by default.",
    )
    parser.add_argument(
        "--objective-target",
        type=str,
        required=True,
        help="Registry name of the target under attack (see 'pyrit_scan list-targets').",
    )
    parser.add_argument(
        "--adversarial-targets",
        type=str,
        required=True,
        nargs="+",
        help="Registry names of adversarial chat targets to benchmark (space-separated).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="Maximum number of concurrent units of work (default: 4).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore the committed benchmark metrics store and the live behavioral cache, "
            "re-running every combination regardless of prior results."
        ),
    )
    parser.add_argument(
        "--benchmark-store-path",
        type=Path,
        default=None,
        help=f"Override the committed benchmark metrics store path (default: {DEFAULT_BENCHMARK_STORE_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "When set, automatically export readable result views and upsert the committed "
            "benchmark metrics store after the scenario completes. When omitted, only the "
            "scenario_result_id and the follow-up export command are printed."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("PyRIT Adversarial Benchmark Script")
    print("=" * 60)
    print(f"Objective target: {args.objective_target}")
    print(f"Adversarial targets: {args.adversarial_targets}")
    print(f"Force re-run: {args.force}")
    print("=" * 60)
    print()

    try:
        scenario_result = asyncio.run(
            run_adversarial_benchmark_async(
                objective_target=args.objective_target,
                adversarial_targets=args.adversarial_targets,
                max_concurrency=args.max_concurrency,
                force=args.force,
                benchmark_store_path=args.benchmark_store_path,
            )
        )
        print()
        print("✓ Scenario run complete!")
        print(f"  scenario_result_id: {scenario_result.id}")

        if args.output_dir is not None:
            export_command = _build_export_command(
                scenario_result_id=str(scenario_result.id),
                output_dir=args.output_dir,
                benchmark_store_path=args.benchmark_store_path,
            )
            print(f"  Exporting results to {args.output_dir} and updating the benchmark metrics store...")
            subprocess.run(export_command, check=True)
        else:
            print("  Next step — export results and update the committed benchmark metrics store:")
            print(
                "    python -m build_scripts.export_adversarial_benchmark_result "
                f"--scenario-result-id {scenario_result.id} --output-dir <output-dir> --update-benchmark-store"
            )
    except KeyboardInterrupt:
        print("\n\nRun interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
