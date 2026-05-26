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
# Adversarial targets are user-provided via the `adversarial_targets` scenario parameter. Each name
# must already be registered in `TargetRegistry` — typically by `TargetInitializer` from the
# `ADVERSARIAL_CHAT_*` env vars, or programmatically via `TargetRegistry.register_instance`. At run
# time the scenario builds the `(technique × target × dataset)` cross-product directly: for each
# adversarial-capable technique in `SCENARIO_TECHNIQUES` and each requested target, it constructs a
# per-pair factory with `adversarial_chat` overridden to that target. No global
# `AttackTechniqueRegistry` state is mutated.
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
# Use `pyrit_scan list-targets` to see every target currently registered, along with its tags.
#
# ### CLI quickstart
#
# ```bash
# pyrit_scan benchmark.adversarial \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --adversarial-targets adversarial_chat \
#   --max-dataset-size 4
# ```
#
# Pass multiple `--adversarial-targets` values to compare across models in a single run:
#
# ```bash
# pyrit_scan benchmark.adversarial \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --adversarial-targets adversarial_chat adversarial_chat_singleturn adversarial_chat_reasoning \
#   --max-dataset-size 4
# ```
#
# **Available strategies:** `light` (the default — a quick snapshot using the cheaper techniques),
# `single_turn`, `multi_turn`, plus one concrete member per adversarial-capable source technique
# (e.g. `red_teaming`, `tap`, `crescendo_simulated`). The default `light` aggregate deliberately
# excludes `tap` and `crescendo_simulated`, which can take hours on a single run.

# %% [markdown]
# ## Setup
#
# `TargetInitializer` populates `TargetRegistry` from the `ADVERSARIAL_CHAT_*` env vars. The
# scenario looks up adversarial targets by registry name from its `adversarial_targets` parameter,
# so the targets must be registered before `scenario.run_async()` runs.

# %%
from pyrit.output import output_scenario_async
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.scenarios.benchmark import AdversarialBenchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import (
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
    ],
)

objective_target = OpenAIChatTarget()

# %% [markdown]
# ## Run the benchmark
#
# Instantiate the scenario, then pass `adversarial_targets` through `initialize_async` via
# `set_params_from_args` (programmatic equivalent of the `--adversarial-targets` CLI flag). The
# default strategy (`light`) runs the benchmark-friendly subset of techniques for a quick
# comparison.

# %%
dataset_config = DatasetConfiguration(dataset_names=["harmbench"], max_dataset_size=4)

scenario = AdversarialBenchmark()
scenario.set_params_from_args(args={"adversarial_targets": ["adversarial_chat"]})
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
#   `{technique}__{target}_{dataset}` names don't match any cached entry and execute fresh.

# %%
scenario_cached = AdversarialBenchmark(skip_cached=True)
scenario_cached.set_params_from_args(args={"adversarial_targets": ["adversarial_chat"]})
await scenario_cached.initialize_async(  # type: ignore
    objective_target=objective_target,
    dataset_config=dataset_config,
)

cached_result = await scenario_cached.run_async()  # type: ignore

await output_scenario_async(cached_result)

# %% [markdown]
# ## Bootstrapping from `.pyrit_conf`
#
# For production / repeated runs, declare the initializer chain and the adversarial-target list in
# `.pyrit_conf`. `TargetInitializer` must precede the scenario so the named targets are present in
# `TargetRegistry` by the time the scenario builds atomic attacks.
#
# ```yaml
# memory_db_type: duckdb
# initializers:
#   - name: target
#   - name: scorer
#   - name: load_default_datasets
# scenario:
#   name: benchmark.adversarial
#   args:
#     adversarial_targets:
#       - adversarial_chat
#       - adversarial_chat_singleturn
#       - adversarial_chat_reasoning
# ```
#
# Then run `pyrit_scan --config-file .pyrit_conf` — the scenario reads `adversarial_targets` from
# the config and builds the cross-product automatically. Unknown names raise `ValueError` listing
# both the unknowns and every registered target so typos fail loudly.

# %% [markdown]
# ## Scorer flexibility (forward-looking)
#
# `AdversarialBenchmark.__init__`'s `objective_scorer` parameter is typed as the broad `Scorer`
# base class. The current runtime contract is still `TrueFalseScorer` — passing a non-true/false
# scorer raises `TypeError` with a pointer to the planned scoring follow-up. The annotation is
# widened ahead of runtime support so callers coding against the signature today see the eventual
# contract, and the follow-up that drops the guard won't require a signature change.
