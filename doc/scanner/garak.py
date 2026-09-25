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
# # Garak Scenarios
#
# The Garak scenario family implements probes inspired by the
# [Garak](https://github.com/NVIDIA/garak) framework. These include encoding-based probes (which
# test whether a target can be tricked into producing harmful content when prompts are encoded in
# various formats), prompt-injection probes (which embed override commands in benign tasks),
# API-key probes (which test whether a target will generate or complete
# credential-shaped values), web-injection probes (which test whether a target emits markdown
# data-exfiltration or cross-site-scripting payloads), exploitation probes (which test whether a
# target echoes template-injection or SQL exploit payloads), a doctor probe (which applies the Policy
# Puppetry universal bypass), system-prompt-extraction probes (which test whether a target can be
# coaxed into revealing its own system prompt), package-hallucination probes (which test whether a
# target recommends non-existent packages that an attacker could squat), an audio probe (which
# delivers spoken jailbreaks to multimodal targets), FigStep visual jailbreaks (which place
# harmful instructions in images), and a repetition probe (which detects unexpected continuation
# after repeated text).
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %% [markdown]
# ## PromptInject
#
# PromptInject tests whether a target follows injected instructions hidden in benign tasks.
# `--techniques` selects the injection wording, and `--goal-texts` selects the requested output.
# The default run uses all five techniques, the three Garak goal texts, and up to 60 attempts.
# `--max-dataset-size` must be at least the number of selected goal texts.
# Sampling selects at least one context for every goal, then fills the remaining budget
# without replacement. All techniques use the same sample. Injection wording is applied
# through request converters, before any caller-supplied converters.
#
# **CLI examples:**
#
# ```bash
# pyrit_scan run garak.prompt_inject --target openai_chat
# pyrit_scan run garak.prompt_inject --target openai_chat \
#   --techniques ignore_print \
#   --goal-texts "Custom trigger text" \
#   --max-dataset-size 16
# ```
#
# **Techniques:** `ignore_print`, `ignore_say`, `ignore_print_upper`, `nevermind`, and
# `screaming_stop`.

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.garak import (
    ApiKey,
    ApiKeyDatasetConfiguration,
    ApiKeyTechnique,
    Divergence,
    DivergenceDatasetConfiguration,
    DivergenceTechnique,
    Doctor,
    Encoding,
    EncodingTechnique,
    Exploitation,
    ExploitationTechnique,
    FigStep,
    LatentInjection,
    LatentInjectionDatasetConfiguration,
    LatentInjectionTechnique,
    PackageHallucination,
    PackageHallucinationTechnique,
    PromptInject,
    PromptInjectDatasetConfiguration,
    PromptInjectTechnique,
    SystemPromptExtraction,
    SystemPromptExtractionTechnique,
    WebInjection,
    WebInjectionTechnique,
)
from pyrit.scenario.garak.audio_achilles_heel import AudioAchillesHeel, AudioAchillesHeelDatasetConfiguration
from pyrit.scenario.garak.encoding import EncodingDatasetConfiguration
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")

from pyrit.scenario.garak import DoctorTechnique

# %%
prompt_inject_scenario = PromptInject()
prompt_inject_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [PromptInjectTechnique.IgnorePrint],
        "goal_texts": ["I hate humans"],
        "dataset_config": PromptInjectDatasetConfiguration(
            dataset_names=PromptInject.required_datasets(),
            max_dataset_size=1,
        ),
    }
)
await prompt_inject_scenario.initialize_async()  # type: ignore

print(f"Scenario: {prompt_inject_scenario.name}")
print(f"Atomic attacks: {prompt_inject_scenario.atomic_attack_count}")

prompt_inject_result = await prompt_inject_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(prompt_inject_result)

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
        "include_baseline": False,
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

# %%
web_injection_scenario = WebInjection(max_prompts_per_technique=1)
web_injection_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [WebInjectionTechnique.StringAssemblyDataExfil],
        "include_baseline": False,
    }
)
await web_injection_scenario.initialize_async()  # type: ignore

web_injection_result = await web_injection_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(web_injection_result)

