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
import os
import sys
import time

from azure.ai.contentsafety.models import TextCategory
from tqdm import tqdm

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import (
    AzureContentFilterScorer,
    FloatScaleThresholdScorer,
    LikertScalePaths,
    RefusalScorerPaths,
    SelfAskLikertScorer,
    SelfAskRefusalScorer,
    SelfAskScaleScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.self_ask_true_false_scorer import (
    SelfAskTrueFalseScorer,
    TrueFalseQuestionPaths,
)
from pyrit.setup import IN_MEMORY, initialize_pyrit_async


async def evaluate_scorers() -> None:
    """
    Evaluate multiple scorers against their configured datasets.

    This will:
    1. Initialize PyRIT with in-memory database
    2. Create a shared chat target for consistency
    3. Instantiate each scorer with appropriate configuration
    4. Run evaluate_async() on each scorer
    5. Save results to scorer_evals directory
    """
    print("Initializing PyRIT...")
    await initialize_pyrit_async(memory_db_type=IN_MEMORY)

    # Targets
    gpt_4o_target = OpenAIChatTarget(
        endpoint=os.environ.get("AZURE_OPENAI_GPT4O_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_GPT4O_KEY"),
        model_name=os.environ.get("AZURE_OPENAI_GPT4O_MODEL"),
    )

    gpt_4o_unsafe = OpenAIChatTarget(
        endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
        model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
    )

    gpt_4o_unsafe_temp9 = OpenAIChatTarget(
        endpoint=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"),
        model_name=os.environ.get("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"),
        temperature=0.9,
    )

    gpt_4o_temp9 = OpenAIChatTarget(
        endpoint=os.environ.get("AZURE_OPENAI_GPT4O_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_GPT4O_KEY"),
        model_name=os.environ.get("AZURE_OPENAI_GPT4O_MODEL"),
        temperature=0.9,
    )

    # Refusal Scorers (all four templates)
    refusal = SelfAskRefusalScorer(
        chat_target=gpt_4o_target,
        refusal_system_prompt_path=RefusalScorerPaths.OBJECTIVE_BLOCK_SAFE,
    )

    refusal_objective_allow_safe = SelfAskRefusalScorer(
        chat_target=gpt_4o_target,
        refusal_system_prompt_path=RefusalScorerPaths.OBJECTIVE_ALLOW_SAFE,
    )

    refusal_no_objective_block_safe = SelfAskRefusalScorer(
        chat_target=gpt_4o_target,
        refusal_system_prompt_path=RefusalScorerPaths.NO_OBJECTIVE_BLOCK_SAFE,
    )

    refusal_no_objective_allow_safe = SelfAskRefusalScorer(
        chat_target=gpt_4o_target,
        refusal_system_prompt_path=RefusalScorerPaths.NO_OBJECTIVE_ALLOW_SAFE,
    )

    refusal_no_objective_allow_safe2 = SelfAskRefusalScorer(
        chat_target=gpt_4o_unsafe,
        refusal_system_prompt_path=RefusalScorerPaths.NO_OBJECTIVE_ALLOW_SAFE,
    )

    refusal_no_objective_allow_safe3= SelfAskRefusalScorer(
        chat_target=gpt_4o_unsafe_temp9,
        refusal_system_prompt_path=RefusalScorerPaths.NO_OBJECTIVE_ALLOW_SAFE,
    )

    # Objective Scorers
    refusal_gpt_4o = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=gpt_4o_target))

    refusal_gpt_4o_unsafe = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=gpt_4o_unsafe))

    refusal_gpt_4o_unsafe_temp9 = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=gpt_4o_unsafe_temp9))

    acs_t1 = FloatScaleThresholdScorer(scorer=AzureContentFilterScorer(), threshold=0.1)
    acs_t5 = FloatScaleThresholdScorer(scorer=AzureContentFilterScorer(), threshold=0.5)
    acs_t7 = FloatScaleThresholdScorer(scorer=AzureContentFilterScorer(), threshold=0.7)

    acs_with_refusal = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[
            FloatScaleThresholdScorer(scorer=AzureContentFilterScorer(), threshold=0.5),
            TrueFalseInverterScorer(
                scorer=SelfAskRefusalScorer(chat_target=gpt_4o_unsafe_temp9),
            ),
        ],
    )

    _scale_scorer_gpt_4o = SelfAskScaleScorer(
        chat_target=gpt_4o_temp9,
    )

    task_achieved_tf_scorer = SelfAskTrueFalseScorer(
        chat_target=gpt_4o_temp9,
        true_false_question_path=TrueFalseQuestionPaths.TASK_ACHIEVED.value,
    )

    scale_gpt_4o_9_threshhold = FloatScaleThresholdScorer(scorer=_scale_scorer_gpt_4o, threshold=0.9)

    scale_and_refusal = TrueFalseCompositeScorer(
        scorers=[scale_gpt_4o_9_threshhold, refusal_gpt_4o],
        aggregator=TrueFalseScoreAggregator.AND,
    )

    # Harm Scorers
    likert_scorers_gpt_4o = []
    for scale in LikertScalePaths:
        if scale.evaluation_files is not None:
            likert_scorers_gpt_4o.append(
                SelfAskLikertScorer(
                    chat_target=gpt_4o_target,
                    likert_scale=scale,
                )
            )

    acs_hate = AzureContentFilterScorer(harm_categories=[TextCategory.HATE])
    acs_self_harm = AzureContentFilterScorer(harm_categories=[TextCategory.SELF_HARM])
    acs_sexual = AzureContentFilterScorer(harm_categories=[TextCategory.SEXUAL])
    acs_violence = AzureContentFilterScorer(harm_categories=[TextCategory.VIOLENCE])

    # Build list of scorers to evaluate
    scorers = [
        refusal,
        refusal_objective_allow_safe,
        refusal_no_objective_block_safe,
        refusal_no_objective_allow_safe,
        refusal_no_objective_allow_safe2,
        refusal_no_objective_allow_safe3,
        # refusal_gpt_4o,
        # refusal_gpt_4o_unsafe,
        # refusal_gpt_4o_unsafe_temp9,
        # acs_t1,
        # acs_t5,
        # acs_t7,
        # acs_with_refusal,
        # scale_gpt_4o_9_threshhold,
        # scale_and_refusal,
        # acs_hate,
        # acs_self_harm,
        # acs_sexual,
        # acs_violence,
        # task_achieved_tf_scorer,
    ]

    # scorers.extend(likert_scorers_gpt_4o)

    print(f"\nEvaluating {len(scorers)} scorer(s)...\n")

    # Use tqdm for progress tracking across all scorers
    scorer_iterator = tqdm(enumerate(scorers, 1), total=len(scorers), desc="Scorers") if tqdm else enumerate(scorers, 1)

    # Evaluate each scorer
    for i, scorer in scorer_iterator:
        scorer_name = scorer.__class__.__name__
        print(f"\n[{i}/{len(scorers)}] Evaluating {scorer_name}...")
        print("  Status: Starting evaluation (this may take several minutes)...")

        start_time = time.time()

        try:
            # Run evaluation with production settings:
            # - num_scorer_trials=3 for variance measurement
            # - add_to_evaluation_results=True to save to registry
            print("  Status: Running evaluations...")
            results = await scorer.evaluate_async(
                num_scorer_trials=3,
                max_concurrency=10,
            )

            elapsed_time = time.time() - start_time

            # Results are saved to disk by evaluate_async() with add_to_evaluation_results=True
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
