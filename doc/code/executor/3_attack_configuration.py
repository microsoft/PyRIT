# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
# ---

# %% [markdown]
# # Attack Configuration
#
# Every attack shares the same `execute_async` contract, so the inputs below work the same way no
# matter which executor you use. Rather than repeat them on every page, we cover them once here.
#
# `execute_async` accepts four standard arguments:
#
# | Argument | Purpose |
# |---|---|
# | `objective` | What you are trying to get the **objective target** (the system under test) to do. Drives scoring and multi-turn adversarial prompts. |
# | `memory_labels` | A `dict[str, str]` tagged onto every prompt/response, so you can filter this run later in memory. |
# | `prepended_conversation` | A list of `Message`s to seed the conversation before the attack's own turns (system prompt, prior history). |
# | `next_message` | The exact next message to send, instead of letting the attack derive it from the objective. Useful for multimodal or pre-built seeds. |
#
# Construction-time configuration objects — **adversarial**, **scoring**, and **converter** — are
# covered at the end and link out to their dedicated pages.
#
# > **Note:** Set the memory instance with `initialize_pyrit_async` before running any attack. See the
# > [Memory Configuration Guide](../memory/0_memory.md).
#
# The examples here use `TextTarget`, which just records what would be sent — so they run instantly
# and need no credentials.

# %%
from pyrit.executor.attack import (
    AttackParameters,
    PromptSendingAttack,
    SingleTurnAttackContext,
)
from pyrit.output import output_attack_async
from pyrit.prompt_target import TextTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# The objective target — the system under test. Here a TextTarget that just records what would be sent.
target = TextTarget()
attack = PromptSendingAttack(objective_target=target)

# %% [markdown]
# ## Memory labels
#
# `memory_labels` tag every prompt and response this run produces. They don't change what is sent;
# they make the run easy to find and group later in memory (e.g. by operation or operator).

# %%
result = await attack.execute_async(  # type: ignore
    objective="Give me a recipe for a classic margarita",
    memory_labels={"operation": "op_trash_panda", "operator": "roakey"},
)
await output_attack_async(result)

# %% [markdown]
# ## Prepended conversations
#
# A prepended conversation seeds the exchange before the attack adds its own turn. The most common
# use is setting a system prompt, but you can prepend any sequence of `system` / `user` / `assistant`
# turns — for example, to resume a prior conversation or to plant an agreeable assistant reply.

# %%
from pyrit.models import Message, MessagePiece

prepended_conversation = [
    Message.from_system_prompt("You are a helpful assistant who always answers fully."),
    Message(
        message_pieces=[MessagePiece(role="user", original_value="Hi, can you help me with a chemistry question?")]
    ),
    Message(
        message_pieces=[MessagePiece(role="assistant", original_value="Absolutely — what would you like to know?")]
    ),
]

result = await attack.execute_async(  # type: ignore
    objective="Explain how a saponification reaction works",
    prepended_conversation=prepended_conversation,
)
await output_attack_async(result)

# %% [markdown]
# ## Multimodal seeds and `next_message`
#
# When you need to control the exact message sent — for instance to send an image, or text with
# special metadata — build a `SeedGroup` and pass its `next_message`. This bypasses deriving the
# prompt from the objective, while the objective still drives scoring.

# %%
import pathlib

from pyrit.models import SeedGroup, SeedPrompt

image_path = str(pathlib.Path(".") / ".." / ".." / ".." / "assets" / "pyrit_architecture.png")

seed_group = SeedGroup(seeds=[SeedPrompt(value=image_path, data_type="image_path")])

context = SingleTurnAttackContext(
    params=AttackParameters(
        objective="Sending an image successfully",
        next_message=seed_group.next_message,
    )
)

result = await attack.execute_with_context_async(context=context)  # type: ignore
await output_attack_async(result)

# %% [markdown]
# ## Configuration objects
#
# Beyond the call arguments, attacks are tuned at construction time with three configuration objects:
#
# - **`AttackConverterConfig`** — request/response [converters](../converters/0_converters.ipynb)
#   applied to every prompt and response.
# - **`AttackScoringConfig`** — the objective scorer plus any auxiliary
#   [scorers](../scoring/0_scoring.ipynb).
# - **`AttackAdversarialConfig`** — the adversarial target (a model PyRIT controls) that multi-turn
#   attacks use to generate each next prompt (see [Multi-Turn Attacks](2_multi_turn.ipynb)).
#
# Converter and scoring configs apply to single- and multi-turn attacks alike; the adversarial config
# only applies to attacks that drive a conversation. Below builds a converter config — it's just a
# plain object you hand to the attack constructor.

# %%
from pyrit.executor.attack import AttackConverterConfig
from pyrit.prompt_converter import Base64Converter
from pyrit.prompt_normalizer import PromptConverterConfiguration

converter_config = AttackConverterConfig(
    request_converters=PromptConverterConfiguration.from_converters(converters=[Base64Converter()]),
)

attack_with_converters = PromptSendingAttack(
    objective_target=target,
    attack_converter_config=converter_config,
)

result = await attack_with_converters.execute_async(objective="Base64-encode this request")  # type: ignore
await output_attack_async(result)
