# Prompt Targets

Prompt Targets are endpoints for where to send prompts. For example, a target could be a GPT-4 or Llama endpoint. Targets are typically used with other components like [attacks](../executor/attack/0_attack.md), [scorers](../scoring/0_scoring.md), and [converters](../converters/0_converters.ipynb).

- An attack's main job is to change prompts to a given format, apply any converters, and then send them off to prompt targets (sometimes using various strategies). Within an attack, prompt targets are (mostly) swappable, meaning you can use the same logic with different target endpoints.
- A scorer's main job is to score a prompt. Often, these use LLMs, in which case, a given scorer can often use different configured targets.
- A converter's job is to transform a prompt. Often, these use LLMs, in which case, a given converter can use different configured targets.

Prompt targets are found [here](https://github.com/microsoft/PyRIT/tree/main/pyrit/prompt_target/) in code.


## Send_Prompt_Async

The main entry method follow the following signature:

```
async def send_prompt_async(self, *, message: Message) -> Message:
```

A `Message` object is a normalized object with all the information a target will need to send a prompt, including a way to get a history for that prompt (in the cases that also needs to be sent). This is discussed in more depth [here](../memory/3_memory_data_types.md).

## Chat-style targets vs general targets

A `PromptTarget` is a generic place to send a prompt. With PyRIT, the idea is that it will eventually be consumed by an AI application, but that doesn't have to be immediate. For example, you could have a SharePoint target. Everything you send a prompt to is a `PromptTarget`. Many attacks work generically with any `PromptTarget` including `RedTeamingAttack` and `PromptSendingAttack`.

With some algorithms, you want to send a prompt, set a system prompt, and modify conversation history (including PAIR [@chao2023pair], TAP [@mehrotra2023tap], and flip attack [@li2024flipattack]). These algorithms require a target whose [`TargetCapabilities`](#target-capabilities) declare both `supports_multi_turn=True` and `supports_editable_history=True` — i.e. you can modify a conversation history. Consumers express this requirement via `CHAT_TARGET_REQUIREMENTS` and validate it against `target.configuration` at construction time. See [Target Capabilities](#target-capabilities) below for the full list of capabilities and how they compose into a `TargetConfiguration`.

Note: The previous `PromptChatTarget` class is **deprecated** as of v0.13.0 and will be removed in v0.15.0. Use `PromptTarget` directly with a `TargetConfiguration` declaring `supports_multi_turn=True` and `supports_editable_history=True`. See [Target Capabilities](#target-capabilities) for details.


Here are some examples:

| Example                             | Chat-style target?                                | Notes                                                                                           |
|-------------------------------------|---------------------------------------------------|-------------------------------------------------------------------------------------------------|
| **OpenAIChatTarget** (e.g., GPT-4)  | **Yes** (multi-turn + editable history)           | Designed for conversational prompts (system messages, conversation history, etc.).               |
| **OpenAIImageTarget**               | **No**                                            | Used for image generation; does not manage conversation history.                                 |
| **HTTPTarget**                      | **No**                                            | Generic HTTP target. Some apps might allow conversation history, but this target doesn't handle it. |
| **AzureBlobStorageTarget**          | **No**                                            | Used primarily for storage; not for conversation-based AI.                                       |

## Target Capabilities

Every `PromptTarget` exposes a `TargetConfiguration` (via `target.configuration`) that declares what the target natively supports. This lets attacks, converters, and scorers reason about whether a given target is suitable for a given workflow — and, where possible, adapt automatically when a capability is missing.

A `TargetConfiguration` composes three concerns:

- **`TargetCapabilities`** — an immutable, declarative description of what the target natively supports.
- **`CapabilityHandlingPolicy`** — for each capability that *can* be adapted, whether to `ADAPT` (apply a normalization step to work around the gap) or `RAISE` (fail immediately).
- **`ConversationNormalizationPipeline`** — the ordered set of normalizers derived from the gap between the declared capabilities and the policy.

### Capabilities

`TargetCapabilities` declares the following flags:

| Capability                       | Meaning                                                                                                                                  |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| `supports_multi_turn`            | The target accepts and uses conversation history (or maintains state externally, e.g. via a WebSocket).                                  |
| `supports_multi_message_pieces`  | The target accepts more than one `MessagePiece` in a single request (e.g. text + image in one message).                                  |
| `supports_editable_history`      | The conversation history can be modified after the fact. Implies `supports_multi_turn`. Required for attacks that rewrite prior turns.   |
| `supports_system_prompt`         | The target natively supports a system-role message.                                                                                      |
| `supports_json_output`           | The target supports a "json" response format that guarantees valid JSON output.                                                          |
| `supports_json_schema`           | The target supports constraining output to a caller-provided JSON schema.                                                                |
| `input_modalities`               | The set of input modality combinations the target accepts (e.g. `{text}`, `{text, image_path}`).                                         |
| `output_modalities`              | The set of output modality combinations the target produces.                                                                             |

Each target class defines defaults; instances can override individual capabilities when they depend on deployment configuration (e.g. `HTTPTarget`, `PlaywrightTarget`).

For well-known underlying models, you can look up a profile with `TargetCapabilities.get_known_capabilities(underlying_model="gpt-4o")`.

### How consumers use capabilities

Components that need a particular capability declare it as a `TargetRequirements` and validate at construction time:

```python
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS

CHAT_TARGET_REQUIREMENTS.validate(target=target)
```

`TargetRequirements.validate` collects every missing capability and raises a single `ValueError`. For one-off checks against a single capability you can also call `target.configuration.ensure_can_handle(capability=...)` directly.

### Adapting vs raising

Some capability gaps can be papered over by PyRIT itself. For example, a single-turn target can be made to *appear* multi-turn by flattening the conversation history into a single prompt before sending. The `CapabilityHandlingPolicy` controls this on a per-capability basis:

- `UnsupportedCapabilityBehavior.RAISE` — fail at construction time. This is the safe default.
- `UnsupportedCapabilityBehavior.ADAPT` — run the corresponding normalizer in the conversation pipeline before the target sees the messages.

Non-adaptable capabilities (e.g. `supports_editable_history`) are not represented in the policy at all; requesting them on a target that lacks them always raises.

### Overriding capabilities per instance

For targets whose capabilities depend on deployment (HTTP endpoints, Playwright-driven UIs, custom backends), pass a `TargetConfiguration` to the constructor:

```python
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

config = TargetConfiguration(
    capabilities=TargetCapabilities(
        supports_multi_turn=True,
        supports_editable_history=True,
        supports_system_prompt=True,
    ),
)
target = MyHTTPTarget(custom_configuration=config, ...)
```

The full implementation lives in [`pyrit/prompt_target/common/target_capabilities.py`](https://github.com/microsoft/PyRIT/blob/main/pyrit/prompt_target/common/target_capabilities.py) and [`pyrit/prompt_target/common/target_configuration.py`](https://github.com/microsoft/PyRIT/blob/main/pyrit/prompt_target/common/target_configuration.py). For runnable examples — inspecting capabilities on a real target, comparing known model profiles, and `ADAPT` vs `RAISE` in action — see [Target Capabilities](./6_1_target_capabilities.ipynb).

## Multi-Modal Targets

Like most of PyRIT, targets can be multi-modal.

- [OpenAI Chat Target](./1_openai_chat_target.ipynb) (*text + image --> text*)
- [OpenAI Image Target](./3_openai_image_target.ipynb) (*text --> image* or *text + image --> image*)
- [OpenAI Video Target](./4_openai_video_target.ipynb) (*text --> video*)
- [OpenAI TTS Target](./5_openai_tts_target.ipynb) (*text --> audio*)
