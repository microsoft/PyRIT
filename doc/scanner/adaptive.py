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
# # Adaptive Scenarios
#
# Adaptive scenarios select attack techniques for each objective from prior success data. They can
# spend more attempts on techniques that have worked well while retaining some exploration.

# %% [markdown]
# ## TextAdaptive
#
# `TextAdaptive` uses an epsilon-greedy selector over text-compatible attack techniques. Each
# objective stops after the first successful technique or after `max_attempts_per_objective`.
# The direct prompt is available as a baseline comparison.
#
# ```bash
# pyrit_scan run adaptive.text_adaptive \
#   --initializers target \
#   --target openai_chat \
#   --dataset-names airt_hate \
#   --max-dataset-size 2 \
#   --max-attempts-per-objective 2
# ```

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.adaptive import TextAdaptive
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")

dataset_config = DatasetAttackConfiguration(dataset_names=["airt_hate"], max_dataset_size=2)

scenario = TextAdaptive()
scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "dataset_config": dataset_config,
        "max_attempts_per_objective": 2,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
