# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
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
# - **AtomicAttack**: An atomic test unit combining an attack technique, objectives, and execution parameters
# - **ScenarioResult**: Contains the aggregated results from all atomic attacks and scenario metadata
#
# ## Use Cases
#
# Some examples of scenarios you might create:
#
# - **VibeCheckScenario**: Randomly selects a few prompts from HarmBench [@mazeika2024harmbench] to quickly assess model behavior
# - **QuickViolence**: Checks how resilient a model is to violent objectives using multiple attack techniques
# - **ComprehensiveFoundry**: Tests a target with all available attack converters and techniques
# - **CustomCompliance**: Tests against specific compliance requirements with curated datasets and attacks
#
# These Scenarios can be updated and added to as you refine what you are testing for.
#
# ## How to Run Scenarios
#
# Scenarios should take almost no effort to run with default values. The [PyRIT Scanner](../../scanner/0_scanner.md) provides two CLIs for running scenarios: [pyrit_scan](../../scanner/1_pyrit_scan.ipynb) for automated execution and [pyrit_shell](../../scanner/2_pyrit_shell.md) for interactive exploration.
#
# For programmatic configuration — customizing datasets, techniques, scorers, and baseline mode — see [Common Scenario Parameters](./1_common_scenario_parameters.ipynb).
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
# 1. **Technique Enum**: Create a `ScenarioTechnique` enum that defines the available attack techniques for your scenario.
#    - Each enum member represents an **attack technique** (the *how* of an attack)
#    - Each member is defined as `(value, tags)` where value is a string and tags is a set of strings
#    - Include an `ALL` aggregate technique that expands to all available techniques
#    - The default technique (what runs when the caller selects nothing) is owned by the catalog, not the scenario: override the `default()` classmethod to return the default member (omit it to fall back to `ALL`)
#
# 2. **Scenario Class**: Extend `Scenario` and pass these to `super().__init__()`:
#    - `technique_class`: Your technique enum class
#    - Implement `_build_atomic_attacks_async(context)` — the single abstract extension point.
#      Matrix-shaped scenarios delegate to `build_matrix_atomic_attacks(context=...)` in one line.
#
# 3. **Default Dataset**: Pass `default_dataset_config=` to `super().__init__()` to specify the datasets your scenario uses out of the box.
#    - Returns a `DatasetConfiguration` with one or more named datasets (e.g., `DatasetConfiguration(dataset_names=["my_dataset"])`)
#    - Users can override this at runtime via `--dataset-names` in the CLI or by passing a custom `dataset_config` programmatically
#
# 4. **Constructor**: Use `@apply_defaults` decorator and call `super().__init__()` with scenario metadata:
#    - `name`: Descriptive name for your scenario
#    - `version`: Integer version number
#    - `technique_class`: The technique enum class for this scenario
#    - `default_dataset_config`: A `DatasetConfiguration` specifying the scenario's default datasets
#    - `objective_scorer`: The scorer used to judge responses
#    - `scenario_result_id`: Optional ID to resume an existing scenario (optional)
#
# 5. **Initialization**: Call `await scenario.initialize_async()` to populate atomic attacks:
#    - `objective_target`: The target system being tested (required)
#    - `scenario_techniques`: List of techniques to execute (optional, defaults to ALL)
#    - `max_concurrency`: Number of concurrent operations (default: 4)
#    - `max_retries`: Number of retry attempts on failure (default: 0)
#    - `memory_labels`: Optional labels for tracking (optional)
#    - `include_baseline`: Whether to prepend a baseline attack (defaults to the scenario type's
#      `BASELINE_ATTACK_POLICY`; most scenarios, including `Jailbreak`, default it on)
#
# ### Example Structure
#
# The construction path: define your technique, dataset config, and constructor, then
# implement `_build_atomic_attacks_async(context)`. Matrix-shaped scenarios delegate to the
# `build_matrix_atomic_attacks` helper, which builds atomic attacks automatically from the
# registered attack techniques.
# %%
from pyrit.common import apply_defaults
from pyrit.scenario import (
    DatasetConfiguration,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers.techniques import TechniqueInitializer

await initialize_pyrit_async(memory_db_type="InMemory")  # type: ignore [top-level-await]
await TechniqueInitializer().initialize_async()  # type: ignore [top-level-await]


class MyTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    # Technique members represent attack techniques
    PromptSending = ("prompt_sending", {"single_turn", "default"})
    RolePlay = ("role_play_movie_script", {"single_turn"})

    @classmethod
    def default(cls) -> "MyTechnique":
        return cls.DEFAULT


class MyScenario(Scenario):
    """Quick-check scenario for testing model behavior across harm categories."""

    VERSION: int = 1

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
            technique_class=MyTechnique,
            default_dataset_config=DatasetConfiguration(dataset_names=["dataset_name"], max_dataset_size=4),
            scenario_result_id=scenario_result_id,
        )

    # Implement the single abstract extension point. Matrix-shaped scenarios delegate
    # to build_matrix_atomic_attacks; pass display_group_fn to customize result grouping
    # (default groups by technique; here we group by dataset instead).
    async def _build_atomic_attacks_async(self, *, context):
        return build_matrix_atomic_attacks(
            context=context,
            objective_scorer=self._objective_scorer,
            display_group_fn=lambda combo: combo.dataset_name,
        )


