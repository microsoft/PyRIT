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
# # 9. Sending Raw Datasets
#
# Sometimes you just want to send a set of prompts through a model and score the responses — no attack
# strategies, no obfuscation, no multi-turn conversation. This "baseline-only" pattern is useful for:
#
# - **Initial assessment**: Understand how a target responds to harmful prompts before applying attacks
# - **Custom datasets**: Test your own datasets against a model without configuring a full attack scenario
# - **Benchmark comparison**: Establish a baseline refusal rate to measure attack effectiveness against
#
# Most PyRIT scenarios can run in baseline-only mode by passing `scenario_strategies=[]` programmatically
# (any scenario that has baseline enabled, which includes `RedTeamAgent` and most others).
# This tells the scenario to skip all attack strategies and just send objectives directly to the target.
#
# For a deeper look at scenario configuration, see the
# [RedTeamAgent deep dive](./1_red_team_agent.ipynb).
#
# ## Loading a Custom Dataset
#
# First, we load a dataset into memory. The example below uses `airt_illegal`, a small built-in dataset
# with harmful prompt objectives. You can substitute any dataset available through
# `SeedDatasetProvider` or load your own YAML files. See
# [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details on available datasets and
# custom loading.

# %%
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory.central_memory import CentralMemory
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[])  # type: ignore

memory = CentralMemory.get_memory_instance()

# Load a dataset from the registry and add it to memory
datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["airt_illegal"])  # type: ignore
await memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by="airt")  # type: ignore

groups = memory.get_seed_groups(dataset_name="airt_illegal")
print(f"Loaded {len(groups)} seed groups from 'airt_illegal'")

# %% [markdown]
# ## Running Baseline-Only with a Scenario
#
# The `RedTeamAgent` scenario is ideal for sending raw datasets because it uses a plain
# `DatasetConfiguration` that works with any dataset. Passing `scenario_strategies=[]` runs only
# the baseline attack — each objective is sent directly to the target without converters or
# multi-turn techniques.
#
# > **Note:** Baseline-only mode is currently supported through the programmatic API.
# > The `pyrit_scan` CLI does not support empty strategies — omitting `--strategies` defaults
# > to running all strategies, not baseline-only.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.foundry import RedTeamAgent

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# Build a DatasetConfiguration from the seed groups we loaded
seed_groups = memory.get_seed_groups(dataset_name="airt_illegal")
dataset_config = DatasetConfiguration(seed_groups=seed_groups, max_dataset_size=5)

# Initialize the scenario in baseline-only mode
scenario = RedTeamAgent()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[],  # Empty list = baseline only
    dataset_config=dataset_config,
)

print(f"Atomic attacks: {scenario.atomic_attack_count}")  # Should be 1 (baseline only)

# %%
scenario_result = await scenario.run_async()  # type: ignore
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Drilling Into Results
#
# The `ScenarioResult` contains all attack results organized by strategy name. For baseline-only runs,
# there is a single strategy called `"baseline"`. You can inspect individual results, check
# success/failure, and view the full conversation:

# %%
from pyrit.executor.attack import ConsoleAttackResultPrinter

# Flatten all attack results
all_results = [result for results in scenario_result.attack_results.values() for result in results]

print(f"Total results: {len(all_results)}")
print(f"Success rate: {scenario_result.objective_achieved_rate():.0f}%")

# Print the first result to see the full conversation
if all_results:
    await ConsoleAttackResultPrinter().print_result_async(result=all_results[0])  # type: ignore

# %% [markdown]
# ## Configuring Scorers
#
# By default, `RedTeamAgent` uses a composite scorer that checks for both harmful content and
# non-refusal. You can customize this by passing a different scorer to the constructor:
#
# ```python
# from pyrit.executor.attack import AttackScoringConfig
# from pyrit.score import SelfAskRefusalScorer
#
# # Use a simpler scorer that only checks for refusals
# scoring_config = AttackScoringConfig(
#     objective_scorer=SelfAskRefusalScorer(chat_target=OpenAIChatTarget())
# )
# scenario = RedTeamAgent(attack_scoring_config=scoring_config)
# await scenario.initialize_async(
#     objective_target=objective_target,
#     scenario_strategies=[],
#     dataset_config=dataset_config,
# )
# ```
#
# ## Re-Scoring and Exporting
#
# After the scenario completes, all results are stored in memory. You can re-score with different
# scorers or export the data for reporting. See the [Memory](../memory/0_memory.md) documentation
# for details on querying and exporting results.
