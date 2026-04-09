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
