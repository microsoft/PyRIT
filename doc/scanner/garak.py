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
# data-exfiltration or cross-site-scripting payloads), and an audio probe (which delivers spoken
# jailbreaks to multimodal targets).
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.prompt_target import RealtimeTarget
from pyrit.registry import TargetRegistry
from pyrit.scenario.garak import Encoding, EncodingStrategy
from pyrit.scenario.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelDatasetConfiguration
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
await audio_scenario.initialize_async(  # type: ignore
    objective_target=audio_target,
    dataset_config=audio_dataset_config,
)

print(f"Scenario: {audio_scenario.name}")
print(f"Atomic attacks: {audio_scenario.atomic_attack_count}")

audio_scenario_result = await audio_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(audio_scenario_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
