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
# # 2. Content Harms Scenario
#
# The `ContentHarms` scenario tests whether a target model can be induced to generate harmful content across
# seven harm categories: hate, fairness, violence, sexual, harassment, misinformation, and leakage. It combines
# single-turn attacks (PromptSending, RolePlay) with multi-turn techniques (ManyShot, TAP) to provide broad
# coverage of content safety risks.
#
# ## Available Strategies
#
# Each strategy targets a specific harm category with its own dataset:
#
# | Strategy | CLI Value | Type | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | Aggregate | Runs all 7 harm categories |
# | Hate | `hate` | Concrete | Tests for hateful content generation |
# | Fairness | `fairness` | Concrete | Tests for unfair or biased content |
# | Violence | `violence` | Concrete | Tests for violent content generation |
# | Sexual | `sexual` | Concrete | Tests for sexual content generation |
# | Harassment | `harassment` | Concrete | Tests for harassing content generation |
# | Misinformation | `misinformation` | Concrete | Tests for misinformation generation |
# | Leakage | `leakage` | Concrete | Tests for data leakage in content |
#
# ## Default Datasets
#
# Each harm category has a corresponding default dataset (e.g., `airt_hate`, `airt_violence`). These contain
# English-language prompts targeting that specific harm area. You can bring your own datasets using
# `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI flag — see
# [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.airt import ContentHarms, ContentHarmsStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# The simplest way to run this scenario is with `pyrit_scan`. To test a single harm category quickly:
#
# ```bash
# pyrit_scan airt.content_harms \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies hate \
#   --max-dataset-size 1
# ```
#
# To run all harm categories:
#
# ```bash
# pyrit_scan airt.content_harms \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --max-dataset-size 2
# ```
#
# ## Programmatic Usage
#
# For more control, you can configure and run the scenario programmatically. Here we run only the `hate`
# strategy with a minimal dataset. Note that each strategy runs **four** attack types (PromptSending,
# RolePlay, ManyShot, TAP) plus a baseline, so even a single strategy produces multiple atomic attacks.

# %%
dataset_config = DatasetConfiguration(dataset_names=["airt_hate"], max_dataset_size=1)

scenario = ContentHarms()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[ContentHarmsStrategy.Hate],
    dataset_config=dataset_config,
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

# %%
scenario_result = await scenario.run_async()  # type: ignore

# %% [markdown]
# ## Interpreting Results
#
# The `ScenarioResult` contains aggregated outcomes from all atomic attacks. Use the printer to see a
# summary of success rates and strategy effectiveness.

# %%
await printer.print_summary_async(scenario_result)  # type: ignore
