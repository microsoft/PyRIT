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
# # Foundry Scenarios
#
# The Foundry scenario family provides the `RedTeamAgent` — a comprehensive red teaming scenario
# that combines converter-based attacks (encoding/obfuscation), multi-turn attacks (Crescendo,
# RedTeaming), and technique composition. It's organized into difficulty levels: EASY, MODERATE,
# and DIFFICULT.
#
# For full programming details, see
# [Common Scenario Parameters](../code/scenarios/1_common_scenario_parameters.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.foundry import FoundryTechnique, RedTeamAgent
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
# %% [markdown]
# ## RedTeamAgent
#
# Tests a target using a wide range of attack techniques — from simple encoding converters to
# complex multi-turn conversations. The default dataset is HarmBench.
#
# **CLI example:**
#
# ```bash
# pyrit_scan foundry.red_team_agent --target openai_chat --techniques base64 --max-dataset-size 1
# ```
#
# **Available techniques by difficulty:**
#
# | Difficulty | Techniques |
# |---|---|
# | **EASY** | AnsiAttack, AsciiArt, AsciiSmuggler, Atbash, Base64, Binary, Caesar, CharacterSpace, CharSwap, Diacritic, Flip, Jailbreak, Leetspeak, Morse, ROT13, StringJoin, SuffixAppend, UnicodeConfusable, UnicodeSubstitution, Url |
# | **MODERATE** | Tense |
# | **DIFFICULT** | Crescendo, MultiTurn, Pair, Tap |
# | **Aggregates** | ALL, EASY, MODERATE, DIFFICULT |

# %%
dataset_config = DatasetAttackConfiguration(dataset_names=["harmbench"], max_dataset_size=1)

scenario = RedTeamAgent()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [FoundryTechnique.Base64],
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
# ## Technique Composition
#
# You can pair a multi-turn attack with one or more converter techniques using `FoundryComposite`.
# Each converter in the composite is applied in sequence before the attack runs.
#
# ```python
# from pyrit.scenario.foundry import FoundryComposite
#
# composed = FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Caesar, FoundryTechnique.CharSwap])
# ```

# %%
# from pyrit.scenario.foundry import FoundryComposite
# composed = FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Caesar, FoundryTechnique.CharSwap])
# scenario_techniques = [FoundryTechnique.Base64, composed]

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
