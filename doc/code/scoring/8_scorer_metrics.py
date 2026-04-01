# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: pyrit (3.13.5)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 8. Scorer Metrics
#
# This demo will walk you through how to measure and gauge performance of PyRIT scoring configurations, both harm scorers and objective scorers.
#
#
# ## Understanding Scorer Metrics
#
# PyRIT has metrics for many scorers checked in. Before diving into how to retrieve or create metrics, it's important to understand what they measure and how scorer identity determines which metrics apply.
#
# ### Scorer Identity
#
# Every scorer has a unique **identity hash** computed from its complete configuration:
# - Scorer type (e.g., `SelfAskRefusalScorer`)
# - System and user prompt templates
# - Target model information (model name)
# - Temperature and other generation parameters
# - Any scorer-specific configuration
#
# This means changing *any* of these values creates a new scorer identity. The reason these are variables is because they _might_ change performance—does changing the temperature increase or decrease accuracy? Metrics let you experiment and find out.
#
# Metrics are stored and retrieved by this identity hash, so the same scorer configuration will always get the same cached metrics.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import SelfAskRefusalScorer
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Create a refusal scorer
refusal_scorer = SelfAskRefusalScorer(chat_target=OpenAIChatTarget(temperature=0.9))

# View the scorer's full identity - this determines which metrics apply
scorer_identity = refusal_scorer.get_identifier()
print("Scorer Identity:")
print(f"  Type: {scorer_identity.class_name}")
system_prompt = scorer_identity.params.get("system_prompt_template")
print(f"  System Prompt: {system_prompt[:50] if system_prompt else 'None'}...")
prompt_target = scorer_identity.get_child("prompt_target")
print(f"  Target Info: {prompt_target}")
print(f"  Identity Hash: {scorer_identity.hash}")

# %% [markdown]
# ### Objective Metrics
#
# Objective scorers produce true/false outputs, usually whether an objective was met (e.g., did this response have instructions on "how to make a Molotov cocktail?"). We evaluate them using standard classification metrics by comparing model predictions against human-labeled ground truth.
#
# - **Accuracy**: Proportion of predictions matching human labels. Simple but can be misleading with imbalanced datasets.
# - **Precision**: Of all "true" predictions, how many were correct? High precision = few false positives.
# - **Recall**: Of all actual "true" cases, how many did we catch? High recall = few false negatives.
# - **F1 Score**: Harmonic mean of precision and recall. Balances both concerns.
# - **Accuracy Standard Error**: Statistical uncertainty in accuracy estimate, useful for confidence intervals.
#
# **Which metric matters most?**
# - If false positives are costly (e.g., flagging safe content as harmful) → prioritize **precision**
# - If false negatives are costly (e.g., missing actual jailbreaks) → prioritize **recall**
# - For balanced scenarios → use **F1 score**

# %% [markdown]
# ### Harm Metrics
#
# Harm scorers produce float scores (0.0-1.0) representing severity. Since these are continuous values, we use different metrics that capture how close the model's scores are to human judgments.
#
# **Error Metrics:**
# - **Mean Absolute Error (MAE)**: Average absolute difference between model and human scores. An MAE of 0.15 means the model is off by 0.15 on average.
# - **MAE Standard Error**: Uncertainty in the MAE estimate.
#
# **Statistical Significance:**
# - **t-statistic**: From a one-sample t-test. Positive = model scores higher than humans; negative = lower.
# - **p-value**: If small (e.g., < 0.05), the difference between model and human scores is statistically significant (not due to chance).
#
# **Inter-Rater Reliability (Krippendorff's Alpha):**
# Measures agreement between evaluators, ranging from -1.0 to 1.0:
# - **1.0**: Perfect agreement
# - **0.8+**: Strong agreement
# - **0.6-0.8**: Moderate agreement
# - **< 0.6**: Weak agreement
#
# Three alpha values are reported:
# - **`krippendorff_alpha_humans`**: Agreement among human evaluators (baseline quality of labels)
# - **`krippendorff_alpha_model`**: Agreement across multiple model scoring trials (model consistency)
# - **`krippendorff_alpha_combined`**: Overall agreement between humans and model

# %% [markdown]
# ## Retrieving Scorer Metrics
#
# When scorer metrics are calculated with `evaluate_async()`, they can be saved to JSONL registry files and retrieved without re-running the evaluation. The PyRIT team has pre-computed metrics for common scorer configurations which you can access immediately.
#
# ### Retrieving Cached Metrics for a Scorer
#
# Use `get_scorer_metrics()` on any scorer instance to retrieve cached results matching its identity:

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import (
    SelfAskRefusalScorer,
    TrueFalseInverterScorer,
)

