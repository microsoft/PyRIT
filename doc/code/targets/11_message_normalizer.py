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
# # 11. MessageNormalizer
#
# MessageNormalizers convert PyRIT's `Message` format into other formats that specific targets require. Different LLMs and APIs expect messages in different formats:
#
# - **OpenAI-style APIs** expect `ChatMessage` objects with `role` and `content` fields
# - **HuggingFace models** expect specific chat templates (ChatML, Llama, Mistral, etc.)
# - **Some models** don't support system messages and need them merged into user messages
# - **Attack components** sometimes need conversation history as a formatted text string
#
# The `MessageNormalizer` classes handle these conversions, making it easy to work with any target regardless of its expected input format.
#
# ## Base Classes
#
# There are two base normalizer types:
# - **`MessageListNormalizer[T]`**: Converts `List[Message]` → `List[T]` (e.g., to `ChatMessage` objects)
# - **`MessageStringNormalizer`**: Converts `List[Message]` → `str` (e.g., to ChatML format)
#
# Some normalizers implement both interfaces.

# %%
from pyrit.models import Message

# Create sample messages for demonstration
system_message = Message.from_prompt(prompt="You are a helpful assistant.", role="system")
user_message = Message.from_prompt(prompt="What is the capital of France?", role="user")
assistant_message = Message.from_prompt(prompt="The capital of France is Paris.", role="assistant")
followup_message = Message.from_prompt(prompt="What about Germany?", role="user")

messages = [system_message, user_message, assistant_message, followup_message]

print("Sample messages created:")
for msg in messages:
    print(f"  {msg.role}: {msg.get_piece().converted_value[:50]}...")

# %% [markdown]
# ## ChatMessageNormalizer
#
# The `ChatMessageNormalizer` converts `Message` objects to `ChatMessage` objects, which are the standard format for OpenAI chat-based API calls. It handles both single-part text messages and multipart messages (with images, audio, etc.).
#
# Key features:
# - Single text pieces become simple string content
# - Multiple pieces become content arrays with type information
# - Supports `use_developer_role=True` for newer OpenAI models that use "developer" instead of "system"

# %%
from pyrit.message_normalizer import ChatMessageNormalizer

# Standard usage
normalizer = ChatMessageNormalizer()
chat_messages = await normalizer.normalize_async(messages)  # type: ignore[top-level-await]

print("ChatMessage output:")
for msg in chat_messages:  # type: ignore[assignment]
    print(f"  Role: {msg.role}, Content: {msg.content}")  # type: ignore[attr-defined]

# %%
# With developer role for newer OpenAI models (o1, o3, gpt-4.1+)
dev_normalizer = ChatMessageNormalizer(use_developer_role=True)
dev_chat_messages = await dev_normalizer.normalize_async(messages)  # type: ignore[top-level-await]

print("ChatMessage with developer role:")
for msg in dev_chat_messages:  # type: ignore[assignment]
    print(f"  Role: {msg.role}, Content: {msg.content}")  # type: ignore[attr-defined]

# %%
# ChatMessageNormalizer also implements MessageStringNormalizer for JSON output
json_output = await normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]
print("JSON string output:")
print(json_output)

# %% [markdown]
# ## GenericSystemSquashNormalizer
#
# Some models don't support system messages. The `GenericSystemSquashNormalizer` merges the system message into the first user message using a standardized instruction format.
#
# The format is:
# ```
# ### Instructions ###
#
# {system_content}
#
# ######
#
# {user_content}
# ```

# %%
from pyrit.message_normalizer import GenericSystemSquashNormalizer

squash_normalizer = GenericSystemSquashNormalizer()
squashed_messages = await squash_normalizer.normalize_async(messages)  # type: ignore[top-level-await]

print(f"Original message count: {len(messages)}")
print(f"Squashed message count: {len(squashed_messages)}")
print("\nFirst message after squashing:")
print(squashed_messages[0].get_piece().converted_value)

