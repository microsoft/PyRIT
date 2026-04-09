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
# # Working with Scenarios Programmatically
#
# This guide demonstrates how to configure, run, and inspect scenarios from Python code using
# `RedTeamAgent` as a concrete example. For running scenarios via CLI, see
# [pyrit_scan](../../scanner/pyrit_scan.ipynb).
#
# ## Setup
#
# Initialize PyRIT and create the target we want to test.

# %%
from pathlib import Path

from pyrit.registry import TargetRegistry
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.foundry import FoundryStrategy, RedTeamAgent
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("../../scanner/pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().get_instance_by_name("openai_chat")
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Dataset Configuration
#
# `DatasetConfiguration` controls which prompts (objectives) are sent to the target.
# The simplest approach uses `dataset_names` to load datasets by name from memory.
# By default, `RedTeamAgent` loads four random objectives from HarmBench [@mazeika2024harmbench].

# %%
from pyrit.scenario import DatasetConfiguration

dataset_config = DatasetConfiguration(dataset_names=["harmbench"], max_dataset_size=2)

# %% [markdown]
# For more control, use `SeedDatasetProvider` to fetch datasets and pass explicit `seed_groups`.
# This is useful when you need to filter, combine, or inspect the prompts before running.

# %%
from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedGroup

datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["harmbench"])  # type: ignore
seed_groups: list[SeedGroup] = datasets[0].seed_groups  # type: ignore

# Pass explicit seed_groups instead of dataset_names
dataset_config = DatasetConfiguration(seed_groups=seed_groups, max_dataset_size=2)

# %% [markdown]
# ## Strategy Selection and Composition
#
# `FoundryStrategy` is an enum that defines which attack strategies the scenario runs. There are
# three ways to specify strategies:
#
# **Individual strategies** — a single converter or multi-turn attack:

# %%
single_strategy = [FoundryStrategy.Base64]

# %% [markdown]
# **Aggregate strategies** — tag-based groups that expand to all matching strategies. For example,
# `EASY` expands to all strategies tagged as easy (Base64, Binary, CharSwap, etc.):

# %%
aggregate_strategy = [FoundryStrategy.EASY]

# %% [markdown]
# **Composite strategies** — multiple converters applied together in sequence using
# `ScenarioCompositeStrategy`:

# %%
from pyrit.scenario import ScenarioCompositeStrategy

composite_strategy = [
    ScenarioCompositeStrategy(strategies=[FoundryStrategy.Caesar, FoundryStrategy.CharSwap])
]

# %% [markdown]
# You can mix all three types in a single list:

# %%
scenario_strategies = [
    FoundryStrategy.Base64,
    FoundryStrategy.Binary,
    ScenarioCompositeStrategy(strategies=[FoundryStrategy.Caesar, FoundryStrategy.CharSwap]),
]

# %% [markdown]
# ## Creating and Running a Scenario
#
# Every scenario follows a three-step lifecycle: **create → initialize → run**.
#
# 1. **Create** the scenario instance (configures scorers and internal settings).
# 2. **Initialize** with `initialize_async` to build the atomic attacks from strategies and datasets.
#    This is where you provide the `objective_target`.
# 3. **Run** with `run_async` to execute all attacks and collect results.

# %%
scenario = RedTeamAgent()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=scenario_strategies,
    dataset_config=dataset_config,
    max_concurrency=10,
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

scenario_result = await scenario.run_async()  # type: ignore

# %% [markdown]
# ## Inspecting Results
#
# `run_async` returns a `ScenarioResult` that aggregates all attack outcomes. Key properties and
# methods include:
#
# - `scenario_run_state` — one of `"CREATED"`, `"IN_PROGRESS"`, `"COMPLETED"`, or `"FAILED"`
# - `get_strategies_used()` — list all attack strategy names
# - `get_objectives()` — list unique objectives tested
# - `objective_achieved_rate()` — success rate as a percentage
# - `attack_results` — dict mapping strategy names to lists of `AttackResult` objects

# %%
print(f"State: {scenario_result.scenario_run_state}")
print(f"Strategies: {scenario_result.get_strategies_used()}")
print(f"Objectives tested: {len(scenario_result.get_objectives())}")
print(f"Success rate: {scenario_result.objective_achieved_rate():.1f}%")

# %% [markdown]
# Use the `ConsoleScenarioResultPrinter` for a formatted summary, and
# `ConsoleAttackResultPrinter` to drill into individual results:

# %%
from pyrit.executor.attack import ConsoleAttackResultPrinter

await printer.print_summary_async(scenario_result)  # type: ignore

all_results = [result for results in scenario_result.attack_results.values() for result in results]
if all_results:
    await ConsoleAttackResultPrinter().print_result_async(result=all_results[0])  # type: ignore

# %% [markdown]
# You can also retrieve past scenario results from memory rather than holding onto the return value:

# %%
from pyrit.memory.central_memory import CentralMemory

memory = CentralMemory.get_memory_instance()
saved_results = memory.get_scenario_results(scenario_name="RedTeamAgent")
print(f"Found {len(saved_results)} RedTeamAgent results in memory.")

# %% [markdown]
# ## Baseline Execution
#
# Pass an empty `scenario_strategies` list to run a baseline-only scenario. The baseline sends each
# objective directly to the target without any converters or multi-turn strategies. This is useful for:
#
# - **Measuring default defenses** — how does the target respond to unmodified harmful prompts?
# - **Establishing comparison points** — compare baseline refusal rates against attack-enhanced runs
# - **Calculating attack lift** — how much does each strategy improve over the baseline?

# %%
baseline_scenario = RedTeamAgent()
await baseline_scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[],  # Empty list = baseline only
    dataset_config=dataset_config,
)
baseline_result = await baseline_scenario.run_async()  # type: ignore
await printer.print_summary_async(baseline_result)  # type: ignore

# %% [markdown]
# To disable the automatic baseline entirely (e.g., when you only want attack strategies with no
# comparison), set `include_baseline=False` in the constructor:
#
# ```python
# scenario = RedTeamAgent(include_baseline=False)
# await scenario.initialize_async(
#     objective_target=objective_target,
#     scenario_strategies=[FoundryStrategy.Base64],
# )
# ```

# %% [markdown]
# ## Custom Scorers
#
# By default, `RedTeamAgent` uses a composite scorer with Azure Content Filter and SelfAsk Refusal
# scorers. You can override this by passing your own `AttackScoringConfig` with a custom
# `objective_scorer`.
#
# For example, to use an inverted refusal scorer (where "True" means the target refused):

# %%
from pyrit.executor.attack import AttackScoringConfig
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer

refusal_scorer = SelfAskRefusalScorer(chat_target=OpenAIChatTarget())
inverted_scorer = TrueFalseInverterScorer(scorer=refusal_scorer)

custom_scenario = RedTeamAgent(
    attack_scoring_config=AttackScoringConfig(objective_scorer=inverted_scorer),
)
await custom_scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[FoundryStrategy.Base64],
    dataset_config=dataset_config,
)
custom_result = await custom_scenario.run_async()  # type: ignore
await printer.print_summary_async(custom_result)  # type: ignore
