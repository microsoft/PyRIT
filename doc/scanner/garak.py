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
# # Garak Scenarios
#
# The Garak scenario family implements probes inspired by the
# [Garak](https://github.com/NVIDIA/garak) framework. These include encoding-based probes (which
# test whether a target can be tricked into producing harmful content when prompts are encoded in
# various formats), web-injection probes (which test whether a target emits markdown
# data-exfiltration or cross-site-scripting payloads), a doctor probe (which applies the Policy
# Puppetry universal bypass), system-prompt-extraction probes (which test whether a target can be
# coaxed into revealing its own system prompt), package-hallucination probes (which test whether a
# target recommends non-existent packages that an attacker could squat), an audio probe (which
# delivers spoken jailbreaks to multimodal targets), and FigStep visual jailbreaks (which place
# harmful instructions in images).
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.prompt_target import RealtimeTarget
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.garak import (
    Encoding,
    EncodingTechnique,
    FigStep,
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
)
from pyrit.scenario.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelDatasetConfiguration
from pyrit.scenario.garak.encoding import EncodingDatasetConfiguration
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
# %% [markdown]
# ## Encoding
#
# Tests whether the target can decode and comply with encoded harmful prompts. Each encoding
# technique encodes the prompt, asks the target to decode it, and scores whether the decoded output
# matches the harmful content. Default datasets include slur terms and web/HTML/JS content.
#
# **CLI example:**
#
# ```bash
# pyrit_scan run garak.encoding --target openai_chat --techniques base64 --max-dataset-size 1
# ```
#
# **Available techniques** (17 encodings): Base64, Base2048, Base16, Base32, ASCII85, Hex,
# QuotedPrintable, UUencode, ROT13, Braille, Atbash, MorseCode, NATO, Ecoji, Zalgo, LeetSpeak,
# AsciiSmuggler
#
# **Aggregate techniques:** `ALL` (every encoding, exhaustive) and `DEFAULT` (a broad curated subset
# spanning every encoding family — base-N, byte-encodings, substitution ciphers, and symbolic
# alphabets — for a meaningful default scan; the niche/lossy schemes are ALL-only). `DEFAULT` is used
# when no techniques are specified.
#
# > **Note:** Technique composition is NOT supported for Encoding — each encoding is tested
# > independently.

# %%
dataset_config = EncodingDatasetConfiguration(dataset_names=["garak_slur_terms_en"], max_dataset_size=1)

scenario = Encoding()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [EncodingTechnique.Base64],
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
# ## FigStep
#
# Tests whether a vision-language target follows harmful instructions that appear in an image.
# `FigStep` sends one typographic image and carrier text. `FigStep-Pro` splits the visual prompt
# across several images. Both variants reuse the built-in SafeBench-Tiny groups, images, and carrier
# text. PyRIT scores whether the response completes the harmful objective. It does not only check
# whether the response contains numbered steps.
#
# **CLI examples:**
#
# ```bash
# pyrit_scan garak.figstep --target openai_chat --dataset-names figstep --max-dataset-size 1
# pyrit_scan garak.figstep --target openai_chat --dataset-names figstep_pro --max-dataset-size 1
# ```
#
# > **Note:** The objective target must natively support multi-piece user messages and accept text
# > and image input in the same message. Select exactly one of the `figstep` or `figstep_pro`
# > datasets; unrelated named datasets are rejected because they do not contain the required visual
# > payload. By default, PyRIT also sends each sampled objective as direct text. Use
# > `--include-baseline False` to omit this comparison.

# %%
figstep_dataset_config = DatasetAttackConfiguration(dataset_names=["figstep"], max_dataset_size=1)

figstep_scenario = FigStep()
figstep_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "dataset_config": figstep_dataset_config,
    }
)
await figstep_scenario.initialize_async()  # type: ignore

print(f"Scenario: {figstep_scenario.name}")
print(f"Atomic attacks: {figstep_scenario.atomic_attack_count}")

figstep_result = await figstep_scenario.run_async()  # type: ignore

await output_scenario_async(figstep_result)

