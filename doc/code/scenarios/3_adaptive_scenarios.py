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
# # Adaptive Scenarios
#
# An **adaptive scenario** doesn't run every attack technique against every objective.
# Instead, it picks which technique to try next per-objective, learns from what worked,
# and stops as soon as one technique succeeds. This concentrates spend on techniques
# that actually work on your target.
#
# ## How it works (high level)
#
# For each objective, the scenario tries up to `max_attempts_per_objective` techniques:
#
# - With probability `epsilon`, it **explores** — picks a random technique.
# - Otherwise it **exploits** — picks the technique with the highest observed success
#   rate so far.
# - It records the outcome and stops early on success.
#
# Unseen techniques are tried first, so the first few objectives effectively round-robin
# through every technique before the scenario settles on the best performers.
#
# ## Adaptive vs. static scenarios
#
# | Feature             | Static scenarios                  | Adaptive scenarios                 |
# |---------------------|-----------------------------------|------------------------------------|
# | Technique selection | Run every selected technique      | Pick per-objective from outcomes   |
# | Early stopping      | No                                | Yes — stops on first success       |
# | Cost                | O(techniques × objectives)        | O(max_attempts × objectives)       |
#
# `AdaptiveScenario` is the modality-agnostic base class.
# [`TextAdaptive`](../../../pyrit/scenario/scenarios/adaptive/text_adaptive.py) is the
# text subclass used in the examples below.

# %% [markdown]
# ## Setup

# %%
from pathlib import Path

