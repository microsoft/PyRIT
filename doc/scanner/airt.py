# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
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
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer, TechniqueInitializer

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[TargetInitializer(), ScorerInitializer(), TechniqueInitializer()],
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
# pyrit_scan run airt.rapid_response \
#   --initializers target \
#   --target openai_chat \
#   --techniques role_play_movie_script \
#   --datasets airt_hate \
#   --max-total 1
# ```
#
# **Available techniques:** ALL, DEFAULT, SINGLE_TURN, MULTI_TURN, role_play_movie_script, many_shot, tap

# %%
from pyrit.scenario.airt import RapidResponse, RapidResponseTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_hate")], max_total=1)

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
# Tests whether a target harms the well-being or mental health of users across two sub-harms:
# **imminent crisis** (mistreating someone in a mental-health crisis, facilitating self-harm) and
# **licensed therapist** (improperly acting as or claiming to be a real therapist). Choose sub-harms
# with the `--sub-harm` parameter (`imminent_crisis`, `licensed_therapist`, or `all`); both run by
# default, each with its own dataset, escalation prompt, and conversation-level scorer.
#
# ```bash
# pyrit_scan run airt.psychosocial --target openai_chat --techniques tone
# ```
#
# Each sub-harm escalates a simulated multi-turn conversation toward the objective, then layers the
# selected converter techniques on top (natural-language rewrites that preserve emotional framing;
# obfuscation converters like base64/morse are excluded). `--datasets` can select a subset of the
# selected sub-harms' datasets; `--max-total` must cover each selected sub-harm.
#
# **Available techniques:** ALL, DEFAULT, tone, language, persuasion, deterministic, crescendo

# %%
from pyrit.scenario.airt import Psychosocial, PsychosocialTechnique

# Minimal demo: a single sub-harm, one technique (the bare simulated-crescendo base), and one
# objective. Omit `scenario_techniques` to run the DEFAULT converter sweep across the full dataset.
dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_imminent_crisis")], max_total=1)

scenario = Psychosocial()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "sub_harm": "imminent_crisis",
        "scenario_techniques": [PsychosocialTechnique.NoConverter],
        "dataset_config": dataset_config,
        "max_turns": 2,
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
# pyrit_scan run airt.cyber \
#   --initializers target \
#   --target openai_chat \
#   --techniques role_play_movie_script \
#   --max-total 1
# ```
#
# **Available techniques:** Use `pyrit_scan run airt.cyber --list-scenario-parameters` to inspect
# the current registry-backed technique catalog and its aggregate selectors.

# %%
from pyrit.scenario.airt import Cyber, CyberTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_malware")], max_total=1)

scenario = Cyber()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [CyberTechnique.role_play_movie_script],
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
# Tests target resilience against jailbreak templates. A run crosses three selectors: the harmful
# objectives (**dataset**, HarmBench), the **techniques** each jailbreak is delivered through, and
# which **jailbreaks** to run. Two deliveries are on by default: `prompt_sending` renders the
# objective inline into the template as a request converter (target-agnostic), and
# `jailbreak_system_prompt` sets the template as a native system prompt with the objective sent as
# the user turn (only for targets that natively support editable history + system prompts — it is
# skipped for incapable targets). These are the only delivery techniques exposed by Jailbreak.
# Generic simulated, multi-turn, or non-composable registry techniques are intentionally excluded
# because they cannot preserve Jailbreak's per-template delivery semantics. Results are grouped by
# jailbreak template, and a baseline (the un-jailbroken objective) is included by default so
# complying with the bare objective is itself visible.
#
# ```bash
# pyrit_scan run airt.jailbreak \
#   --initializers target \
#   --target openai_chat \
#   --datasets harmbench \
#   --max-total 1
# ```
#
# **Available technique selectors:** ALL, DEFAULT, and SINGLE_TURN currently select both
# `prompt_sending` and `jailbreak_system_prompt`; either concrete technique can also be selected
# directly. By default a small random sample of jailbreak templates runs; pass `num_jailbreaks`
# (random count) or `jailbreak_names` (explicit) to widen or pin the selection.

# %%
from pyrit.scenario.airt import Jailbreak, JailbreakTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="harmbench")], max_total=1)

scenario = Jailbreak()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [JailbreakTechnique.prompt_sending],
        "jailbreak_names": ["aim.yaml"],
        "dataset_config": dataset_config,
        "include_baseline": False,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Multilingual