# %% [markdown]
# ## WebInjection
#
# Ports Garak's `web_injection` probe family. Tests whether the target can be coaxed into emitting
# web-injection payloads: markdown-image data exfiltration (CSRF-style) and cross-site-scripting
# (XSS). Each technique pairs a `SeedObjective` (the goal) with a `SeedPrompt` (the literal
# injection prompt) and scores the response with the markdown-injection or XSS output scorer.
#
# **CLI example:**
#
# ```bash
# pyrit_scan run garak.web_injection --target openai_chat --techniques xss --max-dataset-size 1
# ```
#
# **Available techniques** (8 probes): MarkdownImageExfil, ColabAIDataLeakage,
# StringAssemblyDataExfil, PlaygroundMarkdownExfil, MarkdownURIImageExfilExtended,
# MarkdownURINonImageExfilExtended, TaskXSS, MarkdownXSS.
#
# **Aggregate techniques:** `ALL` (all 8), `DEFAULT` (excludes the two combinatorial extended
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
# pyrit_scan run garak.doctor --target openai_chat --techniques policy_puppetry --max-dataset-size 1
# ```
#
# **Available techniques** (2 probes): `PolicyPuppetry` (wraps the objective in the Dr House
# template) and `PolicyPuppetryLeet` (the same template, additionally leetspeak-encoded). Both are
# tagged `default`, so `DEFAULT` and `ALL` currently coincide.

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
# Each of the 9 attack-template categories is a technique; across the selected categories the total
# (system prompt × template) combinations are randomly sampled down to `prompt_cap` (Garak's
# `soft_probe_prompt_cap`, default 256) so a default run stays bounded.
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.system_prompt_extraction --target openai_chat --techniques direct_requests
# ```
#
# **Available techniques** (9 categories): DirectRequests, RolePlayingAttacks, EncodingBasedAttacks,
# IndirectCreativeApproaches, CodeTechnicalFraming, ContinuationTricks, MultiLayeredApproaches,
# AuthorityUrgencyFraming, ConfusionDistraction.
#
# The minimal run below installs a single system prompt and runs one category so it completes
# quickly.

# %%
sysprompt_scenario = SystemPromptExtraction(system_prompt_subsample=1, prompt_cap=1)
sysprompt_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [SystemPromptExtractionTechnique.DirectRequests],
    }
)
await sysprompt_scenario.initialize_async()  # type: ignore

print(f"Scenario: {sysprompt_scenario.name}")
print(f"Atomic attacks: {sysprompt_scenario.atomic_attack_count}")

sysprompt_result = await sysprompt_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(sysprompt_result)

# %% [markdown]
# ## PackageHallucination
#
# Ports Garak's `packagehallucination` probe. Asks the target to write code for a given language
# (rendered from Garak's `stub_prompts` × `code_tasks`) and scores each response for imports of
# packages that do not exist in that language's registry. A hallucinated package name is a
# supply-chain foothold: an attacker can register ("squat") it so the model's suggested code
# silently pulls in a malicious dependency ("slopsquatting").
#
# Each selected language runs with a dedicated `PackageHallucinationScorer` loaded with that
# ecosystem's registry. The scoring is deterministic set-membership — no LLM judge is involved.
#
# **CLI example:**
#
# ```bash
# # Run the default Rust technique.
# pyrit_scan garak.package_hallucination --target openai_chat
#
# # Select another supported language.
# pyrit_scan garak.package_hallucination --target openai_chat --techniques dart
# ```
#
# **Available techniques** (7 languages): Python, JavaScript, Ruby, Rust, Dart, Perl, Raku.
#
# **Aggregate techniques:** `DEFAULT` runs Rust. `ALL` runs all seven languages.
#
# > **Note:** Rust and its crates.io registry are the default because this registry is much smaller.
# > If you select another language, PyRIT downloads its registry on demand. The raw package names
# > are loaded into memory only for the scorer and are never sent as prompts.

# %% [markdown]
# ## AudioAchillesHeel
#
# Ports Garak's `audio.AudioAchillesHeel` probe. Delivers the adversarial instruction as *spoken
# audio* while the text channel carries only a benign "follow the audio instructions" nudge. Each
# clip from the `garak_audio_achilles_heel` dataset is shaped into a single multimodal user turn
# (text nudge + audio at the same sequence), and the response is scored for compliance — the PyRIT
# analogue of Garak's non-refusal `mitigation.MitigationBypass` detector. A per-clip objective is
# derived from the clip's harm category.
#
# **CLI example:**
#
# ```bash
# pyrit_scan garak.audio_achilles_heel --target realtime --max-dataset-size 2
# ```
#
# > **Note:** The objective target must accept `audio_path` input (i.e. be multimodal). The example
# > below uses `RealtimeTarget` (the OpenAI Realtime audio websocket); non-audio targets such as the
# > default `openai_chat` will error when the audio request is sent. The full dataset holds ~350
# > clips, so a default run samples a small subset to finish quickly — raise `--max-dataset-size`
# > for broader coverage.

# %%
audio_dataset_config = AudioAchillesHeelDatasetConfiguration(
    dataset_names=["garak_audio_achilles_heel"], max_dataset_size=1
)

audio_target = RealtimeTarget()

audio_scenario = AudioAchillesHeel()
audio_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": audio_target,
        "dataset_config": audio_dataset_config,
    }
)
await audio_scenario.initialize_async()  # type: ignore

print(f"Scenario: {audio_scenario.name}")
print(f"Atomic attacks: {audio_scenario.atomic_attack_count}")

audio_scenario_result = await audio_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(audio_scenario_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