# %% [markdown]
#
# ## Existing Scenarios

# %%
import logging

from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.cli._output import print_scenario_list

logging.getLogger("pyrit").setLevel(logging.ERROR)

response = await get_scenario_service().list_scenarios_async(limit=200)  # type: ignore
print_scenario_list(items=response.items)

# %% [markdown]
#
# ## Baseline Execution
#
# Every scenario can optionally include a **baseline attack** — a `PromptSendingAttack` that sends
# each objective directly to the target without any converters or multi-turn techniques. This is
# controlled by the `include_baseline` scenario parameter, supplied through the CLI, config, or
# `set_params_from_args` before `initialize_async`; when omitted, each scenario falls back to its
# own `BASELINE_ATTACK_POLICY` class attribute (most scenarios, including `Jailbreak`, default it
# on). See
# [Common Scenario Parameters](./1_common_scenario_parameters.ipynb) for a worked example.
#
# Custom scenarios should choose their `BASELINE_ATTACK_POLICY` based on whether an unmodified
# prompt is a meaningful comparator for the scenario's techniques:
#
# - **`Enabled`** — the baseline is prepended by default and the caller can opt out. Use when an
#   unmodified-prompt run is a meaningful comparison point (most scenarios).
# - **`Disabled`** — the baseline is supported but omitted by default; the caller must opt in. Use
#   when an unmodified-prompt comparison is valid but not useful enough to run by default.
# - **`Forbidden`** — the baseline is unavailable and passing `include_baseline=True` raises. Use
#   when the scenario's semantics make a single-shot unmodified prompt meaningless as a comparator
#   (e.g., benchmarks comparing across adversarial models, or multi-turn-only scenarios).

# %% [markdown]
#
# ## Resiliency
#
# Scenarios can run for a long time, and because of that, things can go wrong. Network issues, rate limits, or other transient failures can interrupt execution. PyRIT provides built-in resiliency features to handle these situations gracefully.
#
# ### Attack Outcomes and Execution Health
#
# A Scenario tracks two independent axes for every objective:
#
# | Axis | Values | Meaning |
# | --- | --- | --- |
# | **Execution health** | completed or incomplete | A completed objective returned an `AttackResult`. An incomplete objective raised an exception before it could return one. |
# | **Objective outcome** | `AttackOutcome.SUCCESS`, `FAILURE`, or `UNDETERMINED` | Whether a completed attack achieved its objective. `FAILURE` is a valid security result, not an execution error. |
#
# A model refusal therefore does not make an objective incomplete. PyRIT persists handled structured
# refusals and content-filter responses as blocked model responses, applies the configured scoring
# policy, and returns a completed `AttackResult`. A refusal that does not achieve the objective normally
# produces `AttackOutcome.FAILURE`; a response that achieves it produces `AttackOutcome.SUCCESS`.

