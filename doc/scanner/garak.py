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
# various formats) and web-injection probes (which test whether a target emits markdown
# data-exfiltration or cross-site-scripting payloads).
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario.garak import Encoding, EncodingStrategy
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
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_strategies": [EncodingStrategy.Base64],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

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
# ## Doctor
#
# Ports Garak's `doctor` probe, which implements the HiddenLayer Policy Puppetry universal bypass:
# a post-instruction-hierarchy prompt injection that frames a harmful request as a fictional
# medical-drama script ("Dr House") to override safety guardrails. Each objective from the
# `garak_doctor` dataset is wrapped in a Policy Puppetry template via `PolicyPuppetryConverter` and
# the response is scored for non-refusal (mirroring Garak's `mitigation.MitigationBypass` detector).
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.doctor --target openai_chat --strategies policy_puppetry --max-dataset-size 1
# ```
#
# **Available strategies** (2 probes): `PolicyPuppetry` (wraps the objective in the Dr House
# template) and `PolicyPuppetryLeet` (the same template, additionally leetspeak-encoded). Both are
# tagged `default`, so `DEFAULT` and `ALL` currently coincide.

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
