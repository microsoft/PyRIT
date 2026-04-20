# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
# ---

# %% [markdown]
# # 1. Configuring Scenarios
#
# This notebook demonstrates how to use a composite strategies — the `FoundryStrategy` — to test a target with multiple
# attack strategies.
# A "composite strategy"  This class encapsulates a collection of ScenarioStrategy instances along with
# an auto-generated descriptive name, making it easy to represent both single strategies
# and composed multi-strategy attacks.
#
# The `Foundry` scenario provides a comprehensive testing approach that includes:
# - **Converter-based attacks**: Apply various encoding/obfuscation techniques (Base64, Caesar cipher, etc.)
# - **Multi-turn attacks**: Complex conversational attack strategies (Crescendo, RedTeaming)
# - **Strategy composition**: Combine multiple converters together
# - **Difficulty levels**: Organized into EASY, MODERATE, and DIFFICULT categories
#
# Note that this is not the easiest way to run the Foundry scenario (or any scenario). This is meant to show how you can configure all the components.
#
# ## Setup
#
# First, we'll initialize PyRIT and configure the target we want to test.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.foundry import FoundryStrategy, RedTeamAgent
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Define Seed Groups
#
# By default, `RedTeamAgent` selects four random objectives from HarmBench. Here we'll retrieve only two for demonstration. If you didn't pass any `seed_groups`, the default would be almost the same except with `max_dataset_size=4`.

# %%
from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedGroup
from pyrit.scenario import DatasetConfiguration

datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["harmbench"])  # type: ignore
seed_groups: list[SeedGroup] = datasets[0].seed_groups  # type: ignore
dataset_config = DatasetConfiguration(seed_groups=seed_groups, max_dataset_size=2)

# %% [markdown]
# ## Select Attack Strategies
#
# You can specify individual strategies or compose multiple converters together.
# The scenario supports three types of strategy specifications:
#
# 1. **Simple strategies**: Individual converter or attack strategies (e.g., `FoundryStrategy.Base64`)
# 2. **Aggregate strategies**: Tag-based groups (e.g., `FoundryStrategy.EASY` expands to all easy strategies)
# 3. **Composite strategies**: Multiple converters applied together (e.g., Caesar + CharSwap)
#
# If not selected, there are always defaults. In this case, the default is `FoundryStrategy.EASY`.

# %%
scenario_strategies = [
    FoundryStrategy.Base64,  # Simple strategy (auto-wrapped internally)
    FoundryStrategy.Binary,  # Simple strategy (auto-wrapped internally)
    ScenarioCompositeStrategy(strategies=[FoundryStrategy.Caesar, FoundryStrategy.CharSwap]),  # Composed strategy
]

# %% [markdown]
# ## Create and Initialize the Scenario
#
# The scenario needs to be initialized before execution. This builds the atomic attacks based on the selected strategies. Most of these have defaults, but the one thing that needs to be supplied is an `objective_target` so the scenario knows what we're attacking.

# %%
foundry_scenario = RedTeamAgent()
await foundry_scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=scenario_strategies,
    max_concurrency=10,
    dataset_config=dataset_config,
)

print(f"Created scenario: {foundry_scenario.name}")
print(f"Number of atomic attacks: {foundry_scenario.atomic_attack_count}")

# %% [markdown]
# ## Execute the Scenario
#
# Now we'll run the scenario and print the results. The scenario will:
# 1. Execute each atomic attack sequentially
# 2. Apply the attack strategy to all objectives
# 3. Score the results using the configured scorer
# 4. Aggregate all results into a `ScenarioResult`
#
# The below example actually executes the scenario, and stores the result.

# %%
scenario_result = await foundry_scenario.run_async()  # type: ignore

# %% [markdown]
# ## Printing Scenarios
#
# The `ScenarioResult` object aggregates all results from a scenario execution. It contains a `scenario_identifier` (with name, description, and version), an `objective_target_identifier` describing the target tested, and an `attack_results` dictionary that maps each atomic attack strategy name to a list of `AttackResult` objects. Key properties include `scenario_run_state` (which can be "CREATED", "IN_PROGRESS", "COMPLETED", or "FAILED"), `labels` for metadata tagging, and `completion_time`. The class provides helper methods like `get_strategies_used()` to list all attack strategies, `get_objectives()` to retrieve unique objectives tested, and `objective_achieved_rate()` to calculate the success rate as a percentage. You can filter these methods by a specific `atomic_attack_name` or aggregate across all attacks.

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# Then to look at all the individual results, you can look at the `attack_results` property of the `ScenarioResult`. Additionally, although, the scenario result is returned from `run_async`, you can also retrieve it from memory.

# %%
from pyrit.executor.attack import ConsoleAttackResultPrinter
from pyrit.memory.central_memory import CentralMemory

memory = CentralMemory.get_memory_instance()
scenario_results_from_memory = memory.get_scenario_results(scenario_name="RedTeamAgent")
last_scenario_result = scenario_results_from_memory[-1]
print(f"Retrieved {len(scenario_results_from_memory)} scenario results from memory.")

# Flatten all attack results from all strategies
all_results = [result for results in last_scenario_result.attack_results.values() for result in results]

successful_attacks = [r for r in all_results if r.outcome.value == "success"]
non_successful_attacks = [r for r in all_results if r.outcome.value != "success"]

if len(successful_attacks) > 0:
    print("\nSuccessful Attacks:")
    for result in successful_attacks:
        await ConsoleAttackResultPrinter().print_result_async(result=result)  # type: ignore
else:
    print("\nNo successful attacks. Here is the first non success...\n")
    await ConsoleAttackResultPrinter().print_result_async(result=non_successful_attacks[0])  # type: ignore

# %% [markdown]
# ## Alternative: Using Difficulty Levels
#
# Instead of specifying individual strategies, you can use aggregate tags like `EASY`, `MODERATE`, or `DIFFICULT` to test multiple strategies at once.

# %%
# Example: Test all EASY strategies
# easy_scenario = RedTeamAgent(
#     objective_target=objective_target,
#     scenario_strategies=[FoundryStrategy.EASY],  # Expands to all easy strategies
#     objectives=objectives,
# )
# await easy_scenario.initialize_async()
# easy_results = await easy_scenario.run_async()
# await printer.print_summary_async(easy_results)

# %% [markdown]
# ## Scenario Resiliency
#
# The `Foundry` scenario supports automatic resume and retry mechanisms:
#
# - **Automatic Resume**: If execution is interrupted, re-running the scenario will continue from where it left off
# - **Retry Mechanism**: Set `max_retries` to automatically retry on transient failures
# - **Progress Tracking**: The scenario tracks completed objectives in memory
#
# For more details on resiliency features, see the [resiliency documentation](../setup/2_resiliency.ipynb).
