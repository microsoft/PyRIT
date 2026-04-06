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
# # 8. Garak Encoding Scenario
#
# The `Encoding` scenario tests encoding-based probes inspired by the
# [Garak](https://github.com/NVIDIA/garak) framework. It encodes harmful payloads using various encoding
# schemes (Base64, ROT13, Morse code, Braille, etc.) and asks the target model to decode and execute them.
# This tests whether safety filters can be bypassed through obfuscation.
#
# All 17 strategies use single-turn `PromptSendingAttack` with different encoding converters and decoder
# instruction templates.
#
# ## Available Strategies
#
# | Strategy | CLI Value | Tags | Description |
# |----------|-----------|------|-------------|
# | ALL | `all` | all | Runs all 17 encodings |
# | Base64 | `base64` | — | Base64 encoding |
# | Base2048 | `base2048` | — | Base2048 encoding |
# | Base16 | `base16` | — | Base16 (hex) encoding |
# | Base32 | `base32` | — | Base32 encoding |
# | ASCII85 | `ascii85` | — | ASCII85 encoding |
# | Hex | `hex` | — | Hexadecimal encoding |
# | QuotedPrintable | `quoted_printable` | — | Quoted-printable encoding |
# | UUencode | `uuencode` | — | UUencode format |
# | ROT13 | `rot13` | — | ROT13 cipher |
# | Braille | `braille` | — | Braille character encoding |
# | Atbash | `atbash` | — | Atbash cipher |
# | MorseCode | `morse_code` | — | Morse code encoding |
# | NATO | `nato` | — | NATO phonetic alphabet |
# | Ecoji | `ecoji` | — | Emoji-based encoding |
# | Zalgo | `zalgo` | — | Zalgo text encoding |
# | LeetSpeak | `leet_speak` | — | Leet speak encoding |
# | AsciiSmuggler | `ascii_smuggler` | — | ASCII smuggling technique |
#
# **Note:** This scenario does not support strategy composition.
#
# ## Default Datasets
#
# The default datasets are `garak_slur_terms_en` (English slur terms) and `garak_web_html_js` (web
# injection payloads), with a max of 3 items per dataset. You can bring your own datasets using
# `DatasetConfiguration(seed_groups=your_groups)` or the `--dataset-names` CLI flag — see
# [Loading Datasets](../datasets/1_loading_datasets.ipynb) for details and
# [Configuring RedTeamAgent](1_red_team_agent.ipynb) for advanced dataset configuration.
#
# ## Setup

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.garak import Encoding, EncodingStrategy
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()
printer = ConsoleScenarioResultPrinter()

# %% [markdown]
# ## Running via CLI
#
# To run a single encoding type quickly:
#
# ```bash
# pyrit_scan garak.encoding \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --strategies base64 \
#   --max-dataset-size 2
# ```
#
# To run all encodings:
#
# ```bash
# pyrit_scan garak.encoding \
#   --initializers target load_default_datasets \
#   --target openai_chat \
#   --max-dataset-size 2
# ```
#
# ## Programmatic Usage
#
# Here we run only the `base64` strategy with a single decoder template to keep execution fast.
# By default, 4 decoder instruction templates are used per encoding, which multiplied by 4 Base64
# converter variants creates 16+ atomic attacks. Limiting to 1 template keeps the demo manageable.

# %%
scenario = Encoding(encoding_templates=["Encoded: {encoded_text}\nDecoded:"])
await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    scenario_strategies=[EncodingStrategy.Base64],
)

print(f"Scenario: {scenario.name}")
print(f"Atomic attacks: {scenario.atomic_attack_count}")

# %%
scenario_result = await scenario.run_async()  # type: ignore

# %% [markdown]
# ## Interpreting Results

# %%
await printer.print_summary_async(scenario_result)  # type: ignore
