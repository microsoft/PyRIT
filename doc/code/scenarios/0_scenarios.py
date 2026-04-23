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
# # Scenarios
#
# A `Scenario` is a higher-level construct that groups multiple Attack Configurations together. This allows you to execute a comprehensive testing campaign with multiple attack methods sequentially. Scenarios are meant to be configured and written to test for specific workflows. As such, it is okay to hard code some values.
#
# ## What is a Scenario?
#
# A `Scenario` represents a comprehensive testing campaign composed of multiple atomic attack tests. It orchestrates the execution of multiple `AtomicAttack` instances sequentially and aggregates the results into a single `ScenarioResult`.
#
# ### Key Components
#
# - **Scenario**: The top-level orchestrator that groups and executes multiple atomic attacks
# - **AtomicAttack**: An atomic test unit combining an attack strategy, objectives, and execution parameters
# - **ScenarioResult**: Contains the aggregated results from all atomic attacks and scenario metadata
#
# ## Use Cases
#
# Some examples of scenarios you might create:
#
# - **VibeCheckScenario**: Randomly selects a few prompts from HarmBench [@mazeika2024harmbench] to quickly assess model behavior
# - **QuickViolence**: Checks how resilient a model is to violent objectives using multiple attack techniques
# - **ComprehensiveFoundry**: Tests a target with all available attack converters and strategies
# - **CustomCompliance**: Tests against specific compliance requirements with curated datasets and attacks
#
# These Scenarios can be updated and added to as you refine what you are testing for.
#
# ## How to Run Scenarios
#
# Scenarios should take almost no effort to run with default values. The [PyRIT Scanner](../../scanner/0_scanner.md) provides two CLIs for running scenarios: [pyrit_scan](../../scanner/1_pyrit_scan.ipynb) for automated execution and [pyrit_shell](../../scanner/2_pyrit_shell.md) for interactive exploration.
#
# For programmatic configuration — customizing datasets, strategies, scorers, and baseline mode — see [Scenario Parameters](./1_scenario_parameters.ipynb).
#
# ## How It Works
#
# Each `Scenario` contains a collection of `AtomicAttack` objects. When executed:
#
# 1. Each `AtomicAttack` is executed sequentially
# 2. Every `AtomicAttack` tests its configured attack against all specified objectives and datasets
# 3. Results are aggregated into a single `ScenarioResult` with all attack outcomes
# 4. Optional memory labels help track and categorize the scenario execution
#
# ## Creating Custom Scenarios
#
# To create a custom scenario, extend the `Scenario` base class and implement the required abstract methods.
#
# ### Required Components
#
# 1. **Strategy Enum**: Create a `ScenarioStrategy` enum that defines the available attack techniques for your scenario.
#    - Each enum member represents an **attack technique** (the *how* of an attack)
#    - Each member is defined as `(value, tags)` where value is a string and tags is a set of strings
#    - Include an `ALL` aggregate strategy that expands to all available strategies
#    - Optionally override `_prepare_strategies()` for custom composition logic (see `FoundryComposite`)
#
# 2. **Scenario Class**: Extend `Scenario` and implement these abstract methods:
#    - `get_strategy_class()`: Return your strategy enum class
#    - `get_default_strategy()`: Return the default strategy (typically `YourStrategy.ALL`)
#    - The base class provides a default `_get_atomic_attacks_async()` that uses the factory/registry
#      pattern. Override it only if your scenario needs custom attack construction logic.
#
# 3. **Default Dataset**: Implement `default_dataset_config()` to specify the datasets your scenario uses out of the box.
#    - Returns a `DatasetConfiguration` with one or more named datasets (e.g., `DatasetConfiguration(dataset_names=["my_dataset"])`)
#    - Users can override this at runtime via `--dataset-names` in the CLI or by passing a custom `dataset_config` programmatically
#
# 4. **Constructor**: Use `@apply_defaults` decorator and call `super().__init__()` with scenario metadata:
#    - `name`: Descriptive name for your scenario
#    - `version`: Integer version number
#    - `strategy_class`: The strategy enum class for this scenario
#    - `objective_scorer_identifier`: Identifier dict for the scoring mechanism (optional)
#    - `include_default_baseline`: Whether to include a baseline attack (default: True)
#    - `scenario_result_id`: Optional ID to resume an existing scenario (optional)
#
# 5. **Initialization**: Call `await scenario.initialize_async()` to populate atomic attacks:
#    - `objective_target`: The target system being tested (required)
#    - `scenario_strategies`: List of strategies to execute (optional, defaults to ALL)
#    - `max_concurrency`: Number of concurrent operations (default: 1)
#    - `max_retries`: Number of retry attempts on failure (default: 0)
#    - `memory_labels`: Optional labels for tracking (optional)
#
# ### Example Structure
#
# The simplest approach uses the **factory/registry pattern**: define your strategy,
# dataset config, and constructor — the base class handles building atomic attacks
# automatically from registered attack techniques.
# %%

