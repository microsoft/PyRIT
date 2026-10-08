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
from pyrit.scenario.adaptive import ImageTechniqueAdaptive, TextAdaptive
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
# ## ImageTechniqueAdaptive
#
# `ImageTechniqueAdaptive` is the image sibling of `TextAdaptive`. It renders each text objective
# into an image (blank canvas, QR code, grid composite, comic panel, ...) and sends it to a
# vision-capable target, then scores the text response. The objective target must accept **both text
# and image input** (so the direct-text baseline stays a valid comparison) and return text — use a
# multimodal target such as `openai_chat` (e.g. gpt-4o). See the
# [Adaptive Scenarios programming guide](../code/scenarios/3_adaptive_scenarios.ipynb) for a full
# walkthrough.
#
# ```bash
# pyrit_scan run adaptive.image_technique_adaptive \
#   --initializers target \
#   --target openai_chat
# ```

# %%
image_dataset_config = DatasetAttackConfiguration(dataset_names=["airt_hate"], max_dataset_size=1)

image_scenario = ImageTechniqueAdaptive()
image_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "dataset_config": image_dataset_config,
        "max_attempts_per_objective": 1,
    }
)
await image_scenario.initialize_async()  # type: ignore

image_scenario_result = await image_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(image_scenario_result)

# %% [markdown]
# For more details, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb) and
# [Configuration](../getting_started/configuration.md).