# This is a simple objective scorer that only detects whether the response was a refusal
gpt4o_endpoint = os.environ.get("AZURE_OPENAI_GPT4O_ENDPOINT")
objective_scorer = TrueFalseInverterScorer(
    scorer=SelfAskRefusalScorer(
        chat_target=OpenAIChatTarget(
            endpoint=gpt4o_endpoint,
            api_key=get_azure_openai_auth(gpt4o_endpoint),
            model_name=os.environ.get("AZURE_OPENAI_GPT4O_MODEL"),
        )
    )
)

# Retrieve pre-computed metrics (from PyRIT team's evaluation runs)
# using the scorer's identity hash
cached_metrics = objective_scorer.get_scorer_metrics()

if cached_metrics:
    print("Evaluation Metrics:")
    print(f"  Dataset Name: {cached_metrics.dataset_name}")
    print(f"  Dataset Version: {cached_metrics.dataset_version}")
    print(f"  F1 Score: {cached_metrics.f1_score}")
    print(f"  Accuracy: {cached_metrics.accuracy}")
    print(f"  Precision: {cached_metrics.precision}")
    print(f"  Recall: {cached_metrics.recall}")
    print(f"  Avg Score time: {cached_metrics.average_score_time_seconds} seconds")
else:
    print("No cached metrics found for this scorer configuration.")

# %% [markdown]
# Harm scorer metrics are retrieved similarly:

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import LikertScalePaths, SelfAskLikertScorer

gpt4o_endpoint = os.environ.get("AZURE_OPENAI_GPT4O_ENDPOINT")
harm_scorer = SelfAskLikertScorer(
    chat_target=OpenAIChatTarget(
        endpoint=gpt4o_endpoint,
        api_key=get_azure_openai_auth(gpt4o_endpoint),
        model_name=os.environ.get("AZURE_OPENAI_GPT4O_MODEL"),
    ),
    likert_scale=LikertScalePaths.EXPLOITS_SCALE,
)

# Retrieve pre-computed metrics using the scorer's identity hash
harm_metrics = harm_scorer.get_scorer_metrics()

if harm_metrics:
    print("Evaluation Metrics:")
    print(f"  Dataset Name: {harm_metrics.dataset_name}")
    print(f"  Dataset Version: {harm_metrics.dataset_version}")
    print(f"  Mean Absolute Error: {harm_metrics.mean_absolute_error:.3f}")
    print(f"  Krippendorff Alpha: {harm_metrics.krippendorff_alpha_combined:.3f}")
    print(f"  P value: {harm_metrics.p_value:.4f}")
    print(f"  Avg Score time: {harm_metrics.average_score_time_seconds} seconds")

else:
    print("No cached metrics found for this scorer configuration.")

# %% [markdown]
# ### Comparing All Scorer Configurations
#
# The evaluation registry stores metrics for all tested scorer configurations. You can load all entries to compare which configurations perform best.
#
# Use `get_all_objective_metrics()` or `get_all_harm_metrics()` to load evaluation results. These return `ScorerMetricsWithIdentity` objects with clean attribute access to both the scorer identity and its metrics.

# %%
from pyrit.score import ConsoleScorerPrinter, get_all_objective_metrics

# Load all objective scorer metrics - returns ScorerMetricsWithIdentity[ObjectiveScorerMetrics]
all_scorers = get_all_objective_metrics()

print(f"Found {len(all_scorers)} scorer configurations in the metrics file\n")

# Sort by F1 score - type checker knows entry.metrics is ObjectiveScorerMetrics
sorted_by_f1 = sorted(all_scorers, key=lambda x: x.metrics.f1_score, reverse=True)

print("Top 5 configurations by F1 Score:")
print("-" * 80)
for _i, entry in enumerate(sorted_by_f1[:5], 1):
    printer = ConsoleScorerPrinter()
    printer.print_objective_scorer(scorer_identifier=entry.scorer_identifier)

# Show best by each metric
print("\n" + "=" * 80)
print(f"Best Accuracy:  {max(all_scorers, key=lambda x: x.metrics.accuracy).metrics.accuracy:.2%}")
print(f"Best Precision: {max(all_scorers, key=lambda x: x.metrics.precision).metrics.precision:.3f}")
print(f"Best Recall:    {max(all_scorers, key=lambda x: x.metrics.recall).metrics.recall:.3f}")
print(
    f"Fastest:        {min(all_scorers, key=lambda x: x.metrics.average_score_time_seconds).metrics.average_score_time_seconds:.3f} seconds"
)
print(
    f"Slowest:        {max(all_scorers, key=lambda x: x.metrics.average_score_time_seconds).metrics.average_score_time_seconds:.3f} seconds"
)

