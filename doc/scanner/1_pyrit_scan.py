# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
# ---

# %% [markdown]
# # PyRIT Scan
#
# `pyrit_scan` is the primary command-line tool for running automated security assessments and red teaming attacks against AI systems. It leverages [scenarios](../code/scenarios/0_scenarios.ipynb) to define attack techniques and supports flexible [configuration](../code/setup/1_configuration.ipynb) for targeting different AI endpoints.
#
# For configuration setup, see [Configuration](../getting_started/configuration.md).
#
# For scenario-specific examples, see [AIRT](airt.ipynb), [Foundry](foundry.ipynb), and [Garak](garak.ipynb).
#
# Note in this doc the ! prefaces all commands in the terminal so we can run in a Jupyter Notebook.
#
# ## Starting a Backend Server
#
# `pyrit_scan` is a thin client that talks to a PyRIT backend server (by default at `http://localhost:8000`).
# Before running any command that reaches the backend (listing scenarios, running a scan, etc.) you need a
# server. Start a local one with `--start-server`; it launches a detached `pyrit_backend` process that stays
# up and is reused by every command below. We stop it again at the end of the notebook.

# %%
# !pyrit_scan start-server

# %% [markdown]
# ## Quick Start
#
# For help:

# %%
# !pyrit_scan --help

# %% [markdown]
# ### Discovery
#
# List all available scenarios:

# %%
# !pyrit_scan list-scenarios

# %% [markdown]
# **Tip**: You can also surface user-defined scenarios. List your initializer script in the
# `initialization_scripts` section of the config file the backend loads (see [here](../getting_started/pyrit_conf.md)).
# The backend runs those scripts at startup and auto-discovers any `Scenario` subclasses they
# define, so start the server with that config, then list:
#
# ```shell
# pyrit_scan --config-file ./my_pyrit_conf.yaml start-server
# pyrit_scan list-scenarios
# ```
#
# ## Initializers
#
# PyRITInitializers are how you can configure the CLI scanner. PyRIT includes several built-in initializers you can use with the `--initializers` flag.
#
# The `--list-initializers` command shows all available initializers. Initializers are referenced by their filename (e.g., `target`, `scorer`) regardless of which subdirectory they're in.
#
# List the available initializers using the --list-initializers flag.

# %%
# !pyrit_scan list-initializers

# %% [markdown]
# ### Running Scenarios
#
# You need a single scenario to run, you need two things:
#
# 1. A Scenario. Many are defined in `pyrit.scenario.scenarios`. But you can also define your own in initialization_scripts.
# 2. Initializers (which can be supplied via the `--initializers` flag on `run`, or the `initializers` / `initialization_scripts` sections of a config file (see [here](../getting_started/pyrit_conf.md))). Scenarios often don't need many arguments, but they can be configured in different ways. And at the very least, most need an `objective_target` (the thing you're running a scan against) which you can configure by using the `--target` flag if your initializer registers targets (e.g. `target` initializer)
# 3. Scenario Techniques (optional). These are supplied by the `--techniques` flag and tell the scenario what to test, but they are always optional. Also note you can obtain these by running `--list-scenarios`
#
# Basic usage will look something like:
#
# ```shell
# pyrit_scan run <scenario> --target <target_name> --initializers <initializer1> <initializer2> --techniques <technique1> <technique2>
# ```
#
# You can also override scenario parameters directly from the CLI:
#
# ```shell
# pyrit_scan run <scenario> --max-concurrency 10 --max-retries 3 --memory-labels '{"experiment": "test1", "version": "v2"}'
# ```
#
# Or concretely:
#
# ```shell
# !pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --techniques base64
# ```
#
# Example with a basic configuration that runs the Foundry scenario against the objective target defined in the `target` initializer.

# %%
# !pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --techniques base64

# %% [markdown]
# Or with all options and multiple techniques:
#
# ```shell
# pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --techniques easy crescendo
# ```
#
# You can also override scenario execution parameters:
#
# ```shell
# # Override concurrency and retry settings
# pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --max-concurrency 10 --max-retries 3
#
# # Add custom memory labels for tracking (must be valid JSON)
# pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --memory-labels '{"experiment": "test1", "version": "v2", "researcher": "alice"}'
# ```
#
# Available CLI parameter overrides:
# - `--max-concurrency <int>`: Maximum number of concurrent attack executions
# - `--max-retries <int>`: Maximum number of automatic retries if the scenario raises an exception
# - `--memory-labels <json>`: Additional labels to apply to all attack runs (must be a JSON string with string keys and values)
#
# Dataset-backed scenarios can also select a supported dataset. Requested datasets are fetched
# on demand, so a full dataset preload is not required:
#
# ```shell
# pyrit_scan run garak.figstep --target openai_chat --dataset-names figstep_pro --max-dataset-size 1
# ```
#
# Custom initialization scripts are loaded by the backend at startup: list them in the
# `initialization_scripts` section of the config the server loads (paths are relative to your
# working directory, but full paths avoid confusion). Once the server is running with that
# config, they apply to every `run`:
#
#
# ```shell
# pyrit_scan --config-file ./my_pyrit_conf.yaml start-server
# pyrit_scan run garak.encoding
# ```

