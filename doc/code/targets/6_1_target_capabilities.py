# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.0
# ---

# %% [markdown]
# # 6.1 Target Capabilities
#
# Every `PromptTarget` carries a `TargetConfiguration` that declares what it natively supports, what to do
# when a capability is missing, and how to adapt the conversation when adaptation is permitted. This notebook
# walks through how to inspect, validate, and override capabilities on a real target — the same machinery
# attacks, scorers, and converters use under the hood.
#
# A `TargetConfiguration` composes three concerns:
#
# * **`TargetCapabilities`** — declarative, immutable description of what the target natively supports.
# * **`CapabilityHandlingPolicy`** — for each adaptable capability, whether to `ADAPT` (run a normalizer)
#   or `RAISE` (fail immediately) when the target lacks it.
# * **`ConversationNormalizationPipeline`** — the ordered set of normalizers derived from the gap between
#   the declared capabilities and the policy.
#
# See [Target Capabilities](./0_prompt_targets.md#target-capabilities) in the overview for the full list
# of capability flags.

# %% [markdown]
# ## 1. Inspect a real target's configuration
#
# We use `OpenAIChatTarget` throughout this notebook. Constructing the target does not make any network
# calls — we are only inspecting its declared configuration.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

target = OpenAIChatTarget(model_name="gpt-4o", endpoint="https://example.invalid/", api_key="sk-not-a-real-key")
caps = target.configuration.capabilities

print("supports_multi_turn:        ", caps.supports_multi_turn)
print("supports_editable_history:  ", caps.supports_editable_history)
print("supports_system_prompt:     ", caps.supports_system_prompt)
print("supports_json_output:       ", caps.supports_json_output)
print("supports_json_schema:       ", caps.supports_json_schema)
print("input_modalities:           ", sorted(sorted(m) for m in caps.input_modalities))
print("output_modalities:          ", sorted(sorted(m) for m in caps.output_modalities))

# %% [markdown]
# ## 2. Default configurations and known model profiles
#
# Each target class declares a `_DEFAULT_CONFIGURATION` class attribute. For well-known underlying models,
# `get_default_configuration(underlying_model=...)` returns a richer profile from
# `TargetCapabilities.get_known_capabilities` — for example, `gpt-5` gains `supports_json_schema=True`
# and other models pick up the right modality combinations automatically. Unknown models fall back to
# the class default.

# %%
class_default = OpenAIChatTarget._DEFAULT_CONFIGURATION.capabilities
gpt_4o = OpenAIChatTarget.get_default_configuration(underlying_model="gpt-4o").capabilities
gpt_5 = OpenAIChatTarget.get_default_configuration(underlying_model="gpt-5").capabilities
unknown = OpenAIChatTarget.get_default_configuration(underlying_model="not-a-real-model").capabilities

print(f"{'capability':<32}{'class default':<18}{'gpt-4o':<10}{'gpt-5':<10}{'unknown':<10}")
print("-" * 80)
for flag in (
    "supports_multi_turn",
    "supports_editable_history",
    "supports_system_prompt",
    "supports_json_output",
    "supports_json_schema",
):
    row = (
        f"{flag:<32}"
        f"{str(getattr(class_default, flag)):<18}"
        f"{str(getattr(gpt_4o, flag)):<10}"
        f"{str(getattr(gpt_5, flag)):<10}"
        f"{str(getattr(unknown, flag)):<10}"
    )
    print(row)

# %% [markdown]
# ## 3. Declare and validate consumer requirements
#
# Components that need particular capabilities declare them as a `TargetRequirements` and validate at
# construction time. PyRIT ships a `CHAT_TARGET_REQUIREMENTS` constant for the common case of needing
# multi-turn + editable history — the replacement for the deprecated `PromptChatTarget` type check.
#
# `TargetRequirements.validate` collects every missing capability and raises a single `ValueError` so
# callers see all violations at once.

# %%
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS

CHAT_TARGET_REQUIREMENTS.validate(target=target)
print("OpenAIChatTarget satisfies CHAT_TARGET_REQUIREMENTS")

# %% [markdown]
# To check a single capability, call `target.configuration.ensure_can_handle(capability=...)` directly.

# %%
from pyrit.prompt_target.common.target_capabilities import CapabilityName

target.configuration.ensure_can_handle(capability=CapabilityName.MULTI_TURN)
print("Multi-turn check passed")

