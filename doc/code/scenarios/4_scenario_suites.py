# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.6
# ---

# %% [markdown]
# # Scenario Suites
#
# - A **scenario** pairs objective datasets with attack techniques and runs them against
#   a target, scoring each result (see [Scenarios](./0_scenarios)).
# - A **preset** is a saved scenario configuration (techniques, objectives, and parameters),
#   without a target; you choose the target when you launch.
# - A **suite** is an ordered collection of preset references. One suite launch runs its
#   presets against the selected target through the ordinary  scenario execution path.
#
# The `scenario_suite` initializer registers read-only suites whose presets are fixed
# snapshots, the same way the `target` and `converter` initializers register components.
#
# This notebook runs the text-only horizontal suite against the default `openai_chat`
# target. Like the other scenario guides, it loads the shared Scanner configuration and
# your configured target, adversarial, and scoring credentials.

# %%
from pathlib import Path

from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteRegistry
from pyrit.setup import ConfigurationLoader
from pyrit.setup.initializers import ScenarioSuiteInitializer

config = ConfigurationLoader.load_with_overrides(config_file=Path("../../scanner/pyrit_conf.yaml"))
await config.initialize_pyrit_async()  # type: ignore

preset_registry = ScenarioPresetRegistry.get_registry_singleton()
suite_registry = ScenarioSuiteRegistry.get_registry_singleton()

# %% [markdown]
# ## The horizontal techniques suite
#
# `HorizontalTechniquesSuite` evaluates text with three presets that run the same
# objectives (the `airt_harms` dataset):
#
# | Preset | Scenario | Fixed contents |
# | --- | --- | --- |
# | `horizontal_techniques_jailbreaks` | `airt.jailbreak` | 50 templates spanning persona, developer-mode, fiction, nesting, authority, model-specific, and in-the-wild jailbreaks, plus the baseline |
# | `horizontal_techniques_multilingual` | `airt.multilingual` | Translation into 10 languages across distinct scripts, including low-resource Amharic and Swahili |
# | `horizontal_techniques_encoding_obfuscation` | `foundry.red_team_agent` | 10 encodings and character obfuscations, one per distinct mechanism |
#
# Every list is explicit, so results stay comparable across runs and PyRIT releases.
# Only the jailbreak preset includes a baseline, and these presets are hidden from the
# individual Scanner list. Scoring follows the existing scenario defaults.
#
# The presets fetch `airt_harms` only if it is missing from memory. To refresh it,
# run the `refresh_datasets` initializer once before launch.

# %%
await ScenarioSuiteInitializer().initialize_async()  # type: ignore

suite = suite_registry.get_suite(name="horizontal_techniques").suite
for entry in suite.entries:
    preset = preset_registry.get_preset(name=entry.preset_name).preset
    print(f"{preset.name}: {preset.scenario_name}, techniques={preset.techniques}, baseline={preset.include_baseline}")
    for key, value in (preset.scenario_params or {}).items():
        print(f"  {key} ({len(value)}): {', '.join(value)}")

# %% [markdown]
# ## Register suites from YAML
#
# ```yaml
# initializers:
#   - name: target
#   - name: scorer
#   - name: technique
#   - name: scenario_suite
# ```
#
# The initializer takes no parameters. A stored document with the same name as a
# registered suite or preset takes precedence over it.

# %% [markdown]
# ## Run the suite
#
# A launch request names the target and lists the reviewed entries, including stored-definition
# versions, so changes since review can be detected. The suite execution service, which also
# backs the GUI Scanner, preflights every entry before submitting any of them. It then queues
# each preset as an ordinary scenario run against `openai_chat`, in order. Runs use the
# configured default adversarial and scoring roles unless the request overrides them. Each
# run fetches the shared objective dataset on demand and records the suite identity on its
# scenario result. The initializer itself never executes attacks.

# %%
import asyncio

from pyrit.backend.services import get_scenario_suite_execution_service
from pyrit.models import (
    LaunchScenarioSuiteRequest,
    ScenarioSuiteExecutionStatus,
    ScenarioSuiteLaunchEntrySnapshot,
)

request = LaunchScenarioSuiteRequest(
    target_name="openai_chat",
    entries=[
        ScenarioSuiteLaunchEntrySnapshot(
            entry_id=entry.entry_id, preset_name=entry.preset_name, origin="registered", preset_version=None
        )
        for entry in suite.entries
    ],
)
suite_service = get_scenario_suite_execution_service()
execution = await suite_service.launch_async(name=suite.name, request=request)  # type: ignore

