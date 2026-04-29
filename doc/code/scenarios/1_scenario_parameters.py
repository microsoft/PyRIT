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
# # Scenario Parameters
#
# This guide covers the key parameters for configuring scenarios programmatically: datasets,
# strategies, baseline execution, and custom scorers. All examples use `RedTeamAgent` but the
# patterns apply to any scenario.
#
# > **Two selection axes**: *Strategies* select attack techniques (*how* attacks run — e.g., prompt
# > sending, role play, TAP). *Datasets* select objectives (*what* is tested — e.g., harm categories,
# > compliance topics). Use `--dataset-names` on the CLI to filter by content category.
#
# > **Running scenarios from the command line?** See the [Scanner documentation](../../scanner/0_scanner.md).
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
# **Composite strategies** — pair an attack with one or more converters using `FoundryComposite`.
# For example, to run Crescendo with Base64 encoding applied:

# %%
from pyrit.scenario.scenarios.foundry import FoundryComposite

composite_strategy = [FoundryComposite(attack=FoundryStrategy.Crescendo, converters=[FoundryStrategy.Base64])]

# %% [markdown]
# You can mix all three types in a single list:

# %%
scenario_strategies = [
    FoundryStrategy.Base64,
    FoundryStrategy.Binary,
    FoundryComposite(attack=FoundryStrategy.Crescendo, converters=[FoundryStrategy.Caesar]),
]

# %% [markdown]
# ## Baseline Execution
#
# The baseline sends each objective directly to the target without any converters or multi-turn
# strategies. It is included automatically when `include_baseline=True` (the default). This is
# useful for:
#
# - **Measuring default defenses** — how does the target respond to unmodified harmful prompts?
# - **Establishing comparison points** — compare baseline refusal rates against attack-enhanced runs
# - **Calculating attack lift** — how much does each strategy improve over the baseline?

# %%
baseline_scenario = RedTeamAgent()
await baseline_scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=None,  # Uses default strategies; baseline is prepended automatically
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