from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.scenarios.adaptive import TextAdaptive
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("../../scanner/pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
printer = PrettyScenarioResultMemoryPrinter()

# %% [markdown]
# ## Basic usage
#
# Defaults: `max_attempts_per_objective=3`, epsilon-greedy selector with `epsilon=0.2`,
# the subclass's default datasets.

# %%
scenario = TextAdaptive()

scenario.set_params_from_args(args={"objective_target": objective_target})  # type: ignore
await scenario.initialize_async()  # type: ignore
result = await scenario.run_async()  # type: ignore
await printer.write_async(result)  # type: ignore

# %% [markdown]
# ## Configuring a run
#
# - **`max_attempts_per_objective`** — caps techniques tried per objective. Higher means
#   more chances to succeed and more API calls. Set via `set_params_from_args`.
# - **`selector`** — a pre-built `TechniqueSelector` instance. Pass an
#   `EpsilonGreedyTechniqueSelector(epsilon=..., random_seed=...)`
#   to tune the selection algorithm. Defaults to an epsilon-greedy selector with
#   `epsilon=0.2`.
# - **`scenario_strategies`** (a run param) — restricts which techniques the
#   selector can pick from. Use `TextAdaptive.get_strategy_class()` to access the enum.
#
# The cell below exercises all of them at once.

# %%
from pyrit.scenario.scenarios.adaptive import EpsilonGreedyTechniqueSelector

strategy_class = TextAdaptive.get_strategy_class()

configured_scenario = TextAdaptive(
    selector=EpsilonGreedyTechniqueSelector(
        epsilon=0.3,
        random_seed=42,
    ),
)
configured_scenario.set_params_from_args(  # type: ignore
    args={
        "max_attempts_per_objective": 5,
        "objective_target": objective_target,
        "scenario_strategies": [strategy_class("single_turn")],
        "dataset_config": DatasetAttackConfiguration(
            dataset_names=["airt_hate", "airt_violence"],
            max_dataset_size=4,
        ),
    }
)
await configured_scenario.initialize_async()  # type: ignore
configured_result = await configured_scenario.run_async()  # type: ignore
await printer.write_async(configured_result)  # type: ignore

# %% [markdown]
# ## Resuming a run
#
# Adaptive scenarios are resumable — pass `scenario_result_id=...` to the `TextAdaptive`
# constructor and the run picks up where it left off. Resume must use the same
# configuration as the original run.

# %%
resumed_scenario = TextAdaptive(
    selector=EpsilonGreedyTechniqueSelector(
        epsilon=0.3,
        random_seed=42,
    ),
    scenario_result_id=str(configured_result.id),
)
resumed_scenario.set_params_from_args(  # type: ignore
    args={
        "max_attempts_per_objective": 5,
        "objective_target": objective_target,
        "scenario_strategies": [strategy_class("single_turn")],
        "dataset_config": DatasetAttackConfiguration(
            dataset_names=["airt_hate", "airt_violence"],
            max_dataset_size=4,
        ),
    }
)
await resumed_scenario.initialize_async()  # type: ignore
resumed_result = await resumed_scenario.run_async()  # type: ignore
await printer.write_async(resumed_result)  # type: ignore

# %% [markdown]
# ## Inspecting which techniques were tried
#
# Every adaptive run persists both the per-objective envelope (a
# `SequentialAttackResult`) AND its per-attempt child rows. Each child row
# carries its own `atomic_attack_identifier`, so the persisted data alone is
# enough to reconstruct the per-attempt trail — no envelope-side metadata, no
# scenario-side lookup tables needed.
#
# Walk the children via the envelope's `child_attack_result_ids` (joined
# against the flat results list), then read each child's attack strategy
# identifier with `child.get_attack_strategy_identifier()`. The returned
# `ComponentIdentifier` exposes `class_name` (e.g. `"CrescendoAttack"`) for a
# human-readable label, and `unique_name` (e.g. `"CrescendoAttack::a1b2c3d4"`)
# when you need to distinguish two factories that wrap the same attack class
# with different configurations.
#
# Use `result.get_display_groups()` to aggregate `attack_results` by the
# per-dataset display label set by the scenario.
#
# If the trail of attacks attempted is shorter than `max_attempts_per_objective`,
# the compatible-technique pool for that seed group was smaller than the cap —
# the run exhausted the pool.

# %%
from collections import Counter

# Per-group: one line per objective (the envelope) showing the per-attempt
# trail, plus a per-technique success-rate table within the group. The child
# rows that compose each envelope are filtered out of the per-objective list so
# it stays one line per objective. Aggregate across groups for a grand-total.
display_groups = resumed_result.get_display_groups()

# Flatten every persisted row across every group so we can look up a child
# AttackResult by its attack_result_id when reconstructing per-envelope trails.
results_by_id = {r.attack_result_id: r for results in display_groups.values() for r in results}


def _technique_label(result) -> str:
    """Display name for the attack strategy that produced ``result``."""
    attack_id = result.get_attack_strategy_identifier()
    return attack_id.class_name if attack_id else "<unknown>"


total_picks: Counter[str] = Counter()
total_wins: Counter[str] = Counter()

for group_name, results in display_groups.items():
    print(f"\n=== Group: {group_name} ===")

    # Collect every child id referenced by any envelope in this group so we
    # can skip the per-attempt child rows when printing per-objective lines.
    # Baseline rows have no envelope and pass through untouched.
    child_ids: set[str] = set()
    for r in results:
        child_ids.update(r.metadata.get("child_attack_result_ids", []) or [])

    for r in results:
        if r.attack_result_id in child_ids:
            continue
        child_id_list = r.metadata.get("child_attack_result_ids", []) or []
        trail_parts: list[str] = []
        for child_id in child_id_list:
            child = results_by_id.get(child_id)
            if child is None:
                continue
            trail_parts.append(f"{_technique_label(child)}({child.outcome.value})")
        trail = " → ".join(trail_parts)
        print(f"  [{r.outcome.value:7s}] {r.objective!r}: {trail}")

    picks: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    for r in results:
        if r.attack_result_id not in child_ids:
            continue
        technique = _technique_label(r)
        picks[technique] += 1
        total_picks[technique] += 1
        if r.outcome.value == "success":
            wins[technique] += 1
            total_wins[technique] += 1

    print("\n  Technique                                wins / picks   rate")
    for technique, n in picks.most_common():
        print(f"  {technique:40s}  {wins[technique]:>4} / {n:<4}   {wins[technique] / n:.0%}")

print("\n=== Overall ===")
print("Technique                                wins / picks   rate")
for technique, n in total_picks.most_common():
    print(f"{technique:40s}  {total_wins[technique]:>4} / {n:<4}   {total_wins[technique] / n:.0%}")

# %% [markdown]
# ## Running from the scanner CLI
#
# You can run `TextAdaptive` directly from the `pyrit_scan` CLI without writing Python:
#
# ```bash
# # Basic run with defaults
# pyrit_scan --scenario TextAdaptive --target openai_chat
#
# # Tune max attempts and restrict strategies
# pyrit_scan --scenario TextAdaptive --target openai_chat \
#     --params max_attempts_per_objective=5 \
#     --strategies single_turn
#
# # Use specific datasets and limit size
# pyrit_scan --scenario TextAdaptive --target openai_chat \
#     --datasets airt_hate airt_violence \
#     --max-dataset-size 10
# ```