# %% [markdown]
# Similarly, you can look at the best harm scorers for a given category:

# %%
from pyrit.score import ConsoleScorerPrinter, get_all_harm_metrics

# Load all harm scorer metrics for a specific category
all_harm_scorers = get_all_harm_metrics(harm_category="violence")

print(f"Found {len(all_harm_scorers)} harm scorer configurations for violence\n")

# Sort by mean absolute error (lower is better)
sorted_by_mae = sorted(all_harm_scorers, key=lambda x: x.metrics.mean_absolute_error)

print("Top configurations by Mean Absolute Error:")
print("-" * 80)
for _i, e in enumerate(sorted_by_mae[:5], 1):
    printer = ConsoleScorerPrinter()
    printer.print_harm_scorer(scorer_identifier=e.scorer_identifier, harm_category="violence")

# %% [markdown]
# ## Creating Scorer Metrics
#
# This section covers how to create new metrics by running evaluations against human-labeled datasets.
#
# ### Caching and Skip Logic
#
# When you call `evaluate_async()` on a scorer, the evaluation framework follows a smart caching strategy to avoid redundant work. It checks the metrics registry (a JSONL file) for an existing entry matching the scorer's identity hash. The decision to skip or run evaluation depends on:
#
# 1. **No existing entry**: Run the full evaluation
# 2. **Dataset version or harm definition version changed**: Re-run and replace the old entry (assumes newer dataset/newer scoring criteria for harm is authoritative)
# 3. **Same version, sufficient trials**: Skip if existing `num_scorer_trials >= requested` (existing metrics are good enough)
# 4. **Same version, fewer trials**: Re-run with more trials and replace (higher fidelity needed)
#
# During evaluation, the scorer processes each entry from human-labeled CSV dataset(s). For each `assistant_response` in the CSV, the scorer generates predictions which are compared against the `human_score` column(s). For objective scorers, this produces accuracy/precision/recall/F1 metrics. For harm scorers, it calculates MAE, t-statistics, and Krippendorff's alpha.
#
# Setting `add_to_evaluation_results=False` bypasses caching entirely—always running fresh evaluations without reading from or writing to the registry. This is useful for testing custom configurations without polluting the official metrics.

# %% [markdown]
# ### Running an Objective Evaluation
#
# Call `evaluate_async()` on any scorer instance. The scorer's identity (including system prompt, model, temperature) determines which cached results apply.

# %%
from typing import cast

from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import (
    ObjectiveScorerMetrics,
    RegistryUpdateBehavior,
    ScorerEvalDatasetFiles,
    SelfAskRefusalScorer,
)

# Create a refusal scorer - uses the chat target to determine if responses are refusals
refusal_scorer = SelfAskRefusalScorer(chat_target=OpenAIChatTarget())

# REAL usage would simply be:
# metrics = await refusal_scorer.evaluate_async()

# For demonstration, use a smaller evaluation file (normally you'd use the full dataset)
# The evaluation_file_mapping tells the evaluator which human-labeled CSV files to use
refusal_scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
    human_labeled_datasets_files=["sample/mini_refusal.csv"],
    result_file="sample/test_refusal_metrics.jsonl",
)

# Run evaluation with:
# - num_scorer_trials=1: Score each response once (use 3+ for production to measure variance)
# - RegistryUpdateBehavior.NEVER_UPDATE: Don't save to the official registry (for testing only)
metrics = await refusal_scorer.evaluate_async(  # type: ignore
    num_scorer_trials=1, update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE
)

if metrics:
    objective_metrics = cast("ObjectiveScorerMetrics", metrics)
    print(f" Accuracy: {objective_metrics.accuracy}")
else:
    raise RuntimeError("Evaluation failed, no metrics returned")

# %% [markdown]
# ### Running a Harm Evaluation

# %%
from typing import cast

from pyrit.score import LikertScalePaths, RegistryUpdateBehavior, SelfAskLikertScorer
from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles
from pyrit.score.scorer_evaluation.scorer_metrics import HarmScorerMetrics

# Create a harm scorer using the hate speech Likert scale
likert_scorer = SelfAskLikertScorer(chat_target=OpenAIChatTarget(), likert_scale=LikertScalePaths.EXPLOITS_SCALE)

# # Configure evaluation to use a small sample dataset
# likert_scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
#     human_labeled_datasets_files=["harm/mini_hate_speech.csv"],
#     result_file="harm/test_hate_speech_metrics.jsonl",
#     harm_category="hate_speech",  # Required for harm evaluations
# )

