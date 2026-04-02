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
# # 7. Scam Scenario
#
# The `Scam` scenario tests whether a target model can be induced to generate phishing, fraud, or scam
# content. It supports both single-turn and multi-turn strategies, including a persuasion-based
# red-teaming approach that uses a defined persona to try to extract scam content.
#
# ## Available Strategies
#
# | Strategy | CLI Value | Tags | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | all | Aggregate — runs all strategies |
# | SINGLE_TURN | `single_turn` | single_turn | Aggregate — single-turn strategies |
# | MULTI_TURN | `multi_turn` | multi_turn | Aggregate — multi-turn strategies |
# | ContextCompliance | `context_compliance` | single_turn | Context compliance attack |
# | RolePlay | `role_play` | single_turn | Persuasion-based role-play |
# | PersuasiveRedTeamingAttack | `persuasive_rta` | multi_turn | Multi-turn persuasive attack (max 5 turns) |
#
# ## Default Datasets
#
# The default dataset is `airt_scams`, containing phishing and fraud generation objectives. You can bring
# your own datasets using `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI
# flag — see [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details and
# [Configuring RedTeamAgent](1_red_team_agent.ipynb) for advanced dataset configuration.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.airt import Scam, ScamStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# To run the fast single-turn context compliance strategy:
#
# ```bash
# pyrit_scan airt.scam \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies context_compliance \
#   --max-dataset-size 2
# ```
#
# To run all strategies:
#
# ```bash
# pyrit_scan airt.scam \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --max-dataset-size 2
# ```
#
# ## Programmatic Usage
#
# Here we run `context_compliance` with a small dataset.

# %%
dataset_config = DatasetConfiguration(dataset_names=["airt_scams"], max_dataset_size=2)

scenario = Scam()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[ScamStrategy.ContextCompliance],
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