# %% [markdown]
# ## 4. Override the configuration per instance
#
# For targets whose capabilities depend on deployment (HTTP endpoints, Playwright UIs, custom backends —
# or simply an OpenAI-compatible model whose actual capabilities differ from `gpt-4o`), pass a
# `TargetConfiguration` via `custom_configuration`. The instance uses your override instead of the class
# default.

# %%
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

restricted_config = TargetConfiguration(
    capabilities=TargetCapabilities(
        supports_multi_turn=False,
        supports_system_prompt=False,
        supports_multi_message_pieces=True,
    ),
)
restricted_target = OpenAIChatTarget(
    model_name="custom-model",
    endpoint="https://example.invalid/",
    api_key="sk-not-a-real-key",
    custom_configuration=restricted_config,
)

print("class default supports_multi_turn:    ", class_default.supports_multi_turn)
print("instance supports_multi_turn:         ", restricted_target.configuration.capabilities.supports_multi_turn)

try:
    CHAT_TARGET_REQUIREMENTS.validate(target=restricted_target)
except ValueError as exc:
    print("\nValidation failed as expected:")
    print(exc)

# %% [markdown]
# ## 5. ADAPT vs RAISE
#
# When a capability is missing, the `CapabilityHandlingPolicy` decides what happens. Only *adaptable*
# capabilities (currently `MULTI_TURN` and `SYSTEM_PROMPT`) can be papered over by PyRIT — for these,
# you can switch the behavior from `RAISE` (default) to `ADAPT`. With `ADAPT`, the conversation goes
# through a normalizer that flattens history or merges system prompts before reaching the target.
#
# Below we wrap a single-turn endpoint two ways and watch the pipeline change. Note that the `RAISE`
# pipeline is **empty**: when a missing capability is configured to raise, there is nothing to
# normalize. The error surfaces later, when a consumer calls `ensure_can_handle` or
# `TargetRequirements.validate`.

# %%
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    UnsupportedCapabilityBehavior,
)

single_turn_caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)

raise_config = TargetConfiguration(
    capabilities=single_turn_caps,
    policy=CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
        }
    ),
)
adapt_config = TargetConfiguration(
    capabilities=single_turn_caps,
    policy=CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
        }
    ),
)

raise_target = OpenAIChatTarget(
    model_name="custom-model",
    endpoint="https://example.invalid/",
    api_key="sk-not-a-real-key",
    custom_configuration=raise_config,
)
adapt_target = OpenAIChatTarget(
    model_name="custom-model",
    endpoint="https://example.invalid/",
    api_key="sk-not-a-real-key",
    custom_configuration=adapt_config,
)

print("RAISE pipeline normalizers: ", [type(n).__name__ for n in raise_target.configuration.pipeline._normalizers])
print("ADAPT pipeline normalizers: ", [type(n).__name__ for n in adapt_target.configuration.pipeline._normalizers])

# %% [markdown]
# With `ADAPT`, running a multi-turn conversation through `normalize_async` collapses it into a single
# user message — the exact payload the target will see when a prompt is sent.

# %%
from pyrit.models import Message

conversation = [
    Message.from_prompt(prompt="What is the capital of France?", role="user"),
    Message.from_prompt(prompt="Paris.", role="assistant"),
    Message.from_prompt(prompt="And of Germany?", role="user"),
]

normalized = await adapt_target.configuration.normalize_async(messages=conversation)  # type: ignore
print(f"original turns:   {len(conversation)}")
print(f"normalized turns: {len(normalized)}")
print("flattened text:")
print(normalized[-1].message_pieces[0].original_value)

# %% [markdown]
# By contrast, the `RAISE` configuration validates eagerly: any consumer requiring `MULTI_TURN` will
# get a `ValueError` before a single prompt is sent.

# %%
try:
    raise_target.configuration.ensure_can_handle(capability=CapabilityName.MULTI_TURN)
except ValueError as exc:
    print(exc)

# %% [markdown]
# ## 6. Non-adaptable capabilities
#
# Some capabilities cannot be safely emulated — for example, `supports_editable_history` is a property
# of the underlying API contract and there is no normalizer that can fake it. These capabilities are
# not represented in the `CapabilityHandlingPolicy` at all; requesting them on a target that lacks
# them always raises, regardless of policy.

# %%
no_editable_history = TargetConfiguration(
    capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=False),
)

try:
    no_editable_history.ensure_can_handle(capability=CapabilityName.EDITABLE_HISTORY)
except ValueError as exc:
    print(exc)
# ---
