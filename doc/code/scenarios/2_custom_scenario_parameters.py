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
# Scenarios can declare typed parameters that flow from CLI flags or YAML
# config into `self.params`. Use this when a scenario has a knob a user
# should be able to turn without editing source code (`max_turns`, dataset
# names, feature flags, etc.).
#
# This is different from [Scenario Parameters](./1_scenario_parameters.ipynb),
# which covers the framework-level configuration surface (datasets, strategies,
# scorers, baseline). This guide is about parameters that scenario authors add
# on their own classes.
#
# ## Declaring a parameter
#
# `Parameter` is the unified declaration shared by initializers and scenarios.
# To declare one on a scenario, override the `supported_parameters()` classmethod
# and return a list. `Scam` declares one (`max_turns`), shown below:

# %%
import inspect

from pyrit.scenario.scenarios.airt.scam import Scam

# Authors declare a parameter by overriding the supported_parameters classmethod.
# Here's the actual declaration on Scam:
print(inspect.getsource(Scam.supported_parameters))

# %% [markdown]
# At runtime the framework calls `supported_parameters()` to inspect declarations.
# It's a classmethod, so this works without instantiating the scenario (which
# would wire up memory and scorers):

# %%
for param in Scam.supported_parameters():
    print(param)

# %% [markdown]
# Each declaration lives inside the scenario class body, in the
# `supported_parameters()` classmethod. End users don't construct `Parameter`
# objects themselves; they pass values via CLI flags or YAML config.
#
# Each `Parameter` carries:
#
# - **name**: dict key in `self.params`, converted to `--kebab-case` for the CLI
# - **description**: shown in `--list-scenarios` and `--help`
# - **required** (`bool`): when True, the user must supply a value
# - **default**: value used when not supplied; deep-copied per run
# - **param_type**: `str`, `int`, `float`, `bool`, `list[str]`, or `None` (raw passthrough)
# - **choices**: optional tuple of allowed values (not supported with `list` types)
#
# A more complete declaration list might look like:

# %%
from pyrit.common import Parameter

# What a scenario author would return from supported_parameters():
example_declarations = [
    # Required scalar: the user must supply a value
    Parameter(name="objective", description="Goal the attack pursues", required=True, param_type=str),
    # Optional scalar with default
    Parameter(name="max_turns", description="Conversation cap", default=5, param_type=int),
    # Choices: behaves like an enum
    Parameter(
        name="mode",
        description="Speed mode",
        default="fast",
        param_type=str,
        choices=("fast", "slow"),
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
# Here's how Scam reads the parameter, in `_get_atomic_attack_from_strategy`:
#
# ```python
# attack_strategy = RedTeamingAttack(
#     objective_target=self._objective_target,
#     attack_scoring_config=self._scorer_config,
#     attack_adversarial_config=self._adversarial_config,
#     max_turns=self.params["max_turns"],
# )
# ```

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

# %% [markdown]
# ## Setting a parameter from a YAML config file
#
# A `scenario:` block names the scenario and supplies parameter values. CLI
# flags override matching keys; absent keys fall back to YAML, then to the
# declared default.

# %%
from pyrit.setup.configuration_loader import ConfigurationLoader

# A YAML-style dict; in practice this comes from your config file.
config_data = {
    "scenario": {
        "name": "airt.scam",
        "args": {"max_turns": 10},
    },
}

config = ConfigurationLoader.from_dict(config_data)
scenario_config = config.scenario_config
assert scenario_config is not None  # narrows the type after parsing
print(f"scenario name: {scenario_config.name}")
print(f"scenario args: {scenario_config.args}")

# %% [markdown]
# A few invocation shapes from the CLI:
#
# ```bash
# pyrit_scan --config-file my_config.yaml                          # config provides scenario name
# pyrit_scan airt.scam --config-file my_config.yaml                # CLI confirms the name
# pyrit_scan airt.scam --config-file my_config.yaml --max-turns 7  # CLI args win per-key
# ```
#
# `pyrit_shell` supports the YAML form when the scenario name is supplied
# explicitly (`run airt.scam ...`). A bare `run` does not fall back to the
# config-file scenario name in the current release.
#
# ## Discovering parameters via --list-scenarios
#
# `--list-scenarios` prints declared parameters alongside each scenario's
# other metadata (description, strategies, datasets). The same formatter the
# CLI uses is callable programmatically:

# %%
from pyrit.cli.frontend_core import format_scenario_metadata
from pyrit.registry import ScenarioRegistry

# Show scam (declares a parameter) and red_team_agent (none), so the
# Supported Parameters section is visible in one and absent in the other.
demo_names = {"airt.scam", "foundry.red_team_agent"}
for metadata in ScenarioRegistry.get_registry_singleton().list_metadata():
    if metadata.registry_name in demo_names:
        format_scenario_metadata(scenario_metadata=metadata)

# %% [markdown]
# Notice the `Supported Parameters:` section under `airt.scam`. It's absent
# from `foundry.red_team_agent` because that scenario doesn't declare any
# custom parameters. Existing scenarios that don't opt in to this feature
# render exactly as before.
#
# ## Resume validation
#
# When you resume a scenario with `--scenario-result-id <id>`, PyRIT compares
# the current effective parameters against the values stored with the original
# result. On mismatch, a new scenario result is created (the original is
# preserved) and a warning is logged. The diff lists key names only, never
# values, so sensitive parameters don't leak into log output.
#
# A typical mismatch warning looks like this:
#
# ```text
# Scenario result ID 7c3f... has mismatched parameters (changed: max_turns).
# Either CLI/config args differ from the original run, or a scenario default
# changed between releases. Creating new scenario result.
# ```
#
# Notice the diff names the changed key (`max_turns`) but never prints the
# stored or current value.

# %% [markdown]
# `Scam.max_turns` was previously hardcoded to `5` in
# `_get_atomic_attack_from_strategy`. Replacing it with a `Parameter` of
# `default=5` keeps the original behavior (no new flag is required to run
# Scam as before) while making the value overridable for users who need it.
