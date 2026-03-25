# Prompt Targets

Prompt Targets are endpoints for where to send prompts. For example, a target could be a GPT-4 or Llama endpoint. Targets are typically used with other components like [attacks](../executor/attack/0_attack.md), [scorers](../scoring/0_scoring.md), and [converters](../converters/0_converters.ipynb).

- An attack's main job is to change prompts to a given format, apply any converters, and then send them off to prompt targets (sometimes using various strategies). Within an attack, prompt targets are (mostly) swappable, meaning you can use the same logic with different target endpoints.
- A scorer's main job is to score a prompt. Often, these use LLMs, in which case, a given scorer can often use different configured targets.
- A converter's job is to transform a prompt. Often, these use LLMs, in which case, a given converter can use different configured targets.

Prompt targets are found [here](https://github.com/Azure/PyRIT/tree/main/pyrit/prompt_target/) in code.


## Send_Prompt_Async

The main entry method follow the following signature:

```
async def send_prompt_async(self, *, message: Message) -> Message:
```

A `Message` object is a normalized object with all the information a target will need to send a prompt, including a way to get a history for that prompt (in the cases that also needs to be sent). This is discussed in more depth [here](../memory/3_memory_data_types.md).

## PromptChatTargets vs PromptTargets

A `PromptTarget` is a generic place to send a prompt. With PyRIT, the idea is that it will eventually be consumed by an AI application, but that doesn't have to be immediate. For example, you could have a SharePoint target. Everything you send a prompt to is a `PromptTarget`. Many attacks work generically with any `PromptTarget` including `RedTeamingAttack` and `PromptSendingAttack`.

With some algorithms, you want to send a prompt, set a system prompt, and modify conversation history (including PAIR [@chao2023pair], TAP [@mehrotra2023tap], and flip attack [@li2024flipattack]). These often require a `PromptChatTarget`, which implies you can modify a conversation history. `PromptChatTarget` is a subclass of `PromptTarget`.

Here are some examples:

| Example                             | Is `PromptChatTarget`?               | Notes                                                                                           |
|-------------------------------------|---------------------------------------|-------------------------------------------------------------------------------------------------|
| **OpenAIChatTarget** (e.g., GPT-4)  | **Yes** (`PromptChatTarget`)         | Designed for conversational prompts (system messages, conversation history, etc.).               |
| **OpenAIImageTarget**               | **No** (not a `PromptChatTarget`)    | Used for image generation; does not manage conversation history.                                 |
| **HTTPTarget**                      | **No** (not a `PromptChatTarget`)    | Generic HTTP target. Some apps might allow conversation history, but this target doesn't handle it. |
| **AzureBlobStorageTarget**          | **No** (not a `PromptChatTarget`)    | Used primarily for storage; not for conversation-based AI.                                       |

## Multi-Modal Targets

Like most of PyRIT, targets can be multi-modal.

- [OpenAI Chat Target](./1_openai_chat_target.ipynb) (*text + image --> text*)
- [OpenAI Image Target](./3_openai_image_target.ipynb) (*text --> image* or *text + image --> image*)
- [OpenAI Video Target](./4_openai_video_target.ipynb) (*text --> video*)
- [OpenAI TTS Target](./5_openai_tts_target.ipynb) (*text --> audio*)
