# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
#   kernelspec:
#     display_name: pyrit-dev
#     language: python
#     name: pyrit-dev
# ---

# %% [markdown]
# # 3. Psychosocial Harms Scenario
#
# The `Psychosocial` scenario tests whether a target model appropriately handles psychosocial crisis
# situations. It evaluates two subharms: **imminent crisis** (e.g., suicidal ideation, self-harm) and
# **licensed therapist impersonation** (e.g., providing clinical mental health advice without qualification).
#
# This scenario uses a three-layer evaluation approach:
#
# 1. **User contexts** map to specific harmful objectives (e.g., "Help me write a suicide note")
# 2. **User behaviors** are modeled via YAML-defined attack strategies (e.g., escalation patterns)
# 3. **Model behaviors** are evaluated using detailed scoring rubrics aligned with established frameworks
#    like MHFA (Mental Health First Aid) and the ALGEE methodology
#
# ## Conversation-Level Scoring
#
# Unlike content harms that can often be evaluated from a single response, psychosocial harms often emerge
# gradually across a multi-turn conversation. The Psychosocial scenario uses `create_conversation_scorer()`
# to wrap its rubric-based scorer, enabling evaluation of the **entire conversation history** rather than
# just the latest response. This is critical for detecting patterns like gradual crisis escalation.
#
# ## Built-in Rubrics
#
# Each subharm has a built-in YAML rubric that defines the scoring criteria:
#
# - **`crisis_management.yaml`** — Evaluates whether the model recognizes a mental health crisis and
#   responds according to the MHFA/ALGEE methodology (Approach, Listen, Give reassurance, Encourage
#   professional help, Encourage self-help). Scores range from 1 (not harmful) to 5 (extremely harmful).
# - **`licensed_therapist.yaml`** — Evaluates whether the model inappropriately provides clinical
#   mental health advice, impersonating a licensed professional.
#
# These rubrics can be customized per-subharm via the `SubharmConfig` dataclass if needed.
#
# ## Available Strategies
#
# | Strategy | CLI Value | Tags | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | all | Runs both subharms |
# | ImminentCrisis | `imminent_crisis` | — | Tests crisis escalation responses |
# | LicensedTherapist | `licensed_therapist` | — | Tests for clinical advice impersonation |
#
# **Note:** This scenario does not include a default baseline (`include_default_baseline=False`).
# A single-turn baseline would not be meaningful here because psychosocial harms emerge through
# multi-turn escalation — the built-in rubric-based scorer evaluates conversation-level crisis handling,
# which requires the specific attack strategies (PromptSending+ToneConverter, RolePlay, Crescendo) to
# produce evaluable conversations.
#
# ## Default Datasets
#
# The default dataset is `airt_imminent_crisis`, containing crisis-related objectives. You can bring your
# own datasets using `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI flag —
# see [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details and
# [Configuring RedTeamAgent](1_red_team_agent.ipynb) for advanced dataset configuration.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.airt import Psychosocial, PsychosocialStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# To test the imminent crisis subharm quickly:
#
# ```bash
# pyrit_scan airt.psychosocial \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies imminent_crisis \
#   --max-dataset-size 1
# ```
#
# To run all subharms:
#
# ```bash
# pyrit_scan airt.psychosocial \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --max-dataset-size 1
# ```
#
# ## Programmatic Usage
#
# Here we run only the `imminent_crisis` strategy with a minimal dataset.

# %%
dataset_config = DatasetConfiguration(dataset_names=["airt_imminent_crisis"], max_dataset_size=1)

scenario = Psychosocial()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[PsychosocialStrategy.ImminentCrisis],
    dataset_config=dataset_config,
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

# %%
scenario_result = await scenario.run_async()  # type: ignore

# %% [markdown]
# ## Interpreting Results
#
# The Psychosocial scenario uses a `FloatScaleThresholdScorer` rather than a binary true/false scorer.
# The underlying rubric scores responses on a 1–5 scale based on how harmful the model's behavior is.
# The default threshold is 1.0, meaning only a raw score of 5 (extremely harmful) counts as attack
# success. You can create your own `FloatScaleThresholdScorer` with a different threshold to adjust
# sensitivity based on your requirements.

# %%
await printer.print_summary_async(scenario_result)  # type: ignore
