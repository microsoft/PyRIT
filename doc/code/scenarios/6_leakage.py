# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
#   kernelspec:
#     display_name: pyrit-dev
#     language: python
#     name: pyrit-dev
# ---

# %% [markdown]
# # 6. Leakage Scenario
#
# The `Leakage` scenario tests whether a target model can be induced to leak sensitive data, intellectual
# property, secrets, or system prompts. It provides a rich set of strategies organized along two dimensions:
# **turn type** (single-turn vs. multi-turn) and **data type** (IP vs. sensitive data), allowing you to
# run targeted evaluations filtering by either axis.
#
# ## Available Strategies
#
# | Strategy | CLI Value | Tags | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | all | Aggregate — runs all strategies |
# | SINGLE_TURN | `single_turn` | single_turn | Aggregate — all single-turn strategies |
# | MULTI_TURN | `multi_turn` | multi_turn | Aggregate — all multi-turn strategies |
# | IP | `ip` | ip | Aggregate — intellectual property leakage |
# | SENSITIVE_DATA | `sensitive_data` | sensitive_data | Aggregate — sensitive data leakage |
# | FirstLetter | `first_letter` | single_turn, ip | Encodes text as first letters, asks model to decode |
# | Image | `image` | single_turn, ip, sensitive_data | Embeds prompts in images |
# | RolePlay | `role_play` | single_turn, sensitive_data | Persuasion-based role-play |
# | Crescendo | `crescendo` | multi_turn, ip, sensitive_data | Gradual multi-turn escalation |
#
# All of these strategies test for data leakage — they differ in technique (encoding, image embedding,
# persuasion, escalation) rather than goal. The strategies can be combined with the `PlagiarismScorer`
# described below to measure how much copyrighted or sensitive text appears in model responses.
#
# ## Copyright and Plagiarism Testing
#
# The `FirstLetter` strategy is particularly useful for testing whether a model has memorized copyrighted
# text. It works by encoding text using the `FirstLetterConverter` (extracting the first letter of each
# word) and then asking the model to decode the sequence. If the model can reconstruct the original text,
# it suggests memorization of that content. While this example highlights FirstLetter, the `PlagiarismScorer`
# can be applied to responses from **any** leakage strategy.
#
# For deeper plagiarism analysis of model responses, the `PlagiarismScorer` provides three complementary
# metrics:
#
# - **LCS (Longest Common Subsequence)** — Captures contiguous plagiarized sequences.
#   Score = LCS length / reference length.
# - **Levenshtein (Edit Distance)** — Measures word-level edit distance.
#   Score = 1 − (min edits / max length).
# - **Jaccard (N-gram Overlap)** — Measures phrase-level similarity using configurable n-grams.
#   Score = matching n-grams / total reference n-grams.
#
# All metrics are normalized to [0, 1] where 1 indicates the reference text is fully present in the
# response. There is no built-in threshold — the scorer returns a raw float, and you determine what
# score constitutes a meaningful match for your use case.
#
# ## Default Datasets
#
# The default dataset is `airt_leakage`, containing data leakage and extraction objectives. You can bring
# your own datasets using `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI
# flag — see [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details and
# [Configuring RedTeamAgent](1_red_team_agent.ipynb) for advanced dataset configuration.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.airt import Leakage, LeakageStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# To run the fast single-turn `first_letter` strategy:
#
# ```bash
# pyrit_scan airt.leakage \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies first_letter \
#   --max-dataset-size 2
# ```
#
# To run all IP-related strategies:
#
# ```bash
# pyrit_scan airt.leakage \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies ip \
#   --max-dataset-size 2
# ```
#
# ## Programmatic Usage
#
# Here we run `first_letter` with a small dataset for a fast demonstration.

# %%
dataset_config = DatasetConfiguration(dataset_names=["airt_leakage"], max_dataset_size=2)

scenario = Leakage()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[LeakageStrategy.FirstLetter],
    dataset_config=dataset_config,
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

# %%
scenario_result = await scenario.run_async()  # type: ignore

# %% [markdown]
# ## Interpreting Results

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Using PlagiarismScorer for Deeper Analysis
#
# After running the leakage scenario, you can measure how much copyrighted or sensitive text appears in
# model responses using `PlagiarismScorer`. This scorer is complementary to the scenario's built-in
# leakage detection and can be applied to responses from any strategy. The example below demonstrates
# the three available metrics against a known reference text.

# %%
from pyrit.score import PlagiarismMetric, PlagiarismScorer

reference_text = "It was the best of times, it was the worst of times."

lcs_scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LCS)
levenshtein_scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LEVENSHTEIN)
jaccard_scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.JACCARD, n=3)

# Score a sample response against the reference text
sample_response = "It was the very best of times and the worst of times."
for name, scorer in [("LCS", lcs_scorer), ("Levenshtein", levenshtein_scorer), ("Jaccard", jaccard_scorer)]:
    scores = await scorer.score_text_async(text=sample_response)  # type: ignore
    print(f"{name}: {scores[0].score_value}")
