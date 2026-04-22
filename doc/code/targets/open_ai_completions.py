# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.0
# ---

# %% [markdown]
# # OpenAI Completions - optional
#
#
# Once you are configured, then you will be able to get completions for your text.

# %%
from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.prompt_target import OpenAICompletionTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Note that max_tokens will default to 16 for completions, so you may want to set the upper limit of allowed tokens for a longer response.
target = OpenAICompletionTarget(max_tokens=2048)

attack = PromptSendingAttack(objective_target=target)
result = await attack.execute_async(objective="Hello! Who are you?")  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore
