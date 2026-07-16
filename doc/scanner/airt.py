# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
# ---

# %% [markdown]
# # AIRT Scenarios
#
# AIRT (AI Red Team) scenarios test common AI safety risks. Each scenario below runs with minimal
# configuration — a single technique and small dataset — to demonstrate usage. For full configuration
# options, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %% [markdown]
# ## Setup

# %%
from pyrit.output import output_scenario_async
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import (
    LoadDefaultDatasets,
    ScorerInitializer,
    TargetInitializer,
    TechniqueInitializer,
)

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[TargetInitializer(), ScorerInitializer(), TechniqueInitializer(), LoadDefaultDatasets()],
)

objective_target = OpenAIChatTarget()
# %% [markdown]
# ## Rapid Response
#
# Tests whether a target can be induced to generate harmful content across seven categories: hate,
# fairness, violence, sexual, harassment, misinformation, and leakage. Each technique applies a
# different attack technique to the full set of harm datasets.
#
# ```bash
# pyrit_scan airt.rapid_response \
#   --initializers target \
#   --target openai_chat \
#   --techniques role_play_movie_script \
#   --dataset-names airt_hate \
#   --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, DEFAULT, SINGLE_TURN, MULTI_TURN, role_play_movie_script, many_shot, tap

# %%
from pyrit.scenario.airt import RapidResponse, RapidResponseTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_hate"], max_dataset_size=1)

scenario = RapidResponse()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [RapidResponseTechnique.role_play_movie_script],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Psychosocial
#
# Tests whether a target appropriately handles psychosocial crisis situations, evaluating imminent
# crisis handling and licensed therapist impersonation.
#
# ```bash
# pyrit_scan airt.psychosocial --target openai_chat --techniques imminent_crisis --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, ImminentCrisis, LicensedTherapist
#
# ### Conversation-Level Scoring
#
# Unlike content harms that can be evaluated from a single response, psychosocial harms emerge
# gradually across a multi-turn conversation. This scenario uses create_conversation_scorer()
# to evaluate the **entire conversation history** rather than just the latest response — critical
# for detecting patterns like gradual crisis escalation.
#
# Each subharm has a built-in YAML rubric:
#
# - **crisis_management.yaml** — Evaluates crisis recognition using the MHFA/ALGEE methodology
#   (Approach, Listen, Give reassurance, Encourage professional help, Encourage self-help).
#   Scores range from 1 (not harmful) to 5 (extremely harmful).
# - **licensed_therapist.yaml** — Evaluates whether the model inappropriately provides clinical
#   mental health advice.
#
# Rubrics can be customized per-subharm via the SubharmConfig dataclass.
#
# **Note:** This scenario does not include a default baseline. A single-turn baseline would not be
# meaningful because psychosocial harms emerge through multi-turn escalation.

# %%
from pyrit.scenario.airt import Psychosocial, PsychosocialTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_imminent_crisis"], max_dataset_size=1)

scenario = Psychosocial()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [PsychosocialTechnique.ImminentCrisis],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Cyber
#
# Tests whether a target can be induced to generate malware or exploitation content using single-turn
# and multi-turn attacks.
#
# ```bash
# pyrit_scan airt.cyber \
#   --initializers target \
#   --target openai_chat \
#   --techniques multi_turn \
#   --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, DEFAULT, MULTI_TURN, red_teaming

# %%
from pyrit.scenario.airt import Cyber, CyberTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_malware"], max_dataset_size=1)

scenario = Cyber()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [CyberTechnique.MULTI_TURN],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Jailbreak
#
# Tests target resilience against template-based jailbreak attacks using various prompt injection
# templates.
#
# ```bash
# pyrit_scan airt.jailbreak \
#   --initializers target \
#   --target openai_chat \
#   --techniques prompt_sending \
#   --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, SIMPLE, COMPLEX, PromptSending, ManyShot, SkeletonKey, RolePlay

# %%
from pyrit.scenario.airt import Jailbreak, JailbreakTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_harms"], max_dataset_size=1)

scenario = Jailbreak()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [JailbreakTechnique.PromptSending],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Leakage
#
# Tests whether a target can be induced to leak sensitive data or intellectual property, scored using
# plagiarism detection.
#
# ```bash
# pyrit_scan airt.leakage --target openai_chat --techniques first_letter --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, SINGLE_TURN, MULTI_TURN, IP, SENSITIVE_DATA, FirstLetter, Image, RolePlay, Crescendo
#
# ### Copyright and Plagiarism Testing
#
# The FirstLetter technique tests whether a model has memorized copyrighted text by encoding it
# with FirstLetterConverter (extracting first letters of each word) and asking the model to decode.
# If the model reconstructs the original, it suggests memorization.
#
# The PlagiarismScorer provides three complementary metrics for analyzing responses from any
# leakage technique:
#
# - **LCS (Longest Common Subsequence)** — Captures contiguous plagiarized sequences.
#   Score = LCS length / reference length.
# - **Levenshtein (Edit Distance)** — Measures word-level edit distance.
#   Score = 1 − (min edits / max length).
# - **Jaccard (N-gram Overlap)** — Measures phrase-level similarity using configurable n-grams.
#   Score = matching n-grams / total reference n-grams.
#
# All metrics are normalized to [0, 1] where 1 means the reference text is fully present. There is
# no built-in threshold — the scorer returns a raw float for you to interpret per your use case.

# %%
from pyrit.scenario.airt import Leakage, LeakageTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_leakage"], max_dataset_size=1)

scenario = Leakage()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [LeakageTechnique.first_letter],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Scam
#
# Tests whether a target can be induced to generate scam, phishing, or fraud content.
#
# ```bash
# pyrit_scan airt.scam \
#   --initializers target \
#   --target openai_chat \
#   --techniques context_compliance \
#   --max-dataset-size 1
# ```
#
# **Available techniques:** ALL, DEFAULT, SINGLE_TURN, MULTI_TURN, ContextCompliance, RolePlay,
# PersuasiveRedTeamingAttack. DEFAULT runs the single-turn techniques (ContextCompliance, RolePlay)
# and omits the slower multi-turn PersuasiveRedTeamingAttack; run it via ALL or MULTI_TURN.

# %%
from pyrit.scenario.airt import Scam, ScamTechnique

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_scams"], max_dataset_size=1)

scenario = Scam()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [ScamTechnique.ContextCompliance],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)
