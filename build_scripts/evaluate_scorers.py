# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Manual script for evaluating multiple scorers against human-labeled datasets.

This is a long-running process that should be run occasionally to benchmark
scorer performance. Results are saved to the scorer_evals directory and checked in.
Blocked judge requests produce undetermined scores. Metrics exclude responses
without a determined final verdict in every trial, and the script reports coverage.

Usage:
    python -m build_scripts.evaluate_scorers
    python -m build_scripts.evaluate_scorers --tags refusal
    python -m build_scripts.evaluate_scorers --tags refusal,default
    python -m build_scripts.evaluate_scorers --max-concurrency 3
"""

import argparse
import asyncio
import sys
import time

from tqdm import tqdm

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.exceptions.exception_context import ComponentRole, execution_context
from pyrit.registry import ScorerRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer
from pyrit.setup.initializers.scorers import ScorerInitializerTags


async def evaluate_scorers(tags: list[str] | None = None, max_concurrency: int = 5) -> None:
    """
    Evaluate multiple scorers against their configured datasets.

    This will:
    1. Initialize PyRIT with in-memory database
    2. Register all scorers from ScorerInitializer into the ScorerRegistry
    3. Select registered scorers by tags, excluding those tagged skip_batch_evaluation
    4. Run evaluate_async() on each scorer
    5. Save results to scorer_evals directory

    Blocked judgments are undetermined rather than fatal. Other evaluation errors,
    including no determined results, cause failure after the remaining scorers run.

    Args:
        tags: Optional list of tags to filter which scorers to evaluate.
            When provided, only eligible scorers matching any of the tags are evaluated.
            When None, all eligible scorers are evaluated. Explicit batch-evaluation
            exclusions apply in both cases.
        max_concurrency: Maximum number of concurrent scoring requests per scorer.
            Defaults to 5.

    Raises:
        RuntimeError: If no eligible scorers are selected, or any evaluation fails or returns no metrics.
    """
    print("Initializing PyRIT...")
    target_init = TargetInitializer()
    target_init.set_params_from_args(args={"tags": ["default", "scorer"]})
    scorer_init = ScorerInitializer()
    await initialize_pyrit_async(
        memory_db_type=IN_MEMORY,
        initializers=[target_init, scorer_init],
    )

    registry = ScorerRegistry.get_registry_singleton()

    # Filter scorers by tags if specified
    if tags:
        scorer_names: list[str] = []
        for tag in tags:
            entries = registry.instances.get_by_tag(tag=tag)
            scorer_names.extend(entry.name for entry in entries if entry.name not in scorer_names)
        scorer_names.sort()
        print(f"\nFiltering by tags: {tags}")
    else:
        scorer_names = registry.instances.get_names()

    excluded_names = {
        entry.name for entry in registry.instances.get_by_tag(tag=ScorerInitializerTags.SKIP_BATCH_EVALUATION)
    }
    for scorer_name in scorer_names:
        if scorer_name in excluded_names:
            print(f"Skipping {scorer_name}: tagged '{ScorerInitializerTags.SKIP_BATCH_EVALUATION.value}'.")
    scorer_names = [name for name in scorer_names if name not in excluded_names]

    if not scorer_names:
        raise RuntimeError(
            "No eligible scorers selected for evaluation. Check environment variable configuration and tag filters."
        )

    print(f"\nEvaluating {len(scorer_names)} scorer(s)...\n")

    # Use tqdm for progress tracking across all scorers
    scorer_iterator = (
        tqdm(enumerate(scorer_names, 1), total=len(scorer_names), desc="Scorers")
        if tqdm
        else enumerate(scorer_names, 1)
    )

    failed_scorers: list[str] = []
    # Evaluate each scorer
    for i, scorer_name in scorer_iterator:
        print(f"\n[{i}/{len(scorer_names)}] Evaluating {scorer_name}...")
        print("  Status: Starting evaluation (this may take several minutes)...")

        start_time = time.time()

        try:
            scorer = registry.instances.get(scorer_name)
            if scorer is None:
                raise ValueError(f"Scorer '{scorer_name}' is no longer registered.")
            scorer = scorer.with_scorer_block_policy(raise_if_scorer_blocks=False)
            print("  Status: Running evaluations...")
            with execution_context(
                component_role=ComponentRole.OBJECTIVE_SCORER,
                component_identifier=scorer.get_identifier(),
            ):
                results = await scorer.evaluate_async(
                    num_scorer_trials=3,
                    max_concurrency=max_concurrency,
                )

            if results is None:
                raise ValueError("Evaluation returned no metrics; check the configured datasets.")

            elapsed_time = time.time() - start_time

            print("  ✓ Evaluation complete (new or cached metrics).")
            print(f"    Elapsed time: {elapsed_time:.1f}s")
            print(f"    Dataset: {results.dataset_name}")
            if results.num_input_responses is None:
                print(f"    Coverage: unknown; {results.num_responses} evaluated, input/excluded counts unavailable.")
            else:
                excluded = results.num_input_responses - results.num_responses
                print(
                    f"    Coverage: {results.num_responses}/{results.num_input_responses} responses evaluated; "
                    f"{excluded} excluded."
                )
                if excluded:
                    print(
                        "    WARNING: Partial coverage; responses without a determined final verdict "
                        "in every trial were excluded from all metric calculations."
                    )

        except Exception as e:
            failed_scorers.append(scorer_name)
            elapsed_time = time.time() - start_time
            print(f"  ✗ Error evaluating {scorer_name} after {elapsed_time:.1f}s: {e}")
            print("    Continuing with next scorer...\n")
            import traceback

            traceback.print_exc()
            continue

    if failed_scorers:
        raise RuntimeError(f"Evaluation failed for {len(failed_scorers)} scorer(s): {', '.join(failed_scorers)}")

    print("=" * 60)
    print("Evaluation complete (new or cached metrics).")
    print(f"Results location: {SCORER_EVALS_PATH}")
    print("=" * 60)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Evaluate PyRIT scorers against human-labeled datasets.",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help=(
            "Comma-separated list of tags to filter which scorers to evaluate (e.g., --tags refusal,default). "
            "Scorers tagged skip_batch_evaluation remain excluded."
        ),
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=5,
        help="Maximum number of concurrent scoring requests per scorer (default: 5)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    tag_list = [t.strip() for t in args.tags.split(",")] if args.tags else None
    max_concurrency = args.max_concurrency

    print("=" * 60)
    print("PyRIT Scorer Evaluation Script")
    print("=" * 60)
    print("This script will evaluate multiple scorers against human-labeled")
    print("datasets. This is a long-running process that may take several")
    print("minutes to hours depending on the number of scorers and datasets.")
    print()
    if tag_list:
        print(f"Filtering by tags: {tag_list}")
    print(f"Max concurrency: {max_concurrency}")
    print("Results will be saved to the registry files in:")
    print(f"  {SCORER_EVALS_PATH}")
    print("=" * 60)
    print()

    try:
        asyncio.run(evaluate_scorers(tags=tag_list, max_concurrency=max_concurrency))
    except KeyboardInterrupt:
        print("\n\nEvaluation interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
