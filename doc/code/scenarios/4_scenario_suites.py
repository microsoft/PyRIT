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
# A **preset** describes a scenario's techniques, objectives, and parameters.
# A **suite** is an ordered collection of preset references.
# One launch runs those presets against a selected target through the ordinary scenario
# execution path. The `scenario_suite` initializer registers read-only suites whose presets
# are fixed snapshots, similar to how the target and converter initializers register components.
#
# This notebook runs the text-only horizontal suite against the default `openai_chat`
# target. Like the other scenario guides, it loads the shared Scanner configuration and
# your configured target, adversarial, and scoring credentials.

# %%
from pathlib import Path

from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteRegistry
from pyrit.setup import ConfigurationLoader
from pyrit.setup.initializers import ScenarioSuiteInitializer

config = ConfigurationLoader.from_yaml_file(Path("../../scanner/pyrit_conf.yaml"))
await config.initialize_pyrit_async()  # type: ignore

preset_registry = ScenarioPresetRegistry.get_registry_singleton()
suite_registry = ScenarioSuiteRegistry.get_registry_singleton()

# %% [markdown]
# ## The horizontal suite
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
# Each row counts the latest outcome for each technique/objective execution unit.
# This summary leaves raw conversations in memory rather than printing them here.

# %%
from pyrit.analytics import compute_scenario_statistics
from pyrit.backend.services import get_scenario_run_service

run_service = get_scenario_run_service()
results = {
    entry.preset_name: await run_service.get_run_results_async(scenario_result_id=entry.run.scenario_result_id)  # type: ignore
    for entry in execution.entries
    if entry.run is not None
}

for preset_name, result in results.items():
    counts = compute_scenario_statistics(result).overall
    outcomes = counts.outcomes
    assert outcomes is not None
    print(
        f"{preset_name}: {counts.completed}/{counts.planned} completed, "
        f"{outcomes.successes} succeeded, {outcomes.failures} failed, "
        f"{outcomes.errors} errors, {outcomes.undetermined} undetermined"
    )
