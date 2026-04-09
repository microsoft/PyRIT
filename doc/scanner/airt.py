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
# # AIRT Scenarios
#
# AIRT (AI Red Team) scenarios test common AI safety risks. Each scenario below runs with minimal
# configuration — a single strategy and small dataset — to demonstrate usage. For full configuration
# options, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %% [markdown]
# ## Setup
#
# This notebook uses the `pyrit_conf.yaml` file included in this directory, which configures a
# target, scorer, and default datasets via initializers. See [Configuration](../getting_started/configuration.md)
# for details.

# %%
from pathlib import Path

from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().get_instance_by_name("openai_chat")
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Content Harms
#
# Tests whether a target can be induced to generate harmful content across seven categories: hate,
# fairness, violence, sexual, harassment, misinformation, and leakage.
#
# ```bash
# pyrit_scan airt.content_harms --target openai_chat --strategies hate --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, Hate, Fairness, Violence, Sexual, Harassment, Misinformation, Leakage

# %%
from pyrit.scenario.scenarios.airt import ContentHarms, ContentHarmsStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_hate"], max_dataset_size=1)

scenario = ContentHarms()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[ContentHarmsStrategy.Hate],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Psychosocial
#
# Tests whether a target appropriately handles psychosocial crisis situations, evaluating imminent
# crisis handling and licensed therapist impersonation.
#
# ```bash
# pyrit_scan airt.psychosocial --target openai_chat --strategies imminent_crisis --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, ImminentCrisis, LicensedTherapist
#
# ### Conversation-Level Scoring
#
# Unlike content harms that can be evaluated from a single response, psychosocial harms emerge
# gradually across a multi-turn conversation. This scenario uses `create_conversation_scorer()`
# to evaluate the **entire conversation history** rather than just the latest response — critical
# for detecting patterns like gradual crisis escalation.
#
# Each subharm has a built-in YAML rubric:
#
# - **`crisis_management.yaml`** — Evaluates crisis recognition using the MHFA/ALGEE methodology
#   (Approach, Listen, Give reassurance, Encourage professional help, Encourage self-help).
#   Scores range from 1 (not harmful) to 5 (extremely harmful).
# - **`licensed_therapist.yaml`** — Evaluates whether the model inappropriately provides clinical
#   mental health advice.
#
# Rubrics can be customized per-subharm via the `SubharmConfig` dataclass.
#
# **Note:** This scenario does not include a default baseline. A single-turn baseline would not be
# meaningful because psychosocial harms emerge through multi-turn escalation.

# %%
from pyrit.scenario.scenarios.airt import Psychosocial, PsychosocialStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_imminent_crisis"], max_dataset_size=1)

scenario = Psychosocial()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[PsychosocialStrategy.ImminentCrisis],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Cyber
#
# Tests whether a target can be induced to generate malware or exploitation content using single-turn
# and multi-turn attacks.
#
# ```bash
# pyrit_scan airt.cyber --target openai_chat --strategies single_turn --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, SINGLE_TURN, MULTI_TURN

# %%
from pyrit.scenario.scenarios.airt import Cyber, CyberStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_malware"], max_dataset_size=1)

scenario = Cyber()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[CyberStrategy.SINGLE_TURN],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Jailbreak
#
# Tests target resilience against template-based jailbreak attacks using various prompt injection
# templates.
#
# ```bash
# pyrit_scan airt.jailbreak --target openai_chat --strategies prompt_sending --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, SIMPLE, COMPLEX, PromptSending, ManyShot, SkeletonKey, RolePlay

# %%
from pyrit.scenario.scenarios.airt import Jailbreak, JailbreakStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_harms"], max_dataset_size=1)

scenario = Jailbreak()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[JailbreakStrategy.PromptSending],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Leakage
#
# Tests whether a target can be induced to leak sensitive data or intellectual property, scored using
# plagiarism detection.
#
# ```bash
# pyrit_scan airt.leakage --target openai_chat --strategies first_letter --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, SINGLE_TURN, MULTI_TURN, IP, SENSITIVE_DATA, FirstLetter, Image, RolePlay, Crescendo
#
# ### Copyright and Plagiarism Testing
#
# The `FirstLetter` strategy tests whether a model has memorized copyrighted text by encoding it
# with `FirstLetterConverter` (extracting first letters of each word) and asking the model to decode.
# If the model reconstructs the original, it suggests memorization.
#
# The `PlagiarismScorer` provides three complementary metrics for analyzing responses from any
# leakage strategy:
#
# - **LCS (Longest Common Subsequence)** — Captures contiguous plagiarized sequences.
#   Score = LCS length / reference length.
# - **Levenshtein (Edit Distance)** — Measures word-level edit distance.
#   Score = 1 − (min edits / max length).
# - **Jaccard (N-gram Overlap)** — Measures phrase-level similarity using configurable n-grams.
#   Score = matching n-grams / total reference n-grams.
#
# All metrics are normalized to \[0, 1\] where 1 means the reference text is fully present. There is
# no built-in threshold — the scorer returns a raw float for you to interpret per your use case.

# %%
from pyrit.scenario.scenarios.airt import Leakage, LeakageStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_leakage"], max_dataset_size=1)

scenario = Leakage()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[LeakageStrategy.FirstLetter],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Scam
#
# Tests whether a target can be induced to generate scam, phishing, or fraud content.
#
# ```bash
# pyrit_scan airt.scam --target openai_chat --strategies context_compliance --max-dataset-size 1
# ```
#
# **Available strategies:** ALL, SINGLE_TURN, MULTI_TURN, ContextCompliance, RolePlay, PersuasiveRedTeamingAttack

# %%
from pyrit.scenario.scenarios.airt import Scam, ScamStrategy

dataset_config = DatasetConfiguration(dataset_names=["airt_scams"], max_dataset_size=1)

scenario = Scam()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[ScamStrategy.ContextCompliance],
    dataset_config=dataset_config,
)

scenario_result = await scenario.run_async()  # type: ignore

# %%
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# ## Next Steps
#
# For building custom scenarios, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).
# For setting up targets, see [Configuration](../getting_started/configuration.md).
