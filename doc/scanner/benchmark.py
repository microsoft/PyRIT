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
# Benchmark scenarios compare the effectiveness of attacks across an axis that varies within the
# scenario itself. The axis can be many things; currently, the only benchmark variant is the
# adversarial benchmark, whose axis of change is the **adversarial chat helper model** used in
# attacks.

# %% [markdown]
# ## Adversarial Benchmark
#
# `AdversarialBenchmark` holds the objective target and dataset constant and varies the adversarial
# chat model used to drive multi-turn attacks (and crescendo-style simulated conversations). Useful
# for evaluating which adversarial helper models produce stronger or weaker attack success rates
# against the same target.
#
# Model fan-out is owned by `BenchmarkInitializer`, which discovers every ADVERSARIAL-tagged target
# in `TargetRegistry` (populated by `TargetInitializer` from `ADVERSARIAL_CHAT_*` env vars) and
# registers one fanned variant per `(adversarial-capable technique, target)` pair into
# `AttackTechniqueRegistry`. The scenario then reads those variants when it builds its strategy
# enum.
#
# ### Prerequisites
#
# Set at least one `ADVERSARIAL_CHAT_*` group of env vars (see `.env_example`):
#
# ```bash
# # Default adversarial target (always available when set)
# ADVERSARIAL_CHAT_ENDPOINT="https://your-endpoint.openai.azure.com/openai/v1"
# ADVERSARIAL_CHAT_KEY="your-key"
# ADVERSARIAL_CHAT_MODEL="deployment-name"
#
# # Optional turn-style variants — auto-discovered by TargetInitializer when set
# ADVERSARIAL_CHAT_SINGLETURN_ENDPOINT="..."
# ADVERSARIAL_CHAT_SINGLETURN_KEY="..."
# ADVERSARIAL_CHAT_SINGLETURN_MODEL="..."
# # ADVERSARIAL_CHAT_MULTITURN_* and ADVERSARIAL_CHAT_REASONING_* follow the same pattern
# ```
#
# If no adversarial-tagged target is registered, `BenchmarkInitializer.initialize_async` raises
# `ValueError` naming the env vars to set.
#
# ### CLI quickstart
#
# ```bash
# pyrit_scan benchmark.adversarial \
#   --initializers target load_default_datasets benchmark \
#   --target openai_chat \
#   --max-dataset-size 4
# ```
#
# **Available strategies (depend on registered adversarial targets):** `all`, `light`, `single_turn`,
# `multi_turn`, plus one concrete member per fanned variant named
# `f"{source_technique}__{target_name}"` (e.g. `red_teaming__adversarial_chat`,
# `tap__adversarial_chat_singleturn`).

# %% [markdown]
# ## Setup
#
# `BenchmarkInitializer` runs after `TargetInitializer` so adversarial-tagged targets are present
# in the registry before the fan-out logic queries it. The initializer chain is executed in list
# order.

# %%
from pyrit.output import output_scenario_async
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.scenarios.benchmark import AdversarialBenchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import (
    BenchmarkInitializer,
    LoadDefaultDatasets,
    ScorerInitializer,
    TargetInitializer,
)

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[
        TargetInitializer(),
        ScorerInitializer(),
        LoadDefaultDatasets(),
        BenchmarkInitializer(),
    ],
)

objective_target = OpenAIChatTarget()

# %% [markdown]
# ## Run the benchmark
#
# Instantiate with no model arguments — adversarial models come from the registry via
# `BenchmarkInitializer`. The default strategy (`light`) runs the benchmark-friendly subset of
# fanned variants for a quick comparison.

# %%
dataset_config = DatasetConfiguration(dataset_names=["harmbench"], max_dataset_size=4)

scenario = AdversarialBenchmark()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    dataset_config=dataset_config,
)

baseline_result = await scenario.run_async()  # type: ignore

# Save this id to resume the run later via AdversarialBenchmark(scenario_result_id=...).
print(f"Scenario result id: {baseline_result.id}")

await output_scenario_async(baseline_result)

# %% [markdown]
# ## Cross-run caching
#
# Re-run the benchmark with `skip_cached=True` and atomic attacks that completed (`SUCCESS` or
# `FAILURE` outcome) in any prior `COMPLETED` run of the same scenario name + version are skipped.
# `ERROR` and `UNDETERMINED` outcomes always retry, so transient failures don't poison the cache.
#
# The cache key is `(atomic_attack_name, technique_eval_hash)` — two atomic attacks that share a
# name but use different technique configurations (e.g. different scorer) don't cross-pollinate.
#
# Useful for:
# * Resuming a long-running benchmark after a crash or `Ctrl-C`.
# * Incrementally adding new adversarial targets without re-running the existing ones — the new
#   fanned variants have new names, so they don't match any cached entry and execute fresh.

# %%
scenario_cached = AdversarialBenchmark(skip_cached=True)
await scenario_cached.initialize_async(  # type: ignore
    objective_target=objective_target,
    dataset_config=dataset_config,
)

cached_result = await scenario_cached.run_async()  # type: ignore

await output_scenario_async(cached_result)

# %% [markdown]
# ## Narrowing the fan-out
#
# Limit the benchmark to a specific subset of registered adversarial targets via
# `BenchmarkInitializer`'s `target_names` parameter. Settable from `.pyrit_conf` (see next section),
# or programmatically:
#
# ```python
# narrow_init = BenchmarkInitializer()
# narrow_init.set_params_from_args(args={"target_names": ["adversarial_chat_singleturn"]})
# await narrow_init.initialize_async()
# ```
#
# Unknown names raise `ValueError` listing both the unknowns and the discovered set, so typos fail
# loudly rather than silently expanding the run.

# %% [markdown]
# ## Bootstrapping from `.pyrit_conf`
#
# For production / repeated runs, declare the full initializer chain in `.pyrit_conf`. Initializers
# run in list order; `target` must precede `benchmark` so adversarial-tagged targets are present
# when `BenchmarkInitializer` queries the registry.
#
# ```yaml
# memory_db_type: duckdb
# initializers:
#   - name: target
#   - name: scorer
#   - name: load_default_datasets
#   - name: benchmark
#     args:
#       target_names:
#         - adversarial_chat_singleturn
#         - adversarial_chat_reasoning
# scenario:
#   name: benchmark.adversarial
# ```
#
# Then run `pyrit_scan --config-file .pyrit_conf` — the scenario picks up the initializer-registered
# fan-out automatically.

# %% [markdown]
# ## Scorer flexibility (forward-looking)
#
# `AdversarialBenchmark.__init__`'s `objective_scorer` parameter is typed as the broad `Scorer`
# base class. The current runtime contract is still `TrueFalseScorer` — passing a non-true/false
# scorer raises `TypeError` with a pointer to the planned scoring follow-up. The annotation is
# widened ahead of runtime support so callers coding against the signature today see the eventual
# contract, and the follow-up that drops the guard won't require a signature change.