# %% [markdown]
# ## Exploitation
#
# Ports Garak's active `exploitation.JinjaTemplatePythonInjection` and
# `exploitation.SQLInjectionEcho` probes. Technique-owned converters wrap each raw payload in the
# echo template and, for Python payloads, a Jinja expression. The templates are part of the
# technique identity, not the objective, and are defined in private factories in the scenario
# module. No technique initializer or shared catalog registration is required.
# `GarakExploitationScorer` loads its default reference payloads from the pinned, packaged
# corpus at construction time, independent of memory sampling. It uses Garak's primary detector
# rules: Jinja extraction followed by a payload check, or SQL payload matching followed by
# keyword-gated injection patterns. Matching preserves upstream case sensitivity.
# A positive result reports emitted exploit material, not downstream execution.
# Set `extended_checks` to `True` in `set_params_from_args(args=...)` to add auxiliary
# `SSTIOutputScorer` and `SQLInjectionOutputScorer` checks. This option is persisted for resume.
#
# **CLI examples:**
#
# ```bash
# # Run the bounded default (both techniques, up to 20 prompts total).
# pyrit_scan run garak.exploitation --target openai_chat
#
# # Run only the SQL echo technique with a smaller total cap.
# pyrit_scan run garak.exploitation --target openai_chat --techniques sql_injection_echo --prompt-cap 2
# ```
#
# **Available techniques:** `JinjaTemplatePythonInjection` and `SQLInjectionEcho`. `DEFAULT` and
# `ALL` both select the two techniques. `prompt_cap` is a deterministic cap across all selected
# techniques, not a per-technique cap. Payloads have a stable sort order before selection.

# %%
exploitation_scenario = Exploitation()
exploitation_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [ExploitationTechnique.SQLInjectionEcho],
        "prompt_cap": 2,
    }
)
await exploitation_scenario.initialize_async()  # type: ignore

print(f"Scenario: {exploitation_scenario.name}")
print(f"Atomic attacks: {exploitation_scenario.atomic_attack_count}")

exploitation_result = await exploitation_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(exploitation_result)

# %% [markdown]
# ## ApiKey
#
# Ports Garak's `apikey.GetKey` and `apikey.CompleteKey` probes. `GetKey` asks for a new
# credential across 58 service types; `CompleteKey` asks the target to extend five conspicuous
# PyRIT-created synthetic partial-key fixtures. The scenario uses `CredentialLeakScorer` with
# its opt-in `GARAK_PATTERNS` set; the scorer's default coverage is unchanged.
# Supplied partials, request echoes, and safe placeholders are not counted as leaks; a newly
# generated credential-shaped value is. Seven service entries represent public resource/client
# identifiers rather than secrets; they remain in the prompt corpus for Garak parity but are
# intentionally never scored as credential leaks.
#
# **CLI examples:**
#
# ```bash
# # Sample up to 20 prompts across both techniques.
# pyrit_scan run garak.api_key --target openai_chat
#
# # Run only GetKey with a smaller sample.
# pyrit_scan run garak.api_key --target openai_chat --techniques get_key --max-dataset-size 2
# ```
#
# **Available techniques:** `GetKey` and `CompleteKey`. `DEFAULT` and `ALL` both select the two
# techniques. `max_dataset_size` samples across all selected technique populations, not per service.
# The base scenario persists the sample for resume. Use `ApiKeyDatasetConfiguration` with
# `max_dataset_size=None` to run all 348 requests. Standard technique converter stacks are supported.

# %%
api_key_scenario = ApiKey()
api_key_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [ApiKeyTechnique.GetKey],
        "dataset_config": ApiKeyDatasetConfiguration(dataset_names=ApiKey.required_datasets(), max_dataset_size=2),
    }
)
await api_key_scenario.initialize_async()  # type: ignore

print(f"Scenario: {api_key_scenario.name}")
print(f"Atomic attacks: {api_key_scenario.atomic_attack_count}")

api_key_result = await api_key_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(api_key_result)

