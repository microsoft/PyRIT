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
# # Execute a Technique
#
# `technique.execute` runs one catalog technique against one free-text objective.
# Use it to start an automated test or continue from a stored manual conversation.
# It does not read datasets.
#
# ## CoPyRIT
#
# In the scenario form, select `technique.execute`, the objective target, and exactly one
# concrete technique. Enter the objective. You can also enter harm categories,
# `prepended_conversation_id`, and `retries_on_objective_failure` in the parameter form.
# Use the existing adversarial target picker to select the attacker model.
#
# The default technique is `red_teaming`. If it is not registered, the scenario uses the
# first registered technique in name order. Plain `crescendo` is available when the
# opt-in `extra` catalog is registered. The `crescendo_*` core techniques use simulated
# conversations; they are not the plain multi-turn Crescendo attack.
#
# Dataset inputs are not supported. Do not set dataset names or size limits. The generic
# GUI can still show these controls. More than one concrete technique causes an error.
# This scenario does not add a "continue from conversation" button.
#
# ## Parameters
#
# The catalog cells below run locally without model calls or credentials.

# %%
from pyrit.scenario.technique import Execute
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import TechniqueInitializer

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[TechniqueInitializer()],
    env_files=[],
    silent=True,
)

[(parameter.name, parameter.type_name) for parameter in Execute.additional_parameters()]

# %%
from pyrit.scenario.technique import ExecuteTechnique

[technique.value for technique in ExecuteTechnique.expand({ExecuteTechnique.default()})]

# %% [markdown]
# ## SDK
#
# Run this example after your configuration registers `openai_chat`, `adversarial_chat`,
# and the objective scorer target. Use your normal persistent memory configuration when
# you supply a source conversation ID. See [Configuration](../getting_started/configuration.md).
#
# ```python
# from pyrit.output import output_scenario_async
# from pyrit.registry import TargetRegistry
# from pyrit.scenario.core.scenario_target_defaults import override_default_adversarial_target
# from pyrit.scenario.technique import Execute, ExecuteTechnique
#
# targets = TargetRegistry.get_registry_singleton()
# with override_default_adversarial_target(targets.instances["adversarial_chat"]):
#     scenario = Execute()
#     scenario.set_params_from_args(
#         args={
#             "objective_target": targets.instances["openai_chat"],
#             "scenario_techniques": [ExecuteTechnique.red_teaming],
#             "objective": "Describe how to stop a Python process.",
#             "harm_categories": ["process_management"],
#             "retries_on_objective_failure": 2,
#             "max_retries": 1,
#             # Add a stored manual conversation to continue from it:
#             # "prepended_conversation_id": "<conversation UUID>",
#         }
#     )
#     await scenario.initialize_async()
#     result = await scenario.run_async()
# await output_scenario_async(result)
# ```
#
# The scenario copies the source history. It keeps roles, message order, data types,
# converted values, and message metadata. Each attempt gets a new conversation ID.
# The source conversation is not changed. Prepended assistant and tool messages use
# simulated roles, so they are not treated as new scoring evidence. Prior request trace
# metadata is removed from copied seeds.
#
# If the source ends with a user message, that message becomes the next message to send.
# Otherwise, the technique creates the next turn. Blocked or error pieces in the source
# cause an error; the scenario does not silently remove them.
#
# For an attack with `max_turns`, the scenario adds the number of prior assistant turns
# to the configured budget. Thus, a long manual conversation does not consume the new
# automated-turn budget. An attack without `max_turns` is not changed.
#
# ## Retries and baseline
#
# `max_retries` handles execution errors. `retries_on_objective_failure` defaults to zero.
# A value of 2 permits up to three fresh technique attempts, stopping at the first success.
# The technique is configured once. Each attempt gets a fresh execution context and conversation.
# Failure, undetermined, and completed error outcomes can start the next attempt.
# Exceptions return to the scenario's error retry path.
#
# With objective retries, one compound unit holds the child attempt results. The run preview
# counts one unit, not each child or turn. The baseline is off by default. Set
# `include_baseline=True` to add one direct prompt-sending unit; it is not repeated by
# `retries_on_objective_failure`.
#
# To resume, pass the saved scenario result ID to `Execute(scenario_result_id=...)` and
# supply the same inputs. A changed objective, source history, or technique configuration
# prevents resume.
# The source-history fingerprint is stored in run metadata, not in launch parameters.
#
# For more details, see [Common Scenario Parameters](../code/scenarios/1_common_scenario_parameters.ipynb).
