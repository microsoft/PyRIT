# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Manual script for evaluating multiple scorers against human-labeled datasets.

This is a long-running process that should be run occasionally to benchmark
scorer performance. Results are saved to the scorer_evals directory and checked in.

Usage:
    python build_scripts/evaluate_scorers.py
"""

import asyncio
import sys
import time

from tqdm import tqdm

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.registry import ScorerRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer


async def evaluate_scorers() -> None:
    """
    Evaluate multiple scorers against their configured datasets.

    This will:
    1. Initialize PyRIT with in-memory database
    2. Register all scorers from ScorerInitializer into the ScorerRegistry
    3. Iterate through all registered scorers
    4. Run evaluate_async() on each scorer
    5. Save results to scorer_evals directory
    """
    print("Initializing PyRIT...")
    target_init = TargetInitializer()
    target_init.params = {"tags": ["default", "scorer"]}
    await initialize_pyrit_async(
        memory_db_type=IN_MEMORY,
        initializers=[target_init, ScorerInitializer()],
    )

    registry = ScorerRegistry.get_registry_singleton()
    scorer_names = registry.get_names()

    if not scorer_names:
        print("No scorers registered. Check environment variable configuration.")
        return

    print(f"\nEvaluating {len(scorer_names)} scorer(s)...\n")

    # Use tqdm for progress tracking across all scorers
    scorer_iterator = (
        tqdm(enumerate(scorer_names, 1), total=len(scorer_names), desc="Scorers")
        if tqdm
        else enumerate(scorer_names, 1)
    )

    # Evaluate each scorer
    for i, scorer_name in scorer_iterator:
        scorer = registry.get_instance_by_name(scorer_name)
        print(f"\n[{i}/{len(scorer_names)}] Evaluating {scorer_name}...")
        print("  Status: Starting evaluation (this may take several minutes)...")

        start_time = time.time()

        try:
            print("  Status: Running evaluations...")
            results = await scorer.evaluate_async(
                num_scorer_trials=3,
                max_concurrency=10,
            )

            elapsed_time = time.time() - start_time

            print("  ✓ Evaluation complete and saved!")
            print(f"    Elapsed time: {elapsed_time:.1f}s")
            if results:
                print(f"    Dataset: {results.dataset_name}")

        except Exception as e:
            elapsed_time = time.time() - start_time
            print(f"  ✗ Error evaluating {scorer_name} after {elapsed_time:.1f}s: {e}")
            print("    Continuing with next scorer...\n")
            import traceback

            traceback.print_exc()
            continue

    print("=" * 60)
    print("Evaluation complete!")
    print(f"Results saved to: {SCORER_EVALS_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    print("=" * 60)
    print("PyRIT Scorer Evaluation Script")
    print("=" * 60)
    print("This script will evaluate multiple scorers against human-labeled")
    print("datasets. This is a long-running process that may take several")
    print("minutes to hours depending on the number of scorers and datasets.")
    print()
    print("Results will be saved to the registry files in:")
    print(f"  {SCORER_EVALS_PATH}")
    print("=" * 60)
    print()

    try:
        asyncio.run(evaluate_scorers())
    except KeyboardInterrupt:
        print("\n\nEvaluation interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
