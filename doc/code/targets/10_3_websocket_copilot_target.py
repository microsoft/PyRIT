# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: pyrit (3.13.5)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 10.3 WebSocket Copilot Target
#
# The `WebSocketCopilotTarget` is an alternative to the `PlaywrightCopilotTarget` that is designed to be more reliable by minimizing dependence on browser automation. Instead of driving the Copilot UI, it communicates directly with Copilot over a WebSocket connection.
#
# By default, this target uses automated authentication which requires:
# - `COPILOT_USERNAME` and `COPILOT_PASSWORD` environment variables
# - Playwright installed: `pip install playwright && playwright install chromium`
#
# Some environments are not suited for automated authentication (e.g. they have security policies with retrieving tokens or have MFA). See the [Alternative Authentication](#alternative-authentication-with-manualcopilotauthenticator) section below.

# %% [markdown]
# ## Basic Usage with `PromptSendingAttack`
#
# The simplest way to interact with the `WebSocketCopilotTarget` is through the `PromptSendingAttack` class.

# %%
# type: ignore
from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.prompt_target import WebSocketCopilotTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

target = WebSocketCopilotTarget()
attack = PromptSendingAttack(objective_target=target)

objective = "Tell me a joke about AI"

result = await attack.execute_async(objective=objective)
await ConsoleAttackResultPrinter().print_conversation_async(result=result)

# %% [markdown]
# ## Multi-Turn Conversations
#
# The `WebSocketCopilotTarget` supports multi-turn conversations by leveraging Copilot's server-side conversation management. It automatically generates consistent `session_id` and `conversation_id` values for each PyRIT conversation, enabling Copilot to maintain context across multiple turns.
#
# However, this target does not support setting a system prompt nor modifying conversation history. As a result, it cannot be used with attack strategies that require altering prior messages (such as PAIR, TAP, or flip attack) or in contexts where a `PromptChatTarget` is required.
#
# Here is a simple multi-turn conversation example:

# %%
from pyrit.executor.attack import ConsoleAttackResultPrinter, MultiPromptSendingAttack
from pyrit.models import Message
from pyrit.prompt_target import WebSocketCopilotTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

target = WebSocketCopilotTarget()

prompts = [
    "I'm thinking of a number between 1 and 10.",
    "It's greater than 5.",
    "It's an even number.",
    "What number am I thinking of?",
]

messages = [Message.from_prompt(prompt=p, role="user") for p in prompts]
multi_turn_attack = MultiPromptSendingAttack(objective_target=target)

result = await multi_turn_attack.execute_async(
    objective="Engage in a multi-turn conversation about a number guessing game",
    user_messages=messages,
)

await ConsoleAttackResultPrinter().print_conversation_async(result=result)

# %% [markdown]
# ## Alternative Authentication with `ManualCopilotAuthenticator`
#
# If browser automation is not suitable for your environment, you can use the `ManualCopilotAuthenticator` instead. This authenticator accepts a pre-obtained access token that you can extract from your browser's DevTools.
#
# How to obtain the access token:
#
# 1. Open the Copilot webapp (e.g., https://m365.cloud.microsoft/chat) in a browser.
# 2. Open DevTools (F12 or Ctrl+Shift+I).
# 3. Go to the Network tab.
# 4. Filter by "Socket" connections or search for "Chathub".
# 5. Start typing in the chat to initiate a WebSocket connection.
# 6. Look for the latest WebSocket connection to `substrate.office.com/m365Copilot/Chathub`.
# 7. You may find the `access_token` in the request URL or in the request payload.
#
# You can either pass the token directly or set the `COPILOT_ACCESS_TOKEN` environment variable.

# %%
from pyrit.auth import ManualCopilotAuthenticator
from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.prompt_target import WebSocketCopilotTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

# Option 1: Pass the token directly
# auth = ManualCopilotAuthenticator(access_token="eyJ0eXAi...")

# Option 2: Use COPILOT_ACCESS_TOKEN environment variable
auth = ManualCopilotAuthenticator()

target = WebSocketCopilotTarget(authenticator=auth)
attack_manual = PromptSendingAttack(objective_target=target)

result_manual = await attack_manual.execute_async(objective="Hello! Who are you?")
await ConsoleAttackResultPrinter().print_conversation_async(result=result_manual)

# %% [markdown]
# ## Multimodal Support (Text and Images)
#
# The `WebSocketCopilotTarget` supports multimodal input, allowing you to send both text and images in a single message. Images are automatically uploaded to Copilot's file service and referenced in the conversation using the same process as the Copilot web interface.
#
# Here's an example of sending an image along with a text prompt:

# %%
from pathlib import Path

from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import WebSocketCopilotTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)

target = WebSocketCopilotTarget()
attack = PromptSendingAttack(objective_target=target)

# Replace with the path to your actual image file
image_path = Path("../converters/benign_cake_question.jpg")

# Create a multimodal message with both text and image pieces
multimodal_message = Message(
    message_pieces=[
        MessagePiece(
            role="user",
            original_value="Answer the question from the image",
            converted_value="Answer the question from the image",
            conversation_id="test_conversation",
            original_value_data_type="text",
            converted_value_data_type="text",
        ),
        MessagePiece(
            role="user",
            original_value=str(image_path),
            converted_value=str(image_path),
            conversation_id="test_conversation",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        ),
    ]
)

result = await attack.execute_async(objective="Answer the question from the image", next_message=multimodal_message)
await ConsoleAttackResultPrinter().print_conversation_async(result=result)
