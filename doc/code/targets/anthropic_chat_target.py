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
# # Anthropic Claude Chat Target
#
# This notebook shows how to use Anthropic Claude models with PyRIT via the `AnthropicChatTarget`.
#
# ## Prerequisites
#
# 1. **Obtain an Anthropic API Key:**
#    - Sign up at https://console.anthropic.com
#    - Generate an API key from the console
#
# 2. **Set Environment Variables:**
#    - `ANTHROPIC_API_KEY`: Your Anthropic API key (required)
#    - `ANTHROPIC_CHAT_MODEL`: The model to use (optional, defaults to `claude-sonnet-4-6`)
#
# ## Basic Usage
#
# After setting your API key, send prompts to Claude using the `AnthropicChatTarget` class.
# %%
from pyrit.executor.attack import PromptSendingAttack
from pyrit.output import output_attack_async
from pyrit.prompt_target import AnthropicChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Defaults to api_key from ANTHROPIC_API_KEY and model from ANTHROPIC_CHAT_MODEL env vars
target = AnthropicChatTarget()

attack = PromptSendingAttack(objective_target=target)
result = await attack.execute_async(objective="Hello! Describe yourself and the company who developed you.")  # type: ignore
await output_attack_async(result)
# %% [markdown]
# ## Custom Parameters
#
# You can configure the target with specific model parameters:
# %%
target_custom = AnthropicChatTarget(
    model_name="claude-sonnet-4-6",
    max_tokens=512,
    temperature=0.7,
    top_p=0.9,
    top_k=50,
)

attack = PromptSendingAttack(objective_target=target_custom)
result = await attack.execute_async(objective="Write a haiku about cybersecurity.")  # type: ignore
await output_attack_async(result)
# %% [markdown]
# ## Using as a Red Team Target
#
# `AnthropicChatTarget` works anywhere a `PromptTarget` is accepted in PyRIT,
# including multi-turn red teaming attacks.
# For example, see the [Red Teaming Attack](../executor/2_multi_turn.ipynb) documentation
# for how to use this target with `RedTeamingAttack`.