from pyrit.common import apply_defaults
from pyrit.scenario import (
    DatasetConfiguration,
    Scenario,
    ScenarioStrategy,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.setup import initialize_pyrit_async

await initialize_pyrit_async(memory_db_type="InMemory")  # type: ignore [top-level-await]


class MyStrategy(ScenarioStrategy):
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    # Strategy members represent attack techniques
    PromptSending = ("prompt_sending", {"single_turn", "default"})
    RolePlay = ("role_play", {"single_turn"})


class MyScenario(Scenario):
    """Quick-check scenario for testing model behavior across harm categories."""

    VERSION: int = 1

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        return MyStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        return MyStrategy.DEFAULT

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        return DatasetConfiguration(dataset_names=["dataset_name"], max_dataset_size=4)

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=self.get_strategy_class(),
            scenario_result_id=scenario_result_id,
        )

    # Optional: override _build_display_group to customize result grouping.
    # Default groups by technique name; override to group by dataset instead:
    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        return seed_group_name

    # No _get_atomic_attacks_async override needed!
    # The base class builds attacks from the (technique x dataset) cross-product
    # using the factory/registry pattern automatically.


# %% [markdown]
#
# ## Existing Scenarios

# %%
from pyrit.cli.frontend_core import FrontendCore, print_scenarios_list_async

await print_scenarios_list_async(context=FrontendCore())  # type: ignore

# %% [markdown]
#
# ## Baseline Execution
#
# Every scenario can optionally include a **baseline attack** — a `PromptSendingAttack` that sends
# each objective directly to the target without any converters or multi-turn techniques. This is
# controlled by the `include_default_baseline` parameter (default: `True` for most scenarios).
#
# To run *only* the baseline (no attack strategies), create a `RedTeamAgent` with
# `include_baseline=True` (the default) and pass `scenario_strategies=None`. See
# [Scenario Parameters](./1_scenario_parameters.ipynb) for a working example.

# %% [markdown]
#
# ## Resiliency
#
# Scenarios can run for a long time, and because of that, things can go wrong. Network issues, rate limits, or other transient failures can interrupt execution. PyRIT provides built-in resiliency features to handle these situations gracefully.
#
# ### Automatic Resume
#
# If you re-run a `scenario`, it will automatically start where it left off. The framework tracks completed attacks and objectives in memory, so you won't lose progress if something interrupts your scenario execution. This means you can safely stop and restart scenarios without duplicating work.
#
# ### Retry Mechanism
#
# You can utilize the `max_retries` parameter to handle transient failures. If any unknown exception occurs during execution, PyRIT will automatically retry the failed operation (starting where it left off) up to the specified number of times. This helps ensure your scenario completes successfully even in the face of temporary issues.
#
# ### Dynamic Configuration
#
# During a long-running scenario, you may want to adjust parameters like `max_concurrency` to manage resource usage, or switch your scorer to use a different target. PyRIT's resiliency features make it safe to stop, reconfigure, and continue scenarios as needed.
#
# For more information, see [resiliency](../setup/2_resiliency.ipynb)