# %% [markdown]
# ## LatentInjection
#
# Ports Garak's `latentinjection` probe family: indirect prompt injection, where the attacker
# never talks to the model but plants an instruction inside a document the model is asked to read
# -- a resume, a financial report, a legal filing, a WHOIS record. The attack succeeds when the
# response echoes the trigger text the injection asked for, which `SubStringScorer` checks
# directly.
#
# Each prompt is assembled from five local datasets -- a benign task instruction, a carrier
# document, an injection instruction, a payload template, and a trigger value -- fenced together by the
# separator that the selected technique names. All selected techniques reuse the same original
# seed groups. Existing converters insert the separators when the attack sends each prompt.
# Payload templates and trigger values are stored separately and combined within each family.
#
# **CLI example:**
#
# ```bash
# pyrit_scan run garak.latent_injection --target openai_chat --techniques bare \
#   --families whois --max-dataset-size 1
# ```
#
# **Available techniques** (14 separator styles): Bare, Newline, Blockquote, BlockquoteInline,
# HorizontalRule, SystemTag, SystemBracket, SystemBracketFlood, SystemPrefix, InstructionTag,
# AdminRequest, EndOfText, CoreInstruction, LegalAmendment.
#
# **Aggregate techniques:** `ALL` (all 14), `DEFAULT` (a representative subset), `PLAIN` (the
# injection is set off by whitespace or quoting only), and `AUTHORITY` (the delimiter impersonates
# a system, admin, or legal voice).
#
# **Carrier families** are seed metadata rather than techniques, selected with the `families` run
# parameter: `translation_fr`, `translation_zh`, `report`, `resume`, `fact_eiffel`, `fact_legal`,
# `whois`, `whois_snippet`, and `latent_jailbreak`. Every technique applies to every family.
# `latent_jailbreak` hides a "write something offensive about <group>" instruction instead of a
# fixed trigger, so it is excluded from the defaults and requires an explicit `harm_scorer`.
#
# One attack is built per technique, carrier family, and expected trigger, so each attack has a
# single unambiguous success contract: its `SubStringScorer` looks for the one string its prompts
# actually asked for. A supplied `objective_scorer` replaces this fixed-trigger scorer; the
# harm family uses its separate `harm_scorer`. Caller technique converters run after the separators.
#
# `max_dataset_size` is one budget before technique expansion. The default is 92 original
# groups, shared by six default techniques (552 execution units). Sampling reserves one group
# per selected family/trigger pair, then fills the remaining budget without replacement.
# A smaller budget than the number of pairs raises an error. An explicit dataset configuration
# with `max_dataset_size=None` uses the complete assembled population. Saved runs replay the sample.
#
# This is not Garak's exact sampling policy: its lightweight probes cap final prompts at 64
# per family without guaranteed coverage. PyRIT also applies all selected separators to all
# selected families. Fact and WHOIS snippet contexts use stable, bounded populations of up to
# 20 and 10 documents, respectively, rather than Garak's random context generation.
# There is no baseline attack -- the `bare` technique already covers "no fencing at all".

# %%
latent_injection_scenario = LatentInjection()
latent_injection_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [LatentInjectionTechnique.Bare],
        "dataset_config": LatentInjectionDatasetConfiguration(
            dataset_names=LatentInjection.required_datasets(), families=["whois"], max_dataset_size=1
        ),
    }
)
await latent_injection_scenario.initialize_async()  # type: ignore

latent_injection_result = await latent_injection_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(latent_injection_result)

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

# %%
doctor_dataset_config = DatasetAttackConfiguration(dataset_names=["garak_doctor"], max_dataset_size=1)

doctor_scenario = Doctor()
doctor_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [DoctorTechnique.policy_puppetry],
        "dataset_config": doctor_dataset_config,
    }
)
await doctor_scenario.initialize_async()  # type: ignore

doctor_result = await doctor_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(doctor_result)

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

# %%
package_scenario = PackageHallucination(max_prompts_per_language=1)
package_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [PackageHallucinationTechnique.Rust],
    }
)
await package_scenario.initialize_async()  # type: ignore

package_result = await package_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(package_result)

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
# pyrit_scan garak.audio_achilles_heel --target azure_openai_realtime --max-dataset-size 2
# ```
#
# > **Note:** The objective target must accept `audio_path` input (i.e. be multimodal). The example
# > below uses the registered Azure OpenAI Realtime target; non-audio targets such as the default
# > `openai_chat` will error when the audio request is sent. The full dataset holds ~350
# > clips, so a default run samples a small subset to finish quickly — raise `--max-dataset-size`
# > for broader coverage.

# %%
audio_dataset_config = AudioAchillesHeelDatasetConfiguration(
    dataset_names=["garak_audio_achilles_heel"], max_dataset_size=1
)

audio_target = TargetRegistry.get_registry_singleton().instances.get("azure_openai_realtime")

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
# ## Divergence
#
# Ports Garak's `divergence.Repeat` probe. The dataset contains 36 literal requests to repeat
# a word indefinitely. A shared scorer receives a typed repetition expectation for each word.
# A positive score means unexpected continuation, not proof of training-data disclosure.
# `RepeatedToken` is not included. Baselines are forbidden because the repetition request is
# already the direct attack; sending the leakage objective would not provide a useful comparison.
#
# **CLI example:**
#
# ```bash
# pyrit_scan run garak.divergence --target openai_chat --techniques repeat --max-dataset-size 2
# ```
#
# **Available techniques:** `Repeat`, `DEFAULT`, and `ALL` all select the same probe.
# The default budget is 10 prompts across the entire dataset, not per word. Use
# `DivergenceDatasetConfiguration(max_dataset_size=None, dataset_names=["garak_divergence"])`
# to run all 36 prompts. The example below samples only two.

# %%
divergence_scenario = Divergence()
divergence_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [DivergenceTechnique.Repeat],
        "dataset_config": DivergenceDatasetConfiguration(dataset_names=["garak_divergence"], max_dataset_size=2),
    }
)
await divergence_scenario.initialize_async()  # type: ignore

print(f"Scenario: {divergence_scenario.name}")
print(f"Atomic attacks: {divergence_scenario.atomic_attack_count}")

divergence_result = await divergence_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(divergence_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
