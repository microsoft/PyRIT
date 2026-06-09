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
# # Attack Techniques
#
# An **attack technique** is *anything that, once configured, generally helps an attack achieve its
# objective* — a role-play framing, a many-shot priming set, a particular jailbreak template, a
# crescendo escalation. A technique is always **specific to an attack**: it is the *how* of one
# configured [attack](../executor/0_executor.md) (the algorithm — e.g. `RolePlayAttack`,
# `TreeOfAttacksWithPruningAttack`), bundled with the seeds and configuration that make it a reusable
# recipe and packaged so a scenario can pick it by name. The objective — the *what* you are probing
# for — stays separate and is supplied by the dataset.
#
# > **Technique vs. attack.** The *attack* (a.k.a. executor) is the algorithm that runs prompts
# > against a target. The *technique* wraps one configured attack so it can be registered, listed,
# > tagged, and selected without the caller knowing how it is built. A [Scenario](./0_scenarios.ipynb)
# > runs a set of techniques against a set of objectives.
#
# A technique is represented by
# [`AttackTechnique`](../../../pyrit/scenario/core/attack_technique.py) and built by an
# [`AttackTechniqueFactory`](../../../pyrit/scenario/core/attack_technique_factory.py). Concretely, in
# PyRIT terms, a technique can bundle:
#
# - the **attack class** (`attack_class`, an `AttackStrategy` subclass) **and all its constructor
#   args** (`attack_kwargs`) — e.g. `max_turns`, `tree_width`/`tree_depth`, or an
#   `AttackConverterConfig` of request/response **converters**;
# - an **adversarial chat** target (`adversarial_chat`) plus its **system prompt**
#   (`adversarial_system_prompt_path`) and **seed prompt** (`adversarial_seed_prompt`), for attacks
#   that drive a conversation;
# - a **`SeedAttackTechniqueGroup`** (`seed_technique`) of general-technique seeds, which can carry a
#   **system prompt**, a **prepended_conversation**, a **simulated_conversation**
#   (`SeedSimulatedConversation`), and a **next_message**;
# - the selection metadata that lets a scenario pick it: its `name` and `strategy_tags`.
#
# The objective is *not* part of the technique — it stays separate and is supplied by the dataset at
# run time. You rarely build a technique by hand; instead you register a **factory** and let scenarios
# construct techniques on demand with the scenario's own objective target and scorer.

# %% [markdown]
# ## Where techniques come from: initializers
#
# Techniques are registered into a singleton
# [`AttackTechniqueRegistry`](../../../pyrit/registry/object_registries/attack_technique_registry.py)
# by an **initializer**. The canonical catalog lives in
# [`ScenarioTechniqueInitializer`](../../../pyrit/setup/initializers/components/scenario_techniques.py),
# which registers a flat list of
# [`AttackTechniqueFactory`](../../../pyrit/scenario/core/attack_technique_factory.py) instances.
# Each factory is self-describing — it knows its `name`, the attack class it builds, its tags, and
# whether it needs an adversarial chat target — so a scenario can construct the technique lazily with
# the scenario's own objective target and scorer.
#
# Registration is per-name idempotent, so initializers compose: run more than one and each adds only
# the techniques that aren't already registered.
#
# The cell below runs the initializer and lists the catalog.

# %%
import pandas as pd

from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers.components import ScenarioTechniqueInitializer

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)  # type: ignore
await ScenarioTechniqueInitializer().initialize_async()  # type: ignore

factories = AttackTechniqueRegistry.get_registry_singleton().get_factories()

rows = [
    {
        "Technique": name,
        "Attack (executor)": f.attack_class.__name__,
        "Adversarial?": "yes" if f.uses_adversarial else "no",
        "Tags": ", ".join(f.strategy_tags),
    }
    for name, f in factories.items()
]

pd.set_option("display.max_rows", None)
pd.set_option("display.max_colwidth", None)
print(pd.DataFrame(rows).to_string(index=False))

# %% [markdown]
# ## How techniques are selected
#
# Scenarios don't reference factories directly. Instead, a scenario's
# [`ScenarioStrategy`](../../../pyrit/scenario/core/scenario_strategy.py) enum is built *from* the
# registered factories: every technique becomes an enum member, and the factory's tags become
# selectable aggregates. That gives you three ways to choose what runs:
#
# - **By name** — pick a single technique (e.g. `role_play`).
# - **By aggregate tag** — pick a group that expands to every matching technique. `ALL` is always
#   present; tags like `single_turn`, `multi_turn`, `default`, and `light` come from the factories.
# - **Composite** — pair a technique with converters (see
#   [Common Scenario Parameters](./1_common_scenario_parameters.ipynb)).
#
# On the command line this is the `--strategy` flag of
# [`pyrit_scan`](../../scanner/1_pyrit_scan.ipynb); programmatically it's the `scenario_strategies`
# argument to `initialize_async`. The grouping is what lets `--strategy single_turn` or
# `--strategy light` fan out to a whole family of techniques without naming each one.
#
# ```mermaid
# flowchart LR
#     I["ScenarioTechniqueInitializer"] -->|registers factories| R["AttackTechniqueRegistry"]
#     R -->|builds enum + tags| S["ScenarioStrategy"]
#     S -->|name / tag / composite| Sc["Scenario"]
#     R -->|create with target + scorer| T["AttackTechnique<br/>(attack + seeds)"]
#     Sc --> T
# ```

# %% [markdown]
# ## Relationship to single-turn attacks
#
# Many single-turn attacks are, in effect, attack techniques: a `PromptSendingAttack` paired with a
# specific set of seeds or a fixed configuration. `crescendo_simulated` and the persona-driven
# crescendo variants in the catalog above are exactly that — a plain `PromptSendingAttack` plus
# different seed groups. When you find yourself reaching for a one-off single-turn attack subclass,
# consider whether it would be better expressed as a registered technique so scenarios can select it
# by name and tag.
#
# ## Defining your own
#
# To add a technique, register a factory. The simplest form names an attack class and tags it:
#
# ```python
# from pyrit.executor.attack import RolePlayAttack, RolePlayPaths
# from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
# from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
#
# AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
#     [
#         AttackTechniqueFactory(
#             name="my_role_play",
#             attack_class=RolePlayAttack,
#             strategy_tags=["single_turn", "custom"],
#             attack_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
#         )
#     ]
# )
# ```
#
# Wrap registration in a `PyRITInitializer` (as `ScenarioTechniqueInitializer` does) when you want it
# to run as part of standard setup. Any scenario built afterwards will see `my_role_play` as a
# selectable strategy.