# %% [markdown]
# ## ConversationContextNormalizer
#
# The `ConversationContextNormalizer` formats conversation history as a turn-based text string. This is useful for:
# - Including conversation history in attack prompts
# - Logging and debugging conversations
# - Creating context strings for adversarial chat
#
# The output format is:
# ```
# Turn 1:
# User: <content>
# Assistant: <content>
#
# Turn 2:
# User: <content>
# ...
# ```

# %%
from pyrit.message_normalizer import ConversationContextNormalizer

context_normalizer = ConversationContextNormalizer()
context_string = await context_normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]

print("Conversation context format:")
print(context_string)

# %% [markdown]
# ## TokenizerTemplateNormalizer
#
# The `TokenizerTemplateNormalizer` uses HuggingFace tokenizer chat templates to format messages. This is essential for:
# - Local LLM inference with proper formatting
# - Matching the exact prompt format a model was trained with
# - Working with various open-source models
#
# ### Using Model Aliases
#
# For convenience, common models have aliases that automatically configure the normalizer:
#
# | Alias | Model | Notes |
# |-------|-------|-------|
# | `chatml` | HuggingFaceH4/zephyr-7b-beta | No auth required |
# | `phi3` | microsoft/Phi-3-mini-4k-instruct | No auth required |
# | `qwen` | Qwen/Qwen2-7B-Instruct | No auth required |
# | `llama3` | meta-llama/Meta-Llama-3-8B-Instruct | Requires HF token |
# | `gemma` | google/gemma-7b-it | Requires HF token, auto-squashes system |
# | `mistral` | mistralai/Mistral-7B-Instruct-v0.2 | Requires HF token |

# %%
from pyrit.message_normalizer import TokenizerTemplateNormalizer

# Using an alias (no auth required for this model)
template_normalizer = TokenizerTemplateNormalizer.from_model("chatml")
formatted = await template_normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]

print("ChatML formatted output:")
print(formatted)

# %% [markdown]
# ### System Message Behavior
#
# The `TokenizerTemplateNormalizer` supports different strategies for handling system messages:
#
# - **`keep`**: Pass system messages as-is (default)
# - **`squash`**: Merge system into first user message using `GenericSystemSquashNormalizer`
# - **`ignore`**: Drop system messages entirely
# - **`developer`**: Change system role to developer role (for newer OpenAI models)

# %%
# Using squash behavior for models that don't support system messages
squash_template_normalizer = TokenizerTemplateNormalizer.from_model("chatml", system_message_behavior="squash")
squashed_formatted = await squash_template_normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]

print("ChatML with squashed system message:")
print(squashed_formatted)

# %% [markdown]
# ### Using Custom Models
#
# You can also use any HuggingFace model with a chat template by providing the full model name.

# %%
# Using a custom HuggingFace model
# Note: Some models require authentication via HUGGINGFACE_TOKEN env var or token parameter
custom_normalizer = TokenizerTemplateNormalizer.from_model("TinyLlama/TinyLlama-1.1B-Chat-v1.0")
custom_formatted = await custom_normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]

print("TinyLlama formatted output:")
print(custom_formatted)

# %% [markdown]
# ## Creating Custom Normalizers
#
# You can create custom normalizers by extending the base classes.

# %%

from pyrit.message_normalizer import MessageStringNormalizer
from pyrit.models import Message


class SimpleMarkdownNormalizer(MessageStringNormalizer):
    """Custom normalizer that formats messages as Markdown."""

    async def normalize_string_async(self, messages: list[Message]) -> str:
        lines = []
        for msg in messages:
            piece = msg.get_piece()
            role = piece.role.capitalize()
            content = piece.converted_value
            lines.append(f"**{role}**: {content}")
        return "\n\n".join(lines)


# Use the custom normalizer
md_normalizer = SimpleMarkdownNormalizer()
md_output = await md_normalizer.normalize_string_async(messages)  # type: ignore[top-level-await]

print("Markdown formatted output:")
print(md_output)