while execution.status in (ScenarioSuiteExecutionStatus.QUEUING, ScenarioSuiteExecutionStatus.IN_PROGRESS):
    await asyncio.sleep(10)  # type: ignore
    execution = await suite_service.get_execution_async(execution_id=execution.suite_execution_id)  # type: ignore

print(f"Suite execution: {execution.status.value}")
for entry in execution.entries:
    print(f"{entry.entry_position}. {entry.preset_name}: {entry.state.value}")

# %% [markdown]
# ## Summarize the results
#
# The output helper loads each entry's stored scenario result and prints every entry from
# the execution snapshot, leaving raw conversations in memory. Each entry counts the latest
# outcome of each technique/objective execution. See
# [Printing Suite Results](../output/0_output.ipynb#printing-suite-results).

# %%
from pyrit.output import output_scenario_suite_async

await output_scenario_suite_async(execution)  # type: ignore

# %% [markdown]
# ## Explore a scenario's results
#
# Each **Scenario Result ID** above identifies a child run stored in memory. Load one
# to see its technique-level summary, individual attacks and/or conversations. This example
# selects the first submitted entry; use another printed ID to explore a different scenario.
# The limits and outcome filter keep the attack list output short.

# %%
from pyrit.memory import CentralMemory
from pyrit.output import output_scenario_async, output_scenario_attacks_async

scenario_result_ids = [entry.run.scenario_result_id for entry in execution.entries if entry.run is not None]
if scenario_result_ids:
    scenario_results = await CentralMemory.get_memory_instance().get_scenario_results_async(  # type: ignore
        scenario_result_ids=scenario_result_ids
    )
else:
    scenario_results = []

if scenario_results:
    scenario_result = scenario_results[1]
    await output_scenario_async(scenario_result)  # type: ignore
    await output_scenario_attacks_async(scenario_result, limit=3)  # type: ignore
else:
    print("No stored scenario result is available yet.")

# %% [markdown]
# ## Define your own suite
#
# ### In code
#
# A builder returns a `ScenarioSuite` and the list of `ScenarioPreset` it references.
# Use unique suite and preset names, and fixed entry IDs (for example, `uuid5` of
# the suite and preset names) so launches reviewed before a restart still match.
# Save this example as `my_suites.py` in your configured
# [custom initializer source](../../getting_started/pyrit_conf.md#custom-initializers-source):
#
# ```python
# from uuid import NAMESPACE_URL, uuid5
#
# from pyrit.models import ScenarioPreset, ScenarioSuite, ScenarioSuiteEntry
# from pyrit.setup.initializers import ScenarioSuiteInitializer
#
#
# def build_my_suite() -> tuple[ScenarioSuite, list[ScenarioPreset]]:
#     """Build a suite and its referenced presets."""
#     preset = ScenarioPreset(
#         name="my_base64",
#         scenario_name="foundry.red_team_agent",
#         techniques=["base64"],
#         include_baseline=False,
#     )
#     entry = ScenarioSuiteEntry(
#         entry_id=uuid5(NAMESPACE_URL, "my_suite:my_base64"), preset_name=preset.name
#     )
#     return ScenarioSuite(name="my_suite", entries=[entry]), [preset]
#
#
# class MySuites(ScenarioSuiteInitializer):
#     """Register the built-in suites and a custom suite."""
#
#     SUITE_BUILDERS = (*ScenarioSuiteInitializer.SUITE_BUILDERS, build_my_suite)
# ```
#
# Keeping `*ScenarioSuiteInitializer.SUITE_BUILDERS` includes the built-in horizontal
# suite; omitting it registers only your builders. Configure the backend to load this
# custom initializer instead of `scenario_suite`:
#
# ```yaml
# allow_custom_initializers: true
# initializers:
#   - name: target
#   - name: scorer
#   - name: technique
#   - name: my_suites
# ```
#
# In an initialized notebook, define or import `MySuites` and call
# `await MySuites().initialize_async()` directly.
#
# ### Without code
#
# In the GUI's **Registry > Scenario presets** page, save your presets, then create
# an ordered suite in the **Scenario suites** section. Alternatively, use
# `POST /api/scenario-presets` and `POST /api/scenario-suites`.
#
# Presets are saved to `scenario_presets_source`; suites are saved to
# `scenario_suites_source`. These stored definitions are editable, unlike code-registered ones.