# This can be called without parameters to update the registry
metrics = await likert_scorer.evaluate_async(  # type: ignore
    num_scorer_trials=1, update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE
)

if metrics:
    harm_metrics = cast("HarmScorerMetrics", metrics)
    print(f'Metrics for harm category "{harm_metrics.harm_category}" created')
else:
    raise RuntimeError("Evaluation failed, no metrics returned")

# %% [markdown]
# ### Custom Evaluation Datasets
#
# You can evaluate scorers against your own human-labeled datasets using `ScorerEvalDatasetFiles`:
#
# ```python
# # Configure custom datasets
# scorer.evaluation_file_mapping = ScorerEvalDatasetFiles(
#     # Glob patterns relative to SCORER_EVALS_PATH
#     human_labeled_datasets_files=["my_datasets/*.csv"],
#     # Where to store results
#     result_file="my_datasets/evaluation_metrics.jsonl",
#     # Required for harm scorers, ignored for objective scorers
#     harm_category="violence",
# )
# ```
#
# ### CSV Human Evaluation Files
#
# Many human scored dataset csv files are available in the `pyrit/datasets/scorer_evals/` directory. These include datasets for refusal detection, hate speech, violence, and other harm categories. You can reference these as templates for creating your own evaluation datasets.
#
# **Required columns:**
# - `assistant_response`: The text to score
# - `objective`: Required for **objective scorers** - the scoring question (e.g., "Did the response refuse?")
# - `harm_category`: Required for **harm scorers** - the harm category (e.g., "hate_speech", "violence")
# - `human_score` or `human_score_1`, `human_score_2`, etc.: Ground truth labels from human raters
#   - For objective scorers: 0 or 1 (converted to bool)
#   - For harm scorers: 0.0-1.0 float values
# - `data_type`: Type of content (defaults to "text")

# %% [markdown]
# ## Batch Evaluation with `evaluate_scorers.py`
#
# While `evaluate_async()` runs evaluations for a single scorer, the `evaluate_scorers.py` script
# evaluates **all registered scorers** in bulk. This is useful for benchmarking after changing scorer
# prompts, adding new variants, or updating human-labeled datasets.
#
# The script initializes PyRIT with `ScorerInitializer` (which registers all configured scorers),
# then runs `evaluate_async()` on each one. Results are saved to the JSONL registry files in
# `pyrit/datasets/scorer_evals/`.
#
# ### Basic Usage
#
# ```bash
# # Evaluate all registered scorers (long-running — can take hours)
# python build_scripts/evaluate_scorers.py
#
# # Evaluate only scorers with specific tags
# python build_scripts/evaluate_scorers.py --tags refusal
# python build_scripts/evaluate_scorers.py --tags refusal,default
#
# # Control parallelism (default: 5, lower if hitting rate limits)
# python build_scripts/evaluate_scorers.py --max-concurrency 3
# ```
#
# ### Tags
#
# `ScorerInitializer` applies tags to scorers during registration. These tags let you target
# specific subsets for evaluation:
#
# - `refusal` — The 4 standalone refusal scorer variants
# - `default` — All scorers registered by default
# - `best_refusal_f1` — The refusal variant with the highest F1 (set dynamically from metrics)
# - `best_objective_f1` — The objective scorer with the highest F1
#
# ### Recommended Workflow: Refusal → Dependent Scorers
#
# When refusal scorer prompts or datasets change, the recommended workflow is:
#
# **Step 1: Evaluate refusal scorers first**
#
# ```bash
# python build_scripts/evaluate_scorers.py --tags refusal
# ```
#
# This evaluates only the 4 refusal variants and writes results to
# `refusal_scorer/refusal_metrics.jsonl`. After this step, `ScorerInitializer` can determine which
# refusal variant has the best F1 and tag it as `best_refusal_f1`.
#
# **Step 2: Re-evaluate all scorers**
#
# ```bash
# python build_scripts/evaluate_scorers.py
# ```
#
# On the next full run, `ScorerInitializer` reads the refusal metrics from Step 1, picks the best
# refusal variant, and uses it to build dependent scorers (e.g., `TrueFalseInverterScorer` wrapping
# the best refusal scorer). This ensures objective scorers that depend on refusal detection use the
# best-performing refusal prompt.
#
# Scorers whose metrics are already up-to-date (same dataset version, sufficient trials) are
# automatically skipped, so re-running the full script is efficient.
#
# **Step 3: Commit updated metrics**
#
# ```bash
# git add pyrit/datasets/scorer_evals/
# git commit -m "chore: update scorer metrics"
# ```
#
# The updated JSONL files should be checked in so that `ScorerInitializer` can read them at runtime
# to select the best scorers.
