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

# %% [markdown]
# ## 7. Discovering live target capabilities
#
# Declared capabilities describe what a target *should* support. For deployments where the actual
# behavior is uncertain — custom OpenAI-compatible endpoints, gateways that strip features, models
# whose support drifts over time — you can probe what the target *actually* accepts at runtime with
# `discover_target_capabilities_async`. It runs both the boolean capability probes and the input
# modality probes and returns a best-effort `TargetCapabilities`.
#
# Internally it walks each capability that has a registered probe (currently
# `SYSTEM_PROMPT`, `MULTI_MESSAGE_PIECES`, `MULTI_TURN`, `JSON_OUTPUT`, `JSON_SCHEMA`), sends a
# minimal request, and includes the capability in the result only if the call succeeds.
# During probing the target's configuration is temporarily replaced with a permissive one so
# `ensure_can_handle` does not short-circuit a probe for a capability the target declares as
# unsupported. The original configuration is restored before the function returns. The same
# treatment is applied to each input modality combination declared in
# `capabilities.input_modalities`, sending a small payload built from optional `test_assets`.
#
# Each probe call is bounded by `per_probe_timeout_s` (default 30s) and is retried once on
# transient errors before being declared failed. The returned `TargetCapabilities` is a merged
# view: probed where possible, declared where probing is unavailable or out of scope.
# "Supported" here means *the request was accepted* — a target that silently ignores a system
# prompt or `response_format` directive will still be reported as supporting that capability.
#
# This function is **not safe to call concurrently** with other operations on the same target
# instance: it temporarily mutates `target._configuration` and writes probe rows to
# `target._memory`. Probe-written memory rows are tagged with
# `prompt_metadata["capability_probe"] == "1"` so consumers can filter them.
#
# Typical usage against a real endpoint:
#
# ```python
# from pyrit.prompt_target import discover_target_capabilities_async
#
# queried = await discover_target_capabilities_async(target=target)
# print(queried)
# ```
#
# Below we mock the target's underlying transport (`_send_prompt_to_target_async`) so the notebook
# stays self-contained — the result shape is the same as a live run. We mock the protected method
# rather than `send_prompt_async` so the probe still exercises the real validation and memory
# pipeline.

# %%
from unittest.mock import AsyncMock

from pyrit.models import MessagePiece
from pyrit.prompt_target import discover_target_capabilities_async


def _ok_response():
    return [
        Message(
            [
                MessagePiece(
                    role="assistant",
                    original_value="ok",
                    original_value_data_type="text",
                    conversation_id="probe",
                    response_error="none",
                )
            ]
        )
    ]


probe_target = OpenAIChatTarget(model_name="gpt-4o", endpoint="https://example.invalid/", api_key="sk-not-a-real-key")
probe_target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

queried = await discover_target_capabilities_async(target=probe_target, per_probe_timeout_s=5.0)  # type: ignore
print("discover_target_capabilities_async result:")
print(f"  supports_multi_turn:           {queried.supports_multi_turn}")
print(f"  supports_system_prompt:        {queried.supports_system_prompt}")
print(f"  supports_multi_message_pieces: {queried.supports_multi_message_pieces}")
print(f"  supports_json_output:          {queried.supports_json_output}")
print(f"  supports_json_schema:          {queried.supports_json_schema}")
print(f"  input_modalities:              {sorted(sorted(m) for m in queried.input_modalities)}")

# %% [markdown]
# To narrow the probe to specific capabilities (faster, fewer calls), pass `capabilities=`:
#
# ```python
# from pyrit.prompt_target.common.target_capabilities import CapabilityName
#
# queried = await discover_target_capabilities_async(
#     target=target,
#     capabilities=[CapabilityName.JSON_SCHEMA, CapabilityName.SYSTEM_PROMPT],
# )
# ```
#
# Similarly, narrow the modality probe set with `test_modalities=` and override the
# packaged default probe assets with `test_assets=`.

# %% [markdown]
# ### Discovering undeclared modalities
#
# By default `discover_target_capabilities_async` only probes modality combinations the target already
# **declares** in `capabilities.input_modalities`. For an OpenAI-compatible endpoint that
# claims text-only but might actually accept images, pass `test_modalities=` explicitly to
# probe combinations beyond the declared baseline. Provide `test_assets=` as well if you need
# to override the packaged defaults or probe a modality without one:
#
# ```python
# queried = await discover_target_capabilities_async(
#     target=target,
#     test_modalities={frozenset({"text"}), frozenset({"text", "image_path"})},
#     test_assets={"image_path": "/path/to/test_image.png"},
# )
# ```
#
# Similarly, when narrowing the probe set with `capabilities=`, capabilities NOT in the
# narrowed set are copied from the target's declared values rather than being reset to
# `False` — narrowing controls *what is re-queried*, not what the returned dataclass
# reports. This makes incremental probing safe:
#
# ```python
# # Re-query only JSON support; other declared flags pass through unchanged.
# queried = await discover_target_capabilities_async(
#     target=target,
#     capabilities={CapabilityName.JSON_OUTPUT, CapabilityName.JSON_SCHEMA},
# )
# ```

