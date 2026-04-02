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
# # 5. Jailbreak Scenario
#
# The `Jailbreak` scenario tests whether a target model is susceptible to prompt injection and jailbreak
# attacks. It uses template-based jailbreak techniques sourced from `TextJailBreak.get_jailbreak_templates()`,
# applying them through different attack types ranging from simple prompt sending to complex multi-turn
# strategies.
#
# ## Available Strategies
#
# | Strategy | CLI Value | Tags | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | all | Aggregate — runs all strategies |
# | SIMPLE | `simple` | simple | Aggregate — currently expands to PromptSending only |
# | COMPLEX | `complex` | complex | Aggregate — runs complex strategies |
# | PromptSending | `prompt_sending` | simple | Single-turn with jailbreak template |
# | ManyShot | `many_shot` | complex | Multi-turn ManyShot jailbreak |
# | SkeletonKey | `skeleton` | complex | SkeletonKey jailbreak technique |
# | RolePlay | `role_play` | complex | Role-play based persuasion |
#
# The scenario also accepts `num_templates` to limit how many jailbreak templates are used per strategy
# (if not passed, the scenario runs all 90+ templates which can take a long time; if passed, templates
# are selected randomly from the full list), `num_attempts` to repeat each template multiple times, and
# `jailbreak_names` to select specific templates by name.
#
# **Note:** This scenario does not include a default baseline (`include_baseline=False`). Jailbreak testing
# is inherently template-based — a raw prompt without a jailbreak template would not test the intended
# attack vector.
#
# ## Default Datasets
#
# The default dataset is `airt_harms`, containing general harmful objectives. You can bring your own
# datasets using `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI flag —
# see [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details and
# [Configuring RedTeamAgent](1_red_team_agent.ipynb) for advanced dataset configuration.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.airt import Jailbreak, JailbreakStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# To run only the fast single-turn strategy:
#
# ```bash
# pyrit_scan airt.jailbreak \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies prompt_sending \
#   --max-dataset-size 2
# ```
#
# To run all simple strategies:
#
# ```bash
# pyrit_scan airt.jailbreak \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies simple \
#   --max-dataset-size 2
# ```
#
# ## Programmatic Usage
#
# Here we run `prompt_sending` with a small dataset and limit to 1 jailbreak template for speed.

# %%
dataset_config = DatasetConfiguration(dataset_names=["airt_harms"], max_dataset_size=2)

scenario = Jailbreak(num_templates=1)
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[JailbreakStrategy.PromptSending],
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