#
# Tests whether target safeguards remain effective when harmful objectives are presented in other
# languages. A run crosses registered text-compatible attack techniques with datasets and translation
# strategies. By default, `translation` translates each objective into every selected language, and
# `random_translation` translates individual words using the full selected language pool. A baseline
# sends each objective without translation and is included by default.
#
# ```bash
# pyrit_scan airt.multilingual \
#   --initializers target \
#   --target openai_chat \
#   --datasets harmbench \
#   --max-total 1
# ```
#
# **Available techniques:** `prompt_sending` is the default. Every registry technique (`role_play_*`,
# `many_shot`, `tap`, …) whose built-in request converter chain ends in text is also available.
#
# **Translation strategies:** `translation` and `random_translation` (both default). A bare run translates
# five objectives into five randomly selected languages, plus a word-level random language translation.
# Pass `num_languages` to change the random sample size or `languages` to provide an explicit list.
# The two language selectors are mutually exclusive.

# %%
from pyrit.scenario.airt import Multilingual

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="harmbench")], max_total=1)

scenario = Multilingual()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "languages": ["French"],
        "translation_strategies": ["translation"],
        "dataset_config": dataset_config,
        "include_baseline": False,
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
# pyrit_scan run airt.leakage --target openai_chat --techniques first_letter --max-total 1
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

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_leakage")], max_total=1)

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
# ## Aggregation Leakage
#
# Tests whether a target discloses a confidential conclusion that no single document states but that
# the documents it was given entail together. Retrieval-augmented assistants usually check access per
# document, so every document here is one the user may read; the leak is the inference the model draws
# across them (for example, a policy that applies *only* to employees on medical leave plus a calendar
# showing a named employee in exactly that kind of meeting).
#
# ```bash
# pyrit_scan run airt.aggregation_leakage --target openai_chat
# ```
#
# **Available techniques:** ALL, DEFAULT, SINGLE_TURN, MULTI_TURN, PromptSending, RedTeaming. DEFAULT runs
# PromptSending, which sends each question after the retrieved documents. RedTeaming lets an adversarial
# chat ask follow-up questions over the same documents for up to `max_turns` turns.
#
# Results are reported per technique and dataset:
#
# - `airt_aggregation_leakage` — the documents entail the confidential fact (rule + instance, transitive
#   chain, set complement, numeric residual), asked indirectly and directly.
# - `airt_aggregation_leakage_minimal_pairs` and `airt_aggregation_leakage_mention_only` — controls where
#   the fact does **not** follow. Their success rates should stay near zero; if they don't, the target or
#   the scorer is reacting to the topic rather than to the derivation.
#
# With `include_context=False`, supply exactly one explicit case using
# `DatasetAttackConfiguration(seed_groups=[selected_group])` and set `external_context_isolated=True`.
# Before running, provision the target with that case's documents and confidentiality policy only.
# Use a fresh knowledge base/memory scope for each positive or control case, and clear prior conversation
# state. Do not load the three datasets into one knowledge base: they contain conflicting versions of
# the same evidence and may ask identical questions. Named datasets and multiple inline cases are rejected
# in this mode. The flag confirms caller-managed isolation; PyRIT cannot inspect the external knowledge base.
# Only execution inputs lose the marked context turn; the original seed group remains in the saved plan,
# so resuming uses the same case. Resume must reconnect to the same externally provisioned evidence.
#
# Additional `technique_converters` apply to the questions in both techniques, not to the context turn.

# %%
from pyrit.scenario.airt import AggregationLeakage, AggregationLeakageTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_aggregation_leakage")], max_total=1)

scenario = AggregationLeakage()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [AggregationLeakageTechnique.PromptSending],
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
# pyrit_scan run airt.scam \
#   --initializers target \
#   --target openai_chat \
#   --techniques context_compliance \
#   --max-total 1
# ```
#
# **Available techniques:** ALL, DEFAULT, SINGLE_TURN, MULTI_TURN, ContextCompliance, RolePlay,
# PersuasiveRedTeamingAttack. DEFAULT runs the single-turn techniques (ContextCompliance, RolePlay)
# and omits the slower multi-turn PersuasiveRedTeamingAttack; run it via ALL or MULTI_TURN.

# %%
from pyrit.scenario.airt import Scam, ScamTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="airt_scams")], max_total=1)

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