# %% [markdown]
# ## 8. Applying probed capabilities back onto the target
#
# `discover_target_capabilities_async` is intentionally pure: it returns a `TargetCapabilities` without
# mutating the target. That lets you inspect (or diff against the declared view, log, gate on
# the result) before committing. Once you're satisfied, call `target.apply_capabilities(...)`
# to install the probed view on the instance. The target's existing
# `CapabilityHandlingPolicy` is preserved — policy expresses user intent (ADAPT vs RAISE),
# which is independent of what the probe found.
#
# Why a two-step pattern rather than auto-apply? Probe results are an upper bound
# ("the request was accepted"); a target that silently ignores a feature still passes its
# probe. Keeping discovery separate from application lets callers diff, log, persist, or
# reject the result before it affects subsequent sends.
#
# Below is the end-to-end pattern: construct a target whose declared capabilities are
# pessimistic, discover what the endpoint actually accepts, diff the two views, then apply.

# %%
# Start with an instance that declares fewer capabilities than the endpoint actually has,
# e.g. a custom gateway whose support we're unsure about.
pessimistic_config = TargetConfiguration(
    capabilities=TargetCapabilities(
        supports_multi_turn=False,
        supports_system_prompt=False,
        supports_multi_message_pieces=False,
        supports_json_output=False,
        supports_json_schema=False,
        # Editable history has no live probe and falls back to the declared value.
        # Declare it True here so the probed view inherits it.
        supports_editable_history=True,
    ),
)
endpoint_target = OpenAIChatTarget(
    model_name="custom-model",
    endpoint="https://example.invalid/",
    api_key="sk-not-a-real-key",
    custom_configuration=pessimistic_config,
)
endpoint_target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

print("declared (before probing):")
print(f"  supports_multi_turn:           {endpoint_target.capabilities.supports_multi_turn}")
print(f"  supports_system_prompt:        {endpoint_target.capabilities.supports_system_prompt}")
print(f"  supports_json_output:          {endpoint_target.capabilities.supports_json_output}")

# Step 1: discover. No mutation yet — `endpoint_target.capabilities` is unchanged.
probed_caps = await discover_target_capabilities_async(target=endpoint_target, per_probe_timeout_s=5.0)  # type: ignore

print("\nprobed (returned from discover_target_capabilities_async, target NOT yet updated):")
print(f"  supports_multi_turn:           {probed_caps.supports_multi_turn}")
print(f"  supports_system_prompt:        {probed_caps.supports_system_prompt}")
print(f"  supports_json_output:          {probed_caps.supports_json_output}")
print(f"  target.capabilities.supports_multi_turn (still declared): {endpoint_target.capabilities.supports_multi_turn}")

# Step 2: diff — see exactly what the probe upgraded.
declared = pessimistic_config.capabilities
upgraded = [
    name
    for name in (
        "supports_multi_turn",
        "supports_system_prompt",
        "supports_multi_message_pieces",
        "supports_json_output",
        "supports_json_schema",
    )
    if getattr(probed_caps, name) and not getattr(declared, name)
]
print(f"\nflags probed True that were declared False: {upgraded}")

# Step 3: apply. Policy is preserved; the normalization pipeline is rebuilt.
original_policy = endpoint_target.configuration.policy
endpoint_target.apply_capabilities(capabilities=probed_caps)

print("\nafter apply_capabilities:")
print(f"  supports_multi_turn:           {endpoint_target.capabilities.supports_multi_turn}")
print(f"  supports_system_prompt:        {endpoint_target.capabilities.supports_system_prompt}")
print(f"  supports_json_output:          {endpoint_target.capabilities.supports_json_output}")
print(f"  policy preserved:              {endpoint_target.configuration.policy is original_policy}")

# Subsequent consumer checks now reflect the probed reality — for example, a chat-style
# requirement that would have failed against the pessimistic declaration now passes.
CHAT_TARGET_REQUIREMENTS.validate(target=endpoint_target)
print("\nCHAT_TARGET_REQUIREMENTS.validate now passes against the probed target")
