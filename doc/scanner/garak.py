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
# # Garak Scenarios
#
# The Garak scenario family implements probes inspired by the
# [Garak](https://github.com/NVIDIA/garak) framework. These include encoding-based probes (which
# test whether a target can be tricked into producing harmful content when prompts are encoded in
# various formats), web-injection probes (which test whether a target emits markdown
# data-exfiltration or cross-site-scripting payloads), and system-prompt-extraction probes (which
# test whether a target can be coaxed into revealing its own system prompt).
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario.garak import (
    Encoding,
    EncodingStrategy,
    SystemPromptExtraction,
    SystemPromptExtractionStrategy,
)
from pyrit.scenario.garak.encoding import EncodingDatasetConfiguration
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
# %% [markdown]
# ## Encoding
#
# Tests whether the target can decode and comply with encoded harmful prompts. Each encoding
# strategy encodes the prompt, asks the target to decode it, and scores whether the decoded output
# matches the harmful content. Default datasets include slur terms and web/HTML/JS content.
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.encoding --target openai_chat --strategies base64 --max-dataset-size 1
# ```
#
# **Available strategies** (17 encodings): Base64, Base2048, Base16, Base32, ASCII85, Hex,
# QuotedPrintable, UUencode, ROT13, Braille, Atbash, MorseCode, NATO, Ecoji, Zalgo, LeetSpeak,
# AsciiSmuggler
#
# > **Note:** Strategy composition is NOT supported for Encoding — each encoding is tested
# > independently.

# %%
dataset_config = EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"], max_dataset_size=1)

scenario = Encoding()
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[EncodingStrategy.Base64],
    dataset_config=dataset_config,
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## WebInjection
#
# Ports Garak's `web_injection` probe family. Tests whether the target can be coaxed into emitting
# web-injection payloads: markdown-image data exfiltration (CSRF-style) and cross-site-scripting
# (XSS). Each strategy pairs a `SeedObjective` (the goal) with a `SeedPrompt` (the literal
# injection prompt) and scores the response with the markdown-injection or XSS output scorer.
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.web_injection --target openai_chat --strategies xss --max-dataset-size 1
# ```
#
# **Available strategies** (8 probes): MarkdownImageExfil, ColabAIDataLeakage,
# StringAssemblyDataExfil, PlaygroundMarkdownExfil, MarkdownURIImageExfilExtended,
# MarkdownURINonImageExfilExtended, TaskXSS, MarkdownXSS.
#
# **Aggregate strategies:** `ALL` (all 8), `DEFAULT` (excludes the two combinatorial extended
# probes), `EXFIL` (the 6 markdown-exfil probes), and `XSS` (TaskXSS + MarkdownXSS).

# %% [markdown]
# ## SystemPromptExtraction
#
# Ports Garak's `sysprompt_extraction` probe. A real system prompt (sourced from the
# `garak_drh_system_prompts` / `garak_tm_system_prompts` libraries) is installed on the target, then
# an extraction request asks the model to reveal it. Responses are scored deterministically by
# `SystemPromptExtractionScorer`, a character n-gram containment overlap between the response and the
# known system prompt (a faithful port of Garak's `PromptExtraction` detector), wrapped by a
# `FloatScaleThresholdScorer` at threshold 0.5.
#
# Each of the 9 attack-template categories is a strategy; across the selected categories the total
# (system prompt × template) combinations are randomly sampled down to `prompt_cap` (Garak's
# `soft_probe_prompt_cap`, default 256) so a default run stays bounded.
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.system_prompt_extraction --target openai_chat --strategies direct_requests
# ```
#
# **Available strategies** (9 categories): DirectRequests, RolePlayingAttacks, EncodingBasedAttacks,
# IndirectCreativeApproaches, CodeTechnicalFraming, ContinuationTricks, MultiLayeredApproaches,
# AuthorityUrgencyFraming, ConfusionDistraction.
#
# The minimal run below installs a single system prompt and runs one category so it completes
# quickly.

# %%
sysprompt_scenario = SystemPromptExtraction(system_prompt_subsample=1, prompt_cap=1)
await sysprompt_scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[SystemPromptExtractionStrategy.DirectRequests],
)

print(f"Scenario: {sysprompt_scenario.name}")
print(f"Atomic attacks: {sysprompt_scenario.atomic_attack_count}")

sysprompt_result = await sysprompt_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(sysprompt_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
