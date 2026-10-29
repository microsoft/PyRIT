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
# The GUI requires a technique selection. SDK calls default to `red_teaming`, or the
# first registered technique in name order when `red_teaming` is not registered.
# To send text that differs from the scoring objective, set `prompt`. Converters rewrite
# that message; the objective remains the scoring goal. For tool role play, enable the
# `tools` initializer and select `role_play_tool_calls`.
# The Objective scorer picker uses the scenario default unless you select a registered
# scorer. It is available for scenarios that accept a custom objective scorer.
#
# The catalog declares `max_concrete_techniques=1`. CoPyRIT uses a single-selection
# technique picker. The backend enforces this limit after aggregate expansion and
# deduplication. Baseline comparison is separate and does not count toward the limit.
#
# Dataset inputs are not supported. Do not set dataset names or size limits. CoPyRIT
# hides unsupported inputs. More than one concrete technique causes an error.
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
# ## Local example
#
# This example executes the real scenario against a local echo target. The target returns
# the request unchanged, and a substring scorer checks the response. This demonstrates
# configuration, execution, and result output without credentials or model calls; it does
# not measure model safety. For a configured model, use the SDK example below.

# %%
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import Message
from pyrit.output import output_scenario_async
from pyrit.prompt_target import PromptTarget
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario import AttackTechniqueFactory
from pyrit.score import SubStringScorer


class EchoTarget(PromptTarget):
    """Return the current request unchanged for this local demonstration."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [Message.from_prompt(prompt=normalized_conversation[-1].get_value(), role="assistant")]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        if not normalized_conversation:
            raise ValueError("The local echo target requires a request.")


AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
    [AttackTechniqueFactory(name="prompt_sending_demo", attack_class=PromptSendingAttack)]
)

from pyrit.scenario.technique import ExecuteTechnique

demo = Execute(objective_scorer=SubStringScorer(substring="Python process"))
demo.set_params_from_args(
    args={
        "objective_target": EchoTarget(),
        "scenario_techniques": [ExecuteTechnique.prompt_sending_demo],
        "objective": "Describe how to stop a Python process.",
        "harm_categories": ["process_management"],
        "retries_on_objective_failure": 2,
    }
)
await demo.initialize_async()  # type: ignore
demo_result = await demo.run_async()  # type: ignore

# %%
await output_scenario_async(demo_result)  # type: ignore

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
# The selected technique must accept the target and source history. An objective-only
# technique rejects history unless it declares `try_adapt=True`. With that permission,
# each attempt uses an objective-only copy, then adds the technique's own seeds.
# The original history is not changed. Without permission, `raise` reports the reason
# directly; `skip` logs the reason. `Execute` still raises an error if its one selected
# technique cannot run, even when a baseline is enabled.
#
# When history is accepted, the scenario copies it. It keeps roles, message order, data types,
# converted values, and message metadata. Each attempt gets a new conversation ID.
# The source conversation is not changed. Prepended assistant and tool messages use
# simulated roles, so they are not treated as new scoring evidence. Prior request trace
# metadata is removed from copied seeds.
#
# `apply_converters_to_prepended_history` defaults to `False`. Copied history keeps
# the values the target already received, without applying baked-in or additional
# request converters again. Set it to `True` to opt into the technique's configured
# history-conversion role scope. New outgoing turns still use the selected converters.
#
# If the source ends with a user message, that message becomes the next message to send.
# It is resent and converted as a new request, regardless of the history-conversion flag.
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
# A finished child does not complete the compound unit if a later child raises an error.
# Error retries and saved-run resume can run that incomplete unit again.
#
# With objective retries, one compound unit holds the child attempt results. The run preview
# counts one unit, not each child or turn. The baseline is off by default. Set
# `include_baseline=True` to add one direct prompt-sending unit; it is not repeated by
# `retries_on_objective_failure`.
# Statistics count the target-facing attempts, not the orchestration envelope as another
# attempt. Three objective attempts produce two retries, while the planned unit count stays one.
#
# To resume, pass the saved scenario result ID to `Execute(scenario_result_id=...)` and
# supply the same inputs. A changed objective, source history, or technique configuration
# prevents resume.
# The source-history fingerprint is stored in run metadata, not in launch parameters.
#
# For more details, see [Common Scenario Parameters](../code/scenarios/1_common_scenario_parameters.ipynb).