# %% [markdown] class="col-page-right"
#
# ```{mermaid}
# %%{init: {"flowchart": {"subGraphTitleMargin": {"bottom": 40}, "wrappingWidth": 260}}}%%
# flowchart TB
#     subgraph objective["One objective in<br/>an AtomicAttack"]
#         START["Execute attack objective"] --> TARGET["Send or continue conversation"]
#         TARGET --> TARGET_RESULT{"Target result"}
#
#         TARGET_RESULT -->|Normal model output| RESPONSE["Persistable model response"]
#         TARGET_RESULT -->|Handled refusal or<br/>content-filter response| REFUSAL["Persistable blocked model response<br/>not an execution failure"]
#         TARGET_RESULT --> RUNTIME_ERROR["Non-retryable runtime error"]
#         TARGET_RESULT -->|Retryable target error| TARGET_RETRY{"Target retry budget remains?"}
#         TARGET_RETRY -->|No / exhausted| EXEC_ERROR["Execution exception propagates"]
#         TARGET_RETRY -->|Yes| RETRY_TARGET["Repeat from<br/>Send or continue conversation"]
#         RUNTIME_ERROR --> EXEC_ERROR
#
#         RESPONSE --> SCORE["Apply configured scorer policy"]
#         REFUSAL --> SCORE
#         SCORE --> SCORE_RESULT{"Scoring result"}
#         SCORE_RESULT -->|Objective not achieved| MORE{"Attack-specific attempt or turn remains?"}
#         SCORE_RESULT -->|Objective achieved| SUCCESS["AttackResult<br/>AttackOutcome.SUCCESS"]
#         SCORE_RESULT -->|No objective scorer| UNDETERMINED["AttackResult<br/>AttackOutcome.UNDETERMINED"]
#         SCORE_RESULT -->|Invalid JSON;<br/>retry remains| RETRY_SCORE["Repeat from<br/>Apply configured scorer policy"]
#         SCORE_RESULT -->|Scorer error or<br/>out of retries| EXEC_ERROR
#         MORE -->|Yes| RETRY_ATTACK["Repeat from<br/>Send or continue conversation"]
#         MORE -->|No| FAILURE["AttackResult<br/>AttackOutcome.FAILURE"]
#
#         FAILURE --> COMPLETE["Completed objective"]
#         SUCCESS --> COMPLETE
#         UNDETERMINED --> COMPLETE
#         EXEC_ERROR --> ERROR_ROW["Error handler may persist<br/>AttackOutcome.ERROR for diagnostics"]
#         ERROR_ROW --> INCOMPLETE["Incomplete objective<br/>exception retained"]
#     end
#
#     subgraph aggregation["Scenario aggregation<br/>and resiliency"]
#         COMPLETE --> EXECUTOR_RESULT["AttackExecutorResult"]
#         INCOMPLETE --> EXECUTOR_RESULT
#         EXECUTOR_RESULT --> HAS_INCOMPLETE{"Any incomplete objectives?"}
#
#         HAS_INCOMPLETE -->|Yes| SCENARIO_RETRY{"Scenario retry budget remains?"}
#         SCENARIO_RETRY -->|Yes; resume only<br/>incomplete objectives| RESUME["Repeat objective flow<br/>for incomplete objectives"]
#         SCENARIO_RETRY -->|No / exhausted| PARTIAL["Raise ScenarioPartialFailureException<br/>structured counts, incomplete objectives, preserved cause<br/>completed_count may be zero"]
#         PARTIAL --> SCENARIO_FAILED["Persist ScenarioRunState.FAILED"]
#
#         HAS_INCOMPLETE -->|No| KEEP["Keep every completed AttackResult<br/>SUCCESS, FAILURE, and UNDETERMINED"]
#         KEEP --> ALL_DONE{"All atomic attacks complete?"}
#         ALL_DONE -->|No| NEXT_ATTACK["Repeat objective flow<br/>for next atomic attack"]
#         ALL_DONE -->|Yes| SCENARIO_COMPLETE["ScenarioResult<br/>ScenarioRunState.COMPLETED"]
#     end
#
#     classDef model fill:#e8f0fe,stroke:#4285f4,color:#15233a;
#     classDef complete fill:#e6f4ea,stroke:#34a853,color:#15233a;
#     classDef incomplete fill:#fce8e6,stroke:#d93025,color:#15233a;
#     classDef retry fill:#fff4e5,stroke:#f9ab00,color:#15233a;
#     class RESPONSE,REFUSAL model;
#     class RETRY_TARGET,RETRY_SCORE,RETRY_ATTACK,RESUME,NEXT_ATTACK retry;
#     class SUCCESS,FAILURE,UNDETERMINED,COMPLETE,SCENARIO_COMPLETE complete;
#     class RUNTIME_ERROR,EXEC_ERROR,ERROR_ROW,INCOMPLETE,PARTIAL,SCENARIO_FAILED incomplete;
# ```

# %% [markdown]
#
# To keep retry paths readable, **Repeat from** nodes name the earlier step where execution resumes
# instead of drawing long return arrows across unrelated branches.
#
# A Scenario reaches `ScenarioRunState.COMPLETED` when every objective execution completes, regardless
# of the mix of successful and unsuccessful attack outcomes. Scenario retries resume only objectives
# that have not completed; already-persisted results are preserved.
#
# If retry exhaustion leaves any incomplete objectives, `ScenarioPartialFailureException` reports
# `completed_count`, `incomplete_count`, and `incomplete_objectives`, and keeps the first objective
# exception as its cause. This typed exception is also used when **none** of the objectives in the
# returned `AttackExecutorResult` completed (`completed_count == 0`). If an `AtomicAttack` raises before
# it can return an `AttackExecutorResult`, the Scenario instead retries and ultimately re-raises that
# exception; multiple concurrent atomic-attack failures are surfaced as an `ExceptionGroup`. In every
# terminal execution-failure case, the persisted Scenario state is `ScenarioRunState.FAILED`.
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
