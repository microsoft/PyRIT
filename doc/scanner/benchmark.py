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
# # Benchmark Scenarios
#
# Benchmark scenarios compare attack effectiveness across an axis that varies within the scenario
# itself. Currently the only benchmark variant is the adversarial benchmark, whose axis of change is
# the **adversarial chat helper model** used in attacks. For full configuration options see
# `pyrit_scan --help` and the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %% [markdown]
# ## Adversarial Benchmark
#
# `AdversarialBenchmark` holds the objective target and dataset constant and varies the adversarial
# chat model used to drive multi-turn attacks. Useful for evaluating which adversarial helper
# models produce stronger or weaker attack success rates against the same target.
#
# Adversarial targets are user-provided via the `adversarial_targets` scenario parameter. Each name
# must already be registered in `TargetRegistry` — typically by `TargetInitializer` from the
# `ADVERSARIAL_CHAT_*` env vars (see `.env_example`). Use `pyrit_scan --list-targets` to see every
# target currently registered.
#
# ```bash
# pyrit_scan benchmark.adversarial \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --adversarial-targets adversarial_chat_singleturn adversarial_chat_multiturn \
#   --max-dataset-size 4
# ```
#
# Pass multiple `--adversarial-targets` values to compare across models in a single run.
#
# **Available strategies:** `light` (default — a quick snapshot using the cheaper techniques),
# `single_turn`, `multi_turn`, plus one member per adversarial-capable source technique
# (e.g. `red_teaming`, `tap`, `crescendo_simulated`). The `light` aggregate excludes `tap` and
# `crescendo_simulated`, which can take hours.

# %% [markdown]
# ## Setup

# %%
from pyrit.output import output_scenario_async
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.benchmark import AdversarialBenchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets, ScorerInitializer, TargetInitializer

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[TargetInitializer(), ScorerInitializer(), LoadDefaultDatasets()],
)

objective_target = OpenAIChatTarget()

# %%
dataset_config = DatasetConfiguration(dataset_names=["harmbench"], max_dataset_size=4)

scenario = AdversarialBenchmark()
scenario.set_params_from_args(
    args={"adversarial_targets": ["adversarial_chat_singleturn", "adversarial_chat_multiturn"]}
)
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)
