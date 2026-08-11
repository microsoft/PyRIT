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
# # Custom Scenario Parameters
#
# Sometimes a scenario needs a custom parameter that a user can set without
# editing source code (`max_turns`, dataset names, feature flags, etc.).
# Scenarios can declare typed parameters that flow from CLI flags into
# `self.params`.
#
# This is different from [Common Scenario Parameters](./1_common_scenario_parameters.ipynb),
# which covers the framework-level configuration surface (datasets, techniques,
# scorers, baseline). This guide is about parameters that scenario authors add
# on their own classes.
#
# ## Declaring a parameter
#
# `Parameter` is the unified declaration shared by initializers and scenarios.
# To declare one on a scenario, override the `additional_parameters()` classmethod
# and return a list of just your extras — the base composes these with the common
# run inputs, so you never repeat (or accidentally drop) them. Here's the actual
# declaration on [`Scam`](../../../pyrit/scenario/scenarios/airt/scam.py):
#
# ```python
# @classmethod
# def additional_parameters(cls) -> list[Parameter]:
#     """Declare custom parameters this scenario accepts from the CLI."""
#     return [
#         Parameter(
#             name="max_turns",
#             description="Maximum conversation turns for the persuasive_rta technique.",
#             param_type=int,
#             default=5,
#         ),
#     ]
# ```
#
# At runtime the framework calls `supported_parameters()` to inspect declarations
# (the common inputs plus your `additional_parameters()`). It's a classmethod, so
# this works without instantiating the scenario (which would wire up memory and
# scorers):

# %%
from pyrit.scenario.airt.scam import Scam
from pyrit.setup import initialize_pyrit_async
from pyrit.setup.initializers.techniques import TechniqueInitializer

await initialize_pyrit_async(memory_db_type="InMemory")  # type: ignore [top-level-await]
await TechniqueInitializer().initialize_async()  # type: ignore [top-level-await]

for param in Scam.supported_parameters():
    print(param)

# %% [markdown]
# Each declaration lives inside the scenario class body, in the
# `additional_parameters()` classmethod. End users don't construct `Parameter`
# objects themselves; they pass values via CLI flags.
#
# Each `Parameter` carries:
#
# - **name**: dict key in `self.params`, converted to `--kebab-case` for the CLI
# - **description**: shown in `--list-scenarios` and `--help`
# - **default**: value used when not supplied; deep-copied per run
# - **param_type**: `str`, `int`, `float`, `bool`, a `Literal[...]`/`Enum` (a
#   constrained scalar that carries its own allowed set), a `list[...]` of any of
#   those, or `None` (raw passthrough)
#
# A more complete declaration list might look like:

# %%
from typing import Literal

from pyrit.models import Parameter

# What a scenario author would return from additional_parameters():
example_declarations = [
    # Scalar with no default — author must guard against None at run time
    Parameter(name="objective", description="Goal the attack pursues", param_type=str),
    # Scalar with default
    Parameter(name="max_turns", description="Conversation cap", default=5, param_type=int),
    # Constrained scalar: a Literal behaves like an enum (the type *is* the allowed set)
    Parameter(
        name="mode",
        description="Speed mode",
        default="fast",
        param_type=Literal["fast", "slow"],
    ),
    # List parameter
    Parameter(name="tags", description="Tag list", default=["default"], param_type=list[str]),
]

for p in example_declarations:
    print(p)

# %% [markdown]
# ## Reading the value
#
# After the framework calls `set_params_from_args` (which `pyrit_scan` and
# `pyrit_shell` do automatically), `self.params["max_turns"]` returns the
# user's value, or the declared default if no value was supplied. There's
# no need for a `.get()` fallback. Mutable defaults like `["a", "b"]` are
# deep-copied on each run, so changes in one scenario instance don't leak
# into another.
#
# Here's how Scam reads the parameter, in `_get_atomic_attack_from_technique`:
#
# ```python
# attack_strategy = RedTeamingAttack(
#     objective_target=self._objective_target,
#     attack_scoring_config=self._scorer_config,
#     attack_adversarial_config=self._adversarial_config,
#     max_turns=self.params["max_turns"],
# )
# ```
#
# Programmatic users (constructing the scenario in Python rather than going
# through the CLI) get the same behavior: `initialize_async()` materializes
# declared defaults the first time it runs, so `self.params["max_turns"]`
# is populated even when no explicit `set_params_from_args` call was made.

# %% [markdown]
# ## Setting a parameter from the CLI
#
# `pyrit_scan` adds one flag per declared parameter, converting the name from
# `snake_case` to `--kebab-case`. Scenario flags go after the scenario name
# and can be mixed with built-in flags:
#
# ```bash
# # Use the declared default (5)
# pyrit_scan airt.scam --target my_target --initializers target
#
# # Override
# pyrit_scan airt.scam --target my_target --initializers target --max-turns 10
# ```
#
# The same flags work in `pyrit_shell`:
#
# ```text
# pyrit_shell> run airt.scam --target my_target --initializers target --max-turns 10
# ```
#
# Declared flags also show up in `pyrit_scan <scenario> --help`, alongside
# the built-in options:
#
# ```bash
# pyrit_scan airt.scam --help
# # ...
# #   --max-turns MAX_TURNS  Conversation turn cap
# ```
#
# ## Discovering parameters via --list-scenarios
#
# `--list-scenarios` prints declared parameters alongside each scenario's
# other metadata (description, techniques, datasets). The same formatter the
# CLI uses is callable programmatically:

# %%
from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.cli._output import print_scenario_list

# Show scam (declares a parameter) and red_team_agent (none), so the
# Supported Parameters section is visible in one and absent in the other.
demo_names = {"airt.scam", "foundry.red_team_agent"}
response = await get_scenario_service().list_scenarios_async(limit=200)  # type: ignore
print_scenario_list(items=[s for s in response.items if s.scenario_name in demo_names])

# %% [markdown]
# Notice the `Supported Parameters:` section under `airt.scam`. It's absent
# from `foundry.red_team_agent` because that scenario doesn't declare any
# custom parameters. Existing scenarios that don't opt in to this feature
# render exactly as before.
#
# ## Resume validation
#
# When you ask to resume by passing `scenario_result_id` to a Scenario constructor,
# PyRIT verifies that the stored result is an exact match for the current
# configuration. Any deviation aborts with a `ValueError` rather than silently
# starting a fresh scenario, so original progress is never orphaned without the
# caller noticing. Mismatch axes:
#
# - **Stored id not found** in memory (typo, wiped DB, never persisted)
# - **Scenario name differs** (e.g., a Scam id passed to a Cyber constructor)
# - **Scenario version differs** (release drift between save and resume)
# - **Effective parameters differ** from those persisted with the original run
#
# A typical param-mismatch error message:
#
# ```text
# Scenario result id '7c3f...' has mismatched parameters (changed: max_turns).
# Drop scenario_result_id to start a new scenario, or pass matching parameters to resume.
# ```
#
# The diff names changed/added/removed keys but never prints values, so sensitive
# parameters don't leak into exception output. To start fresh, drop the
# `scenario_result_id` argument; to resume, pass the same params used originally.
#
# A dedicated `pyrit_scan --resume` CLI flag that loads stored params for you
# (so you can't supply mismatching ones in the first place) is tracked as a
# separate follow-up.

# %% [markdown]
# `Scam.max_turns` was previously hardcoded to `5` in
# `_get_atomic_attack_from_technique`. Replacing it with a `Parameter` of
# `default=5` keeps the original behavior (no new flag is required to run
# Scam as before) while making the value overridable for users who need it.
