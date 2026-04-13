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
# The Garak scenario family implements encoding-based probes inspired by the
# [Garak](https://github.com/NVIDIA/garak) framework. These test whether a target model can be
# tricked into producing harmful content when prompts are encoded in various formats.
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.registry import TargetRegistry
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.garak import Encoding, EncodingStrategy
from pyrit.scenario.scenarios.garak.encoding import EncodingDatasetConfiguration
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().get_instance_by_name("openai_chat")
printer = ConsoleScenarioResultPrinter()

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
await printer.print_summary_async(scenario_result)  # type: ignore

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
