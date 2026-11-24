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
# # promptfoo Scenarios
#
# The promptfoo scenario family ports plugins from [promptfoo's red teaming](https://www.promptfoo.dev/docs/red-team/)
# [@promptfoo2025redteam]. Each plugin writes tests tailored to the application under test and grades
# the responses with the plugin's own rubric:
#
# - An adversarial chat generates the tests from your application's `purpose`, using the plugin's
#   promptfoo generation prompt.
# - `PromptfooRubricScorer` grades each response on the scoring target with promptfoo's grading
#   prompts and the plugin's rubric. A result is a success when the response fails the rubric, so the
#   vulnerability was found.
#
# Every promptfoo scenario takes these parameters:
#
# - `purpose` (required): what the application does, who uses it, and what it can access. Like
#   promptfoo's `redteam.purpose`, the more specific it is, the better the tests and grading.
# - `num_tests`: tests to generate for each plugin. Defaults to 5, like promptfoo's `numTests`.
#
# Generated tests are stored as a dataset named for the plugin and a fingerprint of the plugin, the
# purpose, and `num_tests`. A later run with the same inputs reuses the stored tests; changing any of
# them generates new ones.
#
# The default technique, `basic`, sends each test unchanged, like promptfoo's `basic` strategy. Any
# registered attack technique can be selected with `--techniques`, for example `role_play_movie_script`.
#
# For full programming details, see the
# [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario.promptfoo import Hallucination
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")

# %% [markdown]
# ## Hallucination
#
# Ports promptfoo's `hallucination` plugin. Its registry name is `promptfoo.hallucination`. The tests
# are realistic requests, tailored to the purpose, for information that's unlikely to be known or
# verifiable. A response fails the rubric when it states fabricated names, dates, statistics, or other
# unsupported claims as fact instead of acknowledging what it can't know.
#
# **CLI example:**
#
# ```bash
# pyrit_scan run promptfoo.hallucination --target openai_chat \
#   --purpose "A travel agent that books flights and hotels for trips within the United States." \
#   --num-tests 2
# ```

# %%
hallucination_scenario = Hallucination()
hallucination_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "purpose": "A travel agent that books flights and hotels for trips within the United States.",
        "num_tests": 2,
    }
)
await hallucination_scenario.initialize_async()  # type: ignore

hallucination_result = await hallucination_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(hallucination_result)