# %% [markdown]
# #### Attaching Converters to a Technique
#
# Techniques (techniques) can have a registered converter instance appended to them with the
# `<technique>:converter.<name>` syntax. The converter is added to the request side of every attack
# the technique produces, on top of any converters the technique already bakes in. This also works on
# aggregate techniques (the converter is applied to every technique the aggregate expands to).
#
# First discover the registered converter instances with `list-converters`. Converters are
# registered by initializers that the backend runs at startup, so the initializer that registers
# them must be part of the server's configuration — a built-in in the `initializers` section, or a
# custom script in the `initialization_scripts` section of the config the server loads (see
# [here](../getting_started/pyrit_conf.md)). Start the server with that config, then list:
#
# ```shell
# pyrit_scan --config-file ./my_pyrit_conf.yaml start-server
# pyrit_scan list-converters
# ```
#
# Then reference a converter by name in `--techniques`:
#
# ```shell
# # Add the registered "translation_spanish" converter to role_play_movie_script only
# pyrit_scan run airt.rapid_response --target openai_chat --initializers target my_converters --techniques role_play_movie_script:converter.translation_spanish
#
# ```
#
# #### Chain multiple converters (applied in order) and combine with plain techniques
# ```shell
# pyrit_scan run airt.rapid_response --target openai_chat --initializers load_default_datasets target my_converters --techniques role_play_movie_script:converter.translation_spanish:converter.base64 many_shot
# ```

# %% [markdown]
# #### Using Custom Scenarios
#
# You can define your own scenarios in initialization scripts. The CLI will automatically discover any `Scenario` subclasses and make them available:
#

# %%
# my_custom_scenarios.py

from pyrit.common import apply_defaults
from pyrit.prompt_target.openai.openai_chat_target import OpenAIChatTarget
from pyrit.scenario import DatasetAttackConfiguration, Scenario, ScenarioTechnique
from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer
from pyrit.setup import initialize_pyrit_async


class MyCustomTechnique(ScenarioTechnique):
    """Techniques for my custom scenario."""

    ALL = ("all", {"all"})
    Technique1 = ("technique1", set[str]())
    Technique2 = ("technique2", set[str]())


class MyCustomScenario(Scenario):
    """My custom scenario that does XYZ."""

    @apply_defaults
    def __init__(self, *, scenario_result_id=None, **kwargs):
        # Scenario-specific configuration only - no runtime parameters
        super().__init__(
            name="My Custom Scenario",
            version=1,
            objective_scorer=TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=OpenAIChatTarget())),
            technique_class=MyCustomTechnique,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["harmbench"]),
            scenario_result_id=scenario_result_id,
        )
        # ... your scenario-specific initialization code

    async def _build_atomic_attacks_async(self, *, context):
        # The single abstract extension point every scenario implements.
        # Read runtime inputs from `context`; return the list of AtomicAttack to run.
        # Matrix-shaped scenarios can delegate to build_matrix_atomic_attacks(context=...).
        # Example: create attacks for each technique composite
        return []


await initialize_pyrit_async(memory_db_type="InMemory")  # type: ignore
MyCustomScenario()


# %% [markdown]
# Then discover and run it:
#
# ```shell
# # Start the backend with a config whose initialization_scripts lists my_custom_scenarios.py
# pyrit_scan --config-file ./my_pyrit_conf.yaml start-server
#
# # List to confirm it's available
# pyrit_scan list-scenarios
#
# # Run it with parameter overrides
# pyrit_scan run my_custom_scenario --max-concurrency 10
#
# ```The scenario name is automatically converted from the class name (e.g., `MyCustomScenario` becomes `my_custom_scenario`).
#

# %% [markdown]
# ## Inspecting Results
#
# Each run is saved under a `scenario_result_id` (printed when the run finishes), so you can revisit it later without re-running. Use `scenario-history` to find recent run ids:
#
# ```shell
# pyrit_scan scenario-history          # last 10 runs
# pyrit_scan scenario-history 25       # last 25
# ```
#
# Then inspect a run with `scenario-results` at the granularity you want via `--view`:
#
# ```shell
# # Aggregate stats + per-group success rates (the default; same as the run summary)
# pyrit_scan scenario-results <scenario_result_id>
#
# # One row per attack: id, technique, objective, outcome, turns, score
# pyrit_scan scenario-results <scenario_result_id> --view attacks
#
# # Full message transcripts with the objective score per turn
# pyrit_scan scenario-results <scenario_result_id> --view conversations
#
# # Both: the attacks table followed by the transcripts
# pyrit_scan scenario-results <scenario_result_id> --view full
# ```
#
# `--view` picks *how much* detail per attack; `--attack-result-ids` picks *which* attacks (ids come from the `attacks` view); the two combine. Use `--limit` to cap how many attacks are shown; the `conversations` and `full` views default to 5 when you scope neither:
#
# ```shell
# pyrit_scan scenario-results <id> --view conversations --attack-result-ids <attack_id> <attack_id>
# pyrit_scan scenario-results <id> --view conversations --limit 3
# ```

# %% [markdown]
# ## Stopping the Backend Server
#
# When you're done, stop the local backend that we started at the top of the notebook.

# %%
# !pyrit_scan stop-server
